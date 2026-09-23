"""Upgrading a version 1 Gmail staging manifest with re-acquired label facts.

Manifest version 2 requires two fields on every Gmail record — ``payload.intake_class``
and ``payload.gmail_labels`` — precisely so that a draft, a Spam or a Trash message is
refused on the evidence it carries rather than on the operator's say-so. A version 1 file
cannot be upgraded by guessing them: they are facts about the message that only a fresh
look at the mailbox can supply.

So this module upgrades a manifest **against a separate acquisition file**: a local JSON
document recording, per message id, the labels the mailbox really carries — plus the
sender and the date, which are re-checked against what the old manifest already claimed.
Acquisition and staging stay separate steps, as they are everywhere else in this
directory: **this module opens no network connection and imports no Google client.** It
reads two local files a human can diff and refuse.

The upgrade is deliberately narrow. It adds the two required fields and nothing else. It
invents no observation, drops none, renumbers nothing, and touches no external id: a
record that came out of the September sweep must land in the database as the same record
it was, or the rebuild is not a rebuild. Every disagreement between the two files — a
missing id, an extra id, a sender that changed, a date that changed, a message that has
since been trashed — refuses the whole pass rather than the one record, because a
manifest that silently lost a record is exactly the failure R1 was.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.qa.mailbox_intake_inventory import classify_intake_folder

from .manifest import MANIFEST_VERSION

#: The acquisition-file schema this module understands.
ACQUISITION_VERSION = 1

#: The manifest version this module upgrades *from*. There is one upgrade path, not a
#: general migration framework: version 1 is the only shape that ever reached a database
#: without the two label fields.
UPGRADABLE_FROM_VERSION = 1

#: Intake classes that mean "this message is not evidence". A label mapping onto any of
#: them refuses the pass; the labels win over anything the file declares.
EXCLUDED_INTAKE_CLASSES: frozenset[str] = frozenset(
    {"metadata_only", "excluded_spam", "excluded_trash"}
)


class AcquisitionRefused(Exception):
    """The acquisition file, or the upgrade it was asked for, is not something this tool
    will produce. Nothing was written."""


@dataclass(frozen=True)
class MessageFacts:
    """What one read-only look at the mailbox recorded about one message."""

    gmail_labels: tuple[str, ...]
    sender: str | None
    date: str | None


@dataclass(frozen=True)
class Acquisition:
    provider: str
    acquired_at: str
    method: str
    path: str
    messages: dict[str, MessageFacts]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AcquisitionRefused(message)


def load_acquisition(path: str | Path) -> Acquisition:
    """Read and fully validate an acquisition file. Nothing partial is ever returned."""
    file_path = Path(path)
    _require(file_path.is_file(), f"no such acquisition file: {file_path}")
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AcquisitionRefused(f"{file_path}: unreadable acquisition file ({exc})") from None
    return parse_acquisition(raw, source_path=str(file_path))


def parse_acquisition(raw: Any, *, source_path: str = "<memory>") -> Acquisition:
    _require(isinstance(raw, dict), "the acquisition file must be a JSON object")
    _require(
        raw.get("acquisition_version") == ACQUISITION_VERSION,
        f"acquisition_version must be {ACQUISITION_VERSION}",
    )
    provider = raw.get("provider")
    _require(provider == "gmail", f"provider must be 'gmail' — got {provider!r}")

    acquired_at = raw.get("acquired_at")
    _require(
        isinstance(acquired_at, str) and acquired_at.strip() != "",
        "acquired_at is required: an acquisition with no date cannot be audited",
    )
    method = raw.get("method")
    _require(
        isinstance(method, str) and method.strip() != "",
        "method is required: it is the record of how the mailbox was read, and a "
        "read-only acquisition that does not say so proves nothing",
    )

    entries = raw.get("messages")
    _require(isinstance(entries, dict), "messages must be an object keyed by message id")
    _require(bool(entries), "messages is empty — there is nothing to upgrade against")

    messages: dict[str, MessageFacts] = {}
    for external_id, facts in entries.items():
        where = f"messages[{external_id!r}]"
        _require(
            isinstance(external_id, str) and external_id.strip() != "",
            f"{where}: a message id must be a non-empty string",
        )
        _require(isinstance(facts, dict), f"{where} must be an object")
        labels = facts.get("gmail_labels")
        _require(
            isinstance(labels, list)
            and bool(labels)
            and all(isinstance(x, str) and x.strip() != "" for x in labels),
            f"{where}: gmail_labels must be a non-empty list of non-empty strings",
        )
        for field in ("sender", "date"):
            value = facts.get(field)
            _require(
                value is None or isinstance(value, str),
                f"{where}: {field} must be a string when present",
            )
        messages[external_id.strip()] = MessageFacts(
            gmail_labels=tuple(label.strip() for label in labels),
            sender=facts.get("sender"),
            date=facts.get("date"),
        )

    return Acquisition(
        provider=provider,
        acquired_at=acquired_at,
        method=method,
        path=source_path,
        messages=messages,
    )


def intake_class_for(labels: tuple[str, ...] | list[str], *, where: str) -> str:
    """The intake class the labels themselves establish.

    Inbox or Sent is ``primary_evidence``. Anything left over — a message carrying only
    ``IMPORTANT``, ``STARRED`` or a user label — is archived mail, which is a replay
    candidate rather than primary evidence. A Draft, Spam or Trash label refuses.
    """
    classes = [classify_intake_folder(label) for label in labels]
    for label, label_class in zip(labels, classes):
        _require(
            label_class not in EXCLUDED_INTAKE_CLASSES,
            f"{where}: the label {label!r} classifies as {label_class}, which intake "
            "excludes by rule. This message is no longer stageable evidence and the "
            "whole upgrade is refused rather than quietly dropping the record",
        )
    return "primary_evidence" if "primary_evidence" in classes else "archived"


def upgrade_manifest(raw: Any, acquisition: Acquisition, *, source_path: str = "<memory>") -> dict:
    """Return the version 2 manifest this version 1 manifest and acquisition agree on.

    The input manifest is never mutated and the returned document is a new object.
    """
    _require(isinstance(raw, dict), "the manifest must be a JSON object")
    _require(
        raw.get("manifest_version") == UPGRADABLE_FROM_VERSION,
        f"{source_path}: this upgrades a manifest_version {UPGRADABLE_FROM_VERSION} file "
        f"— got {raw.get('manifest_version')!r}",
    )
    provider = raw.get("provider")
    _require(
        provider == acquisition.provider,
        f"{source_path}: provider is {provider!r} but the acquisition describes "
        f"{acquisition.provider!r} messages",
    )

    entries = raw.get("records")
    _require(isinstance(entries, list) and bool(entries), "records must be a non-empty list")

    manifest_ids = []
    for index, entry in enumerate(entries):
        _require(isinstance(entry, dict), f"records[{index}] must be an object")
        external_id = entry.get("external_id")
        _require(
            isinstance(external_id, str) and external_id.strip() != "",
            f"records[{index}]: external_id is required and must be a non-empty string",
        )
        manifest_ids.append(external_id.strip())

    seen: set[str] = set()
    for external_id in manifest_ids:
        _require(
            external_id not in seen,
            f"external_id {external_id!r} appears twice in the same manifest",
        )
        seen.add(external_id)

    missing = [i for i in manifest_ids if i not in acquisition.messages]
    extra = sorted(set(acquisition.messages) - seen)
    _require(
        not missing,
        f"the acquisition is missing {len(missing)} of the manifest's "
        f"{len(manifest_ids)} messages — re-acquire them or refuse the batch, but do "
        f"not stage a record whose labels nobody looked at: {missing}",
    )
    _require(
        not extra,
        f"the acquisition holds {len(extra)} message(s) the manifest does not: {extra}. "
        "This tool upgrades a manifest in place; it never adds evidence to one",
    )

    records: list[dict] = []
    for index, entry in enumerate(entries):
        external_id = entry["external_id"].strip()
        where = f"records[{index}] ({external_id})"
        facts = acquisition.messages[external_id]

        payload = entry.get("payload", {})
        _require(isinstance(payload, dict), f"{where}: payload must be an object")
        for field in ("intake_class", "gmail_labels"):
            _require(
                field not in payload,
                f"{where}: payload already carries {field!r}. A version 1 manifest that "
                "already holds the version 2 fields is inconsistent with itself and is "
                "refused rather than half-upgraded",
            )

        declared_from = payload.get("from")
        if declared_from is not None and facts.sender is not None:
            _require(
                str(declared_from).strip().lower() == facts.sender.strip().lower(),
                f"{where}: the manifest records a different sender than the mailbox now "
                "reports. The id no longer identifies the message the manifest describes",
            )
        declared_date = payload.get("message_date")
        if declared_date is not None and facts.date is not None:
            _require(
                str(declared_date).strip() == facts.date.strip(),
                f"{where}: the manifest records message_date {declared_date!r} but the "
                f"mailbox reports {facts.date!r}. The id no longer identifies the same "
                "message",
            )

        new_payload = dict(payload)
        new_payload["intake_class"] = intake_class_for(facts.gmail_labels, where=where)
        new_payload["gmail_labels"] = list(facts.gmail_labels)

        new_entry = dict(entry)
        new_entry["payload"] = new_payload
        records.append(new_entry)

    upgraded = dict(raw)
    upgraded["manifest_version"] = MANIFEST_VERSION
    upgraded["records"] = records
    upgraded["reacquired"] = {
        "from_manifest_version": UPGRADABLE_FROM_VERSION,
        "acquired_at": acquisition.acquired_at,
        "method": acquisition.method,
        "messages": len(records),
    }
    return upgraded


def summarize(upgraded: dict) -> dict:
    """PII-safe counts for the console: how many records, and how they classify.

    No id, address, subject or label value belonging to one record is printed — only the
    label *vocabulary* the batch used and how many records each intake class took.
    """
    records = upgraded["records"]
    by_class: dict[str, int] = {}
    labels: set[str] = set()
    for record in records:
        payload = record["payload"]
        by_class[payload["intake_class"]] = by_class.get(payload["intake_class"], 0) + 1
        labels.update(payload["gmail_labels"])
    return {
        "manifest_version": upgraded["manifest_version"],
        "records": len(records),
        "observations": sum(len(r.get("observations", [])) for r in records),
        "intake_class": dict(sorted(by_class.items())),
        "label_vocabulary": sorted(labels),
        "acquired_at": upgraded["reacquired"]["acquired_at"],
    }


__all__ = [
    "ACQUISITION_VERSION",
    "EXCLUDED_INTAKE_CLASSES",
    "UPGRADABLE_FROM_VERSION",
    "Acquisition",
    "AcquisitionRefused",
    "MessageFacts",
    "intake_class_for",
    "load_acquisition",
    "parse_acquisition",
    "summarize",
    "upgrade_manifest",
]

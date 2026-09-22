"""Reading and validating a Gmail/Drive staging manifest.

**This module opens no network connection and imports no Google client.** A staging pass
reads a manifest file an operator produced out-of-band and nothing else. That is not a
temporary shortcut until credentials arrive: it is the boundary. Evidence acquisition and
evidence staging are separate steps so that what reaches the database is always a file a
human could read first, diff, and refuse.

The manifest is deliberately dull — a provider, a list of records, and for each record the
observations it supports. It carries no resolution, no identity, no confirmation and no
merge instruction, because a staging pass is not allowed to decide any of those.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: The manifest schema this module understands. A file declaring anything else is refused
#: rather than read leniently: a silently-misread provenance manifest is the one input that
#: could attribute a fact to the wrong source.
MANIFEST_VERSION = 1

#: provider → the `evidence.source_record.kind` it lands as. Both were added by
#: `supabase/migrations/20260921120000_slice2_gmail_drive_evidence_kinds.sql`.
PROVIDER_SOURCE_KIND: dict[str, str] = {
    "gmail": "gmail_message",
    "drive": "drive_file",
}

#: What each provider is allowed to assert.
#:
#: A message yields addresses and names. A document yields a reference to itself and,
#: sometimes, the organization it names. Neither may assert an affiliation, a postal
#: address or a contacted-address fact: an affiliation is a relationship nobody wrote down
#: in a message header, and `contacted_address` is a claim about *our own* outbound history
#: that only the send ledger may make.
PROVIDER_ASSERTION_KINDS: dict[str, frozenset[str]] = {
    "gmail": frozenset({"contact_address", "organization_name"}),
    "drive": frozenset({"document_reference", "organization_name"}),
}


class ManifestRefused(Exception):
    """The manifest is not something this tool will stage. Nothing was opened."""


@dataclass(frozen=True)
class Observation:
    kind: str
    value: str
    value_norm: str
    value_payload: dict[str, Any] | None


@dataclass(frozen=True)
class StagedRecord:
    external_id: str
    source_kind: str
    dedupe_key: str
    source_uri: str | None
    acquired_at: datetime | None
    payload: dict[str, Any]
    observations: tuple[Observation, ...]


@dataclass(frozen=True)
class Manifest:
    provider: str
    source_kind: str
    path: str
    note: str | None
    records: tuple[StagedRecord, ...]

    @property
    def observation_count(self) -> int:
        return sum(len(record.observations) for record in self.records)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ManifestRefused(message)


def _normalize(kind: str, value: str) -> str:
    """Fold a value the same way the rest of V2 folds it.

    An address is case-insensitive and is compared folded everywhere else in this codebase,
    so folding it here is what lets a staged observation deduplicate against one already
    recorded. A document reference and an organization name are folded for the same reason
    and nothing more is inferred from either.
    """
    folded = " ".join(value.strip().split()).lower()
    _require(folded != "", f"{kind}: an observation value may not be blank")
    if kind == "contact_address":
        # The delimiter check comes first so a pasted `To:` header is diagnosed as what it
        # is, rather than as a generic malformed address.
        _require(
            not any(ch in folded for ch in ",;<>"),
            "contact_address: an observation carries one address, not a header list",
        )
        _require(
            folded.count("@") == 1 and not folded.startswith("@") and not folded.endswith("@"),
            f"contact_address: {value!r} is not a single email address",
        )
    return folded


def _parse_timestamp(raw: Any, where: str) -> datetime | None:
    if raw is None:
        return None
    _require(isinstance(raw, str), f"{where}: acquired_at must be an ISO-8601 string")
    text = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ManifestRefused(f"{where}: acquired_at is not ISO-8601 ({exc})") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def load_manifest(path: str | Path) -> Manifest:
    """Read and fully validate a manifest. Nothing partial is ever returned."""
    file_path = Path(path)
    _require(file_path.is_file(), f"no such manifest file: {file_path}")
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestRefused(f"{file_path}: unreadable manifest ({exc})") from None
    return parse_manifest(raw, source_path=str(file_path))


def parse_manifest(raw: Any, *, source_path: str = "<memory>") -> Manifest:
    _require(isinstance(raw, dict), "the manifest must be a JSON object")
    _require(
        raw.get("manifest_version") == MANIFEST_VERSION,
        f"manifest_version must be {MANIFEST_VERSION}",
    )

    provider = raw.get("provider")
    _require(
        provider in PROVIDER_SOURCE_KIND,
        f"provider must be one of {', '.join(sorted(PROVIDER_SOURCE_KIND))}",
    )
    source_kind = PROVIDER_SOURCE_KIND[provider]
    allowed_kinds = PROVIDER_ASSERTION_KINDS[provider]

    note = raw.get("note")
    _require(note is None or isinstance(note, str), "note must be a string when present")

    entries = raw.get("records")
    _require(isinstance(entries, list), "records must be a list")
    _require(bool(entries), "records is empty — there is nothing to stage")

    records: list[StagedRecord] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(entries):
        where = f"records[{index}]"
        _require(isinstance(entry, dict), f"{where} must be an object")

        external_id = entry.get("external_id")
        _require(
            isinstance(external_id, str) and external_id.strip() != "",
            f"{where}: external_id is required and must be a non-empty string",
        )
        external_id = external_id.strip()
        _require(
            external_id not in seen_ids,
            f"{where}: external_id {external_id!r} appears twice in the same manifest",
        )
        seen_ids.add(external_id)

        payload = entry.get("payload", {})
        _require(isinstance(payload, dict), f"{where}: payload must be an object")

        source_uri = entry.get("source_uri")
        _require(
            source_uri is None or isinstance(source_uri, str),
            f"{where}: source_uri must be a string when present",
        )

        observations_raw = entry.get("observations")
        _require(isinstance(observations_raw, list), f"{where}: observations must be a list")
        _require(
            bool(observations_raw),
            f"{where}: a record with no observation stages nothing and is refused",
        )

        observations: list[Observation] = []
        seen_observations: set[tuple[str, str]] = set()
        for obs_index, obs in enumerate(observations_raw):
            obs_where = f"{where}.observations[{obs_index}]"
            _require(isinstance(obs, dict), f"{obs_where} must be an object")
            kind = obs.get("kind")
            _require(
                kind in allowed_kinds,
                f"{obs_where}: a {provider} record may assert only "
                f"{', '.join(sorted(allowed_kinds))} — got {kind!r}",
            )
            value = obs.get("value")
            _require(isinstance(value, str), f"{obs_where}: value must be a string")
            value_norm = _normalize(kind, value)
            _require(
                (kind, value_norm) not in seen_observations,
                f"{obs_where}: ({kind}, {value_norm!r}) is asserted twice by the same record",
            )
            seen_observations.add((kind, value_norm))
            value_payload = obs.get("value_payload")
            _require(
                value_payload is None or isinstance(value_payload, dict),
                f"{obs_where}: value_payload must be an object when present",
            )
            observations.append(
                Observation(
                    kind=kind,
                    value=value.strip(),
                    value_norm=value_norm,
                    value_payload=value_payload,
                )
            )

        records.append(
            StagedRecord(
                external_id=external_id,
                source_kind=source_kind,
                dedupe_key=f"{source_kind}:{external_id}",
                source_uri=source_uri,
                acquired_at=_parse_timestamp(entry.get("acquired_at"), where),
                payload=payload,
                observations=tuple(observations),
            )
        )

    return Manifest(
        provider=provider,
        source_kind=source_kind,
        path=source_path,
        note=note,
        records=tuple(records),
    )


__all__ = [
    "MANIFEST_VERSION",
    "PROVIDER_ASSERTION_KINDS",
    "PROVIDER_SOURCE_KIND",
    "Manifest",
    "ManifestRefused",
    "Observation",
    "StagedRecord",
    "load_manifest",
    "parse_manifest",
]

"""Operator review of staged Gmail quotation candidates — files in, a decision ledger out.

The 2026-09-24 quote-evidence staging (`audits/quote-evidence-staging-20260924/`, outside
Git) holds 689 source-record-shaped candidates. Only the 140 Gmail ones (queues Q1–Q3) can
ever become a `status='sent'` quote revision, because only they carry an exact RFC Message-ID
reconciled to one Gmail message. This module lets an operator review those 140 and say, for
each one, what it is. It does nothing else:

- **Nothing is created.** No `crm.opportunity`, `crm.quote`, `crm.quote_revision` or
  `evidence.*` row, and no database connection at all — this module imports no driver.
  The staging files are opened read-only; the only file written is the decision ledger,
  which is append-only JSONL. A later, separate load reads the ledger; this module never
  applies it.
- **Nothing is linked by resemblance.** The opportunity a quotation belongs to is typed by
  the operator as a UUID, or recorded as "create a new one" — never inferred from an
  organization name, an address domain, a subject, a filename or any similarity score. The
  review items carry no suggested opportunity.
- **A `CN<serial>` is a proposal.** Filename tokens are shown as proposed numbers. Confirming
  one is an operator act recorded with its basis; an ambiguous item (zero or several
  proposals) has no proposal to confirm, so the operator must type the number and say why.
- **A shared number is a warning, not a verdict.** The same number on several messages is
  a resend/revision hint; it never blocks a decision and never merges anything.
- **Legacy L1 records stay historical.** They have no Message-ID, so they are counted and
  refused as decision targets rather than offered for review.
- **A rejection is kept.** Every decision is appended, never rewritten; the latest one per
  message is its current status, and the whole history stays readable.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.header import decode_header, make_header
from pathlib import Path
from typing import Any

QUEUE_FILE = "operator_review_queue.csv"
SOURCE_RECORDS_FILE = "evidence_source_records.json"

REVIEWABLE_QUEUES = (
    "Q1_gmail_single_cn",
    "Q2_gmail_zero_or_multiple_cn",
    "Q3_gmail_direction_unclear",
)
HISTORICAL_QUEUES = ("Q4_legacy_historical", "Q5_legacy_unnumbered")
GMAIL_KIND = "gmail_message"

DECISION_CONFIRM = "confirm_customer_quotation"
DECISION_REJECT = "reject_non_quotation"
DECISION_PENDING = "leave_pending"
DECISIONS = (DECISION_CONFIRM, DECISION_REJECT, DECISION_PENDING)

OPPORTUNITY_EXISTING = "existing"
OPPORTUNITY_CREATE_NEW = "create_new"
OPPORTUNITY_MODES = (OPPORTUNITY_EXISTING, OPPORTUNITY_CREATE_NEW)

BASIS_CONFIRMED_PROPOSAL = "confirmed_proposal"
BASIS_OPERATOR_ENTERED = "operator_entered"
QUOTE_NUMBER_BASES = (BASIS_CONFIRMED_PROPOSAL, BASIS_OPERATOR_ENTERED)

STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"

WARNING_SHARED_CN = "shared_cn"
WARNING_CROSS_ERA_CN = "cross_era_cn"
WARNING_SHARED_DOCUMENT = "shared_document_bytes"
WARNING_AMBIGUOUS_CN = "ambiguous_cn"
WARNING_NO_CN = "no_cn"
WARNING_DIRECTION_UNCLEAR = "direction_unclear"
WARNING_QUOTE_DOC_WITHOUT_CN = "quote_document_without_cn"

_QUOTE_NUMBER_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,39}$")
_QUOTE_WORD_RE = re.compile(r"(?i)cotiz")


class ReviewRefused(ValueError):
    """A decision the review will not record, with the reason the operator sees."""


class StagingInconsistent(ValueError):
    """The staging files disagree with each other; nothing is reviewed until they agree."""


@dataclass(frozen=True)
class ReviewDocument:
    source_attachment_id: int
    filename: str
    sha256: str
    bytes_hash_verified: bool
    cn_tokens: tuple[str, ...]
    stored_path: str | None
    # The exact Drive file id a migration manifest records for these bytes, when one does.
    # Drive evidence is matched on this id only — never a filename, subject or client.
    drive_file_id: str | None = None


@dataclass(frozen=True)
class ReviewItem:
    email_id: int
    queue: str
    dedupe_key: str
    source_uri: str
    gmail_message_id: str
    gmail_thread_id: str | None
    rfc822_message_id: str | None
    raw_sha256: str | None
    gmail_label_ids: tuple[str, ...]
    sender: str
    recipients: str
    subject_raw: str
    subject: str
    sent_at: str
    direction_hint: str
    documents: tuple[ReviewDocument, ...]
    proposed_quote_numbers: tuple[str, ...]
    same_number_other_emails: tuple[int, ...]
    same_document_other_emails: tuple[int, ...]
    cross_era_number_collision: bool
    warnings: tuple[str, ...]
    missing: tuple[str, ...]
    source_record_sha256: str

    @property
    def cn_is_ambiguous(self) -> bool:
        return len(self.proposed_quote_numbers) != 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "email_id": self.email_id,
            "queue": self.queue,
            "dedupe_key": self.dedupe_key,
            "source_uri": self.source_uri,
            "gmail_message_id": self.gmail_message_id,
            "gmail_thread_id": self.gmail_thread_id,
            "rfc822_message_id": self.rfc822_message_id,
            "raw_sha256": self.raw_sha256,
            "gmail_label_ids": list(self.gmail_label_ids),
            "sender": self.sender,
            "recipients": self.recipients,
            "subject_raw": self.subject_raw,
            "subject": self.subject,
            "sent_at": self.sent_at,
            "direction_hint": self.direction_hint,
            "documents": [
                {
                    "source_attachment_id": d.source_attachment_id,
                    "filename": d.filename,
                    "sha256": d.sha256,
                    "bytes_hash_verified": d.bytes_hash_verified,
                    "cn_tokens": list(d.cn_tokens),
                    "stored_path": d.stored_path,
                    "drive_file_id": d.drive_file_id,
                }
                for d in self.documents
            ],
            "proposed_quote_numbers": list(self.proposed_quote_numbers),
            "same_number_other_emails": list(self.same_number_other_emails),
            "same_document_other_emails": list(self.same_document_other_emails),
            "cross_era_number_collision": self.cross_era_number_collision,
            "warnings": list(self.warnings),
            "missing": list(self.missing),
            "source_record_sha256": self.source_record_sha256,
        }


@dataclass(frozen=True)
class Staging:
    items: tuple[ReviewItem, ...]
    historical_record_count: int
    historical_email_ids: frozenset[int]

    def item(self, email_id: int) -> ReviewItem:
        for it in self.items:
            if it.email_id == email_id:
                return it
        if email_id in self.historical_email_ids:
            raise ReviewRefused(
                f"email {email_id} is a legacy L1 record: historical evidence only, "
                "it has no Message-ID and cannot be confirmed as a sent quotation"
            )
        raise ReviewRefused(f"email {email_id} is not in the Q1–Q3 review queue")


@dataclass
class ItemState:
    status: str = STATUS_PENDING
    latest: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)


def decode_subject(raw: str) -> str:
    """RFC 2047 decode for display only; the raw subject is kept beside it."""
    if not raw or "=?" not in raw:
        return raw or ""
    try:
        return str(make_header(decode_header(raw))).strip()
    except (LookupError, UnicodeDecodeError, ValueError):
        return raw


def _canonical_sha256(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _ids(raw: str) -> tuple[int, ...]:
    return tuple(sorted(int(x) for x in (raw or "").split() if x.strip()))


def _truthy(raw: str) -> bool:
    return (raw or "").strip().lower() == "true"


def load_staging(staging_dir: Path) -> Staging:
    """Read the queue and the source records, and refuse if they disagree on any identifier."""
    with (staging_dir / QUEUE_FILE).open(newline="", encoding="utf-8") as f:
        queue_rows = list(csv.DictReader(f))
    with (staging_dir / SOURCE_RECORDS_FILE).open(encoding="utf-8") as f:
        records = json.load(f)

    records_by_email: dict[int, dict[str, Any]] = {}
    for rec in records:
        email_id = int(rec["payload"]["source_email_id"])
        if email_id in records_by_email:
            raise StagingInconsistent(f"email {email_id} has more than one source record")
        records_by_email[email_id] = rec

    # Exact document bytes shared across Gmail messages: a resend hint, computed from hashes
    # only — never from filenames.
    emails_by_sha: dict[str, set[int]] = {}
    for email_id, rec in records_by_email.items():
        if rec["kind"] != GMAIL_KIND:
            continue
        for doc in rec["payload"]["documents"]:
            emails_by_sha.setdefault(doc["sha256"], set()).add(email_id)

    items: list[ReviewItem] = []
    historical: set[int] = set()
    seen_gmail_ids: set[str] = set()
    for row in queue_rows:
        email_id = int(row["email_id"])
        queue = row["queue"]
        rec = records_by_email.get(email_id)
        if rec is None:
            raise StagingInconsistent(f"queue row {email_id} has no source record")
        if queue in HISTORICAL_QUEUES:
            if rec["kind"] == GMAIL_KIND:
                raise StagingInconsistent(f"email {email_id} is Gmail but queued as legacy")
            historical.add(email_id)
            continue
        if queue not in REVIEWABLE_QUEUES:
            raise StagingInconsistent(f"email {email_id} has unknown queue {queue!r}")
        if rec["kind"] != GMAIL_KIND:
            raise StagingInconsistent(f"email {email_id} is queued {queue} but is {rec['kind']}")
        items.append(_build_item(row, rec, emails_by_sha))
        gid = items[-1].gmail_message_id
        if gid in seen_gmail_ids:
            raise StagingInconsistent(f"Gmail message {gid} appears on more than one queue row")
        seen_gmail_ids.add(gid)

    items.sort(key=lambda it: (it.queue, it.sent_at, it.email_id))
    return Staging(
        items=tuple(items),
        historical_record_count=len(historical),
        historical_email_ids=frozenset(historical),
    )


def _build_item(
    row: dict[str, str],
    rec: dict[str, Any],
    emails_by_sha: dict[str, set[int]],
) -> ReviewItem:
    p = rec["payload"]
    email_id = int(p["source_email_id"])
    gmail_message_id = p.get("gmail_message_id") or ""
    if row["gmail_message_id"] != gmail_message_id:
        raise StagingInconsistent(
            f"email {email_id}: queue says Gmail id {row['gmail_message_id']!r}, "
            f"source record says {gmail_message_id!r}"
        )
    if rec["dedupe_key"] != f"{GMAIL_KIND}:{gmail_message_id}":
        raise StagingInconsistent(f"email {email_id}: dedupe key does not name its Gmail id")
    proposed = tuple(p.get("proposed_quote_numbers") or ())
    if " ".join(proposed) != row["proposed_quote_numbers"].strip():
        raise StagingInconsistent(f"email {email_id}: queue and record disagree on proposed numbers")

    documents = tuple(
        ReviewDocument(
            source_attachment_id=int(d["source_attachment_id"]),
            filename=d["filename"],
            sha256=d["sha256"],
            bytes_hash_verified=bool(d.get("bytes_hash_verified")),
            cn_tokens=tuple(d.get("cn_tokens") or ()),
            stored_path=d.get("stored_path"),
            drive_file_id=(d.get("drive_file_id") or "").strip() or None,
        )
        for d in p["documents"]
    )
    same_doc = tuple(
        sorted({e for d in documents for e in emails_by_sha.get(d.sha256, set())} - {email_id})
    )
    same_number = _ids(row.get("same_number_other_emails", ""))
    cross_era = _truthy(row.get("cross_era_number_collision", ""))

    warnings: list[str] = []
    if len(proposed) == 0:
        warnings.append(WARNING_NO_CN)
    if len(proposed) > 1:
        warnings.append(WARNING_AMBIGUOUS_CN)
    if same_number:
        warnings.append(WARNING_SHARED_CN)
    if cross_era:
        warnings.append(WARNING_CROSS_ERA_CN)
    if same_doc:
        warnings.append(WARNING_SHARED_DOCUMENT)
    if p.get("direction_hint") != "customer_quote_candidate":
        warnings.append(WARNING_DIRECTION_UNCLEAR)
    if any(_QUOTE_WORD_RE.search(d.filename) and not d.cn_tokens for d in documents):
        warnings.append(WARNING_QUOTE_DOC_WITHOUT_CN)

    missing: list[str] = []
    if not p.get("rfc822_message_id"):
        missing.append("rfc822_message_id")
    if not p.get("gmail_thread_id"):
        missing.append("gmail_thread_id")
    if not (p.get("recipients") or "").strip():
        missing.append("recipients")
    if not (p.get("subject_raw") or "").strip():
        missing.append("subject")
    if not documents:
        missing.append("documents")
    if any(not d.bytes_hash_verified for d in documents):
        missing.append("verified_document_bytes")
    if len(proposed) != 1:
        missing.append("quote_number")
    # Always missing until an operator supplies it: nothing in the evidence names a case.
    missing.append("opportunity_id")

    return ReviewItem(
        email_id=email_id,
        queue=row["queue"],
        dedupe_key=rec["dedupe_key"],
        source_uri=rec["source_uri"],
        gmail_message_id=gmail_message_id,
        gmail_thread_id=p.get("gmail_thread_id"),
        rfc822_message_id=p.get("rfc822_message_id"),
        raw_sha256=p.get("raw_sha256"),
        gmail_label_ids=tuple(p.get("gmail_label_ids") or ()),
        sender=p.get("sender") or "",
        recipients=p.get("recipients") or "",
        subject_raw=p.get("subject_raw") or "",
        subject=decode_subject(p.get("subject_raw") or ""),
        sent_at=p.get("sent_at") or "",
        direction_hint=p.get("direction_hint") or "",
        documents=documents,
        proposed_quote_numbers=proposed,
        same_number_other_emails=same_number,
        same_document_other_emails=same_doc,
        cross_era_number_collision=cross_era,
        warnings=tuple(warnings),
        missing=tuple(missing),
        source_record_sha256=_canonical_sha256(rec),
    )


def normalize_quote_number(raw: str) -> str:
    value = (raw or "").strip().upper()
    if not _QUOTE_NUMBER_RE.match(value):
        raise ReviewRefused(
            "quote_number must be 1–40 characters of A–Z, 0–9 and '-', e.g. CN01005"
        )
    return value


def build_decision(
    item: ReviewItem,
    *,
    decision: str,
    operator: str,
    opportunity_mode: str | None = None,
    opportunity_id: str | None = None,
    quote_number: str | None = None,
    quote_number_basis: str | None = None,
    reason: str | None = None,
    quote_document_count: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate one operator decision and return the ledger entry. Writes nothing.

    `quote_document_count` is how many quotation documents the identity audit found in
    this email (`quote_document_review`), when that audit is loaded. With two or more, the
    email cannot be confirmed as one quotation: each document is decided on its own."""
    operator = (operator or "").strip()
    if not operator:
        raise ReviewRefused("an operator must be named on every decision")
    if decision not in DECISIONS:
        raise ReviewRefused(f"decision must be one of {', '.join(DECISIONS)}")
    reason = (reason or "").strip() or None

    entry_opportunity: dict[str, Any] | None = None
    entry_quote_number: str | None = None
    entry_basis: str | None = None

    if decision == DECISION_CONFIRM and (quote_document_count or 0) >= 2:
        raise ReviewRefused(
            f"email {item.email_id} carries {quote_document_count} quotation documents: one "
            "email-level decision cannot choose one of them — confirm each document on its own"
        )
    if decision == DECISION_CONFIRM:
        if opportunity_mode not in OPPORTUNITY_MODES:
            raise ReviewRefused(
                "confirming a quotation needs an explicit opportunity: an existing "
                "opportunity_id, or create_new"
            )
        if opportunity_mode == OPPORTUNITY_EXISTING:
            try:
                oid = str(uuid.UUID((opportunity_id or "").strip()))
            except ValueError:
                raise ReviewRefused("opportunity_id must be a UUID typed by the operator") from None
            entry_opportunity = {"mode": OPPORTUNITY_EXISTING, "opportunity_id": oid}
        else:
            if (opportunity_id or "").strip():
                raise ReviewRefused("create_new must not carry an opportunity_id")
            entry_opportunity = {"mode": OPPORTUNITY_CREATE_NEW, "opportunity_id": None}

        if quote_number_basis not in QUOTE_NUMBER_BASES:
            raise ReviewRefused(
                "quote_number_basis must be confirmed_proposal or operator_entered"
            )
        entry_quote_number = normalize_quote_number(quote_number or "")
        if quote_number_basis == BASIS_CONFIRMED_PROPOSAL:
            if item.cn_is_ambiguous:
                raise ReviewRefused(
                    f"email {item.email_id} has {len(item.proposed_quote_numbers)} proposed "
                    "numbers: there is no single proposal to confirm — type the number "
                    "(operator_entered) and give a reason"
                )
            if entry_quote_number != item.proposed_quote_numbers[0]:
                raise ReviewRefused(
                    f"{entry_quote_number} is not the proposal {item.proposed_quote_numbers[0]}; "
                    "record an edited number as operator_entered"
                )
        elif item.cn_is_ambiguous and not reason:
            raise ReviewRefused(
                f"email {item.email_id} has an ambiguous CN: say why this number is the right one"
            )
        entry_basis = quote_number_basis
    else:
        if opportunity_mode or (opportunity_id or "").strip():
            raise ReviewRefused(f"{decision} does not take an opportunity")
        if (quote_number or "").strip() or quote_number_basis:
            raise ReviewRefused(f"{decision} does not take a quote number")
        if decision == DECISION_REJECT and not reason:
            raise ReviewRefused("a rejection needs a reason, so it stays auditable")

    recorded_at = (now or datetime.now(UTC)).isoformat()
    return {
        "decision_id": str(uuid.uuid4()),
        "recorded_at": recorded_at,
        "operator": operator,
        "decision": decision,
        # Exact identifiers, copied verbatim from the staging record.
        "email_id": item.email_id,
        "dedupe_key": item.dedupe_key,
        "source_uri": item.source_uri,
        "gmail_message_id": item.gmail_message_id,
        "gmail_thread_id": item.gmail_thread_id,
        "rfc822_message_id": item.rfc822_message_id,
        "raw_sha256": item.raw_sha256,
        "document_sha256": [d.sha256 for d in item.documents],
        "source_record_sha256": item.source_record_sha256,
        "queue": item.queue,
        "proposed_quote_numbers": list(item.proposed_quote_numbers),
        "warnings_at_decision": list(item.warnings),
        "opportunity": entry_opportunity,
        "quote_number": entry_quote_number,
        "quote_number_basis": entry_basis,
        "reason": reason,
        # The ledger is a record of intent. Applying it is a separate, later step.
        "applied": False,
    }


class DecisionLedger:
    """Append-only JSONL. Entries are never rewritten; the latest per email is its status."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        with self.path.open(encoding="utf-8") as f:
            for n, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise StagingInconsistent(f"{self.path}:{n} is not valid JSON") from exc
        return out

    def append(self, entry: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def states(self) -> dict[int, ItemState]:
        states: dict[int, ItemState] = {}
        for entry in self.entries():
            st = states.setdefault(int(entry["email_id"]), ItemState())
            st.history.append(entry)
            st.latest = entry
            st.status = {
                DECISION_CONFIRM: STATUS_CONFIRMED,
                DECISION_REJECT: STATUS_REJECTED,
            }.get(entry["decision"], STATUS_PENDING)
        return states


def record_decision(
    staging: Staging,
    ledger: DecisionLedger,
    email_id: int,
    **kwargs: Any,
) -> dict[str, Any]:
    """Validate against the staged item, then append. The only write this module makes."""
    item = staging.item(email_id)
    entry = build_decision(item, **kwargs)
    ledger.append(entry)
    return entry


def review_summary(staging: Staging, ledger: DecisionLedger) -> dict[str, Any]:
    states = ledger.states()
    by_status = {STATUS_PENDING: 0, STATUS_CONFIRMED: 0, STATUS_REJECTED: 0}
    by_queue: dict[str, int] = {q: 0 for q in REVIEWABLE_QUEUES}
    for it in staging.items:
        by_status[states.get(it.email_id, ItemState()).status] += 1
        by_queue[it.queue] += 1
    return {
        "reviewable": len(staging.items),
        "by_queue": by_queue,
        "by_status": by_status,
        "historical_records_not_reviewable": staging.historical_record_count,
        "decisions_recorded": sum(len(s.history) for s in states.values()),
    }


# --- read-only email chain ------------------------------------------------------------
#
# A chain is the staged messages that carry the same exact `gmail_thread_id`, in time
# order. It is a view and nothing more: it builds no record, merges no decision, and
# suggests no opportunity. Messages in another thread that share an exact proposed CN or
# exact document bytes are listed beside it as *related evidence*, each one still its own
# message with its own identifiers. Names, address domains and subjects play no part —
# they are displayed, never compared.


@dataclass(frozen=True)
class SharedDocument:
    sha256: str
    # (email_id, filename) for every staged message in the chain carrying these bytes.
    occurrences: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class RelatedMessage:
    item: ReviewItem
    shared_quote_numbers: tuple[str, ...]
    shared_document_sha256: tuple[str, ...]


@dataclass(frozen=True)
class EmailChain:
    anchor_email_id: int
    gmail_thread_id: str | None
    messages: tuple[ReviewItem, ...]
    shared_quote_numbers: dict[str, tuple[int, ...]]
    duplicate_documents: tuple[SharedDocument, ...]
    related: tuple[RelatedMessage, ...]

    @property
    def email_ids(self) -> tuple[int, ...]:
        return tuple(m.email_id for m in self.messages)

    def related_by_thread(self) -> list[tuple[str | None, tuple[RelatedMessage, ...]]]:
        """Related messages grouped by their own exact thread id — shown apart, never merged."""
        groups: dict[str | None, list[RelatedMessage]] = {}
        for r in self.related:
            groups.setdefault(r.item.gmail_thread_id, []).append(r)
        return [(tid, tuple(rs)) for tid, rs in groups.items()]


def _chronological_key(item: ReviewItem) -> tuple[int, float, int]:
    # Offsets differ across messages (-03:00 / -04:00), so compare instants, not strings.
    try:
        instant = datetime.fromisoformat(item.sent_at).timestamp()
    except (TypeError, ValueError):
        return (1, 0.0, item.email_id)  # an undated message sorts last, never guessed
    return (0, instant, item.email_id)


def email_chain(staging: Staging, email_id: int) -> EmailChain:
    """The staged thread around one candidate, plus exactly-linked evidence outside it."""
    anchor = staging.item(email_id)
    thread_id = anchor.gmail_thread_id
    if thread_id:
        members = [it for it in staging.items if it.gmail_thread_id == thread_id]
    else:
        # Without an exact thread id there is no thread to group by: the message stands alone.
        members = [anchor]
    members.sort(key=_chronological_key)
    member_ids = {m.email_id for m in members}

    by_number: dict[str, set[int]] = {}
    by_sha: dict[str, list[tuple[int, str]]] = {}
    for m in members:
        for cn in m.proposed_quote_numbers:
            by_number.setdefault(cn, set()).add(m.email_id)
        for d in m.documents:
            by_sha.setdefault(d.sha256, []).append((m.email_id, d.filename))
    shared_numbers = {cn: tuple(sorted(ids)) for cn, ids in sorted(by_number.items()) if len(ids) > 1}
    duplicates = tuple(
        SharedDocument(sha256=sha, occurrences=tuple(occ))
        for sha, occ in sorted(by_sha.items())
        if len({e for e, _ in occ}) > 1
    )

    chain_numbers = set(by_number)
    chain_shas = set(by_sha)
    related: list[RelatedMessage] = []
    for it in staging.items:
        if it.email_id in member_ids:
            continue
        numbers = tuple(sorted(chain_numbers & set(it.proposed_quote_numbers)))
        shas = tuple(sorted(chain_shas & {d.sha256 for d in it.documents}))
        if numbers or shas:
            related.append(RelatedMessage(item=it, shared_quote_numbers=numbers, shared_document_sha256=shas))
    related.sort(key=lambda r: (r.item.gmail_thread_id or "", _chronological_key(r.item)))

    return EmailChain(
        anchor_email_id=anchor.email_id,
        gmail_thread_id=thread_id,
        messages=tuple(members),
        shared_quote_numbers=shared_numbers,
        duplicate_documents=duplicates,
        related=tuple(related),
    )


def thread_sizes(staging: Staging) -> dict[str, int]:
    """How many staged messages carry each exact thread id."""
    sizes: dict[str, int] = {}
    for it in staging.items:
        if it.gmail_thread_id:
            sizes[it.gmail_thread_id] = sizes.get(it.gmail_thread_id, 0) + 1
    return sizes

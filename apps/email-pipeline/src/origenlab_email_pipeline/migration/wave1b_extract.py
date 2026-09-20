"""Wave 1B — read-only extraction of the two September 2026 outbound campaigns.

Wave 1A (``docs/DATA.md`` §7) is an immutable historical snapshot taken on
2026-09-05. This module does not revise, replace, regenerate or relabel it. It
produces a **separate** bundle carrying the September campaign facts and the
outbound-safety **deltas** recorded after the Wave 1A snapshot instant, so a
combined baseline can be derived later from the two bundles side by side.

Safety posture:

* the source is opened ``mode=ro`` with a proven ``PRAGMA query_only=ON`` and
  read inside one deferred transaction (:mod:`.readonly_source`);
* the ingest cron is never paused, stopped or modified — its freshness is only
  observed, before and after the read window;
* nothing is written anywhere except the operator-named output directory, which
  must be outside every Git working tree;
* every count is measured from the database. No expected count — 2,000, 279 or
  any other — is ever treated as truth; an expectation supplied by the operator
  can only raise a warning;
* extraction fails closed on a missing or duplicated campaign, an unsupported
  state value, a broken reference, a count disagreement, an unexpected schema,
  an unprovable read-only mode or an output collision.

Exclusions: no message bodies, subjects, attachment bytes, credentials or
database copy. The campaign subject is replaced by its SHA-256 so a later loader
can still match a campaign without the text leaving the source database.
**Unrestricted operator free text never leaves the source database either** —
notes, reasons, evidence paragraphs and raw API error strings are replaced by a
``_present`` flag and a digest of the normalized original
(:mod:`.free_text`).

The combined prior-contact delta is the union of three separately reported
sources, deduplicated with their provenance kept
(:mod:`.sent_history`): campaign execution facts, canonical Gmail Sent-recipient
evidence read through the live outbound gate's own functions, and operator
outreach state. No Gmail network call is made — the Sent evidence is the
already-ingested ``emails`` rows.

Every artifact is owner-only: ``0700`` directories, ``0600`` files and tar
members, written atomically so nothing ever exists at a wider mode.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.contact_domain_suppression import (
    CONTACT_DOMAIN_SUPPRESSION_SCHEMA_SQL,
)
from origenlab_email_pipeline.contact_email_suppression import (
    CONTACT_EMAIL_SUPPRESSION_SCHEMA_SQL,
)
from origenlab_email_pipeline.candidate_export_gate import normalize_export_email
from origenlab_email_pipeline.migration.bundle import (
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    BundleFile,
    assert_no_collision,
    assert_output_root_private,
    create_private_dir,
    manifest_content_digest,
    write_json,
    write_jsonl,
    write_manifest,
    write_sha256sums,
    write_tarball,
    write_text,
)
from origenlab_email_pipeline.migration.free_text import (
    assert_no_free_text_remains,
    free_text_policy_document,
    redact_row,
)
from origenlab_email_pipeline.migration.readonly_source import (
    open_source_readonly,
    single_read_transaction,
)
from origenlab_email_pipeline.migration.sent_history import (
    SOURCE_CAMPAIGN_ACCEPTED,
    SOURCE_OUTREACH_STATE,
    SOURCE_SENT_HISTORY,
    SentHistoryEvidence,
    SentHistoryRefused,
    collect_sent_history,
    combine_prior_contact,
    resolve_mailbox,
)
from origenlab_email_pipeline.outbound_campaign_schema import OUTBOUND_CAMPAIGN_SCHEMA_SQL
from origenlab_email_pipeline.outreach_contact_state import OUTREACH_CONTACT_STATE_SCHEMA_SQL

#: The two campaigns this wave preserves. Campaign identifiers are operator
#: labels, not personal data.
SEPTEMBER_CAMPAIGN_IDS: tuple[str, ...] = (
    "septiembre18-2026-final",
    "septiembre18-2026-wave2",
)

#: Mirrors the CHECK constraints owned by ``outbound_campaign_schema``. A value
#: outside these tuples is an unsupported state and fails the extraction closed.
CAMPAIGN_STATUSES: tuple[str, ...] = ("active", "paused", "completed", "archived")
RECIPIENT_STATES: tuple[str, ...] = (
    "candidate",
    "selected",
    "reserved",
    "sent",
    "blocked",
    "bounced",
    "replied",
    "inactive",
)
ATTEMPT_RESULTS: tuple[str, ...] = ("in_flight", "accepted", "failed", "skipped")
ATTEMPT_MODES: tuple[str, ...] = ("dry_run", "live")
RECONCILIATION_STATUSES: tuple[str, ...] = (
    "unreconciled",
    "confirmed_sent",
    "bounced",
    "no_evidence",
)
MANUAL_STATUSES: tuple[str, ...] = ("active", "inactive", "hold")

#: Recipient states that are a prior-contact fact (the address was reached).
CONTACTED_RECIPIENT_STATES: tuple[str, ...] = ("sent", "bounced", "replied")

#: Tables read. Nothing outside this list is opened.
SOURCE_TABLES: tuple[str, ...] = (
    "outbound_campaign",
    "outbound_campaign_recipient",
    "outbound_send_attempt",
    "manual_contact_status",
    "contact_email_suppression",
    "contact_domain_suppression",
    "outreach_contact_state",
)

#: Columns the canonical Sent-history helpers name on ``emails``. The table is
#: never exported — only these must exist for the predicate to be evaluable.
EMAILS_REQUIRED_COLUMNS: tuple[str, ...] = (
    "source_file",
    "folder",
    "recipients",
    "date_iso",
)

#: Never exported, whatever a future schema adds. Guards the bundle against
#: message content and credentials arriving by column drift.
FORBIDDEN_COLUMNS: frozenset[str] = frozenset(
    {
        "subject",
        "body",
        "body_text",
        "body_html",
        "html",
        "snippet",
        "raw",
        "attachment",
        "attachments",
        "password",
        "token",
        "secret",
        "api_key",
        "credential",
        "credentials",
    }
)

_EXCLUSIONS: tuple[str, ...] = (
    "no email bodies/HTML/subjects/attachment extracts/attachment bytes",
    "no full database copy",
    "no credentials, tokens, connection strings or .env values",
    "no rows from rebuildable projections",
    "no second database, mailbox or hosted service is opened at all",
    "no Gmail network request: the Sent evidence is already-ingested SQLite rows",
    "no unrestricted operator free text — notes, reasons, evidence and raw API "
    "error strings travel only as a presence flag and a one-way digest",
)


class ExtractionRefused(Exception):
    """The extraction failed closed. Messages never carry an address or a path."""


def redact_address(address: str) -> str:
    """Map an address to a stable synthetic token safe for a console or a log.

    The result is deterministic for a given input and reveals neither the local
    part nor the domain. Reserved ``example.invalid`` per the repository's
    public-tree convention.
    """
    digest = hashlib.sha256(address.strip().lower().encode("utf-8")).hexdigest()[:12]
    return f"addr-{digest}@example.invalid"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _iso_from_epoch(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch_from_iso(value: str) -> int:
    return int(datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


@dataclass(frozen=True)
class ExtractionRequest:
    """Everything the extraction depends on. There are no implicit defaults."""

    sqlite_path: Path
    output_dir: Path
    wave1a_snapshot_at: str
    run_timestamp: str
    campaign_ids: tuple[str, ...] = SEPTEMBER_CAMPAIGN_IDS
    operator_manifest: Path | None = None
    expected_remaining_candidates: int | None = None
    bundle_name: str | None = None
    #: Mailbox whose canonical Gmail Sent history is read. ``None`` takes
    #: ``outbound_core.DEFAULT_GMAIL_USER_FALLBACK``, the same fallback the
    #: outbound CLIs use.
    gmail_user: str | None = None
    #: Sent folder labels. ``None`` takes the shared
    #: ``marketing_export_context.DEFAULT_SENT_FOLDERS`` pair.
    sent_folders: tuple[str, ...] | None = None

    def resolved_bundle_name(self) -> str:
        if self.bundle_name:
            return self.bundle_name
        compact = self.run_timestamp.replace("-", "").replace(":", "")
        return f"{compact}_wave1b_v1_safety_bundle"


@dataclass(frozen=True)
class ExtractionResult:
    """What the run produced — counts measured, never assumed."""

    bundle_dir: Path
    archive: Path
    sidecar: Path
    archive_sha256: str
    content_digest: str
    counts: dict[str, int]
    warnings: tuple[str, ...] = ()
    console_lines: tuple[str, ...] = ()


@dataclass
class _Collected:
    """Rows and facts gathered inside the single read transaction."""

    campaigns: list[dict[str, Any]] = field(default_factory=list)
    recipients: list[dict[str, Any]] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    remaining_candidates: list[dict[str, Any]] = field(default_factory=list)
    in_flight: list[dict[str, Any]] = field(default_factory=list)
    delta_email_suppression: list[dict[str, Any]] = field(default_factory=list)
    delta_domain_suppression: list[dict[str, Any]] = field(default_factory=list)
    delta_outreach: list[dict[str, Any]] = field(default_factory=list)
    delta_manual: list[dict[str, Any]] = field(default_factory=list)
    campaign_prior_contact: list[dict[str, Any]] = field(default_factory=list)
    sent_history_prior_contact: list[dict[str, Any]] = field(default_factory=list)
    outreach_prior_contact: list[dict[str, Any]] = field(default_factory=list)
    combined_prior_contact: list[dict[str, Any]] = field(default_factory=list)
    combined_counts: dict[str, Any] = field(default_factory=dict)
    sent_history: SentHistoryEvidence | None = None
    source_schema: dict[str, Any] = field(default_factory=dict)
    reconciliation: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# --- schema -----------------------------------------------------------------


def expected_columns() -> dict[str, tuple[str, ...]]:
    """Column names the schema owners declare, read from their own SQL.

    Built by executing the repository's schema constants against an in-memory
    database, so this expectation cannot drift away from the schema modules.
    """
    conn = sqlite3.connect(":memory:")
    try:
        for script in (
            OUTBOUND_CAMPAIGN_SCHEMA_SQL,
            CONTACT_EMAIL_SUPPRESSION_SCHEMA_SQL,
            CONTACT_DOMAIN_SUPPRESSION_SCHEMA_SQL,
            OUTREACH_CONTACT_STATE_SCHEMA_SQL,
        ):
            conn.executescript(script)
        return {
            table: tuple(row[1] for row in conn.execute(f"PRAGMA table_info({table})"))
            for table in SOURCE_TABLES
        }
    finally:
        conn.close()


def _actual_columns(conn: sqlite3.Connection, table: str) -> tuple[str, ...]:
    return tuple(row[1] for row in conn.execute(f"PRAGMA table_info({table})"))


def _assert_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Refuse an unexpected schema; return the recorded source-schema facts."""
    expected = expected_columns()
    tables: dict[str, Any] = {}
    for table in SOURCE_TABLES:
        actual = _actual_columns(conn, table)
        if not actual:
            raise ExtractionRefused(f"unexpected schema: table {table} is absent")
        if actual != expected[table]:
            missing = sorted(set(expected[table]) - set(actual))
            added = sorted(set(actual) - set(expected[table]))
            raise ExtractionRefused(
                f"unexpected schema for {table}: missing={missing} unknown={added} "
                "or column order changed"
            )
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        tables[table] = {"columns": list(actual), "create_sql": row[0] if row else None}

    # ``emails`` is read only through the canonical Sent-history helpers and is
    # far older and more migrated than the outbound sidecar tables, so its whole
    # column list is not pinned. The four columns those helpers name are.
    emails_columns = _actual_columns(conn, "emails")
    if not emails_columns:
        raise ExtractionRefused(
            "unexpected schema: table emails is absent, so canonical Gmail Sent "
            "evidence cannot be read"
        )
    missing_emails = sorted(set(EMAILS_REQUIRED_COLUMNS) - set(emails_columns))
    if missing_emails:
        raise ExtractionRefused(
            f"unexpected schema for emails: missing={missing_emails}; the canonical "
            "Sent-history predicate cannot be evaluated"
        )
    emails_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='emails'"
    ).fetchone()
    tables["emails"] = {
        "columns_required_by_sent_history": list(EMAILS_REQUIRED_COLUMNS),
        "column_count": len(emails_columns),
        "create_sql_sha256": _sha256_text(emails_sql[0] if emails_sql else ""),
        "note": "read only through the canonical Sent-history helpers; not exported",
    }

    master = conn.execute(
        "SELECT type, name, COALESCE(sql,'') FROM sqlite_master ORDER BY type, name"
    ).fetchall()
    fingerprint = _sha256_text("\n".join(f"{t}\t{n}\t{s}" for t, n, s in master))
    return {
        "tables": tables,
        "sqlite_master_fingerprint_sha256": fingerprint,
        "sqlite_master_object_count": len(master),
    }


# --- row helpers ------------------------------------------------------------


def _rows(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...] = (),
    *,
    table: str,
    drop: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Read rows as dicts: drop ``drop``, redact free text, refuse the forbidden.

    Three guards, in order:

    1. ``drop`` removes columns this extraction never wants at all;
    2. :func:`~origenlab_email_pipeline.migration.free_text.redact_row` replaces
       every unrestricted free-text column of ``table`` with its ``_present`` /
       ``_sha256`` pair, and demotes an out-of-vocabulary reason code to the
       same treatment;
    3. anything still named in :data:`FORBIDDEN_COLUMNS`, or any free-text
       column that somehow survived, refuses the run — a future column carrying
       message content, a credential or an operator note can only reach the
       bundle over these guards.
    """
    out: list[dict[str, Any]] = []
    for row in conn.execute(sql, params):
        record = {key: row[key] for key in row.keys() if key not in drop}
        record = redact_row(table, record)
        forbidden = FORBIDDEN_COLUMNS & set(record)
        if forbidden:
            raise ExtractionRefused(
                f"refusing to export forbidden columns: {sorted(forbidden)}"
            )
        try:
            assert_no_free_text_remains(table, record)
        except ValueError as exc:
            raise ExtractionRefused(str(exc)) from None
        out.append(record)
    return out


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def _assert_vocabulary(
    rows: list[dict[str, Any]], column: str, allowed: tuple[str, ...], table: str
) -> None:
    seen = {row.get(column) for row in rows}
    unsupported = sorted(str(v) for v in seen - set(allowed) if v is not None)
    if unsupported:
        raise ExtractionRefused(
            f"unsupported {table}.{column} state value(s): {unsupported}"
        )


def _placeholders(values: tuple[str, ...]) -> str:
    return ",".join("?" for _ in values)


# --- freshness --------------------------------------------------------------


def _file_facts(path: Path) -> dict[str, Any]:
    """Size / mtime / inode only. Never the absolute path."""
    try:
        st = path.stat()
    except OSError:
        return {"present": False}
    return {
        "present": True,
        "size_bytes": int(st.st_size),
        "mtime_iso": _iso_from_epoch(st.st_mtime),
        "inode": int(st.st_ino),
    }


def _source_freshness(db: Path) -> dict[str, Any]:
    return {
        "db": _file_facts(db),
        "wal": _file_facts(Path(str(db) + "-wal")),
        "shm": _file_facts(Path(str(db) + "-shm")),
    }


_OPERATOR_MANIFEST_SCALARS: tuple[str, ...] = (
    "campaign_mode",
    "api_status",
    "postgres_status",
)


def _operator_manifest_facts(path: Path | None) -> dict[str, Any]:
    """Freshness of the documented operator manifest, with no path and no content.

    Only the file's own integrity facts and a small allowlist of status scalars
    are recorded. Operator notes, file lists and any path inside the manifest
    stay where they are.
    """
    if path is None:
        return {"configured": False}
    p = Path(path).expanduser()
    if not p.is_file():
        return {"configured": True, "present": False}
    import json as _json

    payload = p.read_bytes()
    facts: dict[str, Any] = {
        "configured": True,
        "present": True,
        "basename": p.name,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "mtime_iso": _iso_from_epoch(p.stat().st_mtime),
    }
    try:
        document = _json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        facts["parsed"] = False
        return facts
    facts["parsed"] = True
    if isinstance(document, dict):
        for key in _OPERATOR_MANIFEST_SCALARS:
            value = document.get(key)
            if isinstance(value, (str, int, float, bool)):
                facts[key] = value
        for key in ("canonical_files", "stale_files", "known_warnings"):
            value = document.get(key)
            if isinstance(value, list):
                facts[f"{key}_count"] = len(value)
    return facts


# --- collection -------------------------------------------------------------


def _collect(
    conn: sqlite3.Connection, request: ExtractionRequest, campaign_ids: tuple[str, ...]
) -> _Collected:
    collected = _Collected()
    collected.source_schema = _assert_schema(conn)
    snapshot = request.wave1a_snapshot_at
    marks = _placeholders(campaign_ids)

    # --- campaigns ---------------------------------------------------------
    campaign_rows = _rows(
        conn,
        f"SELECT * FROM outbound_campaign WHERE campaign_id IN ({marks})"
        " ORDER BY campaign_id",
        campaign_ids,
        table="outbound_campaign",
        drop=("subject",),
    )
    found = [row["campaign_id"] for row in campaign_rows]
    missing = sorted(set(campaign_ids) - set(found))
    if missing:
        raise ExtractionRefused(f"campaign not found in the source database: {missing}")
    if len(found) != len(set(found)):
        raise ExtractionRefused("duplicate campaign rows for one campaign_id in the source")
    _assert_vocabulary(campaign_rows, "status", CAMPAIGN_STATUSES, "outbound_campaign")
    # The subject never leaves the source database; its digest is enough to match a
    # campaign later without carrying the text.
    subject_digests = {
        str(cid): (_sha256_text(subject or ""), subject is not None)
        for cid, subject in conn.execute(
            f"SELECT campaign_id, subject FROM outbound_campaign WHERE campaign_id IN ({marks})",
            campaign_ids,
        )
    }
    for row in campaign_rows:
        digest, present = subject_digests[str(row["campaign_id"])]
        row["subject_sha256"] = digest
        row["subject_present"] = present
    collected.campaigns = campaign_rows

    # --- recipients --------------------------------------------------------
    recipients = _rows(
        conn,
        f"SELECT * FROM outbound_campaign_recipient WHERE campaign_id IN ({marks})"
        " ORDER BY id",
        campaign_ids,
        table="outbound_campaign_recipient",
    )
    _assert_vocabulary(recipients, "state", RECIPIENT_STATES, "outbound_campaign_recipient")
    collected.recipients = recipients

    # --- attempts ----------------------------------------------------------
    attempts = _rows(
        conn,
        f"SELECT * FROM outbound_send_attempt WHERE campaign_id IN ({marks}) ORDER BY id",
        campaign_ids,
        table="outbound_send_attempt",
    )
    _assert_vocabulary(attempts, "result", ATTEMPT_RESULTS, "outbound_send_attempt")
    _assert_vocabulary(attempts, "mode", ATTEMPT_MODES, "outbound_send_attempt")
    _assert_vocabulary(
        attempts,
        "reconciliation_status",
        RECONCILIATION_STATUSES,
        "outbound_send_attempt",
    )
    collected.attempts = attempts

    # --- referential integrity (no durable FK exists in V1; prove it here) --
    recipient_ids = {row["id"] for row in recipients}
    recipient_campaign = {row["id"]: row["campaign_id"] for row in recipients}
    for attempt in attempts:
        if attempt["recipient_id"] not in recipient_ids:
            raise ExtractionRefused(
                "broken reference: send attempt "
                f"{attempt['id']} points at an absent campaign recipient"
            )
        if recipient_campaign[attempt["recipient_id"]] != attempt["campaign_id"]:
            raise ExtractionRefused(
                "broken reference: send attempt "
                f"{attempt['id']} and its recipient disagree about the campaign"
            )
    for row in recipients:
        if row["campaign_id"] not in set(campaign_ids):
            raise ExtractionRefused(
                f"broken reference: recipient {row['id']} belongs to an unrequested campaign"
            )

    # --- derived -----------------------------------------------------------
    collected.remaining_candidates = [r for r in recipients if r["state"] == "candidate"]
    collected.in_flight = [
        a for a in attempts if a["result"] == "in_flight" and a["resolved_at"] is None
    ]

    # --- deltas after the Wave 1A snapshot instant -------------------------
    collected.delta_email_suppression = _rows(
        conn,
        "SELECT * FROM contact_email_suppression WHERE updated_at > ? ORDER BY email",
        (snapshot,),
        table="contact_email_suppression",
    )
    collected.delta_domain_suppression = _rows(
        conn,
        "SELECT * FROM contact_domain_suppression WHERE updated_at > ? ORDER BY domain_norm",
        (snapshot,),
        table="contact_domain_suppression",
    )
    collected.delta_outreach = _rows(
        conn,
        "SELECT * FROM outreach_contact_state WHERE updated_at > ? ORDER BY contact_email_norm",
        (snapshot,),
        table="outreach_contact_state",
    )
    collected.delta_manual = _rows(
        conn,
        "SELECT * FROM manual_contact_status WHERE updated_at > ? ORDER BY email_norm",
        (snapshot,),
        table="manual_contact_status",
    )
    _assert_vocabulary(collected.delta_manual, "status", MANUAL_STATUSES, "manual_contact_status")

    # --- the three prior-contact sources, then their deduplicated union ----
    campaign_facts = _campaign_prior_contact(conn, snapshot)
    outreach_facts = _outreach_prior_contact(collected.delta_outreach)

    mailbox = resolve_mailbox(
        gmail_user=request.gmail_user, sent_folders=request.sent_folders
    )
    try:
        evidence = collect_sent_history(
            conn,
            mailbox=mailbox,
            snapshot=snapshot,
            latest_campaign_activity_at=_latest_campaign_activity(collected),
        )
    except SentHistoryRefused as exc:
        raise ExtractionRefused(str(exc)) from None
    collected.sent_history = evidence
    if not evidence.complete:
        collected.warnings.extend(
            f"combined prior-contact baseline is NOT complete: {reason}"
            for reason in evidence.incomplete_reasons
        )

    collected.campaign_prior_contact = list(campaign_facts.values())
    collected.sent_history_prior_contact = list(evidence.recipients.values())
    collected.outreach_prior_contact = list(outreach_facts.values())
    collected.combined_prior_contact, collected.combined_counts = combine_prior_contact(
        campaign_accepted=campaign_facts,
        sent_history=evidence.recipients,
        outreach_state=outreach_facts,
    )
    collected.combined_counts["complete"] = evidence.complete
    collected.combined_counts["incomplete_reasons"] = list(evidence.incomplete_reasons)
    collected.combined_counts["definition"] = _COMBINED_DEFINITION

    # --- reconciliation ----------------------------------------------------
    collected.reconciliation = _reconcile(
        conn,
        collected,
        campaign_ids=campaign_ids,
        snapshot=snapshot,
    )
    return collected


def _normalized_migration_address(value: Any) -> str | None:
    """The one address form that travels: the gate's own normalization.

    ``candidate_export_gate.normalize_export_email`` is what the live outbound
    gate uses to decide whether an address is already contacted, so the
    migration delta keys on exactly the same string.
    """
    if not isinstance(value, str):
        return None
    return normalize_export_email(value)


def _campaign_prior_contact(
    conn: sqlite3.Connection, snapshot: str
) -> dict[str, dict[str, Any]]:
    """Campaign execution prior-contact facts recorded after the snapshot.

    Source category ``campaign_accepted``: accepted send attempts, plus
    recipients the campaign machinery left in ``sent`` / ``bounced`` /
    ``replied``. This is *one* of the three sources of the combined baseline —
    on its own it misses ordinary Gmail Sent mail, which is why
    :mod:`.sent_history` exists.

    Repository-wide, not campaign-scoped: the outbound safety baseline is a
    property of an address, not of one campaign.
    """
    facts: dict[str, dict[str, Any]] = {}

    def _touch(raw: Any) -> dict[str, Any] | None:
        address = _normalized_migration_address(raw)
        if address is None:
            return None
        return facts.setdefault(
            address,
            {
                "address": address,
                "accepted_attempt_ids": [],
                "sent_recipient_ids": [],
                "campaign_ids": [],
                "first_at": None,
                "last_at": None,
            },
        )

    def _extend(record: dict[str, Any] | None, at: str | None) -> None:
        if record is None or not at:
            return
        if record["first_at"] is None or at < record["first_at"]:
            record["first_at"] = at
        if record["last_at"] is None or at > record["last_at"]:
            record["last_at"] = at

    for row in conn.execute(
        "SELECT id, campaign_id, email_norm, attempted_at FROM outbound_send_attempt"
        " WHERE result = 'accepted' AND attempted_at > ? ORDER BY id",
        (snapshot,),
    ):
        record = _touch(row["email_norm"])
        if record is None:
            continue
        record["accepted_attempt_ids"].append(int(row["id"]))
        if row["campaign_id"] not in record["campaign_ids"]:
            record["campaign_ids"].append(row["campaign_id"])
        _extend(record, row["attempted_at"])

    contacted = _placeholders(CONTACTED_RECIPIENT_STATES)
    for row in conn.execute(
        "SELECT id, campaign_id, email_norm, sent_at, last_attempt_at"
        f" FROM outbound_campaign_recipient WHERE state IN ({contacted})"
        " AND (sent_at > ? OR last_attempt_at > ?) ORDER BY id",
        (*CONTACTED_RECIPIENT_STATES, snapshot, snapshot),
    ):
        record = _touch(row["email_norm"])
        if record is None:
            continue
        record["sent_recipient_ids"].append(int(row["id"]))
        if row["campaign_id"] not in record["campaign_ids"]:
            record["campaign_ids"].append(row["campaign_id"])
        _extend(record, row["sent_at"])
        _extend(record, row["last_attempt_at"])

    return {address: facts[address] for address in sorted(facts)}


#: The outreach states the live gate treats as already-contacted
#: (``candidate_export_gate._OUTREACH_REASON`` / ``load_outreach_state_map``).
OUTREACH_PRIOR_CONTACT_STATES: tuple[str, ...] = ("contacted", "replied", "snoozed")


def _outreach_prior_contact(
    delta_outreach: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Operator outreach-state prior-contact facts, from the post-snapshot delta.

    Source category ``outreach_state``. The rows are the already free-text
    redacted ``outreach_contact_state`` delta, so no note travels here.
    """
    facts: dict[str, dict[str, Any]] = {}
    for row in delta_outreach:
        state = row.get("state")
        if not isinstance(state, str) or state.strip().lower() not in (
            OUTREACH_PRIOR_CONTACT_STATES
        ):
            continue
        address = _normalized_migration_address(row.get("contact_email_norm"))
        if address is None:
            continue
        first = row.get("first_contacted_at")
        last = row.get("last_contacted_at") or row.get("updated_at")
        facts[address] = {
            "address": address,
            "state": state.strip().lower(),
            "first_at": first if isinstance(first, str) and first else None,
            "last_at": last if isinstance(last, str) and last else None,
            "source": row.get("source"),
            "updated_by": row.get("updated_by"),
        }
    return {address: facts[address] for address in sorted(facts)}


def _latest_campaign_activity(collected: _Collected) -> str | None:
    """Newest campaign send timestamp, used to detect a stale Sent ingest."""
    stamps = [
        value
        for row in collected.attempts
        for value in (row.get("attempted_at"), row.get("resolved_at"))
        if isinstance(value, str) and value
    ]
    stamps += [
        value
        for row in collected.recipients
        for value in (row.get("sent_at"), row.get("last_attempt_at"))
        if isinstance(value, str) and value
    ]
    return max(stamps) if stamps else None


_COMBINED_DEFINITION = (
    "combined prior-contact delta = deduplicate( "
    "campaign_accepted \u222a sent_history \u222a outreach_state ) over the "
    "normalized migration address, where campaign_accepted = accepted send "
    "attempts and recipients in sent/bounced/replied recorded after the Wave 1A "
    "snapshot; sent_history = recipients of the mailbox's canonical Gmail Sent "
    "rows dated after the snapshot, read through "
    "marketing_export_context.sent_history_where and business_mart.emails_in; "
    "outreach_state = post-snapshot outreach_contact_state rows in "
    "contacted/replied/snoozed. Every entry keeps its source categories."
)


def _reconcile(
    conn: sqlite3.Connection,
    collected: _Collected,
    *,
    campaign_ids: tuple[str, ...],
    snapshot: str,
) -> dict[str, Any]:
    """Re-count everything independently; disagreement fails the run closed."""
    marks = _placeholders(campaign_ids)
    checks: list[dict[str, Any]] = []

    def _check(name: str, extracted: int, measured: int) -> None:
        checks.append({"check": name, "extracted": extracted, "measured": measured})
        if extracted != measured:
            raise ExtractionRefused(
                f"count disagreement for {name}: extracted {extracted}, database reports "
                f"{measured}"
            )

    _check(
        "outbound_campaign",
        len(collected.campaigns),
        _scalar(
            conn,
            f"SELECT count(*) FROM outbound_campaign WHERE campaign_id IN ({marks})",
            campaign_ids,
        ),
    )
    _check(
        "outbound_campaign_recipient",
        len(collected.recipients),
        _scalar(
            conn,
            f"SELECT count(*) FROM outbound_campaign_recipient WHERE campaign_id IN ({marks})",
            campaign_ids,
        ),
    )
    _check(
        "outbound_send_attempt",
        len(collected.attempts),
        _scalar(
            conn,
            f"SELECT count(*) FROM outbound_send_attempt WHERE campaign_id IN ({marks})",
            campaign_ids,
        ),
    )
    _check(
        "remaining_candidates",
        len(collected.remaining_candidates),
        _scalar(
            conn,
            f"SELECT count(*) FROM outbound_campaign_recipient WHERE campaign_id IN ({marks})"
            " AND state = 'candidate'",
            campaign_ids,
        ),
    )
    _check(
        "unresolved_in_flight_attempts",
        len(collected.in_flight),
        _scalar(
            conn,
            f"SELECT count(*) FROM outbound_send_attempt WHERE campaign_id IN ({marks})"
            " AND result = 'in_flight' AND resolved_at IS NULL",
            campaign_ids,
        ),
    )
    for name, rows, table, column in (
        ("delta_contact_email_suppression", collected.delta_email_suppression,
         "contact_email_suppression", "updated_at"),
        ("delta_contact_domain_suppression", collected.delta_domain_suppression,
         "contact_domain_suppression", "updated_at"),
        ("delta_outreach_contact_state", collected.delta_outreach,
         "outreach_contact_state", "updated_at"),
        ("delta_manual_contact_status", collected.delta_manual,
         "manual_contact_status", "updated_at"),
    ):
        _check(
            name,
            len(rows),
            _scalar(conn, f"SELECT count(*) FROM {table} WHERE {column} > ?", (snapshot,)),
        )

    # Per-campaign state buckets must sum to the campaign's recipient total.
    per_campaign: dict[str, Any] = {}
    for campaign_id in campaign_ids:
        buckets = {
            state: sum(
                1
                for r in collected.recipients
                if r["campaign_id"] == campaign_id and r["state"] == state
            )
            for state in RECIPIENT_STATES
        }
        total = sum(1 for r in collected.recipients if r["campaign_id"] == campaign_id)
        if sum(buckets.values()) != total:
            raise ExtractionRefused(
                f"count disagreement: recipient state buckets do not sum to the total"
            )
        per_campaign[campaign_id] = {
            "recipients_total": total,
            "recipients_by_state": buckets,
            "attempts_total": sum(
                1 for a in collected.attempts if a["campaign_id"] == campaign_id
            ),
            "attempts_by_result": {
                result: sum(
                    1
                    for a in collected.attempts
                    if a["campaign_id"] == campaign_id and a["result"] == result
                )
                for result in ATTEMPT_RESULTS
            },
        }

    # The combined delta must be a plain set union: no address may appear twice,
    # and every source address must appear exactly once in the union.
    combined_addresses = [row["address"] for row in collected.combined_prior_contact]
    if len(combined_addresses) != len(set(combined_addresses)):
        raise ExtractionRefused(
            "count disagreement: the combined prior-contact delta contains a "
            "duplicated address"
        )
    union = set(combined_addresses)
    for label, rows in (
        (SOURCE_CAMPAIGN_ACCEPTED, collected.campaign_prior_contact),
        (SOURCE_SENT_HISTORY, collected.sent_history_prior_contact),
        (SOURCE_OUTREACH_STATE, collected.outreach_prior_contact),
    ):
        missing = {row["address"] for row in rows} - union
        if missing:
            raise ExtractionRefused(
                f"count disagreement: {len(missing)} {label} address(es) are absent "
                "from the combined prior-contact delta"
            )
    _check(
        "combined_prior_contact",
        len(collected.combined_prior_contact),
        len(
            {row["address"] for row in collected.campaign_prior_contact}
            | {row["address"] for row in collected.sent_history_prior_contact}
            | {row["address"] for row in collected.outreach_prior_contact}
        ),
    )

    return {
        "checks": checks,
        "per_campaign": per_campaign,
        "combined_prior_contact": collected.combined_counts,
        "sent_history": (
            collected.sent_history.to_report() if collected.sent_history else None
        ),
        "free_text_policy": free_text_policy_document(),
        "note": (
            "Every number here was measured from the source database in the single read "
            "transaction. No expected count is treated as truth."
        ),
    }


# --- orchestration ----------------------------------------------------------


_README = """# OrigenLab Wave 1B — September 2026 outbound campaign extract

Read-only extraction from the live V1 email-pipeline SQLite database: one
`mode=ro` connection with a proven `PRAGMA query_only=ON`, read inside a single
`BEGIN DEFERRED` transaction. Not a database copy; no message bodies, subjects,
attachment extracts or attachment bytes; no credentials.

This bundle is **separate from and additive to** Wave 1A
(`20260905T042425Z_wave1a_v1_safety_bundle`, `docs/DATA.md` §7). Wave 1A is an
immutable historical snapshot and is neither revised nor regenerated here.

- `manifest.json` — provenance, per-file SHA-256, measured row counts, and
  `content_digest_sha256`: the digest of this manifest with its wall-clock `run`
  block removed, so two runs over the same snapshot share it.
- `SHA256SUMS` — `sha256sum -c SHA256SUMS` verifies every data file.
- `schema/source_schema.json` — the source CREATE statements, column lists and
  the whole-database `sqlite_master` fingerprint.
- `exact/` — the campaign, recipient and send-attempt rows for the requested
  campaigns, in primary-key order. The campaign subject is replaced by
  `subject_sha256`.
- `derived/` — the remaining candidates and the unresolved `in_flight` attempts.
- `delta/` — rows recorded **after** the Wave 1A snapshot instant: address and
  domain suppressions, outreach contact state and manual contact status, plus
  the three prior-contact sources and their deduplicated union.
- `reports/reconciliation.json` — every count re-measured independently, the
  combined prior-contact arithmetic, and the free-text policy.
- `reports/sent_history.json` — which mailbox and Sent folders were read, which
  canonical functions did the reading, and whether the baseline is complete.
- `reports/freshness.json` — before/after freshness of the source database and
  of the documented operator manifest. The ingest cron was observed, never
  paused, stopped or modified.

## The combined prior-contact delta

`delta/combined_prior_contact.jsonl` is the deduplicated union of three
separately reported sources, each also kept raw beside it:

| Source category | File | What it is |
|---|---|---|
| `campaign_accepted` | `delta/campaign_prior_contact.jsonl` | accepted send attempts and recipients in `sent` / `bounced` / `replied` |
| `sent_history` | `delta/sent_history_prior_contact.jsonl` | recipients of the mailbox's canonical Gmail **Sent** rows dated after the snapshot — ordinary mail, not just campaign mail |
| `outreach_state` | `delta/outreach_prior_contact.jsonl` | post-snapshot `outreach_contact_state` rows in `contacted` / `replied` / `snoozed` |

Every combined entry records its `source_categories`, so an address reached by
more than one route stays traceable. `reports/reconciliation.json` carries the
raw per-source totals, every pairwise and three-way overlap and the
deduplicated total, and states explicitly whether the baseline is `complete`.

The Sent evidence is the **already-ingested** `emails` rows: no Gmail network
call is made, and the folder predicate and the address parser are imported from
the live outbound gate (`marketing_export_context.sent_history_where`,
`business_mart.emails_in`), so this extract cannot drift away from what the
gate blocks on. Missing, mismatched or undatable Sent evidence refuses the run.

## Free text never left the source database

Unrestricted operator free text — `outreach_contact_state.notes`,
`manual_contact_status.reason` and `.evidence`, the suppression
`suppression_reason_text` columns, and `outbound_send_attempt.error_detail` —
is **not** exported. Each is replaced by `<column>_present` and, when present,
`<column>_sha256`: SHA-256 over the original normalized to Unicode NFC, trimmed,
with internal whitespace collapsed. **The digest proves two values were equal;
it cannot reconstruct the content.** Closed reason codes with defined semantics
(`suppression_reason_code`, `block_reason`, `selection_reason`, `error_code`)
travel verbatim only while their value is inside the declared vocabulary; any
other value is treated as free text and digested.
`reports/reconciliation.json` → `free_text_policy` carries the full policy.

## Permissions

Everything here is owner-only: directories `0700`, files and tar members
`0600`, written atomically through an owner-only temporary file so nothing ever
existed at a wider mode. Extract with `tar -xzf` as the owner; the modes are
carried in the archive.
"""


def extract(request: ExtractionRequest) -> ExtractionResult:
    """Run one Wave 1B extraction. Reads the source; writes only the bundle."""
    campaign_ids = tuple(request.campaign_ids)
    if len(set(campaign_ids)) != len(campaign_ids):
        raise ExtractionRefused("duplicate campaign_id requested")
    if not campaign_ids:
        raise ExtractionRefused("no campaign_id requested")

    output_dir = assert_output_root_private(request.output_dir)
    bundle_dir = output_dir / request.resolved_bundle_name()
    assert_no_collision(bundle_dir)
    assert_no_collision(Path(f"{bundle_dir}.tar.gz"))

    source = Path(request.sqlite_path).expanduser()
    freshness_before = _source_freshness(source)
    manifest_before = _operator_manifest_facts(request.operator_manifest)

    conn = open_source_readonly(source)
    try:
        with single_read_transaction(conn) as txn:
            collected = _collect(conn, request, campaign_ids)
            data_version_end = txn.data_version_now()
            journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
            sqlite_version = sqlite3.sqlite_version
        total_changes = conn.total_changes
    finally:
        conn.close()

    freshness_after = _source_freshness(source)
    manifest_after = _operator_manifest_facts(request.operator_manifest)

    if request.expected_remaining_candidates is not None:
        measured = len(collected.remaining_candidates)
        if measured != request.expected_remaining_candidates:
            collected.warnings.append(
                "expected remaining candidates "
                f"{request.expected_remaining_candidates}, measured {measured} — "
                "the measured value is the fact; the expectation is only an operator hint"
            )
    if data_version_end != txn.data_version_start:
        collected.warnings.append(
            "an external commit landed during the read window; the read transaction "
            "still returned one consistent snapshot"
        )

    create_private_dir(bundle_dir)

    entries: list[BundleFile] = [
        write_text(bundle_dir, "README.md", _README, kind="readme"),
        write_json(bundle_dir, "schema/source_schema.json", collected.source_schema,
                   kind="schema"),
        write_jsonl(bundle_dir, "exact/outbound_campaign.jsonl", collected.campaigns,
                    kind="exact"),
        write_jsonl(bundle_dir, "exact/outbound_campaign_recipient.jsonl",
                    collected.recipients, kind="exact"),
        write_jsonl(bundle_dir, "exact/outbound_send_attempt.jsonl", collected.attempts,
                    kind="exact"),
        write_jsonl(bundle_dir, "derived/remaining_candidates.jsonl",
                    collected.remaining_candidates, kind="derived"),
        write_jsonl(bundle_dir, "derived/unresolved_in_flight_attempts.jsonl",
                    collected.in_flight, kind="derived"),
        write_jsonl(bundle_dir, "delta/contact_email_suppression.jsonl",
                    collected.delta_email_suppression, kind="delta"),
        write_jsonl(bundle_dir, "delta/contact_domain_suppression.jsonl",
                    collected.delta_domain_suppression, kind="delta"),
        write_jsonl(bundle_dir, "delta/outreach_contact_state.jsonl",
                    collected.delta_outreach, kind="delta"),
        write_jsonl(bundle_dir, "delta/manual_contact_status.jsonl", collected.delta_manual,
                    kind="delta"),
        write_jsonl(bundle_dir, "delta/campaign_prior_contact.jsonl",
                    collected.campaign_prior_contact, kind="delta"),
        write_jsonl(bundle_dir, "delta/sent_history_prior_contact.jsonl",
                    collected.sent_history_prior_contact, kind="delta"),
        write_jsonl(bundle_dir, "delta/outreach_prior_contact.jsonl",
                    collected.outreach_prior_contact, kind="delta"),
        write_jsonl(bundle_dir, "delta/combined_prior_contact.jsonl",
                    collected.combined_prior_contact, kind="delta"),
        write_json(bundle_dir, "reports/reconciliation.json", collected.reconciliation,
                   kind="report"),
        write_json(bundle_dir, "reports/sent_history.json",
                   collected.sent_history.to_report() if collected.sent_history else {},
                   kind="report"),
    ]

    freshness = {
        "source_database": {"before": freshness_before, "after": freshness_after},
        "operator_manifest": {"before": manifest_before, "after": manifest_after},
        "ingest_cron": (
            "observed only; never paused, stopped or modified by this extraction"
        ),
    }
    entries.append(write_json(bundle_dir, "reports/freshness.json", freshness, kind="report"))
    sums = write_sha256sums(bundle_dir, entries)

    counts = {
        "outbound_campaign": len(collected.campaigns),
        "outbound_campaign_recipient": len(collected.recipients),
        "outbound_send_attempt": len(collected.attempts),
        "remaining_candidates": len(collected.remaining_candidates),
        "unresolved_in_flight_attempts": len(collected.in_flight),
        "delta_contact_email_suppression": len(collected.delta_email_suppression),
        "delta_contact_domain_suppression": len(collected.delta_domain_suppression),
        "delta_outreach_contact_state": len(collected.delta_outreach),
        "delta_manual_contact_status": len(collected.delta_manual),
        "delta_campaign_prior_contact": len(collected.campaign_prior_contact),
        "delta_sent_history_prior_contact": len(collected.sent_history_prior_contact),
        "delta_outreach_prior_contact": len(collected.outreach_prior_contact),
        "delta_combined_prior_contact": len(collected.combined_prior_contact),
    }

    manifest = {
        "wave": "1b",
        "purpose": (
            "V1 September 2026 outbound campaign preservation plus the outbound-safety "
            "deltas recorded after the Wave 1A snapshot. Separate from and additive to "
            "Wave 1A, which is immutable."
        ),
        "wave1a": {
            "immutable": True,
            "snapshot_at_utc": request.wave1a_snapshot_at,
            "relationship": "delta baseline; Wave 1A counts are never revised here",
        },
        "campaign_ids": list(campaign_ids),
        "exclusions": list(_EXCLUSIONS),
        "row_counts": counts,
        "combined_prior_contact": collected.combined_counts,
        "sent_history": (
            collected.sent_history.to_report() if collected.sent_history else None
        ),
        "free_text_policy": free_text_policy_document(),
        "permissions": {
            "output_root_mode": oct(PRIVATE_DIR_MODE),
            "bundle_dir_mode": oct(PRIVATE_DIR_MODE),
            "file_mode": oct(PRIVATE_FILE_MODE),
            "tar_member_mode": oct(PRIVATE_FILE_MODE),
            "write_strategy": (
                "owner-only O_EXCL temporary file in the destination directory, "
                "then an atomic rename; no artifact ever exists at a wider mode"
            ),
            "existing_output_root_policy": (
                "refuse a symlink, a root not owned by the current user, or a "
                "group/world-writable root; otherwise tighten the root itself to "
                "0700 and never recurse into existing content"
            ),
        },
        "reconciliation": collected.reconciliation,
        "freshness": freshness,
        "source": {
            "path_basename": source.name,
            "path_sha256": _sha256_text(str(source.resolve())),
            "journal_mode": journal_mode,
            "sqlite_master_fingerprint_sha256": collected.source_schema[
                "sqlite_master_fingerprint_sha256"
            ],
        },
        "extraction": {
            "uri_mode": "mode=ro",
            "pragma_query_only": 1,
            "immutable": False,
            "transaction": (
                "single BEGIN (DEFERRED) read transaction; COMMIT ends it; no writes"
            ),
            "data_version_txn_start": txn.data_version_start,
            "data_version_txn_end": data_version_end,
            "external_commits_observed_during_window": data_version_end
            != txn.data_version_start,
            "total_changes_on_source_connection": total_changes,
            "sqlite_library_version": sqlite_version,
        },
        "files": [entry.to_manifest_entry() for entry in (*entries, sums)],
        "warnings": list(collected.warnings),
        # Wall clock and the name it produces live here alone: `content_digest_sha256`
        # excludes this block, so it identifies what was extracted, not when.
        "run": {
            "bundle_name": bundle_dir.name,
            "run_timestamp_utc": request.run_timestamp,
            "expected_remaining_candidates": request.expected_remaining_candidates,
        },
    }
    write_manifest(bundle_dir, manifest)
    archive, sidecar, archive_sha = write_tarball(
        bundle_dir, mtime=_epoch_from_iso(request.run_timestamp)
    )

    evidence = collected.sent_history
    combined = collected.combined_counts
    console = [
        f"wave: 1b  bundle: {bundle_dir.name}",
        f"campaigns: {', '.join(campaign_ids)}",
        *[f"  {name}: {value}" for name, value in sorted(counts.items())],
        "combined prior contact:",
        *[
            f"  raw {name}: {value}"
            for name, value in sorted(combined.get("raw_by_source", {}).items())
        ],
        *[
            f"  overlap {name}: {value}"
            for name, value in sorted(combined.get("overlaps", {}).items())
        ],
        f"  deduplicated total: {combined.get('deduplicated_total')}",
        f"  complete: {combined.get('complete')}",
        "sent history:",
        f"  mailbox: {redact_address(evidence.mailbox.gmail_user) if evidence else 'n/a'}",
        f"  folders: {len(evidence.mailbox.sent_folders) if evidence else 0} label(s)",
        f"  rows after snapshot: {evidence.rows_after_snapshot if evidence else 0}",
        f"content_digest_sha256: {manifest_content_digest(manifest)}",
        f"archive_sha256: {archive_sha}",
        "addresses are never printed; the bundle holds them",
        "operator free text was never read out of the source database",
        f"artifacts are owner-only ({oct(PRIVATE_DIR_MODE)} dirs, {oct(PRIVATE_FILE_MODE)} files)",
        *[f"WARNING: {w}" for w in collected.warnings],
    ]

    return ExtractionResult(
        bundle_dir=bundle_dir,
        archive=archive,
        sidecar=sidecar,
        archive_sha256=archive_sha,
        content_digest=manifest_content_digest(manifest),
        counts=counts,
        warnings=tuple(collected.warnings),
        console_lines=tuple(console),
    )

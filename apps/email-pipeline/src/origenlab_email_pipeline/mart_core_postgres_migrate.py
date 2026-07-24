"""SQLite mart core → Postgres sync (archive + canonical mirrors).

Read-only SQLite; scratch Postgres writes only. Canonical rows are derived from a
single scan of Gmail operativo emails (~1k rows), not repeated EXISTS over the archive.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urlparse

from origenlab_email_pipeline.config import load_settings
from origenlab_email_pipeline.contacto_gmail_source import (
    CONTACTO_GMAIL_SOURCE_SQL_LIKE_VALUE,
    sql_predicate_contacto_gmail_source,
)
from origenlab_email_pipeline.email_business_filters import EMAIL_RE
from origenlab_email_pipeline.operational_scope import is_operational_noise_entity
from origenlab_email_pipeline.postgres_outbound_audit import redact_postgres_url
from origenlab_email_pipeline.progress import tqdm_stderr

try:
    import psycopg
    from psycopg import sql as pg_sql
    from psycopg.types.json import Json
except ImportError as exc:  # pragma: no cover
    psycopg = None  # type: ignore[misc, assignment]
    pg_sql = None  # type: ignore[misc, assignment]
    Json = None  # type: ignore[misc, assignment]
    _PSYCOPG_IMPORT_ERROR = exc
else:
    _PSYCOPG_IMPORT_ERROR = None

_SEQ_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

MartTablesArg = Literal["all", "archive", "canonical"]
PhaseLogger = Callable[[str], None]

_SCRATCH_URL_TOKENS: tuple[str, ...] = (
    "scratch",
    "staging",
    "stage",
    "test",
    "dev",
    "local",
    "localhost",
    "127.0.0.1",
)

_IN_CHUNK_SIZE = 500
_FETCH_BATCH = 1000

TABLE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "source": "contact_master",
        "target": "mart.contact_master",
        "pk": "email",
        "columns": (
            "email",
            "contact_name_best",
            "domain",
            "organization_name_guess",
            "organization_type_guess",
            "first_seen_at",
            "last_seen_at",
            "total_emails",
            "inbound_emails",
            "outbound_emails",
            "quote_email_count",
            "invoice_email_count",
            "purchase_email_count",
            "business_doc_email_count",
            "quote_doc_count",
            "invoice_doc_count",
            "top_equipment_tags",
            "confidence_score",
        ),
        "timestamp_columns": frozenset({"first_seen_at", "last_seen_at"}),
        "delete_order": 3,
        "table_group": "archive",
    },
    {
        "source": "organization_master",
        "target": "mart.organization_master",
        "pk": "domain",
        "columns": (
            "domain",
            "organization_name_guess",
            "organization_type_guess",
            "first_seen_at",
            "last_seen_at",
            "total_emails",
            "total_contacts",
            "quote_email_count",
            "invoice_email_count",
            "purchase_email_count",
            "business_doc_email_count",
            "quote_doc_count",
            "invoice_doc_count",
            "top_equipment_tags",
            "key_contacts",
        ),
        "timestamp_columns": frozenset({"first_seen_at", "last_seen_at"}),
        "delete_order": 2,
        "table_group": "archive",
    },
    {
        "source": "opportunity_signals",
        "target": "mart.opportunity_signals",
        "pk": "id",
        "columns": (
            "id",
            "signal_type",
            "entity_kind",
            "entity_key",
            "email_id",
            "attachment_id",
            "score",
            "details_json",
            "created_at",
        ),
        "timestamp_columns": frozenset({"created_at"}),
        "json_columns": frozenset({"details_json"}),
        "delete_order": 1,
        "reset_sequence": True,
        "table_group": "archive",
    },
)

_CONTACT_COLS = TABLE_SPECS[0]["columns"]
_ORG_COLS = TABLE_SPECS[1]["columns"]
_OPP_COLS = TABLE_SPECS[2]["columns"]

CANONICAL_TABLE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "source": "contact_master_canonical",
        "target": "mart.contact_master_canonical",
        "pk": "email",
        "columns": _CONTACT_COLS,
        "timestamp_columns": frozenset({"first_seen_at", "last_seen_at"}),
        "requires_tables": ("contact_master", "emails"),
        "delete_order": 6,
        "table_group": "canonical",
        "canonical_fast": "contact",
    },
    {
        "source": "organization_master_canonical",
        "target": "mart.organization_master_canonical",
        "pk": "domain",
        "columns": _ORG_COLS,
        "timestamp_columns": frozenset({"first_seen_at", "last_seen_at"}),
        "requires_tables": ("organization_master", "contact_master", "emails"),
        "delete_order": 5,
        "table_group": "canonical",
        "canonical_fast": "organization",
    },
    {
        "source": "opportunity_signals_canonical",
        "target": "mart.opportunity_signals_canonical",
        "pk": "id",
        "columns": _OPP_COLS,
        "timestamp_columns": frozenset({"created_at"}),
        "json_columns": frozenset({"details_json"}),
        "requires_tables": ("opportunity_signals", "emails"),
        "delete_order": 4,
        "reset_sequence": True,
        "table_group": "canonical",
        "canonical_fast": "opportunity",
    },
)

ALL_TABLE_SPECS: tuple[dict[str, Any], ...] = TABLE_SPECS + CANONICAL_TABLE_SPECS

_OPP_SEQ_TABLES = (
    "mart.opportunity_signals",
    "mart.opportunity_signals_canonical",
)


class ConversionError(Exception):
    def __init__(self, table: str, row_id: Any, column: str, value: Any) -> None:
        super().__init__(
            f"invalid conversion {table} id={row_id!r} column={column!r} value={value!r}"
        )


class ValidationError(RuntimeError):
    """Post-load validation failed; caller must roll back the apply transaction."""


def sanitize_migrate_error(
    message: BaseException | str, *, postgres_url: str | None = None
) -> str:
    text = str(message) if not isinstance(message, BaseException) else (
        str(message) or type(message).__name__
    )
    if postgres_url:
        for secret in (postgres_url, redact_postgres_url(postgres_url)):
            if secret and secret in text:
                text = text.replace(secret, "<redacted>")
    for key in (
        "ORIGENLAB_POSTGRES_URL",
        "ALEMBIC_DATABASE_URL",
        "ORIGENLAB_CLOUD_POSTGRES_URL",
        "ORIGENLAB_POSTGRES_TEST_URL",
    ):
        val = os.environ.get(key)
        if val and val in text:
            text = text.replace(val, "<redacted>")
    return text


@dataclass(frozen=True)
class CanonicalSyncContext:
    """Participant keys from one canonical Gmail email scan."""

    canonical_email_ids: frozenset[int]
    participant_emails: frozenset[str]
    contact_emails: frozenset[str]
    canonical_domains: frozenset[str]
    contact_rows: tuple[tuple[Any, ...], ...]
    organization_rows: tuple[tuple[Any, ...], ...]
    opportunity_rows: tuple[tuple[Any, ...], ...]

    @property
    def contact_count(self) -> int:
        return len(self.contact_rows)

    @property
    def organization_count(self) -> int:
        return len(self.organization_rows)

    @property
    def opportunity_count(self) -> int:
        return len(self.opportunity_rows)


def phase_log(message: str, *, log: PhaseLogger | None = None) -> None:
    sink = log or (lambda m: print(m, flush=True))
    sink(message)


def parse_tables_arg(value: str) -> MartTablesArg:
    v = (value or "all").strip().lower()
    if v in ("all", "archive", "canonical"):
        return v  # type: ignore[return-value]
    raise ValueError("--tables must be one of: all, archive, canonical")


def table_specs_for_group(group: MartTablesArg) -> tuple[dict[str, Any], ...]:
    if group == "archive":
        return TABLE_SPECS
    if group == "canonical":
        return CANONICAL_TABLE_SPECS
    return ALL_TABLE_SPECS


def canonical_emails_where(alias: str | None = None) -> str:
    return sql_predicate_contacto_gmail_source(table_alias=alias, coalesce_null=False)


def connect_sqlite_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=120.0)
    conn.execute("PRAGMA query_only=ON")
    apply_readonly_sqlite_perf_pragmas(conn)
    return conn


def apply_readonly_sqlite_perf_pragmas(conn: sqlite3.Connection) -> None:
    """Read-only tuning; does not persist changes to the database file."""
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-524288")
    conn.execute("PRAGMA mmap_size=268435456")


def normalize_email(addr: str | None) -> str:
    if not addr:
        return ""
    found = EMAIL_RE.findall(addr)
    if found:
        return found[0].lower().strip()
    s = addr.strip().lower()
    return s if "@" in s else ""


def participant_emails_from_row(sender: Any, recipients: Any) -> set[str]:
    out: set[str] = set()
    for text in (sender, recipients):
        if text is None:
            continue
        for match in EMAIL_RE.findall(str(text)):
            em = match.lower().strip()
            if em and "@" in em:
                out.add(em)
    return out


def chunked(items: list[str], size: int = _IN_CHUNK_SIZE) -> list[list[str]]:
    if not items:
        return []
    return [items[i : i + size] for i in range(0, len(items), size)]


def sqlite_has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def collect_sqlite_index_advisories(conn: sqlite3.Connection) -> list[str]:
    advisories: list[str] = []

    def _has_index(table: str, want_cols: tuple[str, ...]) -> bool:
        rows = conn.execute(f"PRAGMA index_list({table})").fetchall()
        for row in rows:
            idx_name = row[1]
            cols = [
                str(r[2]).lower()
                for r in conn.execute(f"PRAGMA index_info({idx_name})").fetchall()
            ]
            if cols[: len(want_cols)] == [c.lower() for c in want_cols]:
                return True
        return False

    if sqlite_has_table(conn, "emails") and not _has_index("emails", ("source_file",)):
        advisories.append(
            "Recommended optional index for faster canonical sync: "
            "CREATE INDEX IF NOT EXISTS idx_emails_source_file ON emails(source_file);"
        )
    if sqlite_has_table(conn, "emails") and not _has_index("emails", ("id",)):
        pass  # PK already indexes id
    if sqlite_has_table(conn, "contact_master") and not _has_index(
        "contact_master", ("email",)
    ):
        advisories.append(
            "Recommended optional index for faster canonical sync: "
            "CREATE INDEX IF NOT EXISTS idx_contact_master_email ON contact_master(email);"
        )
    if sqlite_has_table(conn, "organization_master") and not _has_index(
        "organization_master", ("domain",)
    ):
        advisories.append(
            "Recommended optional index for faster canonical sync: "
            "CREATE INDEX IF NOT EXISTS idx_organization_master_domain "
            "ON organization_master(domain);"
        )
    if sqlite_has_table(conn, "opportunity_signals") and not _has_index(
        "opportunity_signals", ("email_id",)
    ):
        advisories.append(
            "Recommended optional index for faster canonical sync: "
            "CREATE INDEX IF NOT EXISTS idx_opportunity_signals_email_id "
            "ON opportunity_signals(email_id);"
        )
    return advisories


def build_canonical_sync_context(
    conn: sqlite3.Connection,
    *,
    log: PhaseLogger | None = None,
) -> CanonicalSyncContext | None:
    required = ("emails", "contact_master", "organization_master", "opportunity_signals")
    missing = [t for t in required if not sqlite_has_table(conn, t)]
    if missing:
        phase_log(
            f"[canonical] unavailable (missing SQLite tables: {', '.join(missing)})",
            log=log,
        )
        return None

    where = canonical_emails_where()
    phase_log(
        f"[canonical] scanning emails WHERE lower(source_file) LIKE "
        f"'{CONTACTO_GMAIL_SOURCE_SQL_LIKE_VALUE}' ...",
        log=log,
    )
    t0 = time.monotonic()
    cur = conn.execute(
        f"SELECT id, sender, recipients FROM emails WHERE {where}",
    )
    canonical_ids: set[int] = set()
    participants: set[str] = set()
    email_rows: list[tuple[int, Any, Any]] = []
    while True:
        batch = cur.fetchmany(_FETCH_BATCH)
        if not batch:
            break
        for row in batch:
            eid = int(row[0])
            canonical_ids.add(eid)
            participants.update(participant_emails_from_row(row[1], row[2]))
            email_rows.append((eid, row[1], row[2]))
    elapsed = time.monotonic() - t0
    phase_log(
        f"[canonical] emails scan done count={len(canonical_ids)} "
        f"participant_emails={len(participants)} elapsed={elapsed:.1f}s",
        log=log,
    )

    contact_cols = ", ".join(_CONTACT_COLS)
    contact_rows: list[tuple[Any, ...]] = []
    participant_list = sorted(participants)
    t1 = time.monotonic()
    phase_log("[canonical] loading contact_master by participant email (batched)...", log=log)
    for chunk in tqdm_stderr(
        chunked(participant_list),
        total=max(1, (len(participant_list) + _IN_CHUNK_SIZE - 1) // _IN_CHUNK_SIZE),
        desc="contacts",
        unit="batch",
    ):
        placeholders = ",".join("?" * len(chunk))
        sql = (
            f"SELECT {contact_cols} FROM contact_master "
            f"WHERE lower(trim(email)) IN ({placeholders}) ORDER BY email"
        )
        params = [e.lower().strip() for e in chunk]
        contact_rows.extend(conn.execute(sql, params).fetchall())
    contact_emails: set[str] = set()
    filtered_contacts: list[tuple[Any, ...]] = []
    email_idx = _CONTACT_COLS.index("email")
    domain_idx = _CONTACT_COLS.index("domain")
    for row in contact_rows:
        em = str(row[email_idx] or "").lower().strip()
        if not em or is_operational_noise_entity("contact", em):
            continue
        filtered_contacts.append(row)
        contact_emails.add(em)
    domains: set[str] = set()
    for row in filtered_contacts:
        dom = str(row[domain_idx] or "").lower().strip()
        if dom and not is_operational_noise_entity("organization", dom):
            domains.add(dom)
    phase_log(
        f"[canonical] contact_master done count={len(filtered_contacts)} "
        f"domains={len(domains)} elapsed={time.monotonic() - t1:.1f}s",
        log=log,
    )

    org_cols = ", ".join(_ORG_COLS)
    org_rows: list[tuple[Any, ...]] = []
    domain_list = sorted(domains)
    t2 = time.monotonic()
    phase_log("[canonical] loading organization_master by domain (batched)...", log=log)
    for chunk in tqdm_stderr(
        chunked(domain_list),
        total=max(1, (len(domain_list) + _IN_CHUNK_SIZE - 1) // _IN_CHUNK_SIZE),
        desc="orgs",
        unit="batch",
    ):
        placeholders = ",".join("?" * len(chunk))
        sql = (
            f"SELECT {org_cols} FROM organization_master "
            f"WHERE lower(trim(domain)) IN ({placeholders}) ORDER BY domain"
        )
        org_rows.extend(conn.execute(sql, [d.lower().strip() for d in chunk]).fetchall())
    domain_idx_org = _ORG_COLS.index("domain")
    filtered_orgs: list[tuple[Any, ...]] = []
    for row in org_rows:
        dom = str(row[domain_idx_org] or "").lower().strip()
        if not dom or is_operational_noise_entity("organization", dom):
            continue
        if dom not in domains:
            continue
        filtered_orgs.append(row)
    phase_log(
        f"[canonical] organization_master done count={len(filtered_orgs)} "
        f"elapsed={time.monotonic() - t2:.1f}s",
        log=log,
    )

    opp_cols = ", ".join(_OPP_COLS)
    opp_rows: list[tuple[Any, ...]] = []
    t3 = time.monotonic()
    phase_log("[canonical] loading opportunity_signals (batched filters)...", log=log)
    id_list = sorted(canonical_ids)
    seen_ids: set[int] = set()
    if id_list:
        for chunk in tqdm_stderr(
            chunked([str(i) for i in id_list]),
            total=max(1, (len(id_list) + _IN_CHUNK_SIZE - 1) // _IN_CHUNK_SIZE),
            desc="signals-email_id",
            unit="batch",
        ):
            placeholders = ",".join("?" * len(chunk))
            sql = (
                f"SELECT {opp_cols} FROM opportunity_signals "
                f"WHERE email_id IN ({placeholders}) ORDER BY id"
            )
            for row in conn.execute(sql, [int(x) for x in chunk]).fetchall():
                rid = int(row[0])
                if rid in seen_ids:
                    continue
                ek = str(row[_OPP_COLS.index("entity_key")] or "").lower().strip()
                if is_operational_noise_entity(
                    str(row[_OPP_COLS.index("entity_kind")] or ""), ek
                ):
                    continue
                seen_ids.add(rid)
                opp_rows.append(row)
    contact_keys = sorted(contact_emails)
    if contact_keys:
        for chunk in tqdm_stderr(
            chunked(contact_keys),
            total=max(1, (len(contact_keys) + _IN_CHUNK_SIZE - 1) // _IN_CHUNK_SIZE),
            desc="signals-contact",
            unit="batch",
        ):
            placeholders = ",".join("?" * len(chunk))
            sql = (
                f"SELECT {opp_cols} FROM opportunity_signals "
                f"WHERE lower(trim(entity_kind)) = 'contact' "
                f"AND lower(trim(entity_key)) IN ({placeholders}) ORDER BY id"
            )
            for row in conn.execute(sql, chunk).fetchall():
                rid = int(row[0])
                if rid in seen_ids:
                    continue
                ek = str(row[_OPP_COLS.index("entity_key")] or "").lower().strip()
                if is_operational_noise_entity("contact", ek):
                    continue
                seen_ids.add(rid)
                opp_rows.append(row)
    domain_keys = sorted(domains)
    if domain_keys:
        for chunk in tqdm_stderr(
            chunked(domain_keys),
            total=max(1, (len(domain_keys) + _IN_CHUNK_SIZE - 1) // _IN_CHUNK_SIZE),
            desc="signals-org",
            unit="batch",
        ):
            placeholders = ",".join("?" * len(chunk))
            sql = (
                f"SELECT {opp_cols} FROM opportunity_signals "
                f"WHERE lower(trim(entity_kind)) = 'organization' "
                f"AND lower(trim(entity_key)) IN ({placeholders}) ORDER BY id"
            )
            for row in conn.execute(sql, chunk).fetchall():
                rid = int(row[0])
                if rid in seen_ids:
                    continue
                ek = str(row[_OPP_COLS.index("entity_key")] or "").lower().strip()
                if is_operational_noise_entity("organization", ek):
                    continue
                seen_ids.add(rid)
                opp_rows.append(row)
    opp_rows.sort(key=lambda r: int(r[0]))
    phase_log(
        f"[canonical] opportunity_signals done count={len(opp_rows)} "
        f"elapsed={time.monotonic() - t3:.1f}s",
        log=log,
    )

    return CanonicalSyncContext(
        canonical_email_ids=frozenset(canonical_ids),
        participant_emails=frozenset(participants),
        contact_emails=frozenset(contact_emails),
        canonical_domains=frozenset(domains),
        contact_rows=tuple(filtered_contacts),
        organization_rows=tuple(filtered_orgs),
        opportunity_rows=tuple(opp_rows),
    )


def count_archive_source(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def collect_sqlite_source_counts(
    conn: sqlite3.Connection,
    specs: tuple[dict[str, Any], ...],
    *,
    skip_counts: bool = False,
    canonical_ctx: CanonicalSyncContext | None = None,
    log: PhaseLogger | None = None,
) -> tuple[dict[str, int], list[str], dict[str, bool]]:
    counts: dict[str, int] = {}
    warnings: list[str] = []
    exists_map: dict[str, bool] = {}

    for spec in specs:
        src = str(spec["source"])
        fast_kind = spec.get("canonical_fast")
        if fast_kind:
            required = tuple(spec.get("requires_tables") or ())
            missing = [t for t in required if not sqlite_has_table(conn, t)]
            exists_map[src] = not missing
            if missing:
                counts[src] = 0
                warnings.append(
                    f"SQLite canonical source unavailable for {src} "
                    f"(missing: {', '.join(missing)})"
                )
                continue
            if skip_counts:
                counts[src] = -1
                phase_log(f"[count] {src} skipped (--skip-counts)", log=log)
                continue
            if canonical_ctx is None:
                counts[src] = 0
                continue
            if fast_kind == "contact":
                counts[src] = canonical_ctx.contact_count
            elif fast_kind == "organization":
                counts[src] = canonical_ctx.organization_count
            else:
                counts[src] = canonical_ctx.opportunity_count
            phase_log(
                f"[count] {src} done count={counts[src]} (canonical fast path)",
                log=log,
            )
            continue

        exists = sqlite_has_table(conn, src)
        exists_map[src] = exists
        if not exists:
            counts[src] = 0
            warnings.append(f"SQLite source table missing: {src} (treated as 0 rows)")
            continue
        if skip_counts:
            counts[src] = -1
            phase_log(f"[count] {src} skipped (--skip-counts)", log=log)
            continue
        phase_log(f"[count] {src}...", log=log)
        t0 = time.monotonic()
        counts[src] = count_archive_source(conn, src)
        phase_log(
            f"[count] {src} done count={counts[src]} elapsed={time.monotonic() - t0:.1f}s",
            log=log,
        )
    return counts, warnings, exists_map


def normalize_postgres_url(url: str) -> str:
    u = url.strip()
    for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://"):
        if u.startswith(prefix):
            return "postgresql://" + u[len(prefix) :]
    return u


def resolve_sqlite_path(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    env = (os.environ.get("ORIGENLAB_SQLITE_PATH") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return load_settings().resolved_sqlite_path()


def resolve_postgres_url(explicit: str | None) -> str:
    if explicit and explicit.strip():
        return normalize_postgres_url(explicit.strip())
    for key in (
        "ORIGENLAB_POSTGRES_URL",
        "ALEMBIC_DATABASE_URL",
        "ORIGENLAB_CLOUD_POSTGRES_URL",
    ):
        v = (os.environ.get(key) or "").strip()
        if v:
            return normalize_postgres_url(v)
    raise ValueError(
        "Postgres URL required. Pass --postgres-url or set ORIGENLAB_POSTGRES_URL, "
        "ALEMBIC_DATABASE_URL, or ORIGENLAB_CLOUD_POSTGRES_URL."
    )


def assert_scratch_postgres_target(pg_url: str, *, allow_non_scratch: bool) -> None:
    if allow_non_scratch:
        return
    low = pg_url.lower()
    if any(tok in low for tok in _SCRATCH_URL_TOKENS):
        return
    parsed = urlparse(pg_url)
    host = (parsed.hostname or "").lower()
    db = (parsed.path or "").lstrip("/").lower()
    if host in ("localhost", "127.0.0.1") or "scratch" in db or "staging" in db:
        return
    raise ValueError(
        "Postgres URL does not look like scratch/staging (no scratch/staging/test/dev/local). "
        "Pass --allow-non-scratch-postgres only when you intend a non-scratch target."
    )


def _require_psycopg() -> None:
    if psycopg is None:
        raise RuntimeError(
            "psycopg is required. Install: uv sync --group postgres "
            f"({_PSYCOPG_IMPORT_ERROR})"
        )


def _normalize_iso_z(value: str) -> str:
    s = value.strip()
    if len(s) >= 1 and s[-1] in ("Z", "z"):
        return s[:-1] + "+00:00"
    return s


def iso_text_to_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("expected str or NULL")
    s = _normalize_iso_z(value.strip())
    if not s:
        raise ValueError("empty timestamp string")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_jsonb_python(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        raise ValueError("expected str, dict, list, or NULL for JSON")
    s = value.strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return {"raw": s}


def adapt_jsonb_for_postgres(value: Any) -> Any:
    _require_psycopg()
    assert Json is not None
    if value is None:
        return None
    if isinstance(value, Json):
        return value
    parsed = parse_jsonb_python(value)
    if parsed is None:
        return None
    return Json(parsed)


def pg_table_exists(cur: psycopg.Cursor, schema: str, table: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = %s AND table_name = %s
        """,
        (schema, table),
    )
    return cur.fetchone() is not None


def should_refuse_nonempty_targets(*, any_nonempty: bool, replace: bool, dry_run: bool) -> bool:
    return any_nonempty and not replace and not dry_run


def format_load_progress(
    *, pg_table: str, loaded_so_far: int, total: int, elapsed_s: float, batch_len: int
) -> str:
    pct = (100.0 * loaded_so_far / total) if total else 0.0
    return (
        f"[load] {pg_table}: {loaded_so_far}/{total} ({pct:.1f}%) "
        f"elapsed={elapsed_s:.1f}s batch={batch_len}"
    )


def _convert_row(
    row: tuple[Any, ...],
    *,
    table: str,
    pk: str,
    columns: tuple[str, ...],
    timestamp_columns: frozenset[str],
    json_columns: frozenset[str],
) -> tuple[Any, ...]:
    out: list[Any] = []
    row_id = row[0] if row else None
    for i, col in enumerate(columns):
        v = row[i]
        if col in timestamp_columns:
            try:
                out.append(iso_text_to_datetime(v))
            except ValueError:
                raise ConversionError(table, row_id, col, v) from None
            continue
        if col in json_columns:
            try:
                out.append(adapt_jsonb_for_postgres(v))
            except ValueError:
                raise ConversionError(table, row_id, col, v) from None
            continue
        if col == pk and v is None:
            raise ConversionError(table, row_id, col, v)
        out.append(v)
    return tuple(out)


def _insert_sql(target: str, columns: tuple[str, ...]) -> str:
    cols = ", ".join(columns)
    vals = ", ".join(["%s"] * len(columns))
    return f"INSERT INTO {target} ({cols}) VALUES ({vals})"


def _rows_for_canonical_spec(
    spec: dict[str, Any], ctx: CanonicalSyncContext
) -> tuple[tuple[Any, ...], ...]:
    kind = spec.get("canonical_fast")
    if kind == "contact":
        return ctx.contact_rows
    if kind == "organization":
        return ctx.organization_rows
    return ctx.opportunity_rows


def load_table(
    sconn: sqlite3.Connection,
    pconn: psycopg.Connection,
    *,
    spec: dict[str, Any],
    source_exists: bool,
    t_start: float,
    canonical_ctx: CanonicalSyncContext | None = None,
    fetch_batch: int = _FETCH_BATCH,
    log: PhaseLogger | None = None,
) -> int:
    """Insert one mart target into the caller-owned transaction.

    Never commits or rolls back. Batching is for statement size only.
    """
    if not source_exists:
        return 0

    source = str(spec["source"])
    target = str(spec["target"])
    pk = str(spec["pk"])
    columns = tuple(spec["columns"])
    timestamp_columns = frozenset(spec.get("timestamp_columns") or ())
    json_columns = frozenset(spec.get("json_columns") or ())

    if spec.get("canonical_fast"):
        if canonical_ctx is None:
            return 0
        all_rows = _rows_for_canonical_spec(spec, canonical_ctx)
        total = len(all_rows)
        phase_log(f"[load] {target} starting rows={total} (canonical fast path)", log=log)
    else:
        total = count_archive_source(sconn, source)
        phase_log(f"[load] {target} starting rows={total}", log=log)
        all_rows = None

    loaded = 0
    sql = _insert_sql(target, columns)
    if all_rows is not None:
        row_iter = tqdm_stderr(all_rows, total=total or None, desc=target.split(".")[-1], unit="row")
        batch: list[tuple[Any, ...]] = []
        for row in row_iter:
            batch.append(
                _convert_row(
                    row,
                    table=source,
                    pk=pk,
                    columns=columns,
                    timestamp_columns=timestamp_columns,
                    json_columns=json_columns,
                )
            )
            if len(batch) >= fetch_batch:
                with pconn.cursor() as pcur:
                    pcur.executemany(sql, batch)
                loaded += len(batch)
                phase_log(
                    format_load_progress(
                        pg_table=target,
                        loaded_so_far=loaded,
                        total=total,
                        elapsed_s=time.monotonic() - t_start,
                        batch_len=len(batch),
                    ),
                    log=log,
                )
                batch = []
        if batch:
            with pconn.cursor() as pcur:
                pcur.executemany(sql, batch)
            loaded += len(batch)
            phase_log(
                format_load_progress(
                    pg_table=target,
                    loaded_so_far=loaded,
                    total=total,
                    elapsed_s=time.monotonic() - t_start,
                    batch_len=len(batch),
                ),
                log=log,
            )
        phase_log(f"[load] {target} done loaded={loaded}", log=log)
        return loaded

    scur = sconn.cursor()
    scur.execute(f"SELECT {', '.join(columns)} FROM {source} ORDER BY {pk}")
    while True:
        rows = scur.fetchmany(fetch_batch)
        if not rows:
            break
        batch = [
            _convert_row(
                r,
                table=source,
                pk=pk,
                columns=columns,
                timestamp_columns=timestamp_columns,
                json_columns=json_columns,
            )
            for r in rows
        ]
        with pconn.cursor() as pcur:
            pcur.executemany(sql, batch)
        loaded += len(batch)
        phase_log(
            format_load_progress(
                pg_table=target,
                loaded_so_far=loaded,
                total=total,
                elapsed_s=time.monotonic() - t_start,
                batch_len=len(batch),
            ),
            log=log,
        )
    phase_log(f"[load] {target} done loaded={loaded}", log=log)
    return loaded


def lock_mart_targets(cur: psycopg.Cursor, specs: tuple[dict[str, Any], ...]) -> None:
    """Serialize competing replace writers on selected targets; SELECTs remain allowed."""
    cur.execute("SET LOCAL lock_timeout = '30s'")
    for spec in sorted(specs, key=lambda s: (int(s["delete_order"]), str(s["target"]))):
        cur.execute(f"LOCK TABLE {spec['target']} IN SHARE ROW EXCLUSIVE MODE")


def delete_targets_for_specs(
    cur: psycopg.Cursor, specs: tuple[dict[str, Any], ...]
) -> None:
    """DELETE selected targets only. Never commits. Does not touch sequences."""
    for spec in sorted(specs, key=lambda s: int(s["delete_order"])):
        cur.execute(f"DELETE FROM {spec['target']}")


def _parse_qualified_sequence_name(seq_identity: str) -> tuple[str, str]:
    """Parse ``schema.seqname`` from ``pg_get_serial_sequence``; fail closed on junk."""
    raw = (seq_identity or "").strip().strip('"')
    parts = raw.split(".")
    if len(parts) != 2:
        raise ValueError(f"malformed sequence identity: {seq_identity!r}")
    schema = parts[0].strip().strip('"')
    name = parts[1].strip().strip('"')
    if not _SEQ_IDENT_RE.match(schema) or not _SEQ_IDENT_RE.match(name):
        raise ValueError(f"malformed sequence identity: {seq_identity!r}")
    return schema, name


def _owned_serial_sequence_for_table(cur: psycopg.Cursor, table: str) -> str:
    cur.execute("SELECT pg_get_serial_sequence(%s, 'id')", (table,))
    row = cur.fetchone()
    if not row or not row[0]:
        raise ValueError(
            f"no owned serial sequence for {table}.id; cannot restart transactionally"
        )
    return str(row[0])


def assert_opp_sequence_restart_authority(
    cur: psycopg.Cursor, specs: tuple[dict[str, Any], ...]
) -> None:
    """Fail closed unless current role can ``ALTER SEQUENCE`` selected opp sequences.

    ``ALTER SEQUENCE … RESTART`` requires sequence ownership or immediately usable
    privileges of the owner role. Accept only:

    * ``current_user`` is the exact sequence owner;
    * ``current_user`` is a superuser; or
    * ``pg_has_role(current_user, owner, 'USAGE')`` (owner privileges are
      immediately available without ``SET ROLE``).

    Do **not** treat ``pg_has_role(..., 'MEMBER')`` alone as authority: a
    ``NOINHERIT`` membership path can be a member without usable privileges until
    ``SET ROLE``, which this loader does not execute.

    BIGSERIAL sequences are owned by the Alembic migration role; mirror apply must
    use that owning role or an immediately inheriting path (not ``origenlab_api_ro``).
    """
    if psycopg is None:
        raise RuntimeError(f"psycopg is required: {_PSYCOPG_IMPORT_ERROR}")
    loaded_targets = {str(s["target"]) for s in specs}
    for seq_table in _OPP_SEQ_TABLES:
        if seq_table not in loaded_targets:
            continue
        seq_identity = _owned_serial_sequence_for_table(cur, seq_table)
        schema, name = _parse_qualified_sequence_name(seq_identity)
        cur.execute(
            """
            SELECT
              pg_catalog.pg_get_userbyid(c.relowner) AS owner_role,
              current_user AS current_role,
              (
                c.relowner = (SELECT oid FROM pg_roles WHERE rolname = current_user)
                OR EXISTS (
                  SELECT 1 FROM pg_roles r
                  WHERE r.rolname = current_user AND r.rolsuper
                )
                OR pg_has_role(current_user, c.relowner, 'USAGE')
              ) AS can_alter
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s
              AND c.relname = %s
              AND c.relkind = 'S'
            """,
            (schema, name),
        )
        auth = cur.fetchone()
        if auth is None:
            raise ValueError(f"sequence catalog entry missing for {seq_identity}")
        owner_role, current_role, can_alter = auth
        if not can_alter:
            raise PermissionError(
                f"current role {current_role!r} cannot ALTER SEQUENCE {seq_identity} "
                f"(owner={owner_role!r}); mart publication requires exact sequence "
                "ownership, superuser, or immediately available owner privileges "
                "(pg_has_role USAGE / INHERIT) — not MEMBER-only NOINHERIT membership; "
                "typically the Alembic migration / mirror apply role, not origenlab_api_ro"
            )


def restart_opp_sequences(
    cur: psycopg.Cursor, specs: tuple[dict[str, Any], ...]
) -> None:
    """Transactional sequence repair after explicit-ID inserts. Never commits.

    Uses ``ALTER SEQUENCE … RESTART WITH`` (transactional). Does **not** use
    ``setval`` (non-transactional). Next value is ``MAX(id)+1``, or ``1`` if empty.
    """
    if psycopg is None or pg_sql is None:
        raise RuntimeError(f"psycopg is required: {_PSYCOPG_IMPORT_ERROR}")
    loaded_targets = {str(s["target"]) for s in specs}
    for seq_table in _OPP_SEQ_TABLES:
        if seq_table not in loaded_targets:
            continue
        seq_identity = _owned_serial_sequence_for_table(cur, seq_table)
        schema, name = _parse_qualified_sequence_name(seq_identity)
        table_schema, table_name = seq_table.split(".", 1)
        if not _SEQ_IDENT_RE.match(table_schema) or not _SEQ_IDENT_RE.match(table_name):
            raise ValueError(f"malformed opportunity table identity: {seq_table!r}")
        cur.execute(
            pg_sql.SQL("SELECT COALESCE(MAX({}), 0) FROM {}.{}").format(
                pg_sql.Identifier("id"),
                pg_sql.Identifier(table_schema),
                pg_sql.Identifier(table_name),
            )
        )
        max_id = int(cur.fetchone()[0])
        next_val = 1 if max_id <= 0 else max_id + 1
        stmt = pg_sql.SQL("ALTER SEQUENCE {}.{} RESTART WITH {}").format(
            pg_sql.Identifier(schema),
            pg_sql.Identifier(name),
            pg_sql.Literal(next_val),
        )
        try:
            cur.execute(stmt)
        except Exception as exc:
            raise RuntimeError(
                f"ALTER SEQUENCE {schema}.{name} RESTART WITH {next_val} failed: {exc}"
            ) from exc


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sqlite-db", type=Path, default=None)
    p.add_argument("--postgres-url", default=None)
    p.add_argument(
        "--tables",
        choices=("all", "archive", "canonical"),
        default="all",
        help="Which mart mirrors to load (default: all)",
    )
    p.add_argument(
        "--replace",
        action="store_true",
        help="DELETE selected Postgres targets before load",
    )
    p.add_argument("--dry-run", action="store_true", help="Validate only; no writes")
    p.add_argument(
        "--skip-counts",
        action="store_true",
        help="Skip SQLite COUNT prechecks (canonical uses fast path counts when loaded)",
    )
    p.add_argument("--json-out", type=Path, default=None)
    p.add_argument(
        "--allow-non-scratch-postgres",
        action="store_true",
        help="Allow Postgres URLs that do not look like scratch/staging",
    )
    return p


def _empty_result() -> dict[str, Any]:
    return {
        "ok": False,
        "dry_run": False,
        "replace": False,
        "transaction_committed": False,
        "tables": "all",
        "sqlite_counts": {},
        "postgres_counts_before": {},
        "postgres_counts_after": {},
        "loaded": {},
        "attempted_loaded": {},
        "validation": {},
        "errors": [],
        "warnings": [],
        "elapsed_seconds": 0.0,
    }


def _write_json(path: Path | None, doc: dict[str, Any]) -> None:
    if path is None:
        return
    path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def run_migration(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    tables_group = parse_tables_arg(args.tables)
    active_specs = table_specs_for_group(tables_group)

    result = _empty_result()
    result["dry_run"] = bool(args.dry_run)
    result["replace"] = bool(args.replace)
    result["tables"] = tables_group
    result["loaded"] = {str(spec["source"]): 0 for spec in active_specs}
    result["attempted_loaded"] = {str(spec["source"]): 0 for spec in active_specs}

    phase_log(
        f"[start] mart core migrate tables={tables_group} "
        f"replace={bool(args.replace)} dry_run={bool(args.dry_run)}"
    )

    sqlite_path = resolve_sqlite_path(args.sqlite_db)
    if not sqlite_path.is_file():
        result["errors"].append(f"SQLite file not found: {sqlite_path}")
        _write_json(args.json_out, result)
        print(result["errors"][-1], file=sys.stderr)
        return 2

    try:
        sconn = connect_sqlite_readonly(sqlite_path)
    except sqlite3.Error as exc:
        result["errors"].append(f"SQLite open failed: {exc}")
        _write_json(args.json_out, result)
        print(result["errors"][-1], file=sys.stderr)
        return 2

    for advisory in collect_sqlite_index_advisories(sconn):
        result["warnings"].append(advisory)
        phase_log(f"[advisory] {advisory}")

    canonical_ctx: CanonicalSyncContext | None = None
    needs_canonical = tables_group in ("all", "canonical")
    if needs_canonical:
        canonical_ctx = build_canonical_sync_context(sconn)

    sqlite_counts, warnings, exists_map = collect_sqlite_source_counts(
        sconn,
        active_specs,
        skip_counts=bool(args.skip_counts),
        canonical_ctx=canonical_ctx,
        log=phase_log,
    )
    result["sqlite_counts"] = sqlite_counts
    result["warnings"].extend(warnings)

    try:
        _require_psycopg()
        pg_url = resolve_postgres_url(args.postgres_url)
        assert_scratch_postgres_target(pg_url, allow_non_scratch=bool(args.allow_non_scratch_postgres))
    except (RuntimeError, ValueError) as exc:
        sconn.close()
        result["errors"].append(str(exc))
        _write_json(args.json_out, result)
        print(result["errors"][-1], file=sys.stderr)
        return 2

    assert psycopg is not None
    try:
        pconn = psycopg.connect(pg_url, autocommit=False)
    except Exception as exc:  # noqa: BLE001
        sconn.close()
        result["errors"].append(
            "Postgres connect failed: " + sanitize_migrate_error(exc, postgres_url=pg_url)
        )
        _write_json(args.json_out, result)
        print(result["errors"][-1], file=sys.stderr)
        return 2

    t0 = time.monotonic()
    try:
        with pconn.cursor() as cur:
            for spec in active_specs:
                schema, table = str(spec["target"]).split(".", 1)
                if not pg_table_exists(cur, schema, table):
                    raise ValueError(
                        f"Postgres target missing: {spec['target']}. "
                        "Run: uv run alembic -c alembic.ini upgrade head"
                    )
            for spec in active_specs:
                cur.execute(f"SELECT COUNT(*) FROM {spec['target']}")
                result["postgres_counts_before"][str(spec["source"])] = int(cur.fetchone()[0])

        selected_before = {
            str(spec["source"]): result["postgres_counts_before"][str(spec["source"])]
            for spec in active_specs
        }
        any_nonempty = any(v > 0 for v in selected_before.values())
        if should_refuse_nonempty_targets(
            any_nonempty=any_nonempty, replace=bool(args.replace), dry_run=bool(args.dry_run)
        ):
            sconn.close()
            result["errors"].append(
                "Selected mart core targets are not empty. Use --replace to reload on scratch."
            )
            _write_json(args.json_out, result)
            print(result["errors"][-1], file=sys.stderr)
            return 1

        if args.dry_run:
            result["ok"] = True
            result["transaction_committed"] = False
            result["elapsed_seconds"] = round(time.monotonic() - t0, 3)
            sconn.close()
            _write_json(args.json_out, result)
            print("dry-run ok: mart core prechecks passed; no writes performed.")
            return 0

        result["attempted_loaded"] = {str(spec["source"]): 0 for spec in active_specs}
        try:
            with pconn.cursor() as cur:
                lock_mart_targets(cur, active_specs)
                assert_opp_sequence_restart_authority(cur, active_specs)
                for spec in active_specs:
                    cur.execute(f"SELECT COUNT(*) FROM {spec['target']}")
                    result["postgres_counts_before"][str(spec["source"])] = int(
                        cur.fetchone()[0]
                    )
                selected_locked = {
                    str(spec["source"]): result["postgres_counts_before"][str(spec["source"])]
                    for spec in active_specs
                }
                if should_refuse_nonempty_targets(
                    any_nonempty=any(v > 0 for v in selected_locked.values()),
                    replace=bool(args.replace),
                    dry_run=False,
                ):
                    raise ValueError(
                        "Selected mart core targets are not empty. "
                        "Use --replace to reload on scratch."
                    )
                if args.replace:
                    phase_log(f"[replace] deleting selected targets ({tables_group})...")
                    delete_targets_for_specs(cur, active_specs)
                    phase_log("[replace] delete staged (uncommitted)")

            for spec in active_specs:
                src = str(spec["source"])
                loaded = load_table(
                    sconn,
                    pconn,
                    spec=spec,
                    source_exists=bool(exists_map[src]),
                    t_start=t0,
                    canonical_ctx=canonical_ctx,
                    log=phase_log,
                )
                result["attempted_loaded"][src] = loaded
                result["loaded"][src] = loaded
                if sqlite_counts.get(src) == -1:
                    sqlite_counts[src] = loaded

            with pconn.cursor() as cur:
                restart_opp_sequences(cur, active_specs)
                row_count_ok = True
                for spec in active_specs:
                    src = str(spec["source"])
                    target = str(spec["target"])
                    cur.execute(f"SELECT COUNT(*) FROM {target}")
                    after = int(cur.fetchone()[0])
                    result["postgres_counts_after"][src] = after
                    if not exists_map[src]:
                        continue
                    expected = sqlite_counts[src]
                    if expected < 0:
                        expected = result["loaded"][src]
                    row_count_ok = row_count_ok and (expected == after)
                result["validation"]["row_counts_match"] = row_count_ok
                if not row_count_ok:
                    raise ValidationError(
                        "Post-load validation failed for mart core migration."
                    )

            pconn.commit()
            result["transaction_committed"] = True
            result["ok"] = True
        except Exception:
            pconn.rollback()
            result["transaction_committed"] = False
            result["ok"] = False
            result["loaded"] = {str(spec["source"]): 0 for spec in active_specs}
            raise

        result["elapsed_seconds"] = round(time.monotonic() - t0, 3)
        sconn.close()
        _write_json(args.json_out, result)
        print("migration completed:", json.dumps(result["loaded"], indent=2))
        return 0
    except (ConversionError, ValueError, ValidationError) as exc:
        result["errors"].append(sanitize_migrate_error(exc, postgres_url=pg_url))
        result["elapsed_seconds"] = round(time.monotonic() - t0, 3)
        result["transaction_committed"] = False
        result["ok"] = False
        try:
            sconn.close()
        except Exception:  # noqa: BLE001
            pass
        _write_json(args.json_out, result)
        print(result["errors"][-1], file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(sanitize_migrate_error(exc, postgres_url=pg_url))
        result["elapsed_seconds"] = round(time.monotonic() - t0, 3)
        result["transaction_committed"] = False
        result["ok"] = False
        try:
            pconn.rollback()
        except Exception:  # noqa: BLE001
            pass
        try:
            sconn.close()
        except Exception:  # noqa: BLE001
            pass
        _write_json(args.json_out, result)
        print(result["errors"][-1], file=sys.stderr)
        return 1
    finally:
        pconn.close()


def main(argv: list[str] | None = None) -> int:
    return run_migration(argv)

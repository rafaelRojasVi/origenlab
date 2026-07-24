#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# EXPERIMENTAL_PARKED: Postgres migration path — not daily runtime. Trial on scratch
# Postgres only; do not run without explicit operator approval.
# See docs/EXPERIMENTAL_PARKED.md and docs/SCRIPT_MAP.md — "Break-glass scripts".
# -----------------------------------------------------------------------------
# SAFETY (break-glass): Writes to Postgres outbound.*; --replace DELETEs target
# sidecar tables before reload. Verify --postgres-url before running.
# See docs/SCRIPT_MAP.md — "Break-glass scripts".
# -----------------------------------------------------------------------------
"""Copy SQLite outbound sidecar tables into Postgres outbound schema.

Scope:
    - Reads SQLite in read-only mode.
    - Writes only outbound.contact_email_suppression, outbound.contact_domain_suppression,
      outbound.outreach_contact_state.
    - Does not modify SQLite runtime and does not touch other Postgres tables.

Behavior:
    - Missing SQLite source sidecar tables are warnings (count 0), not hard failures.
    - Missing Postgres target sidecar tables are hard failures.
    - Default mode refuses to write if any target table is non-empty.
    - --replace clears only the three outbound sidecar target tables.
    - Apply publishes all three targets in **one** PostgreSQL transaction (delete +
      load + validate, then a single commit). Readers never see empty/partial sets.
    - In --replace mode, a missing SQLite source still publishes an empty target
      for that table (logical zero rows) as part of the same atomic set.
    - --dry-run validates only and performs no writes.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from origenlab_email_pipeline.config import load_settings  # noqa: E402
from origenlab_email_pipeline.postgres_outbound_audit import redact_postgres_url  # noqa: E402

_VALIDATE_PATH = REPO / "scripts" / "qa" / "validate_sqlite_archive_for_postgres.py"
_spec = importlib.util.spec_from_file_location("validate_sqlite_archive_for_postgres", _VALIDATE_PATH)
assert _spec and _spec.loader
_validate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_validate_mod)
_connect_readonly = _validate_mod._connect_readonly
_normalize_iso_z = _validate_mod._normalize_iso_z

try:
    import psycopg
except ImportError as exc:  # pragma: no cover
    psycopg = None  # type: ignore[misc, assignment]
    _PSYCOPG_IMPORT_ERROR = exc
else:
    _PSYCOPG_IMPORT_ERROR = None

TABLE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "source": "contact_email_suppression",
        "target": "outbound.contact_email_suppression",
        "pk": "email",
        "columns": (
            "email",
            "suppression_reason_code",
            "suppression_reason_text",
            "suppression_source",
            "last_bounced_at",
            "updated_at",
            "updated_by",
        ),
        "timestamp_columns": frozenset({"last_bounced_at", "updated_at"}),
    },
    {
        "source": "contact_domain_suppression",
        "target": "outbound.contact_domain_suppression",
        "pk": "domain_norm",
        "columns": (
            "domain_norm",
            "suppression_reason_text",
            "updated_at",
            "updated_by",
        ),
        "timestamp_columns": frozenset({"updated_at"}),
    },
    {
        "source": "outreach_contact_state",
        "target": "outbound.outreach_contact_state",
        "pk": "contact_email_norm",
        "columns": (
            "contact_email_norm",
            "state",
            "first_contacted_at",
            "last_contacted_at",
            "source",
            "notes",
            "updated_at",
            "updated_by",
            "lead_id",
        ),
        "timestamp_columns": frozenset({"first_contacted_at", "last_contacted_at", "updated_at"}),
    },
)

# Deterministic lock order for competing replace writers (deadlock-safe).
OUTBOUND_LOCK_TARGETS: tuple[str, ...] = tuple(
    str(spec["target"]) for spec in TABLE_SPECS
)
# Bound wait for writer serialization; ordinary SELECT readers are unaffected.
OUTBOUND_LOCK_TIMEOUT = "30s"


class ConversionError(Exception):
    def __init__(self, table: str, row_id: Any, column: str, value: Any) -> None:
        super().__init__(
            f"invalid conversion {table} id={row_id!r} column={column!r} value={value!r}"
        )


class ValidationError(RuntimeError):
    """Post-load validation failed; caller must roll back the apply transaction."""


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
    for key in ("ORIGENLAB_POSTGRES_URL", "ALEMBIC_DATABASE_URL"):
        v = (os.environ.get(key) or "").strip()
        if v:
            return normalize_postgres_url(v)
    raise ValueError(
        "Postgres URL required. Pass --postgres-url or set ORIGENLAB_POSTGRES_URL "
        "or ALEMBIC_DATABASE_URL."
    )


def _require_psycopg() -> None:
    if psycopg is None:
        raise RuntimeError(
            "psycopg is required for this script. Install the postgres group: "
            f"uv sync --group postgres ({_PSYCOPG_IMPORT_ERROR})"
        )


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


def sqlite_has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


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
        f"loading {pg_table}: {loaded_so_far}/{total} ({pct:.1f}%) "
        f"elapsed={elapsed_s:.1f}s batch={batch_len}"
    )


def collect_sqlite_source_counts(
    conn: sqlite3.Connection, specs: tuple[dict[str, Any], ...]
) -> tuple[dict[str, int], list[str], dict[str, bool]]:
    counts: dict[str, int] = {}
    warnings: list[str] = []
    exists_map: dict[str, bool] = {}
    for spec in specs:
        src = str(spec["source"])
        exists = sqlite_has_table(conn, src)
        exists_map[src] = exists
        if not exists:
            counts[src] = 0
            warnings.append(f"SQLite source table missing: {src} (treated as 0 rows)")
            continue
        counts[src] = int(conn.execute(f"SELECT COUNT(*) FROM {src}").fetchone()[0])
    return counts, warnings, exists_map


def fetch_sqlite_pk_set(conn: sqlite3.Connection, *, table: str, pk: str) -> set[str]:
    rows = conn.execute(
        f"SELECT {pk} FROM {table} WHERE {pk} IS NOT NULL ORDER BY {pk}"
    ).fetchall()
    return {str(r[0]) for r in rows}


def fetch_pg_pk_set(cur: psycopg.Cursor, *, target: str, pk: str) -> set[str]:
    cur.execute(f"SELECT {pk} FROM {target} WHERE {pk} IS NOT NULL ORDER BY {pk}")
    return {str(r[0]) for r in cur.fetchall()}


def _convert_row(
    row: tuple[Any, ...], *, table: str, pk: str, columns: tuple[str, ...], timestamp_columns: frozenset[str]
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
        # Ensure PK stays non-null in converted payload.
        if col == pk and v is None:
            raise ConversionError(table, row_id, col, v)
        out.append(v)
    return tuple(out)


def _insert_sql(target: str, columns: tuple[str, ...]) -> str:
    cols = ", ".join(columns)
    vals = ", ".join(["%s"] * len(columns))
    return f"INSERT INTO {target} ({cols}) VALUES ({vals})"


def sanitize_migrate_error(
    message: BaseException | str, *, postgres_url: str | None = None
) -> str:
    """Sanitize CLI/JSON errors without inventing a second redaction stack."""
    text = str(message) if not isinstance(message, BaseException) else (
        str(message) or type(message).__name__
    )
    if postgres_url:
        for secret in (postgres_url, redact_postgres_url(postgres_url)):
            if secret and secret in text:
                text = text.replace(secret, "<redacted>")
    for key in ("ORIGENLAB_POSTGRES_URL", "ALEMBIC_DATABASE_URL"):
        val = os.environ.get(key)
        if val and val in text:
            text = text.replace(val, "<redacted>")
    return text


def lock_outbound_sidecar_targets(cur: Any) -> None:
    """Serialize competing replace writers; ordinary SELECT readers remain allowed."""
    cur.execute(f"SET LOCAL lock_timeout = '{OUTBOUND_LOCK_TIMEOUT}'")
    for target in OUTBOUND_LOCK_TARGETS:
        cur.execute(f"LOCK TABLE {target} IN SHARE ROW EXCLUSIVE MODE")


def delete_outbound_sidecar_targets(cur: Any) -> None:
    """Delete targets in dependency-safe order (state → domain → email)."""
    cur.execute("DELETE FROM outbound.outreach_contact_state")
    cur.execute("DELETE FROM outbound.contact_domain_suppression")
    cur.execute("DELETE FROM outbound.contact_email_suppression")


def load_sidecar_table(
    sconn: sqlite3.Connection,
    pconn: psycopg.Connection,
    *,
    source: str,
    target: str,
    pk: str,
    columns: tuple[str, ...],
    timestamp_columns: frozenset[str],
    source_exists: bool,
    t_start: float,
    fetch_batch: int = 1000,
) -> int:
    """Insert one sidecar table into the caller-owned transaction.

    Never commits or rolls back. Batching is for statement size only.
    """
    if not source_exists:
        return 0

    total = int(sconn.execute(f"SELECT COUNT(*) FROM {source}").fetchone()[0])
    loaded = 0
    sql = _insert_sql(target, columns)
    scur = sconn.cursor()
    scur.execute(f"SELECT {', '.join(columns)} FROM {source} ORDER BY {pk}")
    while True:
        rows = scur.fetchmany(fetch_batch)
        if not rows:
            break
        batch = [
            _convert_row(
                r, table=source, pk=pk, columns=columns, timestamp_columns=timestamp_columns
            )
            for r in rows
        ]
        with pconn.cursor() as pcur:
            pcur.executemany(sql, batch)
        loaded += len(batch)
        print(
            format_load_progress(
                pg_table=target,
                loaded_so_far=loaded,
                total=total,
                elapsed_s=time.monotonic() - t_start,
                batch_len=len(batch),
            ),
            flush=True,
        )
    return loaded


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sqlite-db", type=Path, default=None, help="SQLite path (else env/settings)")
    p.add_argument(
        "--postgres-url",
        default=None,
        help="Postgres URL (else ORIGENLAB_POSTGRES_URL / ALEMBIC_DATABASE_URL)",
    )
    p.add_argument("--replace", action="store_true", help="Clear outbound sidecar targets before load")
    p.add_argument("--dry-run", action="store_true", help="Validate only; do not write")
    p.add_argument("--json-out", type=Path, default=None, help="Write JSON summary")
    p.add_argument("--sample-limit", type=int, default=10, metavar="N")
    return p


def _empty_result() -> dict[str, Any]:
    return {
        "ok": False,
        "dry_run": False,
        "replace": False,
        "transaction_committed": False,
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
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def _validate_outbound_publication(
    *,
    sconn: sqlite3.Connection,
    cur: Any,
    exists_map: dict[str, bool],
    sqlite_counts: dict[str, int],
    result: dict[str, Any],
) -> None:
    """Populate validation fields; raise ValidationError if checks fail."""
    for spec in TABLE_SPECS:
        src = str(spec["source"])
        pk = str(spec["pk"])
        target = str(spec["target"])
        cur.execute(f"SELECT COUNT(*) FROM {target}")
        result["postgres_counts_after"][src] = int(cur.fetchone()[0])
        cur.execute(f"SELECT COUNT(*) FROM {target} WHERE {pk} IS NULL")
        result["validation"][f"{src}.target_null_pk"] = int(cur.fetchone()[0])

        if exists_map[src]:
            sqlite_pk = fetch_sqlite_pk_set(sconn, table=src, pk=pk)
            pg_pk = fetch_pg_pk_set(cur, target=target, pk=pk)
            result["validation"][f"{src}.pk_set_match"] = sqlite_pk == pg_pk
        else:
            result["validation"][f"{src}.pk_set_match"] = True

    cur.execute(
        """
        SELECT state, COUNT(*) AS c
        FROM outbound.outreach_contact_state
        GROUP BY state
        ORDER BY c DESC, state
        """
    )
    result["validation"]["outreach_state_distribution"] = [
        {"state": r[0], "count": int(r[1])} for r in cur.fetchall()
    ]

    row_count_ok = True
    pk_ok = True
    null_pk_ok = True
    for spec in TABLE_SPECS:
        src = str(spec["source"])
        if exists_map[src]:
            row_count_ok = row_count_ok and (
                sqlite_counts[src] == result["postgres_counts_after"][src]
            )
        pk_ok = pk_ok and bool(result["validation"][f"{src}.pk_set_match"])
        null_pk_ok = null_pk_ok and (int(result["validation"][f"{src}.target_null_pk"]) == 0)

    result["validation"]["row_counts_match"] = row_count_ok
    result["validation"]["pk_sets_match"] = pk_ok
    result["validation"]["target_null_primary_keys"] = null_pk_ok
    if not (row_count_ok and pk_ok and null_pk_ok):
        raise ValidationError("Post-load validation failed for outbound sidecar migration.")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sample_limit = max(1, int(args.sample_limit))
    _ = sample_limit  # reserved for future sample windows while preserving CLI contract

    result = _empty_result()
    result["dry_run"] = bool(args.dry_run)
    result["replace"] = bool(args.replace)
    result["loaded"] = {str(spec["source"]): 0 for spec in TABLE_SPECS}
    result["attempted_loaded"] = {str(spec["source"]): 0 for spec in TABLE_SPECS}

    sqlite_path = resolve_sqlite_path(args.sqlite_db)
    if not sqlite_path.is_file():
        result["errors"].append(f"SQLite file not found: {sqlite_path}")
        _write_json(args.json_out, result)
        print(result["errors"][-1], file=sys.stderr)
        return 2

    try:
        sconn = _connect_readonly(sqlite_path)
    except sqlite3.Error as exc:
        result["errors"].append(f"SQLite open failed: {exc}")
        _write_json(args.json_out, result)
        print(result["errors"][-1], file=sys.stderr)
        return 2

    sqlite_counts, warnings, exists_map = collect_sqlite_source_counts(sconn, TABLE_SPECS)
    result["sqlite_counts"] = sqlite_counts
    result["warnings"].extend(warnings)

    pg_url: str | None = None
    try:
        _require_psycopg()
        pg_url = resolve_postgres_url(args.postgres_url)
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
            for spec in TABLE_SPECS:
                schema, table = str(spec["target"]).split(".", 1)
                if not pg_table_exists(cur, schema, table):
                    raise ValueError(f"Postgres target table missing: {spec['target']}")
            for spec in TABLE_SPECS:
                cur.execute(f"SELECT COUNT(*) FROM {spec['target']}")
                result["postgres_counts_before"][str(spec["source"])] = int(cur.fetchone()[0])

        any_nonempty = any(v > 0 for v in result["postgres_counts_before"].values())
        if should_refuse_nonempty_targets(
            any_nonempty=any_nonempty, replace=bool(args.replace), dry_run=bool(args.dry_run)
        ):
            sconn.close()
            result["errors"].append(
                "Target outbound sidecar tables are not empty. Use --replace to reload."
            )
            _write_json(args.json_out, result)
            print(result["errors"][-1], file=sys.stderr)
            return 1

        if args.dry_run:
            with pconn.cursor() as cur:
                for spec in TABLE_SPECS:
                    src = str(spec["source"])
                    pk = str(spec["pk"])
                    target = str(spec["target"])
                    if not exists_map[src]:
                        continue
                    cur.execute(f"SELECT COUNT(*) FROM {target} WHERE {pk} IS NULL")
                    result["validation"][f"{src}.target_null_pk"] = int(cur.fetchone()[0])
            result["ok"] = True
            result["transaction_committed"] = False
            result["elapsed_seconds"] = round(time.monotonic() - t0, 3)
            sconn.close()
            _write_json(args.json_out, result)
            print("dry-run ok: outbound sidecar prechecks passed; no writes performed.")
            return 0

        # Single apply transaction: lock → (optional delete) → load all → validate → commit.
        try:
            with pconn.cursor() as cur:
                lock_outbound_sidecar_targets(cur)
                for spec in TABLE_SPECS:
                    cur.execute(f"SELECT COUNT(*) FROM {spec['target']}")
                    result["postgres_counts_before"][str(spec["source"])] = int(
                        cur.fetchone()[0]
                    )
                any_nonempty_locked = any(
                    v > 0 for v in result["postgres_counts_before"].values()
                )
                if should_refuse_nonempty_targets(
                    any_nonempty=any_nonempty_locked,
                    replace=bool(args.replace),
                    dry_run=False,
                ):
                    raise ValueError(
                        "Target outbound sidecar tables are not empty. Use --replace to reload."
                    )
                if args.replace:
                    delete_outbound_sidecar_targets(cur)

            for spec in TABLE_SPECS:
                src = str(spec["source"])
                loaded = load_sidecar_table(
                    sconn,
                    pconn,
                    source=src,
                    target=str(spec["target"]),
                    pk=str(spec["pk"]),
                    columns=tuple(spec["columns"]),
                    timestamp_columns=frozenset(spec["timestamp_columns"]),
                    source_exists=bool(exists_map[src]),
                    t_start=t0,
                )
                result["attempted_loaded"][src] = loaded
                result["loaded"][src] = loaded

            with pconn.cursor() as cur:
                _validate_outbound_publication(
                    sconn=sconn,
                    cur=cur,
                    exists_map=exists_map,
                    sqlite_counts=sqlite_counts,
                    result=result,
                )
            pconn.commit()
            result["transaction_committed"] = True
            result["ok"] = True
        except Exception:
            pconn.rollback()
            result["transaction_committed"] = False
            result["ok"] = False
            # Do not report uncommitted row counts as published.
            result["loaded"] = {str(spec["source"]): 0 for spec in TABLE_SPECS}
            raise

        result["elapsed_seconds"] = round(time.monotonic() - t0, 3)
        sconn.close()
        _write_json(args.json_out, result)
        print("migration completed:", json.dumps(result["loaded"], indent=2))
        return 0
    except (ConversionError, ValueError, ValidationError) as exc:
        msg = sanitize_migrate_error(exc, postgres_url=pg_url)
        result["errors"].append(msg)
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
        msg = sanitize_migrate_error(exc, postgres_url=pg_url)
        result["errors"].append(msg)
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


if __name__ == "__main__":
    raise SystemExit(main())

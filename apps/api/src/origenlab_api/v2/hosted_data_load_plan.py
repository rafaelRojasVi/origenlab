"""Fase 1 — the verified logical copy of the clean room into hosted V2 (pure part).

Everything here is decidable without a database: which tables move and in what order, how a
row is rewritten on its way (two local operators become hosted Rafael), how a table is hashed so
source-after-remap and target-after-load can be compared row by row, and which facts refuse a
run. Reading facts and moving bytes live in ``apps/api/scripts/hosted_data_load_io.py``.

Spec: ~/data/origenlab-v2-migration/specs/2026-10-04-hosted-data-load-design.md
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

#: Load order. Parents before children where a FK is not cyclic; cyclic FKs are made
#: deferrable for the transaction, so the order only needs to be stable and documented.
TABLES: tuple[str, ...] = (
    "comms.mailbox",
    "evidence.source_record",
    "evidence.assertion",
    "crm.organization",
    "crm.contact_point",
    "crm.opportunity",
    "crm.quote",
    "crm.quote_revision",
    "crm.opportunity_organization",
    "crm.opportunity_evidence",
    "outbound.campaign",
    "outbound.campaign_recipient",
    "outbound.send_attempt",
    "outbound.contact_control",
    "platform.command_receipt",
    "crm.domain_event",
)

OPERATOR_COLUMNS: dict[str, tuple[str, ...]] = {
    "evidence.assertion": ("resolved_by_operator_id",),
    "crm.organization": ("confirmed_by_operator_id",),
    "crm.opportunity": ("owner_operator_id",),
    "crm.quote": ("created_by_operator_id",),
    "crm.quote_revision": ("created_by_operator_id",),
    "crm.opportunity_organization": ("confirmed_by_operator_id",),
    "crm.opportunity_evidence": ("linked_by_operator_id",),
    "platform.command_receipt": ("operator_id",),
    "crm.domain_event": ("actor_operator_id",),
}
PAYLOAD_COLUMNS: dict[str, tuple[str, ...]] = {"crm.domain_event": ("payload",)}

LOCAL_OPERATOR_IDS: tuple[str, str] = (
    "714dd8d0-9575-4769-8c12-cbc01909bd5e",  # Rafael, google_account, local
    "2f04bdd6-d9e0-4064-8ef9-57c346d9c220",  # Operador Local
)
SEQUENCE = "crm.domain_event_stream_position_seq"
SEQUENCE_COLUMN = ("crm.domain_event", "stream_position")

#: Tables that must be empty on the target before a load, beyond TABLES themselves.
EMPTY_SCHEMAS = ("crm", "evidence", "outbound", "comms", "catalog")
SINGLETONS = {"outbound.send_control": 1, "outbound.campaign_block": 1}

SESSION_SETTINGS: tuple[tuple[str, str], ...] = (
    ("TimeZone", "UTC"),
    ("DateStyle", "ISO, YMD"),
    ("IntervalStyle", "postgres"),
    ("extra_float_digits", "3"),
    ("bytea_output", "hex"),
)

ADVISORY_LOCK_KEY = int.from_bytes(
    hashlib.sha256(b"origenlab:hosted_data_load").digest()[:8], "big", signed=True
)


def _q(col: str) -> str:
    return '"' + col.replace('"', '""') + '"'


def remap_select_sql(table: str, columns: Sequence[str], hosted_operator: str) -> str:
    """The projection COPY reads from the source: same columns, same order, remap applied."""
    ops = set(OPERATOR_COLUMNS.get(table, ()))
    payloads = set(PAYLOAD_COLUMNS.get(table, ()))
    locals_sql = ", ".join(f"'{u}'::uuid" for u in LOCAL_OPERATOR_IDS)
    parts: list[str] = []
    for col in columns:
        q = _q(col)
        if col in ops:
            parts.append(f"case when {q} in ({locals_sql}) then '{hosted_operator}'::uuid else {q} end as {q}")
        elif col in payloads:
            expr = f"{q}::text"
            for u in LOCAL_OPERATOR_IDS:
                expr = f"replace({expr}, '{u}', '{hosted_operator}')"
            parts.append(f"({expr})::jsonb as {q}")
        else:
            parts.append(q)
    return f"select {', '.join(parts)} from {table}"


def row_hash_sql(select_sql: str, pk: Sequence[str]) -> str:
    order = ", ".join(f"t.{_q(c)}" for c in pk)
    return (
        "select count(*), coalesce(md5(string_agg(md5(t::text), '' order by "
        f"{order})), '') from ({select_sql}) t"
    )


def plan_sha256(plan: Mapping) -> str:
    return hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()

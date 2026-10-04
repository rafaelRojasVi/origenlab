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
from datetime import datetime, timedelta

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

#: Schemas whose tables must hold nothing but TABLES and the SINGLETONS rows: before a load (preflight and
#: again inside apply's write transaction) and before rollback deletes anything.
EMPTY_SCHEMAS = ("crm", "evidence", "outbound", "comms", "catalog", "procurement")
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

#: What "the hosted roster is unchanged" means: these identity columns, hashed by every call site
#: (plan, apply's invariants, verify, rollback). Never the PIN-throttle columns (failed_attempts,
#: lockout_count, locked_until, last_failed_at, pin_attempt_*), which any failed PIN writes and nothing
#: resets (migration 20260928194000_slice1_pin_throttle_definer.sql), nor pin_set_at/created_at/updated_at.
#: Names checked against the migrations (2026-10-04): all exist as spelled.
ROSTER_IDENTITY_COLUMNS: dict[str, tuple[str, ...]] = {
    "platform.operator": ("id", "auth_user_id", "email_norm", "display_name", "role", "status",
                          "invited_by_operator_id", "version", "sign_in_kind"),
    "platform.operator_profile": ("operator_id", "operator_sign_in_kind", "principal_id", "profile_key", "pin_hash",
                                  "status", "sort_order", "version"),
    "platform.auth_principal": ("id", "provider", "email_norm", "provider_subject", "provider_issuer", "status",
                                "version"),
}


def _q(col: str) -> str:
    return '"' + col.replace('"', '""') + '"'


def qcols(cols: Sequence[str]) -> str:
    """A quoted, comma-separated column list (the one quoting rule for every projection)."""
    return ", ".join(_q(c) for c in cols)


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


PLAN_MAX_AGE = timedelta(hours=24)
REQUIRED_HEAD = "20260930120000"
REQUIRED_LEDGER_COUNT = 41
HOLD_KEY = "septiembre18-2026-wave2"


def _fp_summary(fp: Mapping | None) -> dict | None:
    """Schemas + category hashes only; the diagnostic lines never enter the plan file."""
    if not fp:
        return None
    return {"schemas": list(fp.get("schemas", [])), "categories": dict(fp.get("categories", {}))}


def fingerprint_diff(a: Mapping | None, b: Mapping | None) -> list[str]:
    """Names of the fingerprint parts that differ (``schemas`` or a category); all of them if one side is absent."""
    a, b = _fp_summary(a), _fp_summary(b)
    if a is None or b is None:
        return ["missing"]
    out = ["schemas"] if a["schemas"] != b["schemas"] else []
    cats = sorted(set(a["categories"]) | set(b["categories"]))
    return out + [c for c in cats if a["categories"].get(c) != b["categories"].get(c)]


def build_plan(*, source: Mapping, target: Mapping, hosted_operator: str, generated_at: str) -> dict:
    tables = []
    for name in TABLES:
        s = source["tables"][name]
        tables.append({
            "name": name,
            "columns": [list(c) for c in s["columns"]],
            "pk": list(s["pk"]),
            "count": int(s["count"]),
            "hash": s["hash"],                       # hash of the REMAPPED projection
            "triggers": sorted(target["tables"][name]["triggers"]),
            "fks": sorted(fk[0] for fk in target["tables"][name]["fks"]),
        })
    seq_source = int(source["sequences"].get(SEQUENCE, 0))
    return {
        "generated_at": generated_at,
        "source": {"system_identifier": source["system_identifier"],
                   "ledger_head": source["ledger"][-1] if source["ledger"] else None, "ledger_count": len(source["ledger"])},
        "target": {"project_ref": target["project_ref"], "pooler_host": target["pooler_host"],
                   "ledger_head": target["ledger"][-1] if target["ledger"] else None,
                   "ledger_count": len(target["ledger"]) if target["ledger"] is not None else None},
        "schema_fingerprint": _fp_summary(source.get("schema_fingerprint")),
        "remap": {"from": list(LOCAL_OPERATOR_IDS), "to": hosted_operator},
        "tables": tables,
        "payload_rows_with_local_uuid": int(source["payload_rows_with_local_uuid"]),
        "sequence": {"name": SEQUENCE, "target_last_value": seq_source},
        "roster_hash": target["roster_hash"],
        "send_control_hash": target["send_control"]["hash"],
        "campaign_block_hash": target["campaign_block"]["hash"],
    }


def _parse_iso(s: str) -> datetime:
    """Parse ISO 8601 timestamp; raise ValueError if malformed or naive (no tzinfo)."""
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("naive timestamp")
    return dt


def evaluate(*, plan: Mapping, plan_sha: str, expected_sha: str, source: Mapping, target: Mapping,
             host: Mapping, now_iso: str) -> list[dict]:
    """One row per guard; a run proceeds only when every row is ok. Pure: no I/O."""
    checks: list[dict] = []

    def check(name: str, ok: bool, detail=None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    # plan
    check("plan_sha256_matches", plan_sha == expected_sha, {"actual": plan_sha, "expected": expected_sha})
    plan_is_fresh_ok = False
    try:
        now = _parse_iso(now_iso)
        generated = _parse_iso(plan["generated_at"])
        age = now - generated
        plan_is_fresh_ok = timedelta(0) <= age <= PLAN_MAX_AGE
    except (ValueError, TypeError):
        pass
    check("plan_is_fresh", plan_is_fresh_ok, "unparseable" if not plan_is_fresh_ok else None)
    check("remap_target_is_hosted_operator", plan["remap"]["to"] != "" and plan["remap"]["from"] == list(LOCAL_OPERATOR_IDS), plan["remap"])

    # ledgers
    check("source_ledger_head", source["ledger"][-1:] == [REQUIRED_HEAD] and len(source["ledger"]) == REQUIRED_LEDGER_COUNT,
          {"head": source["ledger"][-1:], "count": len(source["ledger"])})
    check("source_ledger_matches_disk", list(source["ledger"]) == list(source["migrations_on_disk"]), None)
    if target["ledger"] is None:
        check("ledgers_identical", True,
              "target ledger unreadable (platform-owned); schema_fingerprint_identical decides")
    else:
        check("ledgers_identical", list(source["ledger"]) == list(target["ledger"]),
              {"source": len(source["ledger"]), "target": len(target["ledger"])})
    sfp, tfp, pfp = (source.get("schema_fingerprint"), target.get("schema_fingerprint"), plan.get("schema_fingerprint"))
    if not (sfp and tfp and pfp):
        fp_detail = ["missing"]
    else:
        fp_detail = sorted(set(fingerprint_diff(sfp, tfp)) | set(fingerprint_diff(sfp, pfp)))
    check("schema_fingerprint_identical", not fp_detail, fp_detail)
    check("source_system_identifier_matches_plan", source["system_identifier"] == plan["source"]["system_identifier"], None)
    check("target_project_matches_plan", target["project_ref"] == plan["target"]["project_ref"], None)

    # source shape
    bad_counts = {t["name"]: (t["count"], source["tables"][t["name"]]["count"]) for t in plan["tables"]
                  if source["tables"][t["name"]]["count"] != t["count"]}
    check("source_counts_match_plan", not bad_counts, bad_counts)
    ids = sorted(op[0] for op in source["operators"])
    check("source_operators_are_the_two_known", ids == sorted(LOCAL_OPERATOR_IDS), ids)
    check("only_known_sequence", set(source["sequences"]) <= {SEQUENCE}, sorted(source["sequences"]))
    check("no_other_source_sessions", not source["other_sessions"], source["other_sessions"])

    # target shape
    for t in plan["tables"]:
        name = t["name"]
        check(f"column_contract_identical:{name}",
              [list(c) for c in target["tables"][name]["columns"]] == t["columns"]
              and [list(c) for c in source["tables"][name]["columns"]] == t["columns"], None)
        check(f"trigger_inventory_matches_plan:{name}", sorted(target["tables"][name]["triggers"]) == t["triggers"],
              sorted(target["tables"][name]["triggers"]))
        check(f"fk_inventory_matches_plan:{name}", sorted(fk[0] for fk in target["tables"][name]["fks"]) == t["fks"], None)
    non_empty = {n: c for n, c in target["all_business_counts"].items() if c != SINGLETONS.get(n, 0)}
    check("target_tables_empty", not non_empty, non_empty)
    hosted = [op for op in target["operators"] if op[0] == plan["remap"]["to"]]
    check("hosted_operator_is_active_admin", bool(hosted) and hosted[0][2] == "admin" and hosted[0][3] == "active", hosted)
    check("hosted_roster_shape", target["principals"] == 1 and target["profiles"] == 3 and len(target["operators"]) == 3
          and target["command_receipts"] == 0,
          {"principals": target["principals"], "profiles": target["profiles"], "operators": len(target["operators"])})
    check("hosted_roster_unchanged_since_plan", target["roster_hash"] == plan["roster_hash"], None)
    sc = target["send_control"]
    check("send_flags_false", sc["marketing_enabled"] is False and sc["transactional_enabled"] is False, sc)
    cb = target["campaign_block"]
    check("hold_row_active", cb["scope"] == "legacy_campaign" and cb["legacy_campaign_key"] == HOLD_KEY and cb["lifted_at"] is None, cb)
    check("no_other_target_sessions", not target["other_sessions"], target["other_sessions"])
    check("advisory_lock_free", bool(target["advisory_lock_free"]), ADVISORY_LOCK_KEY)
    check("server_is_postgres_17", int(target["server_version_num"]) // 10000 == 17, target["server_version_num"])
    check("pg_dump_can_reach_target", bool(target["pg_dump_probe_ok"]), None)

    # host
    check("target_file_mode_0600", host["target_file_regular"] and host["target_file_mode"] == 0o600, oct(host["target_file_mode"]))
    check("route_is_supavisor_session_5432", host["route"] == "supavisor-session" and host["port"] == 5432, (host["route"], host["port"]))
    check("sslmode_verify_full", host["sslmode"] == "verify-full", host["sslmode"])
    check("ca_file_exists", bool(host["ca_exists"]), None)
    check("container_is_this_worktrees", bool(host["container_label_ok"]), None)
    check("no_local_process_names_source", not host["processes"], host["processes"])
    check("no_crontab_names_source", not host["crontab"], host["crontab"])
    return checks


def refused(checks) -> list[str]:
    return [c["check"] for c in checks if not c["ok"]]

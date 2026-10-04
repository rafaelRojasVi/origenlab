from __future__ import annotations

import json
import re

from origenlab_api.v2 import hosted_data_load_plan as hp

HOSTED = "3b2cddb9-2c18-4d12-93ed-6714d3e8b57a"


def test_table_order_is_the_spec_order_and_has_16_entries():
    assert len(hp.TABLES) == 16
    assert hp.TABLES[0] == "comms.mailbox"
    assert hp.TABLES[-1] == "crm.domain_event"
    # parents before children: every operator-free parent precedes the tables that reference it
    assert hp.TABLES.index("crm.opportunity") < hp.TABLES.index("crm.quote")
    assert hp.TABLES.index("platform.command_receipt") < hp.TABLES.index("crm.domain_event")


def test_operator_columns_cover_the_nine_spec_columns():
    flat = {(t, c) for t, cols in hp.OPERATOR_COLUMNS.items() for c in cols}
    assert flat == {
        ("evidence.assertion", "resolved_by_operator_id"),
        ("crm.organization", "confirmed_by_operator_id"),
        ("crm.opportunity", "owner_operator_id"),
        ("crm.quote", "created_by_operator_id"),
        ("crm.quote_revision", "created_by_operator_id"),
        ("crm.opportunity_organization", "confirmed_by_operator_id"),
        ("crm.opportunity_evidence", "linked_by_operator_id"),
        ("platform.command_receipt", "operator_id"),
        ("crm.domain_event", "actor_operator_id"),
    }


def test_remap_select_wraps_operator_columns_and_keeps_order():
    sql = hp.remap_select_sql("crm.quote", ["id", "created_by_operator_id", "number"], HOSTED)
    assert sql.startswith("select ")
    assert "from crm.quote" in sql
    assert re.search(r'case when "created_by_operator_id" in \(.*714dd8d0.*2f04bdd6.*\) then \'%s\'::uuid' % HOSTED, sql)
    assert sql.index('"id"') < sql.index('"created_by_operator_id"') < sql.index('"number"')


def test_remap_select_replaces_only_exact_uuids_in_payload():
    sql = hp.remap_select_sql("crm.domain_event", ["id", "payload"], HOSTED)
    assert 'replace(replace("payload"::text' in sql
    assert "'714dd8d0-9575-4769-8c12-cbc01909bd5e'" in sql and "'2f04bdd6-d9e0-4064-8ef9-57c346d9c220'" in sql
    assert sql.count("::jsonb") == 1


def test_remap_select_without_remapped_columns_is_a_plain_projection():
    sql = hp.remap_select_sql("comms.mailbox", ["id", "address"], HOSTED)
    assert sql == 'select "id", "address" from comms.mailbox'


def test_row_hash_sql_orders_by_primary_key():
    sql = hp.row_hash_sql('select "id" from comms.mailbox', ["id"])
    assert "md5(string_agg(md5(t::text), '' order by t.\"id\"))" in sql
    assert sql.strip().startswith("select count(*)")


def test_plan_sha256_is_canonical():
    a = hp.plan_sha256({"b": 1, "a": [1, 2]})
    b = hp.plan_sha256({"a": [1, 2], "b": 1})
    assert a == b == hp.plan_sha256(json.loads('{"a":[1,2],"b":1}'))


LOCAL1, LOCAL2 = hp.LOCAL_OPERATOR_IDS
LEDGER = [f"2026090{i:02d}000000" for i in range(1, 41)] + ["20260930120000"]


def _table(count, hash_="h", cols=(("id", "uuid"),), pk=("id",), triggers=(), fks=()):
    return {"columns": [list(c) for c in cols], "pk": list(pk), "count": count, "hash": hash_,
            "triggers": list(triggers), "fks": [list(f) for f in fks]}


def good_source(**over):
    s = {
        "system_identifier": "7688040058322931752",
        "ledger": LEDGER, "migrations_on_disk": LEDGER,
        "tables": {t: _table(5, f"h-{t}") for t in hp.TABLES},
        "operators": [[LOCAL1, "Rafael", "admin", "active", "google_account"],
                      [LOCAL2, "Operador Local", "admin", "active", "google_account"]],
        "other_sessions": [], "sequences": {hp.SEQUENCE: 23095},
        "payload_rows_with_local_uuid": 44,
    }
    s.update(over)
    return s


def good_target(**over):
    t = {
        "project_ref": "txgsamojgvkymitcdcpo", "pooler_host": "aws-0-sa-east-1.pooler.supabase.com",
        "server_version_num": 170006, "ledger": LEDGER,
        "tables": {t: _table(0, "", triggers=(), fks=()) for t in hp.TABLES},
        "all_business_counts": {**{t: 0 for t in hp.TABLES}, "outbound.send_control": 1, "outbound.campaign_block": 1},
        "operators": [[HOSTED, "Rafael", "admin", "active", "shared_profile"],
                      ["7f57bb38-f0ac-48cc-84cb-de6d82c7aaef", "Tati", "admin", "active", "shared_profile"],
                      ["2998f4ae-aa60-4469-86ab-b1096ecda4c5", "Karla", "sales", "active", "shared_profile"]],
        "principals": 1, "profiles": 3, "command_receipts": 0,
        "send_control": {"marketing_enabled": False, "transactional_enabled": False, "hash": "sc"},
        "campaign_block": {"scope": "legacy_campaign", "legacy_campaign_key": "septiembre18-2026-wave2",
                           "lifted_at": None, "hash": "cb"},
        "roster_hash": "roster", "advisory_lock_free": True, "other_sessions": [],
        "sequences": {hp.SEQUENCE: 1}, "pg_dump_probe_ok": True,
    }
    t.update(over)
    return t


def good_host(**over):
    h = {"target_file_mode": 0o600, "target_file_regular": True, "route": "supavisor-session",
         "port": 5432, "sslmode": "verify-full", "ca_exists": True, "processes": [], "crontab": [],
         "container_label_ok": True}
    h.update(over)
    return h


def _plan(source=None, target=None):
    return hp.build_plan(source=source or good_source(), target=target or good_target(),
                         hosted_operator=HOSTED, generated_at="2026-10-05T12:00:00Z")


def _eval(plan=None, source=None, target=None, host=None, sha=None):
    plan = plan or _plan()
    real = hp.plan_sha256(plan)
    return hp.evaluate(plan=plan, plan_sha=real, expected_sha=sha or real,
                       source=source or good_source(), target=target or good_target(),
                       host=host or good_host(), now_iso="2026-10-05T13:00:00Z")


def test_build_plan_records_tables_remap_and_hashes():
    plan = _plan()
    assert [t["name"] for t in plan["tables"]] == list(hp.TABLES)
    assert plan["remap"] == {"from": list(hp.LOCAL_OPERATOR_IDS), "to": HOSTED}
    assert plan["payload_rows_with_local_uuid"] == 44
    assert plan["sequence"] == {"name": hp.SEQUENCE, "target_last_value": 23095}
    assert plan["source"]["ledger_head"] == plan["target"]["ledger_head"] == "20260930120000"


def test_good_facts_pass():
    assert hp.refused(_eval()) == []


def test_plan_sha_mismatch_refuses():
    assert "plan_sha256_matches" in hp.refused(_eval(sha="0" * 64))


def test_stale_plan_refuses():
    plan = _plan()
    plan["generated_at"] = "2026-10-01T00:00:00Z"
    assert "plan_is_fresh" in hp.refused(_eval(plan=plan))


def test_ledger_drift_refuses():
    assert "ledgers_identical" in hp.refused(_eval(target=good_target(ledger=LEDGER[:-1])))
    assert "source_ledger_matches_disk" in hp.refused(_eval(source=good_source(migrations_on_disk=LEDGER[:-1])))


def test_source_count_drift_refuses():
    src = good_source()
    src["tables"]["crm.quote"]["count"] = 66
    assert "source_counts_match_plan" in hp.refused(_eval(source=src))


def test_column_contract_drift_refuses():
    tgt = good_target()
    tgt["tables"]["crm.quote"]["columns"] = [["id", "uuid"], ["extra", "text"]]
    assert "column_contract_identical:crm.quote" in hp.refused(_eval(target=tgt))


def test_non_empty_target_refuses():
    tgt = good_target()
    tgt["tables"]["crm.quote"]["count"] = 1
    tgt["all_business_counts"]["crm.quote"] = 1
    assert "target_tables_empty" in hp.refused(_eval(target=tgt))


def test_unexpected_business_rows_elsewhere_refuse():
    tgt = good_target()
    tgt["all_business_counts"]["crm.person"] = 2
    assert "target_tables_empty" in hp.refused(_eval(target=tgt))


def test_hosted_operator_must_be_active_admin():
    tgt = good_target()
    tgt["operators"][0] = [HOSTED, "Rafael", "sales", "active", "shared_profile"]
    assert "hosted_operator_is_active_admin" in hp.refused(_eval(target=tgt))
    tgt = good_target()
    tgt["operators"] = tgt["operators"][1:]
    assert "hosted_operator_is_active_admin" in hp.refused(_eval(target=tgt))


def test_roster_shape_refuses_when_changed():
    assert "hosted_roster_shape" in hp.refused(_eval(target=good_target(profiles=2)))


def test_flags_and_hold_row_are_required():
    tgt = good_target()
    tgt["send_control"]["marketing_enabled"] = True
    assert "send_flags_false" in hp.refused(_eval(target=tgt))
    tgt = good_target()
    tgt["campaign_block"]["lifted_at"] = "2026-10-01T00:00:00Z"
    assert "hold_row_active" in hp.refused(_eval(target=tgt))
    # the migration's real hold row is scope legacy_campaign (all_campaigns requires a NULL key); a wrong scope is refused
    assert "hold_row_active" not in hp.refused(_eval(target=good_target()))
    tgt = good_target()
    tgt["campaign_block"]["scope"] = "all_campaigns"
    assert "hold_row_active" in hp.refused(_eval(target=tgt))


def test_concurrency_refusals():
    assert "no_other_source_sessions" in hp.refused(_eval(source=good_source(other_sessions=[{"pid": 1}])))
    assert "no_other_target_sessions" in hp.refused(_eval(target=good_target(other_sessions=[{"pid": 1}])))
    assert "advisory_lock_free" in hp.refused(_eval(target=good_target(advisory_lock_free=False)))
    assert "no_local_process_names_source" in hp.refused(_eval(host=good_host(processes=["psql origenlab_clean"])))
    assert "no_crontab_names_source" in hp.refused(_eval(host=good_host(crontab=["* * * * * x origenlab_clean"])))


def test_target_file_and_tls_refusals():
    assert "target_file_mode_0600" in hp.refused(_eval(host=good_host(target_file_mode=0o644)))
    assert "route_is_supavisor_session_5432" in hp.refused(_eval(host=good_host(port=6543)))
    assert "sslmode_verify_full" in hp.refused(_eval(host=good_host(sslmode="require")))
    assert "ca_file_exists" in hp.refused(_eval(host=good_host(ca_exists=False)))
    assert "server_is_postgres_17" in hp.refused(_eval(target=good_target(server_version_num=160000)))


def test_trigger_and_fk_inventory_must_match_plan():
    tgt = good_target()
    tgt["tables"]["crm.opportunity"]["triggers"] = ["opportunity_stage_guard", "new_guard"]
    assert "trigger_inventory_matches_plan:crm.opportunity" in hp.refused(_eval(target=tgt))


def test_unknown_sequence_refuses():
    src = good_source()
    src["sequences"]["crm.other_seq"] = 5
    assert "only_known_sequence" in hp.refused(_eval(source=src))


def test_source_operators_must_be_the_two_known():
    src = good_source()
    src["operators"].append(["aaaaaaaa-0000-4000-8000-000000000000", "X", "admin", "active", "google_account"])
    assert "source_operators_are_the_two_known" in hp.refused(_eval(source=src))


def test_pg_dump_probe_required():
    assert "pg_dump_can_reach_target" in hp.refused(_eval(target=good_target(pg_dump_probe_ok=False)))


def test_source_ledger_head_wrong_head():
    assert "source_ledger_head" in hp.refused(_eval(source=good_source(ledger=LEDGER[:-1] + ["20260930110000"])))


def test_source_ledger_head_wrong_count():
    assert "source_ledger_head" in hp.refused(_eval(source=good_source(ledger=LEDGER[:-1])))


def test_source_system_identifier_matches_plan():
    assert "source_system_identifier_matches_plan" in hp.refused(_eval(source=good_source(system_identifier="9999999999999999999")))


def test_target_project_matches_plan():
    assert "target_project_matches_plan" in hp.refused(_eval(target=good_target(project_ref="different-ref")))


def test_hosted_roster_unchanged_since_plan():
    assert "hosted_roster_unchanged_since_plan" in hp.refused(_eval(target=good_target(roster_hash="different-hash")))


def test_fk_inventory_matches_plan_first_table():
    tgt = good_target()
    tgt["tables"]["crm.opportunity"]["fks"] = [["new_fk"]]
    assert "fk_inventory_matches_plan:crm.opportunity" in hp.refused(_eval(target=tgt))


def test_container_is_this_worktrees():
    assert "container_is_this_worktrees" in hp.refused(_eval(host=good_host(container_label_ok=False)))


def test_remap_target_is_hosted_operator_from_altered():
    plan = _plan()
    plan["remap"]["from"] = ["wrong-uuid"]
    assert "remap_target_is_hosted_operator" in hp.refused(_eval(plan=plan))


def test_target_file_mode_0600_via_regular_false():
    assert "target_file_mode_0600" in hp.refused(_eval(host=good_host(target_file_regular=False)))


def test_hosted_roster_shape_principals():
    assert "hosted_roster_shape" in hp.refused(_eval(target=good_target(principals=2)))


def test_hosted_roster_shape_command_receipts():
    assert "hosted_roster_shape" in hp.refused(_eval(target=good_target(command_receipts=1)))


def test_hosted_roster_shape_operators():
    assert "hosted_roster_shape" in hp.refused(_eval(target=good_target(operators=good_target()["operators"] + [["x", "y", "z", "w", "v"]])))


def test_route_is_supavisor_session_5432_via_route():
    assert "route_is_supavisor_session_5432" in hp.refused(_eval(host=good_host(route="direct")))


def test_empty_target_ledger():
    assert "ledgers_identical" in hp.refused(_eval(target=good_target(ledger=[])))


def test_empty_source_ledger():
    assert "source_ledger_head" in hp.refused(_eval(source=good_source(ledger=[])))


def test_empty_ledger_in_build_plan():
    plan = hp.build_plan(source=good_source(ledger=[]), target=good_target(ledger=[]), hosted_operator=HOSTED, generated_at="2026-10-05T12:00:00Z")
    assert plan["source"]["ledger_head"] is None
    assert plan["target"]["ledger_head"] is None


def test_future_generated_at():
    plan = _plan()
    plan["generated_at"] = "2026-10-05T14:00:00Z"
    assert "plan_is_fresh" in hp.refused(_eval(plan=plan))


def test_naive_generated_at():
    plan = _plan()
    plan["generated_at"] = "2026-10-05T12:00:00"
    checks = _eval(plan=plan)
    assert "plan_is_fresh" in hp.refused(checks)
    assert any(c["detail"] == "unparseable" for c in checks if c["check"] == "plan_is_fresh")


def test_garbage_generated_at():
    plan = _plan()
    plan["generated_at"] = "garbage"
    checks = _eval(plan=plan)
    assert "plan_is_fresh" in hp.refused(checks)
    assert any(c["detail"] == "unparseable" for c in checks if c["check"] == "plan_is_fresh")


def test_pg18_refused():
    assert "server_is_postgres_17" in hp.refused(_eval(target=good_target(server_version_num=180000)))

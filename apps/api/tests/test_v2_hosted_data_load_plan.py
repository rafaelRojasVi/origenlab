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

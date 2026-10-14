"""The duplicate-maker cleanup: merge a maker loaded under a second name into the first. Every name is invented."""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import psycopg
import pytest

from origenlab_api.v2.catalog import importing
from origenlab_api.v2.catalog.reads import V2CatalogReads
from origenlab_api.v2.identity import OperatorIdentity
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "catalog"
sys.path.insert(0, str(_SCRIPTS))
import _common  # noqa: E402
import merge_makers as mm  # noqa: E402


def test_plan_items_one_per_merge() -> None:
    [item] = mm.merge_items({"merges": [{"keep": "Marca", "drop": "Marca GmbH", "rename_to": "Marca Uno"}]})
    assert item["action"] == "merge_maker" and item["key"] == "merge:marca gmbh->marca"
    assert item["fields"] == {"keep": "Marca", "drop": "Marca GmbH", "rename_to": "Marca Uno"}


@pytest.mark.parametrize("bad", [{"keep": "A", "drop": "a"}, {"keep": "A"}, {"keep": "", "drop": "B"},
                                 {"keep": "A", "drop": "B", "extra": 1}],
                         ids=["same", "no-drop", "blank", "extra-field"])
def test_plan_refuses_a_bad_merge(bad) -> None:
    with pytest.raises(ValueError):
        mm.merge_items({"merges": [bad]})


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


def _owner(dsn: str, sql: str, params: tuple = ()):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


@pytest.fixture(scope="module")
def operator(disposable_database) -> OperatorIdentity:
    email = f"admin-{uuid.uuid4().hex[:8]}@example.test"
    op_id = _owner(disposable_database,
                   "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                   "values (gen_random_uuid(), %s, 'Admin Prueba', 'admin', 'active') returning id::text", (email,))[0][0]
    return OperatorIdentity(operator_id=op_id, email_norm=email, display_name="Admin Prueba", role="admin",
                            status="active")


def _argv(cmd, dsn, plan, sha, out, operator=None, *extra):
    argv = [cmd, "--target-dsn", runtime_dsn(dsn), "--plan", str(plan), "--plan-sha256", sha, "--out", str(out)]
    if cmd in ("apply", "rollback"):
        argv += ["--admin-dsn", dsn]
    if operator is not None:
        argv += ["--operator-email", operator.email_norm]
    return argv + list(extra)


def _org(dsn, name, kind="manufacturer"):
    return _owner(dsn, "insert into crm.organization (kind, name, confirmation) values (%s, %s, 'machine_proposed') "
                       "returning id::text", (kind, name))[0][0]


def _product(dsn, maker, model):
    return _owner(dsn, "insert into catalog.product (manufacturer_organization_id, model_number, name) values (%s, %s, %s) "
                       "returning id::text", (maker, model, model))[0][0]


def _obs(dsn, product, supplier, price):
    _owner(dsn, "insert into catalog.supplier_product (supplier_organization_id, product_id, as_of, price, currency, "
                "price_kind, provenance_note) values (%s, %s, now(), %s, 'EUR', 'list', 'test')", (supplier, product, price))


@needs_db
def test_merge_apply_verify_and_reads_follow_it(disposable_database, operator, tmp_path) -> None:
    dsn = disposable_database
    keep, drop = _org(dsn, "Marca Ficticia"), _org(dsn, "Marca Ficticia GmbH")
    twin_keep, twin_drop, only_drop = _product(dsn, keep, "SYN-1"), _product(dsn, drop, "SYN 1"), _product(dsn, drop, "SYN-9")
    _obs(dsn, twin_keep, keep, 10)
    _obs(dsn, twin_drop, drop, 12)
    _obs(dsn, only_drop, drop, 30)

    src = tmp_path / "merges.json"
    src.write_text(json.dumps({"merges": [{"keep": "Marca Ficticia", "drop": "Marca Ficticia GmbH",
                                           "rename_to": "Marca"}]}), encoding="utf-8")
    assert mm.main(["plan", "--merges", str(src), "--out", str(tmp_path / "p")]) == 0
    plan = tmp_path / "p" / "plan.json"
    sha = importing.sha256_bytes(plan.read_bytes())

    assert mm.main(_argv("apply", dsn, plan, sha, tmp_path / "a1", operator)) == 0
    assert json.loads((tmp_path / "a1" / "apply-report.json").read_text())["inserted"] == 1
    assert mm.main(_argv("apply", dsn, plan, sha, tmp_path / "a2", operator)) == 0
    assert json.loads((tmp_path / "a2" / "apply-report.json").read_text())["already_present"] == 1
    assert mm.main(_argv("verify", dsn, plan, sha, tmp_path / "v")) == 0

    assert _owner(dsn, "select merged_into_organization_id::text from crm.organization where id = %s", (drop,)) == [(keep,)]
    assert _owner(dsn, "select name, confirmation from crm.organization where id = %s", (keep,)) == [("Marca", "confirmed")]
    assert _owner(dsn, "select active, merged_into_product_id::text from catalog.product where id = %s",
                  (twin_drop,)) == [(False, twin_keep)]
    assert _owner(dsn, "select manufacturer_organization_id::text, active from catalog.product where id = %s",
                  (only_drop,)) == [(keep, True)]
    events = _owner(dsn, "select event_type, count(*) from crm.domain_event where event_type in "
                         "('product.merged', 'organization.merged', 'organization.updated', 'organization.confirmed') "
                         "and aggregate_id::text = any(%s) group by 1 order by 1", ([keep, drop, twin_drop, only_drop],))
    assert events == [("organization.confirmed", 1), ("organization.merged", 1), ("organization.updated", 1),
                      ("product.merged", 1)]

    reads = V2CatalogReads(psycopg.connect, runtime_dsn(dsn))
    history = reads.product_detail(uuid.UUID(twin_keep))["cost_history"]
    assert sorted(str(h["price"]) for h in history) == ["10.000000", "12.000000"]
    assert {h["supplier"]["display_name"] for h in history} == {"Marca"}
    # A price list recorded on the duplicate maker still reaches the product, under the kept name.
    _owner(dsn, "insert into catalog.product_document (supplier_organization_id, kind, title, drive_file_id, mime_type, "
                "source) values (%s, 'price_list', 'Lista.xlsx', 'SyntheticList00009', 'application/pdf', 'operator')",
           (drop,))
    lists = reads.product_detail(uuid.UUID(twin_keep))["documents"]["price_lists"]
    assert [(d["title"], d["supplier"]["display_name"]) for d in lists] == [("Lista.xlsx", "Marca")]
    names = {s["display_name"] for s in reads.suppliers()["items"]}
    assert "Marca" in names and "Marca Ficticia GmbH" not in names
    index = reads.catalog_index()
    rows = {r[0]: dict(zip(index["fields"], r, strict=True)) for r in index["items"]}
    assert twin_drop not in rows and rows[twin_keep]["supplier_ids"] == [keep]
    assert rows[only_drop]["supplier_ids"] == [keep] and rows[only_drop]["manufacturer"] == "Marca"


@needs_db
def test_an_unknown_maker_is_unmatched_and_nothing_changes(disposable_database, operator, tmp_path) -> None:
    dsn = disposable_database
    src = tmp_path / "m.json"
    src.write_text(json.dumps({"merges": [{"keep": "No Existe", "drop": "Tampoco Existe"}]}), encoding="utf-8")
    assert mm.main(["plan", "--merges", str(src), "--out", str(tmp_path / "p")]) == 0
    plan = tmp_path / "p" / "plan.json"
    sha = importing.sha256_bytes(plan.read_bytes())
    assert mm.main(_argv("apply", dsn, plan, sha, tmp_path / "a", operator)) == 0
    report = json.loads((tmp_path / "a" / "apply-report.json").read_text())
    assert report["inserted"] == 0 and report["by_action"]["merge_maker"] == {"unmatched": 1}


@needs_db
def test_rollback_is_refused_and_points_at_the_backup(disposable_database, tmp_path, capsys) -> None:
    src = tmp_path / "m.json"
    src.write_text(json.dumps({"merges": [{"keep": "A Uno", "drop": "A Dos"}]}), encoding="utf-8")
    assert mm.main(["plan", "--merges", str(src), "--out", str(tmp_path / "p")]) == 0
    plan = tmp_path / "p" / "plan.json"
    sha = importing.sha256_bytes(plan.read_bytes())
    code = mm.main(_argv("rollback", disposable_database, plan, sha, tmp_path / "r", None, "--confirm-delete-loaded-rows"))
    assert code == _common.EXIT_REFUSED
    assert "backup" in capsys.readouterr().err

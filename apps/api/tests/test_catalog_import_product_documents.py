"""The Drive documents importer: data sheets and price lists from a private manifest. Every name and id is invented."""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import psycopg
import pytest

from origenlab_api.v2.catalog import importing
from origenlab_api.v2.catalog.keys import LabdeliveryRefused
from origenlab_api.v2.identity import OperatorIdentity
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "catalog"
sys.path.insert(0, str(_SCRIPTS))
import _common  # noqa: E402
import import_product_documents as ipd  # noqa: E402

SHEET = {"kind": "datasheet", "title": "Sonda SYN.pdf", "drive_file_id": "SyntheticSheet0001",
         "mime_type": "application/pdf", "model_codes": ["syn-1", "SYN 2"]}
LIST = {"kind": "price_list", "title": "Lista 2026.xlsx", "drive_file_id": "SyntheticList00001",
        "mime_type": "application/pdf", "as_of": "2026-01-01", "supplier_name": "Proveedor Ficticio"}


# ------------------------------------------------------------------ plan (no database)

def test_a_data_sheet_becomes_one_item_per_model_code_by_model_key() -> None:
    items = ipd.document_items({"documents": [SHEET]})
    assert [i["fields"]["model_key"] for i in items] == ["SYN1", "SYN2"]
    assert {i["action"] for i in items} == {"add_product_document"}
    assert items[0]["key"] == "doc:datasheet:SyntheticSheet0001:SYN1"


def test_a_price_list_names_its_supplier() -> None:
    [item] = ipd.document_items({"documents": [LIST]})
    assert item["fields"]["supplier_name"] == "Proveedor Ficticio" and item["fields"]["as_of"] == "2026-01-01"


@pytest.mark.parametrize("bad", [
    {**SHEET, "kind": "brochure"},
    {**SHEET, "drive_file_id": "bad id!"},
    {**SHEET, "model_codes": []},
    {**SHEET, "title": ""},
    {**SHEET, "title": "CN01234-Cliente-UP200St.pdf"},
    {**SHEET, "title": "01187-26 r1 — algo.pdf"},
    {**LIST, "supplier_name": " "},
], ids=["kind", "drive-id", "no-codes", "no-title", "quote-pdf", "filed-quote-pdf", "no-supplier"])
def test_the_plan_refuses_a_bad_entry(bad) -> None:
    with pytest.raises(ValueError):
        ipd.document_items({"documents": [bad]})


def test_labdelivery_is_refused() -> None:
    with pytest.raises(LabdeliveryRefused):
        ipd.document_items({"documents": [{**SHEET, "title": "Labdelivery ficha.pdf"}]})


def test_plan_writes_no_path(tmp_path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"documents": [SHEET, LIST]}), encoding="utf-8")
    assert ipd.main(["plan", "--manifest", str(manifest), "--out", str(tmp_path / "o")]) == 0
    text = (tmp_path / "o" / "plan.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in text and json.loads(text)["counts"]["add_product_document"] == 3


# ------------------------------------------------------------------ apply / verify / rollback

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
    email = f"sales-{uuid.uuid4().hex[:8]}@example.test"
    op_id = _owner(disposable_database,
                   "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                   "values (gen_random_uuid(), %s, 'Vendedora Prueba', 'sales', 'active') returning id::text",
                   (email,))[0][0]
    return OperatorIdentity(operator_id=op_id, email_norm=email, display_name="Vendedora Prueba", role="sales",
                            status="active")


def _argv(cmd, dsn, plan, sha, out, operator=None, *extra):
    argv = [cmd, "--target-dsn", runtime_dsn(dsn), "--plan", str(plan), "--plan-sha256", sha, "--out", str(out)]
    if cmd in ("apply", "rollback"):
        argv += ["--admin-dsn", dsn]
    if operator is not None:
        argv += ["--operator-email", operator.email_norm]
    return argv + list(extra)


def _report(out: Path, name: str) -> dict:
    return json.loads((out / name).read_text(encoding="utf-8"))


@needs_db
def test_apply_verify_rollback(disposable_database, operator, tmp_path) -> None:
    dsn = disposable_database
    [(maker,)] = _owner(dsn, "insert into crm.organization (kind, name, confirmation) values "
                             "('manufacturer', 'Fabricante Ficticio', 'machine_proposed') returning id::text")
    [(supplier,)] = _owner(dsn, "insert into crm.organization (kind, name, confirmation) values "
                                "('supplier', 'Proveedor Ficticio', 'machine_proposed') returning id::text")
    [(prod,)] = _owner(dsn, "insert into catalog.product (manufacturer_organization_id, model_number, name) "
                            "values (%s, 'SYN-1', 'Sonda') returning id::text", (maker,))
    _owner(dsn, "insert into catalog.supplier_product (supplier_organization_id, product_id, as_of, price, currency, "
                "price_kind, provenance_note) values (%s, %s, now(), 1, 'EUR', 'list', 'test')", (supplier, prod))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"documents": [{**SHEET, "model_codes": ["SYN-1", "NOPE-9"]},
                                                  {**LIST, "supplier_name": "proveedor ficticio"}]}),
                        encoding="utf-8")
    assert ipd.main(["plan", "--manifest", str(manifest), "--out", str(tmp_path / "p")]) == 0
    plan = tmp_path / "p" / "plan.json"
    sha = importing.sha256_bytes(plan.read_bytes())

    assert ipd.main(_argv("apply", dsn, plan, sha, tmp_path / "a1", operator)) == 0
    r1 = _report(tmp_path / "a1", "apply-report.json")
    assert r1["inserted"] == 2 and r1["by_action"]["add_product_document"].get("unmatched") == 1
    assert ipd.main(_argv("apply", dsn, plan, sha, tmp_path / "a2", operator)) == 0
    r2 = _report(tmp_path / "a2", "apply-report.json")
    assert r2["inserted"] == 0 and r2["already_present"] == 2

    assert ipd.main(_argv("verify", dsn, plan, sha, tmp_path / "v")) == 0
    assert _report(tmp_path / "v", "verify-report.json")["counts"] == {"present": 2, "unmatched": 1}

    rows = _owner(dsn, "select kind, coalesce(product_id, supplier_organization_id)::text, source, "
                       "created_by_operator_id::text from catalog.product_document order by kind")
    assert rows == [("datasheet", prod, "drive_manifest", operator.operator_id),
                    ("price_list", supplier, "drive_manifest", operator.operator_id)]
    events = _owner(dsn, "select event_type, count(*) from crm.domain_event where event_type in "
                         "('product.document_recorded', 'organization.price_list_recorded') group by 1 order by 1")
    assert events == [("organization.price_list_recorded", 1), ("product.document_recorded", 1)]

    assert ipd.main(_argv("rollback", dsn, plan, sha, tmp_path / "r", None, "--confirm-delete-loaded-rows")) == 0
    assert _owner(dsn, "select count(*) from catalog.product_document") == [(0,)]
    assert _owner(dsn, "select count(*) from crm.domain_event where event_type in "
                       "('product.document_recorded', 'organization.price_list_recorded')") == [(0,)]


def test_exit_code_for_a_refused_manifest(tmp_path) -> None:
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"documents": [{**SHEET, "kind": "x"}]}), encoding="utf-8")
    assert ipd.main(["plan", "--manifest", str(manifest), "--out", str(tmp_path / "o")]) == _common.EXIT_REFUSED

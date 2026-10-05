"""Supplier-document and costing-sheet importer. Every name and number is invented (ACME Labs GmbH, ACME-1 @ 1000.00 EUR).

The JSON inputs are generated here in the shape the private extraction produces; no real extraction
output, supplier, price, discount, margin, quote number or client appears in this repository.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from origenlab_api.v2.catalog import importing
from origenlab_api.v2.catalog.keys import LabdeliveryRefused
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "catalog"
sys.path.insert(0, str(_SCRIPTS))
import _common  # noqa: E402
import import_supplier_documents as isd  # noqa: E402

SUPPLIER = "ACME Labs GmbH"
US = "OrigenLab"


def _line(model="ACME-1", unit=1000.0, net=None, disc=None, qty=2, total=None, **extra) -> dict:
    line = {"model": model, "description": f"{model} instrument", "qty": qty, "unit_price": unit,
            "discount_pct": disc, "net_unit_price": net, "line_total": total if total is not None else qty * (net or unit)}
    return {**line, **extra}


def _doc(doc_type="supplier_quote", issuer=SUPPLIER, recipient=US, number="ACME-Q-1", date="2026-03-02",
         currency="EUR", lines=None, **extra) -> dict:
    doc = {"doc_type": doc_type, "issuer": issuer, "recipient": recipient, "doc_number": number, "date": date,
           "currency": currency, "incoterm": "EXW", "lines": lines if lines is not None else [_line()],
           "packing": None, "freight": None, "total": None}
    return {**doc, **extra}


def _write(path: Path, data) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _plan(tmp_path: Path, docs: list[dict], sheets=None, **kw) -> importing.Plan:
    paths = [(("document", _write(tmp_path / f"doc{n}.json", d), None)) for n, d in enumerate(docs)]
    if sheets is not None:
        paths.append(("costing_sheet", _write(tmp_path / "sheets.json", sheets), kw.pop("sheet_as_of", None)))
    return isd.build_supplier_document_plan(paths, **kw)


def _obs(plan: importing.Plan) -> list[dict]:
    return [i for i in plan["items"] if i["action"] == "add_observation"]


def _sheet(lines=None, quote="Q-CLIENT-9", **extra) -> dict:
    return {"quote_number": quote, "fx_eur": 1000, "fx_usd": 900, "fx_date": "2026-04-01",
            "source": "SRC-PRIVATE-7", "expenses": {"flete": 1}, "totals": {"total": 1}, "iva": 0.19,
            "lines": lines if lines is not None else [
                {"brand": SUPPLIER, "model": "ACME-1", "supplier_unit_cost": 1000.0, "cost_currency": "EUR"}], **extra}


# ------------------------------------------------------------------ mapping

def test_document_kinds_are_mapped(tmp_path) -> None:
    plan = _plan(tmp_path, [
        _doc("supplier_quote", number="A-1", lines=[_line("ACME-1")]),
        _doc("supplier_proforma", number="A-2", lines=[_line("ACME-2")]),
        _doc("purchase_order", issuer=US, recipient=SUPPLIER, number="PO-1", lines=[_line("ACME-3")])])
    kinds = {i["fields"]["price_kind"] for i in _obs(plan)}
    assert kinds == {"supplier_offer", "order_confirmation", "purchase_order"}
    assert all(i["fields"]["currency"] == "EUR" and i["fields"]["as_of"] == "2026-03-02" for i in _obs(plan))
    assert {f["source_document"] for f in (i["fields"] for i in _obs(plan))} == {"A-1", "A-2", "PO-1"}
    orgs = [i["fields"] for i in plan["items"] if i["action"] == "create_org"]
    assert [o["name"] for o in orgs] == [SUPPLIER]  # OrigenLab itself is never a supplier row


def test_net_unit_price_preferred_and_discount_kept(tmp_path) -> None:
    plan = _plan(tmp_path, [_doc(lines=[_line(unit=1000.0, net=850.0, disc=15)])])
    [f] = [i["fields"] for i in _obs(plan)]
    assert Decimal(f["price"]) == Decimal("850") and Decimal(f["list_price"]) == Decimal("1000")
    assert Decimal(f["discount_pct"]) == Decimal("0.15")
    plain = _plan(tmp_path, [_doc(lines=[_line(unit=1000.0)])])
    [g] = [i["fields"] for i in _obs(plain)]
    assert Decimal(g["price"]) == Decimal("1000") and g["list_price"] is None and g["discount_pct"] is None


def test_sales_invoice_and_paperwork_are_skipped_and_counted(tmp_path) -> None:
    plan = _plan(tmp_path, [_doc("sales_invoice", issuer=US, recipient="Cliente Ficticio"),
                            _doc("company_paperwork", lines=[]), _doc()])
    assert plan["counts"]["skipped_sales_invoice"] == 1 and plan["counts"]["skipped_company_paperwork"] == 1
    assert len(_obs(plan)) == 1
    reasons = sorted(s["reason"] for s in plan["skipped"])
    assert reasons == ["company_paperwork", "sales_invoice"]
    text = importing.plan_bytes(plan).decode()
    assert str(tmp_path) not in text and "Cliente Ficticio" not in text
    assert all(set(s) == {"path_sha256", "reason"} and len(s["path_sha256"]) == 64 for s in plan["skipped"])


def test_purchase_order_rule(tmp_path) -> None:
    import_po = _doc("purchase_order", issuer=US, recipient=SUPPLIER, number="PO-1")
    clp_foreign = _doc("purchase_order", issuer=US, recipient="Proveedor Chileno Ficticio", currency="CLP", number="PO-2")
    clp_domestic = _doc("purchase_order", issuer=US, recipient="Proveedor Local Ficticio", currency="CLP", number="PO-3")
    not_ours = _doc("purchase_order", issuer="Otra Empresa Ficticia", recipient=SUPPLIER, number="PO-4")
    plan = _plan(tmp_path, [import_po, clp_foreign, clp_domestic, not_ours],
                 domestic_suppliers=["Proveedor Local Ficticio"])
    assert {i["fields"]["source_document"] for i in _obs(plan)} == {"PO-1", "PO-3"}
    assert plan["counts"]["skipped_purchase_order_rule"] == 2


def test_packing_pct_is_packing_over_subtotal(tmp_path) -> None:
    doc = _doc(issuer="Ortoalresa", lines=[_line(unit=1000.0, qty=2, total=2000.0), _line("ACME-2", unit=500.0, qty=2, total=1000.0)],
               packing=45.0)
    plan = _plan(tmp_path, [doc])
    [terms] = [i for i in plan["items"] if i["action"] == "set_terms"]
    assert Decimal(terms["fields"]["packing_pct"]) == Decimal("0.015") and terms["fields"]["currency"] == "EUR"
    assert terms["fields"]["route"] == "import_courier" and terms["fields"]["map_enforced"] is False
    assert importing.decimal_text(Decimal(terms["fields"]["packing_pct"])).count(".") == 1


def test_packing_pct_rounds_to_four_places(tmp_path) -> None:
    doc = _doc(issuer="Ortoalresa", lines=[_line(unit=300.0, qty=1, total=300.0)], packing=10.0)
    [terms] = [i for i in _plan(tmp_path, [doc])["items"] if i["action"] == "set_terms"]
    assert Decimal(terms["fields"]["packing_pct"]) == Decimal("0.0333")


def test_no_terms_for_other_suppliers_or_without_packing(tmp_path) -> None:
    plan = _plan(tmp_path, [_doc(packing=45.0), _doc(issuer="Ortoalresa", number="B", packing=None)])
    assert not [i for i in plan["items"] if i["action"] == "set_terms"]


def test_costing_sheet_lines(tmp_path) -> None:
    plan = _plan(tmp_path, [], sheets=_sheet())
    [f] = [i["fields"] for i in _obs(plan)]
    assert f["price_kind"] == "costing_sheet" and f["source_document"] == "costing sheet"
    assert Decimal(f["price"]) == Decimal("1000") and f["currency"] == "EUR" and f["as_of"] == "2026-04-01"
    text = importing.plan_bytes(plan).decode()
    assert "Q-CLIENT-9" not in text and "CLIENT" not in text.upper().replace("CLIENTE", "")


def test_costing_sheet_date_from_flag_and_required(tmp_path) -> None:
    sheet = _sheet()
    del sheet["fx_date"]
    with pytest.raises(ValueError, match=r"sheets\.json: fx_date"):
        _plan(tmp_path, [], sheets=sheet)
    plan = _plan(tmp_path, [], sheets=sheet, sheet_as_of="2026-05-05")
    assert _obs(plan)[0]["fields"]["as_of"] == "2026-05-05"
    sheet["fx_date"] = "05/05/2026"  # invalid: the flag is the fallback, else refused
    assert _obs(_plan(tmp_path, [], sheets=sheet, sheet_as_of="2026-05-06"))[0]["fields"]["as_of"] == "2026-05-06"
    with pytest.raises(ValueError, match=r"sheets\.json: fx_date"):
        _plan(tmp_path, [], sheets=sheet)
    sheet["fx_date"] = "2026-04-01"
    assert _obs(_plan(tmp_path, [], sheets=[sheet], sheet_as_of="2026-05-06"))[0]["fields"]["as_of"] == "2026-04-01"


def test_costing_sheet_private_keys_never_reach_the_plan(tmp_path) -> None:
    plan = _plan(tmp_path, [], sheets=[_sheet(quote="Q-SECRET-42")])
    text = importing.plan_bytes(plan).decode()
    for private in ("Q-SECRET-42", "SRC-PRIVATE-7", "flete", "quote_number", "expenses", "totals"):
        assert private not in text


def test_costing_sheet_as_list_and_line_without_cost(tmp_path) -> None:
    lines = [{"brand": SUPPLIER, "model": "ACME-1", "supplier_unit_cost": 10.5, "cost_currency": "USD"},
             {"brand": SUPPLIER, "model": "ACME-2", "supplier_unit_cost": None, "cost_currency": "USD"}]
    plan = _plan(tmp_path, [], sheets=[_sheet(lines, "Q-1"), _sheet(lines, "Q-2")])
    assert len(_obs(plan)) == 1 and plan["counts"]["lines_without_price"] == 2
    assert plan["counts"]["duplicates_merged"] >= 1


# ------------------------------------------------------------------ refusals

def test_labdelivery_issuer_and_recipient_refused(tmp_path) -> None:
    with pytest.raises(LabdeliveryRefused):
        _plan(tmp_path, [_doc(issuer="Labdelivery SpA")])
    with pytest.raises(LabdeliveryRefused):
        _plan(tmp_path, [_doc(recipient="Lab Delivery Chile")])


def test_labdelivery_anywhere_in_the_input_or_path_refused(tmp_path) -> None:
    with pytest.raises(LabdeliveryRefused):
        _plan(tmp_path, [_doc(lines=[_line(description="Labdelivery stock")])])
    path = _write(tmp_path / "labdelivery-doc.json", _doc())
    with pytest.raises(LabdeliveryRefused):
        isd.build_supplier_document_plan([("document", path, None)])
    with pytest.raises(LabdeliveryRefused):
        _plan(tmp_path, [], sheets=_sheet([{"brand": "Labdelivery", "model": "X", "supplier_unit_cost": 1,
                                            "cost_currency": "EUR"}]))


@pytest.mark.parametrize("mutate, field", [
    (lambda d: d.pop("issuer"), "issuer"),
    (lambda d: d.pop("date"), "date"),
    (lambda d: d.update(date="02/03/2026"), "date"),
    (lambda d: d.update(currency="euro"), "currency"),
    (lambda d: d.update(doc_type="mystery"), "doc_type"),
    (lambda d: d.update(lines="nope"), "lines"),
    (lambda d: d["lines"][0].update(unit_price="abc"), "unit_price"),
    (lambda d: d["lines"][0].update(unit_price=-1), "unit_price"),
    (lambda d: d["lines"][0].pop("model"), "model"),
    (lambda d: d["lines"][0].update(discount_pct=150), "discount_pct"),
    (lambda d: d.update(packing="x"), "packing"),
])
def test_malformed_document_refused_with_basename_and_field(tmp_path, mutate, field) -> None:
    good, bad = _doc(number="G"), _doc(number="B")
    mutate(bad)
    path = _write(tmp_path / "broken-input.json", bad)
    with pytest.raises(ValueError) as exc:
        isd.build_supplier_document_plan([("document", _write(tmp_path / "ok.json", good), None),
                                          ("document", path, None)])
    assert "broken-input.json" in str(exc.value) and field in str(exc.value) and str(tmp_path) not in str(exc.value)


def test_malformed_costing_sheet_refused(tmp_path) -> None:
    for bad in ({"brand": SUPPLIER, "model": "X", "supplier_unit_cost": "abc", "cost_currency": "EUR"},
                {"brand": SUPPLIER, "model": "X", "cost_currency": "EUR"},
                {"model": "X", "supplier_unit_cost": 1, "cost_currency": "EUR"},
                {"brand": SUPPLIER, "model": "X", "supplier_unit_cost": 1, "cost_currency": "eur"}):
        with pytest.raises(ValueError, match="sheets.json"):
            _plan(tmp_path, [], sheets=_sheet([bad]))


def test_unreadable_json_refused(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="bad.json"):
        isd.build_supplier_document_plan([("document", path, None)])


def test_unknown_input_kind_refused(tmp_path) -> None:
    with pytest.raises(ValueError, match="kind"):
        isd.build_supplier_document_plan([("other", _write(tmp_path / "a.json", _doc()), None)])


# ------------------------------------------------------------------ plan shape

def test_plan_never_contains_paths_and_inputs_sorted_by_hash(tmp_path) -> None:
    plan = _plan(tmp_path, [_doc(number="A", lines=[_line("ACME-1")]), _doc(number="B", lines=[_line("ACME-2")])],
                 sheets=_sheet())
    text = importing.plan_bytes(plan).decode()
    assert str(tmp_path) not in text and "doc0.json" not in text
    assert all(set(i) == {"path_sha256", "file_sha256", "kind"} for i in plan["inputs"])
    hashes = [i["file_sha256"] for i in plan["inputs"]]
    assert hashes == sorted(hashes) and plan["importer"] == "supplier_documents"


def test_plan_is_deterministic_and_money_is_text(tmp_path) -> None:
    docs = [_doc(number="A"), _doc(number="B", lines=[_line("ACME-2")])]
    a, b = _plan(tmp_path, docs), _plan(tmp_path, list(reversed(docs)))
    assert a["items"] == b["items"] and a["counts"] == b["counts"]
    assert importing.plan_bytes(a) == importing.plan_bytes(_plan(tmp_path, docs))
    assert isinstance(_obs(_plan(tmp_path, docs))[0]["fields"]["price"], str)


def test_conflicting_documents_are_recorded(tmp_path) -> None:
    plan = _plan(tmp_path, [_doc(number="A", lines=[_line(unit=1000.0)]), _doc(number="B", lines=[_line(unit=1100.0)])])
    assert plan["counts"]["conflicting_duplicates"] == 1 and len(_obs(plan)) == 1


def test_cli_plan_writes_plan_and_never_prints_values(tmp_path, capsys) -> None:
    d = tmp_path / "in"
    d.mkdir()
    _write(d / "a.json", _doc())
    sheets = _write(tmp_path / "s.json", _sheet())
    out = tmp_path / "out"
    assert isd.main(["plan", "--documents", str(d), "--costing-sheets", str(sheets), "--out", str(out)]) == 0
    assert (out / "plan.json").exists()
    shown = capsys.readouterr()
    assert "1000" not in shown.out + shown.err and str(tmp_path) not in shown.out + shown.err


def test_cli_refuses_malformed_with_basename(tmp_path, capsys) -> None:
    bad = _write(tmp_path / "broken.json", _doc(date="never"))
    rc = isd.main(["plan", "--documents", str(bad), "--out", str(tmp_path / "o")])
    assert rc == _common.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "broken.json" in err and "date" in err and not (tmp_path / "o" / "plan.json").exists()


def test_cli_needs_an_input(tmp_path) -> None:
    assert isd.main(["plan", "--out", str(tmp_path / "o")]) == _common.EXIT_REFUSED


# ------------------------------------------------------------------ database

@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


def _db_plan(tmp_path: Path, name: str, docs: list[dict]) -> tuple[Path, str]:
    d = tmp_path / f"in-{name}"
    d.mkdir()
    for n, doc in enumerate(docs):
        _write(d / f"d{n}.json", doc)
    out = tmp_path / name
    assert isd.main(["plan", "--documents", str(d), "--out", str(out)]) == 0
    return out / "plan.json", importing.sha256_bytes((out / "plan.json").read_bytes())


@needs_db
def test_idempotent_apply_verify_and_terms(disposable_database, tmp_path) -> None:
    import uuid

    import psycopg

    dsn = disposable_database

    def owner(sql, params=()):
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("set role origenlab_owner")
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else None

    email = f"sales-{uuid.uuid4().hex[:8]}@example.test"
    owner("insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
          "values (gen_random_uuid(), %s, 'Vendedora Prueba', 'sales', 'active')", (email,))
    docs = [_doc(issuer="Ortoalresa", lines=[_line(unit=1000.0, qty=1, total=1000.0)], packing=20.0, number="Z-1"),
            _doc("supplier_proforma", number="Z-2", lines=[_line("ACME-9", unit=10.0)])]
    plan, sha = _db_plan(tmp_path, "p", docs)

    def argv(cmd, out, *extra):
        a = [cmd, "--target-dsn", runtime_dsn(dsn), "--plan", str(plan), "--plan-sha256", sha, "--out", str(out)]
        if cmd in ("apply", "rollback"):
            a += ["--admin-dsn", dsn]
        if cmd == "apply":
            a += ["--operator-email", email]
        return a + list(extra)

    assert isd.main(argv("apply", tmp_path / "a1")) == 0
    r1 = json.loads((tmp_path / "a1" / "apply-report.json").read_text())
    assert r1["already_present"] == 0 and r1["inserted"] >= 5
    assert isd.main(argv("apply", tmp_path / "a2")) == 0
    r2 = json.loads((tmp_path / "a2" / "apply-report.json").read_text())
    assert r2["inserted"] == 0 and r2["already_present"] == r1["inserted"]
    assert isd.main(argv("verify", tmp_path / "v")) == 0
    rows = owner("select sp.price_kind, sp.source_document, sp.price from catalog.supplier_product sp "
                 "join crm.organization o on o.id = sp.supplier_organization_id "
                 "where o.name in ('Ortoalresa', 'ACME Labs GmbH') order by 1, 2")
    assert ("supplier_offer", "Z-1", Decimal("1000.000000")) in rows
    assert ("order_confirmation", "Z-2", Decimal("10.000000")) in rows
    [(pct,)] = owner("select packing_pct from catalog.supplier_terms t join crm.organization o on "
                     "o.id = t.supplier_organization_id where o.name = 'Ortoalresa'")
    assert pct == Decimal("0.020000")

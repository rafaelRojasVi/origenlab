"""Quote reads (workspace, numbering, preview, kit suggestions) against PostgreSQL as the `origenlab_api` role.

Every name, number, cost and rate is invented. The commands that build the quotes run through
`V2QuoteRepository.execute` exactly as the routes call them (the helpers of test_v2_quote_commands).
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import psycopg
import pytest
from psycopg.types.json import Jsonb

from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.quotes.draft import PreviewQuoteBody
from origenlab_api.v2.quotes.reads import V2QuoteReads
from test_v2_quote_commands import (
    NOW,
    PROFILE,
    WARRANTY,
    _case,
    _make_operator,
    _org,
    _owner,
    _param,
    _revision,
    _run,
    fake_fx,
)
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn


@pytest.fixture(scope="module")
def world():
    for dsn in build_disposable_database():
        operator = _make_operator(dsn)
        institution = _org(dsn, "institution", "Universidad Ficticia")
        _owner(dsn, "insert into crm.external_identifier (scheme, value_norm, organization_id) values ('rut', %s, %s)",
               ("76543210-K", institution))
        supplier = _org(dsn, "supplier", "Proveedor Ficticio")
        maker = _org(dsn, "manufacturer", "Fabricante Ficticio")
        _owner(dsn, "insert into catalog.supplier_terms (supplier_organization_id, currency, origin_country, route) "
                    "values (%s, 'EUR', 'DE', 'import_courier')", (supplier,))
        product = _owner(dsn, "insert into catalog.product (manufacturer_organization_id, model_number, name_es, "
                              "product_kind, weight_kg, origin_country, content_origin) values (%s, 'FX-100', "
                              "'Equipo ficticio', 'equipment', 10, 'DE', 'operator') returning id::text",
                         (maker,))[0][0]
        accessory = _owner(dsn, "insert into catalog.product (manufacturer_organization_id, model_number, name_es, "
                                "product_kind, content_origin) values (%s, 'ACC-7', 'Accesorio ficticio', "
                                "'accessory', 'operator') returning id::text", (maker,))[0][0]
        _owner(dsn, "insert into catalog.supplier_product (supplier_organization_id, product_id, as_of, price, "
                    "currency, price_kind, provenance_note) values (%s, %s, now() - interval '3 days', 1000, 'EUR', "
                    "'dealer_net', 'prueba')", (supplier, product))
        _param(dsn, "quote_number_seed", 2100)
        for key, value in PROFILE.items():
            _param(dsn, key, value)
        _param(dsn, "quote_default_warranty", WARRANTY)
        yield {"dsn": dsn, "operator": operator, "institution": institution, "supplier": supplier,
               "product": product, "accessory": accessory}


def _numbers(value):
    """The computed JSON with every number as a Decimal: the save prices typed costs, the reload the
    stored numeric(18,6) ones — the same amounts, written with a different number of decimals."""
    if isinstance(value, dict):
        return {k: _numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_numbers(v) for v in value]
    if isinstance(value, str):
        try:
            return Decimal(value)
        except ArithmeticError:
            return value
    return value


def _reads(dsn: str) -> V2QuoteReads:
    return V2QuoteReads(psycopg.connect, runtime_dsn(dsn), fx_resolver=fake_fx, clock=lambda: NOW)


def _new_quote(world: dict) -> tuple[str, dict]:
    case = _case(world["dsn"], world["operator"], world["institution"])
    return case, _run(world, "create-quote", {"opportunity_id": case})


DRAFT = {
    "items": [{"description": "Equipo ficticio FX-100", "quantity": "1"},
              {"description": "Línea libre", "quantity": "2", "unit_cost": "15000", "cost_currency": "CLP",
               "margin_mode": "margin", "margin_pct": "0.25", "line_note_es": "Nota de prueba"}],
    "expense_method": "profile",
    "terms": {"warranty_terms_es": WARRANTY, "payment_terms": "30 días", "validity_days": 20,
              "terms_extra": [{"label_es": "Capacitación", "text_es": "Una sesión"}]},
}


def _draft(world: dict) -> dict:
    return {**DRAFT, "items": [{**DRAFT["items"][0], "product_id": world["product"]}, DRAFT["items"][1]]}


def _save(world: dict, created: dict, draft: dict) -> dict:
    return _run(world, "save-quote-draft", {"revision_id": created["revision_id"],
                                            "expected_version": created["version"], "draft": draft})


@needs_db
def test_workspace_returns_the_draft_as_saved_and_its_live_computation(world) -> None:
    case, created = _new_quote(world)
    saved = _save(world, created, _draft(world))
    ws = _reads(world["dsn"]).workspace(created["quote_id"])

    assert ws["quote"] == {"id": created["quote_id"], "quote_number": created["quote_number"], "opportunity_id": case}
    assert ws["case"]["institution"]["id"] == world["institution"] and ws["case"]["institution"]["rut"] == "76543210-K"
    [participant] = ws["case"]["participants"]
    assert participant["primary"] is True and participant["name"] == "Persona Ficticia"
    assert [r["id"] for r in ws["revisions"]] == [created["revision_id"]]

    current = ws["current"]
    assert current["id"] == created["revision_id"] and current["version"] == saved["version"]
    draft = current["draft"]
    # The draft round-trips: what comes back is what was saved, cost made explicit.
    assert draft["expense_method"] == "profile"
    assert draft["terms"]["payment_terms"] == "30 días" and draft["terms"]["validity_days"] == 20
    assert draft["terms"]["terms_extra"] == [{"label_es": "Capacitación", "text_es": "Una sesión"}]
    assert [i["description"] for i in draft["items"]] == ["Equipo ficticio FX-100", "Línea libre"]
    assert draft["items"][0]["product_id"] == world["product"] and draft["items"][0]["cost_currency"] == "EUR"
    assert draft["items"][1]["line_note_es"] == "Nota de prueba" and draft["items"][1]["margin_mode"] == "margin"
    assert draft["manual_expenses"] == []
    # Re-priced live, it equals the save.
    assert current["computed_error"] is None
    assert _numbers(current["computed"]) == _numbers(saved["computed"])


@needs_db
def test_workspace_revision_list_carries_labels_and_totals(world) -> None:
    _, created = _new_quote(world)
    saved = _save(world, created, _draft(world))
    [rev] = _reads(world["dsn"]).workspace(created["quote_id"])["revisions"]
    assert rev["label"] == created["quote_number"] and rev["status"] == "draft"
    assert (rev["revision_no"], rev["version"], rev["approved_at"], rev["sent_at"]) == (1, saved["version"], None, None)
    assert Decimal(rev["grand_total"]) == Decimal(saved["computed"]["totals"]["total"])


@needs_db
def test_lo_que_pidio_is_the_latest_case_memory(world) -> None:
    case, created = _new_quote(world)
    reads = _reads(world["dsn"])
    assert reads.workspace(created["quote_id"])["case"]["lo_que_pidio"] is None
    for version, summary in ((1, "Pidió un equipo"), (2, "Pidió un equipo y su accesorio")):
        _owner(world["dsn"], "insert into evidence.subject_memory (subject_kind, subject_id, version, memory) "
                             "values ('opportunity', %s, %s, %s)", (case, version, Jsonb({"summary": summary})))
    assert reads.workspace(created["quote_id"])["case"]["lo_que_pidio"] == "Pidió un equipo y su accesorio"


@needs_db
def test_unknown_quote_is_none(world) -> None:
    assert _reads(world["dsn"]).workspace(uuid.uuid4()) is None


@needs_db
def test_numbering_reports_the_next_number_without_reserving_it(world) -> None:
    _, created = _new_quote(world)
    reads = _reads(world["dsn"])
    serial = int(created["quote_number"].split("-")[0])
    status = reads.numbering()
    assert status["configured"] is True and status["next_number"] == f"{serial + 1:05d}-26"
    assert reads.numbering()["next_number"] == status["next_number"]  # nothing was reserved


@needs_db
def test_preview_prices_as_the_save_and_writes_nothing(world) -> None:
    _, created = _new_quote(world)
    saved = _save(world, created, _draft(world))
    before = _revision(world["dsn"], created["revision_id"])
    [(events_before,)] = _owner(world["dsn"], "select count(*) from crm.domain_event")
    [(receipts_before,)] = _owner(world["dsn"], "select count(*) from platform.command_receipt")

    reads = _reads(world["dsn"])
    out = reads.preview(PreviewQuoteBody(revision_id=created["revision_id"], draft=_draft(world)))
    assert out["revision_id"] == created["revision_id"]
    assert out["computed"] == saved["computed"]  # the same body: the same figures, to the digit
    # Without a revision, the current IVA parameter prices it: the same here.
    assert reads.preview(PreviewQuoteBody(draft=_draft(world)))["computed"] == saved["computed"]

    after = _revision(world["dsn"], created["revision_id"])
    assert (after["version"], after["updated_at"]) == (before["version"], before["updated_at"])
    assert _owner(world["dsn"], "select count(*) from crm.domain_event") == [(events_before,)]
    assert _owner(world["dsn"], "select count(*) from platform.command_receipt") == [(receipts_before,)]


@needs_db
def test_preview_refusals(world) -> None:
    reads = _reads(world["dsn"])
    with pytest.raises(CommandRefused) as exc:
        reads.preview(PreviewQuoteBody(revision_id=uuid.uuid4(), draft=_draft(world)))
    assert (exc.value.status_code, exc.value.code) == (404, "revision_not_found")
    with pytest.raises(CommandRefused) as exc:
        reads.preview(PreviewQuoteBody(draft={"items": [{"product_id": str(uuid.uuid4()), "description": "x",
                                                         "quantity": "1"}]}))
    assert (exc.value.status_code, exc.value.code) == (404, "product_not_found")


@needs_db
def test_preview_reads_in_a_read_only_transaction_and_asks_the_resolver_once(world) -> None:
    seen = []

    def spying_fx(currency, on):
        seen.append((currency, on))
        return fake_fx(currency, on)

    reads = V2QuoteReads(psycopg.connect, runtime_dsn(world["dsn"]), fx_resolver=spying_fx, clock=lambda: NOW)
    out = reads.preview(PreviewQuoteBody(draft=_draft(world)))
    assert seen == [("EUR", NOW.date())] and [f["currency"] for f in out["computed"]["fx"]] == ["EUR"]
    with reads._read() as cur:
        cur.execute("select current_setting('transaction_read_only')")
        assert cur.fetchone() == ("on",)


@needs_db
def test_an_issued_revision_shows_its_stored_lines_and_snapshot(world) -> None:
    _, created = _new_quote(world)
    saved = _save(world, created, _draft(world))
    _run(world, "approve-quote-revision", {"revision_id": created["revision_id"], "expected_version": saved["version"]})
    current = _reads(world["dsn"]).workspace(created["quote_id"])["current"]
    assert current["status"] == "approved" and "draft" not in current and "computed" not in current
    assert current["party_snapshot"]["quote_number"] == created["quote_number"]
    assert [line["line_no"] for line in current["lines"] if line["line_kind"] == "item"] == [1, 2]
    assert current["grand_total"] is not None


@needs_db
def test_mentioned_products_come_from_the_case_emails_only(world) -> None:
    dsn = world["dsn"]
    case, created = _new_quote(world)

    def email(linked: bool, unlinked: bool = False) -> str:
        gid = uuid.uuid4().hex
        sid = _owner(dsn, "insert into evidence.source_record (kind, dedupe_key, payload) values ('gmail_message', "
                          "%s, %s) returning id::text", (f"gmail_message:{gid}", Jsonb({"gmail_message_id": gid})))[0][0]
        if linked:
            _owner(dsn, "insert into crm.opportunity_evidence (opportunity_id, source_record_id, relation, "
                        "linked_by_operator_id, unlinked_at, unlink_reason) values (%s, %s, 'mentions', %s, "
                        + ("now(), 'otro caso'" if unlinked else "null, null") + ")",
                   (case, sid, world["operator"].operator_id))
        return sid

    def mention(sid: str, norm: str, value: dict) -> None:
        _owner(dsn, "insert into evidence.assertion (source_record_id, kind, value_norm, value) "
                    "values (%s, 'product_mention', %s, %s)", (sid, norm, Jsonb(value)))

    linked = email(True)
    mention(linked, "ACC7", {"source": "model", "model": "ACC-7", "description": "accesorio",
                             "catalog_product_id": world["accessory"]})
    mention(linked, "CENTRIFUGA", {"source": "model", "model": None, "description": "una centrífuga",
                                   "catalog_product_id": "no-es-un-uuid"})
    mention(email(False), "OTRO1", {"source": "model", "model": "OTRO-1", "catalog_product_id": None})
    mention(email(True, unlinked=True), "OTRO2", {"source": "model", "model": "OTRO-2", "catalog_product_id": None})

    mentioned = _reads(dsn).workspace(created["quote_id"])["suggestions"]["mentioned"]
    assert mentioned == [
        {"product_id": world["accessory"], "model": "ACC-7", "name": "Accesorio ficticio", "model_key": "ACC7"},
        {"product_id": None, "model": None, "name": "una centrífuga", "model_key": "CENTRIFUGA"},
    ]


def _quote_document(dsn: str, models: list[str]) -> None:
    sid = _owner(dsn, "insert into evidence.source_record (kind, dedupe_key, payload) values ('quote_document', %s, "
                      "%s) returning id::text", (f"quote_document:{uuid.uuid4().hex}", Jsonb({"quote_date": "2026-09-01"})))[0][0]
    for n, key in enumerate(models, start=1):
        _owner(dsn, "insert into evidence.document_line (source_record_id, line_no, model, model_key, description, "
                    "qty, line_total, currency, extractor, check_status) values (%s, %s, %s, %s, %s, 1, 1000, 'CLP', "
                    "'test', 'verified')", (sid, n, key, key, f"Ítem {key}"))


@needs_db
def test_kit_suggestions_follow_r_a5(world) -> None:
    dsn = world["dsn"]
    reads = _reads(dsn)
    # Two documents with the principal: under the three-document minimum, nothing is suggested.
    _quote_document(dsn, ["KITP1", "KITA"])
    _quote_document(dsn, ["KITP1", "KITA"])
    with reads._read() as cur:
        assert reads.kit_suggestions(cur, "kitp-1") == []
    # Four: KITA on 3/4 (75%), ACC7 on 2/4 (50%, a catalog product), KITLOW on 1/4 (25%, under 30%).
    _quote_document(dsn, ["KITP1", "KITA", "ACC7"])
    _quote_document(dsn, ["KITP1", "ACC7", "KITLOW"])
    _quote_document(dsn, ["KITA", "KITLOW"])  # without the principal: does not count
    with reads._read() as cur:
        kits = reads.kit_suggestions(cur, "KITP1")
    assert [(k["model_key"], k["product_id"], k["name"], k["share"], k["documents"]) for k in kits] == [
        ("KITA", None, "KITA", "0.7500", 3),
        ("ACC7", world["accessory"], "Accesorio ficticio", "0.5000", 2),
    ]


@needs_db
def test_the_workspace_offers_kits_and_history_of_the_principal(world) -> None:
    dsn = world["dsn"]
    for models in (["FX100", "KITB"], ["FX100", "KITB"], ["FX100"]):
        _quote_document(dsn, models)
    _, created = _new_quote(world)
    _save(world, created, _draft(world))
    ws = _reads(dsn).workspace(created["quote_id"])
    assert ws["current"]["computed"]["principal_line_no"] == 1
    assert [k["model_key"] for k in ws["suggestions"]["kits"]] == ["KITB"]
    assert ws["price_history"]["model_key"] == "FX100" and len(ws["price_history"]["items"]) == 3

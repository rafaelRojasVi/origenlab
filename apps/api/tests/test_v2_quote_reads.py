"""Quote reads (workspace, numbering, preview, kit suggestions) against PostgreSQL as the `origenlab_api` role.

Every name, number, cost and rate is invented. The commands that build the quotes run through
`V2QuoteRepository.execute` exactly as the routes call them (the helpers of test_v2_quote_commands).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest
from psycopg.types.json import Jsonb

from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.quotes.commands import V2QuoteRepository
from origenlab_api.v2.quotes.draft import QUOTE_BODIES, PreviewQuoteBody
from origenlab_api.v2.quotes.inputs import no_provider
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
    no_fx,
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
    inactive = _owner(dsn, "insert into catalog.product (manufacturer_organization_id, model_number, name_es, active, "
                           "content_origin) select manufacturer_organization_id, 'INACT-9', 'Retirado', false, "
                           "'operator' from catalog.product where id = %s returning id::text", (world["accessory"],))[0][0]
    mention(linked, "INACT9", {"source": "model", "model": "INACT-9", "description": "equipo retirado",
                               "catalog_product_id": inactive})
    mention(email(False), "OTRO1", {"source": "model", "model": "OTRO-1", "catalog_product_id": None})
    mention(email(True, unlinked=True), "OTRO2", {"source": "model", "model": "OTRO-2", "catalog_product_id": None})

    mentioned = _reads(dsn).workspace(created["quote_id"])["suggestions"]["mentioned"]
    assert mentioned == [
        {"product_id": world["accessory"], "model": "ACC-7", "name": "Accesorio ficticio", "model_key": "ACC7"},
        {"product_id": None, "model": None, "name": "una centrífuga", "model_key": "CENTRIFUGA"},
        # An inactive product is not offered as a product, only as what the email said (as for kits).
        {"product_id": None, "model": "INACT-9", "name": "equipo retirado", "model_key": "INACT9"},
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


# ------------------------------------------------------------------ exchange rates on the caller's connection

FUTURE = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)  # stored rates dated here stay invisible to the NOW tests


class _Counting:
    """A `connect` that counts connections and statements (every `execute`, pipelined or not), and
    the most connections open at once."""

    def __init__(self) -> None:
        self.connections = 0
        self.statements = 0
        self.open = 0
        self.most_open = 0

    def connect(self, dsn, **kwargs):
        self.connections += 1
        counter = self

        class _Cursor(psycopg.Cursor):
            def execute(self, query, params=None, **kw):
                counter.statements += 1
                return super().execute(query, params, **kw)

        class _Connection(psycopg.Connection):
            def close(self) -> None:
                if not self.closed:
                    counter.open -= 1
                super().close()

        conn = _Connection.connect(dsn, cursor_factory=_Cursor, **kwargs)
        self.open += 1
        self.most_open = max(self.most_open, self.open)
        return conn


def _never(currency, on):
    raise AssertionError(f"the resolver was asked for {currency}: a stored rate should have answered")


def _two_currency_draft(world: dict) -> dict:
    usd = {"description": "Accesorio en dólares", "quantity": "1", "item_type": "accessory", "unit_cost": "50",
           "cost_currency": "USD", "supplier_organization_id": world["supplier"]}
    return {**_draft(world), "items": [*_draft(world)["items"], usd]}


@pytest.fixture(scope="module")
def stored_rates(world):
    dsn, operator = world["dsn"], world["operator"]
    for currency, rate in (("EUR", 1111), ("USD", 999)):
        _owner(dsn, "insert into catalog.fx_rate (rate_date, currency, clp_per_unit, source, provider) "
                    "values ('2026-11-18', %s, %s, 'bcentral', 'bde')", (currency, rate))
    # A manual rate the same day comes first; an older one never.
    _owner(dsn, "insert into catalog.fx_rate (rate_date, currency, clp_per_unit, source, provider, reason, "
                "recorded_by_operator_id) values ('2026-11-18', 'EUR', 1200, 'manual', 'operator', 'prueba', %s)",
           (operator.operator_id,))
    _owner(dsn, "insert into catalog.fx_rate (rate_date, currency, clp_per_unit, source, provider) "
                "values ('2026-11-10', 'USD', 1, 'bcentral', 'bde')")
    return world


@needs_db
def test_preview_reads_stored_rates_on_its_own_connection_in_few_statements(stored_rates) -> None:
    world = stored_rates
    _, created = _new_quote(world)
    counting = _Counting()
    reads = V2QuoteReads(counting.connect, runtime_dsn(world["dsn"]), fx_resolver=_never, clock=lambda: FUTURE)
    out = reads.preview(PreviewQuoteBody(revision_id=created["revision_id"], draft=_two_currency_draft(world)))
    assert {f["currency"]: (f["clp_per_unit"], f["as_of"], f["source"]) for f in out["computed"]["fx"]} == {
        "EUR": ("1200.000000", "2026-11-18", "manual"), "USD": ("999.000000", "2026-11-18", "bcentral")}
    assert counting.connections == 1
    assert counting.statements <= 10, counting.statements


@needs_db
def test_workspace_reads_on_one_connection(stored_rates) -> None:
    world = stored_rates
    _, created = _new_quote(world)
    _save(world, created, _draft(world))
    counting = _Counting()
    reads = V2QuoteReads(counting.connect, runtime_dsn(world["dsn"]), fx_resolver=_never, clock=lambda: FUTURE)
    assert reads.workspace(created["quote_id"])["current"]["computed"]["fx"][0]["clp_per_unit"] == "1200.000000"
    assert counting.connections == 1


@needs_db
def test_a_stale_stored_rate_falls_back_to_the_resolver(stored_rates) -> None:
    world = stored_rates
    seen = []

    def fallback(currency, on):
        seen.append(currency)
        return fake_fx(currency, on)

    late = datetime(2026, 12, 10, 15, 0, tzinfo=UTC)  # 22 days after the newest stored rate
    reads = V2QuoteReads(psycopg.connect, runtime_dsn(world["dsn"]), fx_resolver=fallback, clock=lambda: late)
    reads.preview(PreviewQuoteBody(draft=_two_currency_draft(world)))
    assert seen == ["EUR", "USD"]


def _command(world: dict, connect, clock, command: str, raw: dict) -> dict:
    body = QUOTE_BODIES[command](**raw)
    repo = V2QuoteRepository(connect, runtime_dsn(world["dsn"]), fx_resolver=no_provider, clock=clock)
    return repo.execute(command_name=command, operator=world["operator"], fields=body.model_dump(mode="json"),
                        idempotency_key=uuid.uuid4().hex, digest=request_digest(command, body))


@needs_db
def test_save_prices_with_the_stored_rate_on_its_own_connection(stored_rates) -> None:
    world = stored_rates
    _, created = _new_quote(world)
    counting = _Counting()
    saved = _command(world, counting.connect, lambda: FUTURE, "save-quote-draft",
                     {"revision_id": created["revision_id"], "expected_version": created["version"],
                      "draft": _two_currency_draft(world)})
    assert counting.connections == 1
    assert {f["currency"]: f["clp_per_unit"] for f in saved["computed"]["fx"]} == {
        "EUR": "1200.000000", "USD": "999.000000"}
    # The preview of the same draft at the same moment shows the same figures the save stored.
    reads = V2QuoteReads(psycopg.connect, runtime_dsn(world["dsn"]), fx_resolver=_never, clock=lambda: FUTURE)
    preview = reads.preview(PreviewQuoteBody(revision_id=created["revision_id"], draft=_two_currency_draft(world)))
    assert preview["computed"] == saved["computed"]


@needs_db
def test_save_with_nothing_stored_is_409_and_asks_no_provider(stored_rates) -> None:
    world = stored_rates
    _, created = _new_quote(world)
    with pytest.raises(CommandRefused) as exc:
        _command(world, psycopg.connect, lambda: datetime(2027, 3, 1, 15, 0, tzinfo=UTC), "save-quote-draft",
                 {"revision_id": created["revision_id"], "expected_version": created["version"],
                  "draft": _two_currency_draft(world)})
    assert (exc.value.status_code, exc.value.code) == (409, "fx_unavailable")
    assert _revision(world["dsn"], created["revision_id"])["version"] == created["version"]


LATER = datetime(2027, 6, 10, 15, 0, tzinfo=UTC)  # far from every other test's stored rates


@needs_db
def test_a_draft_priced_differently_today_needs_saving_and_approval_freezes_the_save(world) -> None:
    """The workspace prices a draft live (today's rate, today's parameters); approval freezes what was
    saved. The workspace says so: `saved` is what approval would freeze, `needs_saving` whether the
    live figures differ from it."""
    dsn = world["dsn"]
    _owner(dsn, "insert into catalog.fx_rate (rate_date, currency, clp_per_unit, source, provider) "
                "values ('2027-06-09', 'EUR', 1000, 'bcentral', 'bde')")
    _, created = _new_quote(world)
    saved = _command(world, psycopg.connect, lambda: LATER, "save-quote-draft",
                     {"revision_id": created["revision_id"], "expected_version": created["version"],
                      "draft": _draft(world)})
    reads = V2QuoteReads(psycopg.connect, runtime_dsn(dsn), fx_resolver=_never, clock=lambda: LATER)

    def figures(computed: dict) -> dict:
        totals = computed["totals"]
        return {"totals": {k: Decimal(totals[k]) for k in ("net", "iva", "total")},
                "lines": [{"line_no": r["line_no"], "unit_price_qc": Decimal(r["unit_price"]),
                           "line_total_qc": Decimal(r["line_total"])} for r in computed["lines"]]}

    current = reads.workspace(created["quote_id"])["current"]
    assert current["needs_saving"] is False
    assert _numbers(current["saved"]) == figures(saved["computed"])

    _owner(dsn, "insert into catalog.fx_rate (rate_date, currency, clp_per_unit, source, provider) "
                "values ('2027-06-10', 'EUR', 1100, 'bcentral', 'bde')")
    current = reads.workspace(created["quote_id"])["current"]
    assert [f["clp_per_unit"] for f in current["computed"]["fx"]] == ["1100.000000"]
    assert current["needs_saving"] is True
    assert _numbers(current["saved"]) == figures(saved["computed"])  # still the save, not today's

    _command(world, psycopg.connect, lambda: LATER, "approve-quote-revision",
             {"revision_id": created["revision_id"], "expected_version": saved["version"]})
    rev = _revision(dsn, created["revision_id"])
    assert {"net": rev["subtotal"], "iva": rev["tax_total"], "total": rev["grand_total"]} == figures(
        saved["computed"])["totals"]


@needs_db
def test_a_rate_miss_asks_the_provider_with_no_connection_held(world) -> None:
    """On a stored-rate miss the read transaction is closed before the provider is asked (its HTTP
    call and its own write of the answer), then the draft is priced again in a fresh one."""
    counting = _Counting()
    seen = []

    def provider(currency, on):
        seen.append((currency, counting.open))
        with counting.connect(runtime_dsn(world["dsn"])) as conn:  # where PricingFx stores its answer
            conn.execute("select 1")
        return fake_fx(currency, on)

    miss = datetime(2027, 9, 1, 15, 0, tzinfo=UTC)  # nothing stored within the window
    reads = V2QuoteReads(counting.connect, runtime_dsn(world["dsn"]), fx_resolver=provider, clock=lambda: miss)
    out = reads.preview(PreviewQuoteBody(draft=_two_currency_draft(world)))
    assert {f["currency"]: f["clp_per_unit"] for f in out["computed"]["fx"]} == {"EUR": "1000", "USD": "900"}
    assert seen == [("EUR", 0), ("USD", 0)]  # every missing currency in one pass, none while reading
    assert counting.most_open == 1 and counting.connections == 4  # read, two provider writes, read again

    _, created = _new_quote(world)
    _save(world, created, _draft(world))
    seen.clear()
    current = reads.workspace(created["quote_id"])["current"]
    assert current["computed_error"] is None and current["computed"]["fx"][0]["clp_per_unit"] == "1000"
    assert seen == [("EUR", 0)] and counting.most_open == 1


@needs_db
def test_a_rate_no_provider_has_is_still_refused_after_the_one_retry(world) -> None:
    calls = []

    def unavailable(currency, on):
        calls.append(currency)
        return no_fx(currency, on)

    miss = datetime(2027, 9, 1, 15, 0, tzinfo=UTC)
    reads = V2QuoteReads(psycopg.connect, runtime_dsn(world["dsn"]), fx_resolver=unavailable, clock=lambda: miss)
    with pytest.raises(CommandRefused) as exc:
        reads.preview(PreviewQuoteBody(draft=_draft(world)))
    assert (exc.value.status_code, exc.value.code) == (409, "fx_unavailable") and calls == ["EUR"]

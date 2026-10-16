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
from origenlab_api.v2.quotes.numbering import revision_label
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
    case_email,
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
def test_the_recipient_candidates_are_the_people_on_the_case_s_emails(world) -> None:
    dsn, operator = world["dsn"], world["operator"]
    case = _case(dsn, operator, world["institution"], participant=False)
    created = _run(world, "create-quote", {"opportunity_id": case})
    tag = uuid.uuid4().hex[:6]
    asker, buyer = f"persona-{tag}@cliente-ficticio.test", f"compras-{tag}@cliente-ficticio.test"
    thread = uuid.uuid4().hex
    # Inbound: its sender. Outbound on the same thread (not linked itself): its recipients, not OrigenLab's own.
    case_email(dsn, case, operator, sender=f'"Persona Ficticia" <{asker}>', recipients="contacto@origenlab.cl",
               thread=thread)
    case_email(dsn, case, operator, sender="Ventas <ventas@origenlab.cl>", outbound=True, linked=False,
               thread=thread, recipients=f"Compras <{buyer}>; {asker}, otra@labdelivery.cl")
    # Nothing of Labdelivery, and nothing from an email of another case.
    case_email(dsn, case, operator, sender=f"Lab Delivery <persona-{tag}@labdelivery.cl>")
    case_email(dsn, case, operator, sender=f"Ajeno <ajeno-{tag}@cliente-ficticio.test>", linked=False)
    point = _owner(dsn, "insert into crm.contact_point (kind, value_norm, value_display, usage, confirmation) "
                        "values ('email', %s, %s, 'unattributed', 'machine_proposed') returning id::text",
                   (buyer, buyer))[0][0]
    customer = f"cliente-{tag}@cliente-ficticio.test"
    forward = _owner(dsn, "insert into evidence.source_record (kind, dedupe_key, payload, review_status) values "
                          "('forwarded_request', %s, %s, 'promoted') returning id::text",
                     (f"forwarded_request:{tag}", Jsonb({"original_email": customer, "original_name": "Cliente Reenviado",
                                                         "origin": "contacto"})))[0][0]
    _owner(dsn, "update crm.opportunity set origin_source_record_id = %s where id = %s", (forward, case))

    contacts = _reads(dsn).workspace(created["quote_id"])["case"]["contacts"]
    by_email = {c["email"]: c for c in contacts}
    assert set(by_email) == {asker, buyer, customer}
    # Listed once, from its newest email (OrigenLab's reply), with the name its own email gave it.
    assert by_email[asker] == {"participant_id": None, "person_id": None, "contact_point_id": None,
                               "name": "Persona Ficticia", "email": asker, "source": "email_to"}
    assert by_email[buyer] == {"participant_id": None, "person_id": None, "contact_point_id": point,
                               "name": "Compras", "email": buyer, "source": "email_to"}
    assert by_email[customer]["source"] == "forwarded_request" and by_email[customer]["name"] == "Cliente Reenviado"
    assert not any("labdelivery" in c["email"] or "origenlab" in c["email"] for c in contacts)

    # Picked and saved, the address is a participant: listed first, once.
    _run(world, "save-quote-draft", {"revision_id": created["revision_id"], "expected_version": created["version"],
                                     "draft": {"recipient": {"email": asker, "name": "Persona Ficticia"}}})
    contacts = _reads(dsn).workspace(created["quote_id"])["case"]["contacts"]
    assert contacts[0]["source"] == "participant" and contacts[0]["email"] == asker
    assert contacts[0]["name"] == "Persona Ficticia" and contacts[0]["participant_id"] is not None
    assert [c["email"] for c in contacts].count(asker) == 1 and len(contacts) == 3


@needs_db
def test_the_recipient_candidates_keep_every_address_rule_and_the_newest_comes_first(world) -> None:
    dsn, operator = world["dsn"], world["operator"]
    case = _case(dsn, operator, world["institution"], participant=False)
    created = _run(world, "create-quote", {"opportunity_id": case})
    tag = uuid.uuid4().hex[:6]

    def address(name: str) -> str:
        return f"{name}-{tag}@cliente-ficticio.test"

    # Newest first by the instant, not by the text: 23:00 at -03:00 is after 01:00 UTC the next day.
    case_email(dsn, case, operator, sender=address("antes"), sent_at="2026-10-11T01:00:00+00:00")
    case_email(dsn, case, operator, sender=address("despues"), sent_at="2026-10-10T23:00:00-03:00")
    case_email(dsn, case, operator, sender=address("sin-fecha"), sent_at=None)  # captured now: newest
    # OrigenLab's own subdomains are OrigenLab.
    case_email(dsn, case, operator, sender=f"Interno <alguien-{tag}@correo.origenlab.cl>")
    # A deactivated address is not offered (the save would refuse it).
    inactive = address("inactiva")
    _owner(dsn, "insert into crm.contact_point (kind, value_norm, value_display, usage, confirmation, status, "
                "deactivated_at, deactivated_by_operator_id) values ('email', %s, %s, 'unattributed', "
                "'machine_proposed', 'inactive', now(), %s)", (inactive, inactive, operator.operator_id))
    case_email(dsn, case, operator, sender=inactive)
    # A rejected or quarantined linked email names nobody, and brings no thread with it.
    for kw in ({"review_status": "rejected"}, {"quarantined": True}):
        thread = uuid.uuid4().hex
        case_email(dsn, case, operator, sender=address("descartado"), thread=thread, **kw)
        case_email(dsn, case, operator, sender=address("en-su-hilo"), thread=thread, linked=False)

    contacts = _reads(dsn).workspace(created["quote_id"])["case"]["contacts"]
    assert [c["email"] for c in contacts] == [address("sin-fecha"), address("despues"), address("antes")]


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


def test_automatic_senders_are_never_offered_as_recipients() -> None:
    """Notification and no-reply senders on a case's emails are nobody to quote to; a generic
    mailbox a person reads (ventas@, contacto@, info@) still is. Pure: no database."""
    from origenlab_api.v2.quotes.reads import email_people

    automatic = [
        "no-reply@cliente-ficticio.test", "noreply@cliente-ficticio.test", "donotreply@cliente-ficticio.test",
        "do-not-reply@cliente-ficticio.test", "alertas-noreply@cliente-ficticio.test",
        "noreply+abc@cliente-ficticio.test", "MAILER-DAEMON@cliente-ficticio.test",
        "postmaster@cliente-ficticio.test", "notifications@cliente-ficticio.test",
        "notification@cliente-ficticio.test", "gemini-notes@google.com", "calendar-notification@google.com",
        "comments-noreply@docs.google.com", "avisos@calendar.google.com",
    ]
    people = ["ventas@cliente-ficticio.test", "contacto@cliente-ficticio.test", "info@cliente-ficticio.test",
              "persona@google.com", "noreplyca@cliente-ficticio.test"]
    rows = [{"kind": "email", "sender": f"Alguien <{a}>", "direction_hint": "inbound", "comms_direction": "inbound",
             "recipients": "contacto@origenlab.cl", "at": None} for a in automatic + people]
    offered = {p["email"] for p in email_people(rows)}
    assert offered == set(people)


# ------------------------------------------------------------------ the Cotizador module: list, clients, params

def _titled_case(world: dict, institution: str, title: str, *, closed: bool = False, participant: bool = True) -> str:
    case = _case(world["dsn"], world["operator"], institution, closed=closed, participant=participant)
    _owner(world["dsn"], "update crm.opportunity set title = %s where id = %s", (title, case))
    return case


def _historical(world: dict, case: str, number: str) -> str:
    """An imported, sent quote (no lines in crm.quote_line), as the historical import leaves one."""
    dsn = world["dsn"]
    record = _owner(dsn, "insert into evidence.source_record (kind, dedupe_key, payload) values ('quote_document', "
                         "%s, '{}'::jsonb) returning id", (f"hist-{uuid.uuid4().hex}",))[0][0]
    quote = _owner(dsn, "insert into crm.quote (opportunity_id, quote_number, number_origin) values (%s, %s, "
                        "'printed_historical') returning id::text", (case, number))[0][0]
    _owner(dsn, "insert into crm.quote_revision (quote_id, revision_no, status, origin, quote_currency, price_decimals, "
                "pdf_sha256, sent_at, origin_source_record_id, grand_total) values (%s, 1, 'sent', 'historical_import', "
                "'CLP', 0, %s, now(), %s, 119000)", (quote, "c" * 64, record))
    return quote


def _list(world: dict, **kw) -> dict:
    args = {"status": "all", "q": None, "limit": 50, "cursor": None, **kw}
    return _reads(world["dsn"]).quote_list(**args)


@needs_db
def test_the_quote_list_shows_each_quote_at_its_current_revision_with_counts(world) -> None:
    dsn, tag = world["dsn"], uuid.uuid4().hex[:8]
    institution = _org(dsn, "institution", f"Instituto Lista {tag}")
    draft_case = _titled_case(world, institution, f"Caso borrador {tag}")
    draft = _run(world, "create-quote", {"opportunity_id": draft_case})
    saved = _save(world, draft, {**_draft(world), "items": [
        *_draft(world)["items"],
        {"description": "Tercera línea", "quantity": "1", "unit_cost": "10", "cost_currency": "CLP"},
        {"description": "Cuarta línea", "quantity": "1", "unit_cost": "10", "cost_currency": "CLP"}]})
    _, approved = _new_quote({**world, "institution": institution})
    approved_saved = _save(world, approved, _draft(world))
    _run(world, "approve-quote-revision", {"revision_id": approved["revision_id"],
                                           "expected_version": approved_saved["version"]})
    _, voided = _new_quote({**world, "institution": institution})
    _run(world, "void-quote-revision", {"revision_id": voided["revision_id"], "expected_version": voided["version"],
                                        "reason": "duplicada"})
    historical = _historical(world, _titled_case(world, institution, f"Caso histórico {tag}"), f"H{tag}")

    out = _list(world, q=tag)
    assert out["counts"] == {"draft": 1, "approved": 1, "sent": 1, "void": 1, "all": 4}
    assert out["next_cursor"] is None
    rows = out["rows"]
    assert [r["quote_id"] for r in rows] == [historical, voided["quote_id"], approved["quote_id"], draft["quote_id"]]
    assert [r["updated_at"] for r in rows] == sorted((r["updated_at"] for r in rows), reverse=True)
    by_id = {r["quote_id"]: r for r in rows}

    row = by_id[draft["quote_id"]]
    assert row == {
        "quote_id": draft["quote_id"], "quote_number": draft["quote_number"], "label": draft["quote_number"],
        "status": "draft", "origin": "authored",
        "opportunity": {"id": draft_case, "title": f"Caso borrador {tag}", "closed": False},
        "client": {"organization_id": institution, "name": row["client"]["name"]},
        "items": {"first": ["Equipo ficticio FX-100", "Línea libre", "Tercera línea"], "count": 4},
        "grand_total": row["grand_total"], "currency": "CLP", "updated_at": row["updated_at"],
    }
    assert row["client"]["name"].startswith(f"Instituto Lista {tag}")
    assert Decimal(row["grand_total"]) == Decimal(saved["computed"]["totals"]["total"])
    assert by_id[approved["quote_id"]]["status"] == "approved"
    assert by_id[voided["quote_id"]]["status"] == "void"
    hist = by_id[historical]
    assert (hist["status"], hist["origin"], hist["label"]) == ("sent", "historical_import", None)
    assert hist["items"] == {"first": [], "count": 0} and Decimal(hist["grand_total"]) == 119000

    only_drafts = _list(world, q=tag, status="draft")
    assert [r["quote_id"] for r in only_drafts["rows"]] == [draft["quote_id"]]
    assert only_drafts["counts"] == out["counts"]  # counts are per status under the same search


@needs_db
def test_an_open_draft_over_an_issued_revision_is_the_listed_one(world) -> None:
    tag = uuid.uuid4().hex[:8]
    institution = _org(world["dsn"], "institution", f"Instituto Revisión {tag}")
    _, created = _new_quote({**world, "institution": institution})
    saved = _save(world, created, _draft(world))
    _run(world, "approve-quote-revision", {"revision_id": created["revision_id"], "expected_version": saved["version"]})
    _run(world, "create-quote-revision", {"quote_id": created["quote_id"]})
    [row] = _list(world, q=tag)["rows"]
    assert row["status"] == "draft" and row["label"] == revision_label(created["quote_number"], 2)
    assert _list(world, q=tag)["counts"]["approved"] == 0


@needs_db
def test_the_quote_list_searches_number_institution_case_title_and_lines(world) -> None:
    dsn, tag = world["dsn"], uuid.uuid4().hex[:8]
    institution = _org(dsn, "institution", f"Laboratorio Buscado {tag}")
    plain = _org(dsn, "institution", "Otra Institución")
    by_institution = _run(world, "create-quote", {"opportunity_id": _titled_case(world, institution, "Caso uno")})
    by_title = _run(world, "create-quote", {"opportunity_id": _titled_case(world, plain, f"Centrífuga {tag}")})
    by_line = _run(world, "create-quote", {"opportunity_id": _titled_case(world, plain, "Caso tres")})
    _save(world, by_line, {"items": [{"description": f"Rotor especial {tag}", "quantity": "1", "unit_cost": "10",
                                      "cost_currency": "CLP"}]})

    def found(q: str) -> set[str]:
        return {r["quote_id"] for r in _list(world, q=q)["rows"]}

    assert found(tag) == {by_institution["quote_id"], by_title["quote_id"], by_line["quote_id"]}
    assert found(f"laboratorio buscado {tag}") == {by_institution["quote_id"]}
    assert found(f"centrífuga {tag}") == {by_title["quote_id"]}
    assert found(f"ROTOR ESPECIAL {tag}") == {by_line["quote_id"]}
    assert found(by_title["quote_number"]) == {by_title["quote_id"]}
    # LIKE wildcards are text, not patterns.
    assert found(f"%{tag}") == set() and found(f"Rotor_especial {tag}") == set()


@needs_db
def test_the_quote_list_pages_by_keyset_in_a_constant_number_of_statements(world) -> None:
    tag = uuid.uuid4().hex[:8]
    institution = _org(world["dsn"], "institution", f"Instituto Páginas {tag}")
    made = [_new_quote({**world, "institution": institution})[1]["quote_id"] for _ in range(3)]

    seen, cursor, pages = [], None, 0
    while True:
        out = _list(world, q=tag, limit=2, cursor=cursor)
        seen += [r["quote_id"] for r in out["rows"]]
        pages += 1
        cursor = out["next_cursor"]
        if cursor is None:
            break
    assert pages == 2 and seen == list(reversed(made))

    def statements(limit: int) -> int:
        counting = _Counting()
        reads = V2QuoteReads(counting.connect, runtime_dsn(world["dsn"]), fx_resolver=_never, clock=lambda: NOW)
        assert len(reads.quote_list(status="all", q=None, limit=limit, cursor=None)["rows"]) == limit
        assert counting.connections == 1
        return counting.statements

    assert statements(1) == statements(3) <= 3


@needs_db
def test_a_cursor_the_list_did_not_issue_is_refused(world) -> None:
    import base64
    import json

    def encoded(value) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode()

    moment = "2026-10-15T12:00:00+00:00"
    for bad in ("nonsense", encoded({"x": 1}), encoded(["2026-10-15T12:00:00", str(uuid.uuid4())]),
                encoded([moment, "no-uuid"]), encoded([1, 2]),
                # Forged types: an id that is no string, a pair that is no list, one item too many.
                encoded([moment, 5]), encoded([moment, None]), encoded([moment, [1]]), encoded([moment, 10**40]),
                encoded({moment: str(uuid.uuid4()), "b": 1}), encoded([moment, str(uuid.uuid4()), 1]),
                encoded("ab"), encoded(None)):
        with pytest.raises(CommandRefused) as exc:
            _list(world, cursor=bad)
        assert (exc.value.status_code, exc.value.code) == (422, "bad_cursor")


def _supplier_role(dsn: str, organization: str, role: str = "supplier", *, ended: bool = False) -> None:
    if ended:
        _owner(dsn, "insert into crm.organization_relationship (organization_id, role, valid_from, valid_to) "
                    "values (%s, %s, current_date - 30, current_date - 1)", (organization, role))
    else:
        _owner(dsn, "insert into crm.organization_relationship (organization_id, role, valid_from) "
                    "values (%s, %s, current_date - 30)", (organization, role))


def _clients(world: dict, q: str) -> dict:
    return _reads(world["dsn"]).client_suggestions(q)


@needs_db
def test_client_suggestions_offer_institutions_with_their_open_cases_never_suppliers(world) -> None:
    dsn, tag = world["dsn"], uuid.uuid4().hex[:8]
    clinic = _org(dsn, "institution", f"Clínica {tag}")
    _owner(dsn, "insert into crm.organization_domain (organization_id, domain_norm, scope) values (%s, %s, 'exclusive')",
           (clinic, f"clinica-{tag}.test"))
    open_case = _titled_case(world, clinic, f"Balanza {tag}")
    _titled_case(world, clinic, "Caso cerrado", closed=True)
    # A twin merged into the clinic: its open case is the clinic's (the merge survivor), the twin is not offered.
    twin = _org(dsn, "institution", f"Clínica {tag} duplicada")
    twin_case = _titled_case(world, twin, f"Pipeta {tag}")
    _owner(dsn, "update crm.organization set merged_into_organization_id = %s where id = %s", (clinic, twin))
    supplier = _org(dsn, "institution", f"Clínica {tag} proveedora")
    _supplier_role(dsn, supplier)
    maker = _org(dsn, "institution", f"Clínica {tag} fabricante")
    _supplier_role(dsn, maker, "manufacturer")
    former = _org(dsn, "institution", f"Clínica {tag} ex proveedora")
    _supplier_role(dsn, former, ended=True)
    _owner(dsn, "insert into crm.organization (kind, name, confirmation, status, archived_at, archived_by_operator_id, "
                "archive_reason) values ('institution', %s, 'machine_proposed', 'archived', now(), %s, 'duplicado')",
           (f"Clínica {tag} archivada", world["operator"].operator_id))
    _org(dsn, "institution", f"Lab Delivery Clínica {tag}")
    own = _org(dsn, "institution", f"Clínica {tag} interna")
    _owner(dsn, "insert into crm.organization_domain (organization_id, domain_norm, scope) values (%s, %s, 'shared')",
           (own, f"x{tag}.origenlab.cl"))

    orgs = _clients(world, tag)["organizations"]
    assert {o["id"] for o in orgs} == {clinic, former}
    [found] = [o for o in orgs if o["id"] == clinic]
    assert found["kind"] == "institution" and found["domains"] == [f"clinica-{tag}.test"]
    assert {c["id"] for c in found["open_cases"]} == {open_case, twin_case}
    assert set(found["open_cases"][0]) == {"id", "title", "stage", "requested_at"}
    # By a domain too.
    assert [o["id"] for o in _clients(world, f"clinica-{tag}.te")["organizations"]] == [clinic]


@needs_db
def test_client_suggestions_offer_people_and_addresses_with_their_open_cases(world) -> None:
    dsn, tag = world["dsn"], uuid.uuid4().hex[:8]
    clinic = _org(dsn, "institution", f"Hospital {tag}")
    clinic_case = _titled_case(world, clinic, f"Caso hospital {tag}")
    elsewhere = _titled_case(world, _org(dsn, "institution", "Otra"), f"Caso de otra {tag}", participant=False)

    person = _owner(dsn, "insert into crm.person (display_name, confirmation) values (%s, 'machine_proposed') "
                         "returning id::text", (f"Ana Prueba {tag}",))[0][0]
    point = _owner(dsn, "insert into crm.contact_point (kind, value_norm, value_display, person_id, usage, confirmation) "
                        "values ('email', %s, %s, %s, 'personal', 'machine_proposed') returning id::text",
                   (f"ana-{tag}@cliente-ficticio.test", f"ana-{tag}@cliente-ficticio.test", person))[0][0]
    _owner(dsn, "insert into crm.affiliation (person_id, organization_id, valid_from, confirmation) "
                "values (%s, %s, current_date - 10, 'machine_proposed')", (person, clinic))
    _owner(dsn, "insert into crm.opportunity_participant (opportunity_id, person_id, role, is_primary, valid_from, "
                "confirmation) values (%s, %s, 'end_user', false, current_date, 'machine_proposed')", (elsewhere, person))
    bare = _owner(dsn, "insert into crm.contact_point (kind, value_norm, value_display, usage, confirmation) values "
                       "('email', %s, %s, 'unattributed', 'machine_proposed') returning id::text",
                  (f"compras-{tag}@cliente-ficticio.test", f"compras-{tag}@cliente-ficticio.test"))[0][0]
    # Never offered: OrigenLab's own and Labdelivery's addresses, an inactive address, a supplier's person.
    for address in (f"ventas-{tag}@origenlab.cl", f"pedidos-{tag}@labdelivery.cl"):
        _owner(dsn, "insert into crm.contact_point (kind, value_norm, value_display, usage, confirmation) values "
                    "('email', %s, %s, 'unattributed', 'machine_proposed')", (address, address))
    _owner(dsn, "insert into crm.contact_point (kind, value_norm, value_display, usage, confirmation, status, "
                "deactivated_at, deactivated_by_operator_id) values ('email', %s, %s, 'unattributed', "
                "'machine_proposed', 'inactive', now(), %s)",
           (f"viejo-{tag}@cliente-ficticio.test", f"viejo-{tag}@cliente-ficticio.test", world["operator"].operator_id))
    supplier = _org(dsn, "institution", "Proveedora de personas")
    _supplier_role(dsn, supplier)
    vendor = _owner(dsn, "insert into crm.person (display_name, confirmation) values (%s, 'machine_proposed') "
                         "returning id::text", (f"Vendedor {tag}",))[0][0]
    _owner(dsn, "insert into crm.affiliation (person_id, organization_id, valid_from, confirmation) "
                "values (%s, %s, current_date - 10, 'machine_proposed')", (vendor, supplier))

    people = _clients(world, tag)["people"]
    by_email = {p["email"]: p for p in people}
    assert set(by_email) == {f"ana-{tag}@cliente-ficticio.test", f"compras-{tag}@cliente-ficticio.test"}
    ana = by_email[f"ana-{tag}@cliente-ficticio.test"]
    assert (ana["person_id"], ana["contact_point_id"], ana["name"]) == (person, point, f"Ana Prueba {tag}")
    assert ana["organization"]["id"] == clinic
    assert {c["id"] for c in ana["open_cases"]} == {clinic_case, elsewhere}
    compras = by_email[f"compras-{tag}@cliente-ficticio.test"]
    assert (compras["person_id"], compras["contact_point_id"], compras["organization"]) == (None, bare, None)
    assert compras["open_cases"] == []
    # By part of the address.
    assert [p["person_id"] for p in _clients(world, f"ana-{tag}")["people"]] == [person]


@needs_db
def test_client_suggestions_cap_each_list_and_read_in_a_few_statements(world) -> None:
    dsn, tag = world["dsn"], uuid.uuid4().hex[:8]
    for n in range(10):
        org = _org(dsn, "institution", f"Centro {tag} {n}")
        _titled_case(world, org, f"Caso {n}")
        _owner(dsn, "insert into crm.contact_point (kind, value_norm, value_display, usage, confirmation) values "
                    "('email', %s, %s, 'unattributed', 'machine_proposed')",
               (f"centro{n}-{tag}@cliente-ficticio.test", f"centro{n}-{tag}@cliente-ficticio.test"))
    counting = _Counting()
    reads = V2QuoteReads(counting.connect, runtime_dsn(dsn), fx_resolver=_never, clock=lambda: NOW)
    out = reads.client_suggestions(tag)
    assert len(out["organizations"]) == 8 and len(out["people"]) == 8
    assert all(len(o["open_cases"]) == 1 for o in out["organizations"])
    assert counting.connections == 1 and counting.statements <= 4, counting.statements


@needs_db
def test_the_workspace_carries_the_preference_values_with_their_fallbacks(world) -> None:
    _, created = _new_quote(world)
    params = _reads(world["dsn"]).workspace(created["quote_id"])["params"]
    assert params == {"markups": {"equipment": "0.40", "accessory": "0.45", "consumable": "0.45",
                                  "spare_part": "0.45", "service": "0.45", "other": "0.45"},
                      "margin_target_min": "0.25", "margin_target_max": "0.45", "price_deviation_warn_pct": None}
    _param(world["dsn"], "default_markup_service", Decimal("0.3"))
    _param(world["dsn"], "price_deviation_warn_pct", Decimal("0.15"))
    params = _reads(world["dsn"]).workspace(created["quote_id"])["params"]
    assert Decimal(params["markups"]["service"]) == Decimal("0.3")
    assert Decimal(params["price_deviation_warn_pct"]) == Decimal("0.15")
    assert params["markups"]["equipment"] == "0.40"


@needs_db
def test_a_person_is_offered_by_an_address_a_quote_may_go_to(world) -> None:
    """Of a person's addresses, one of OrigenLab's or Labdelivery's is never the one shown when the
    person has another (it would hide them from the list)."""
    dsn, tag = world["dsn"], uuid.uuid4().hex[:8]
    person = _owner(dsn, "insert into crm.person (display_name, confirmation) values (%s, 'machine_proposed') "
                         "returning id::text", (f"Carla Prueba {tag}",))[0][0]
    for address in (f"carla-{tag}@origenlab.cl", f"carla-{tag}@labdelivery.cl", f"carla-{tag}@cliente-ficticio.test"):
        _owner(dsn, "insert into crm.contact_point (kind, value_norm, value_display, person_id, usage, confirmation) "
                    "values ('email', %s, %s, %s, 'personal', 'machine_proposed')", (address, address, person))
    [found] = _clients(world, f"Carla Prueba {tag}")["people"]
    assert (found["person_id"], found["email"]) == (person, f"carla-{tag}@cliente-ficticio.test")


@needs_db
def test_the_command_reads_a_client_s_open_cases_as_the_dialog_shows_them(world) -> None:
    """`client_open_cases` (what `create-quote` decides on) and the suggestions agree, uncapped."""
    from origenlab_api.v2.quotes.reads import client_institution, client_open_cases

    dsn, tag = world["dsn"], uuid.uuid4().hex[:8]
    clinic = _org(dsn, "institution", f"Policlínico {tag}")
    for n in range(3):
        _titled_case(world, clinic, f"Caso {n} {tag}")
    elsewhere = _titled_case(world, _org(dsn, "institution", "Otra"), f"Caso ajeno {tag}", participant=False)
    person = _owner(dsn, "insert into crm.person (display_name, confirmation) values (%s, 'machine_proposed') "
                         "returning id::text", (f"Dora Prueba {tag}",))[0][0]
    point = _owner(dsn, "insert into crm.contact_point (kind, value_norm, value_display, person_id, usage, confirmation) "
                        "values ('email', %s, %s, %s, 'personal', 'machine_proposed') returning id::text",
                   (f"dora-{tag}@cliente-ficticio.test", f"dora-{tag}@cliente-ficticio.test", person))[0][0]
    _owner(dsn, "insert into crm.affiliation (person_id, organization_id, valid_from, confirmation) "
                "values (%s, %s, current_date - 10, 'machine_proposed')", (person, clinic))
    _owner(dsn, "insert into crm.opportunity_participant (opportunity_id, person_id, role, is_primary, valid_from, "
                "confirmation) values (%s, %s, 'end_user', false, current_date, 'machine_proposed')", (elsewhere, person))

    [shown] = _clients(world, f"dora-{tag}")["people"]
    with psycopg.connect(runtime_dsn(dsn)) as conn, conn.cursor() as cur:
        institution = client_institution(cur, person_id=person, point_organization_id=None)
        cases = client_open_cases(cur, organization_id=institution["organization_id"], person_id=person,
                                  contact_point_id=point)
    assert institution["organization_id"] == shown["organization"]["id"] == clinic
    assert institution["is_supplier"] is False
    assert [c["id"] for c in cases] == [c["id"] for c in shown["open_cases"]] and len(cases) == 4

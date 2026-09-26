"""What an organization card and a contact card reach through durable commercial rows.

`GET /v2/organizations` ranks institutions by how connected they are to OrigenLab, and the two
cards open into the whole commercial picture: cases, what each case seeks, its quotes (latest
revision only), its activities and the evidence it is linked to. These tests pin the one rule
that makes that picture trustworthy — **every connection is a foreign key someone recorded**:

* an organization reaches a case through `crm.opportunity.organization_id` or a *current*
  `crm.opportunity_organization` row, whatever its role — never by name or mail domain;
* a contact point reaches a case through a *current* `crm.opportunity_participant` row naming
  it, or naming the durable person `crm.contact_point.person_id` records for it;
* counts are computed without the list bound, and withdrawn interests, closed part rows and
  unlinked evidence do not count.

Database-backed and opt-in: they skip without `ORIGENLAB_V2_TEST_DSN` (a maintenance login)
and `ORIGENLAB_V2_API_TEST_DSN` (the `origenlab_api` login). The database is created and
dropped by `v2_command_harness`, so `origenlab_dev` and `origenlab_clean` are unreachable by
construction. Every name and domain is invented; `.test` is a reserved TLD.
"""

from __future__ import annotations

import uuid

import pytest

from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

pytestmark = needs_db

ABSENT = "00000000-0000-4000-8000-0000deadbeef"


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


def _insert(cur, sql: str, params: tuple) -> str:
    cur.execute(sql + " returning id::text", params)
    return cur.fetchone()[0]


@pytest.fixture(scope="module")
def world(disposable_database):
    """One well-connected institution, one decoy that shares its mail domain, and noise.

    Written as `origenlab_owner` in **one** transaction, because the requesting-institution
    agreement between `crm.opportunity.organization_id` and its part row is a deferred
    constraint trigger checked at commit.
    """
    import psycopg

    tag = uuid.uuid4().hex[:10]
    domain = f"uni-{tag}.test"
    w: dict[str, str] = {"tag": tag, "domain": domain}
    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        op_id = w["operator_id"] = _insert(
            cur,
            "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
            "values (gen_random_uuid(), %s, 'Pytest Operador', 'sales', 'active')",
            (f"pytest-conn-{tag}@example.test",),
        )
        sr = w["source_record_id"] = _insert(
            cur,
            "insert into evidence.source_record (kind, dedupe_key, payload, source_uri) "
            "values ('gmail_message', %s, '{}'::jsonb, %s)",
            (f"pytest-conn:{tag}", f"gmail://msg/{tag}"),
        )

        def org(name: str, kind: str = "institution") -> str:
            return _insert(
                cur,
                "insert into crm.organization (kind, name, confirmation) "
                "values (%s, %s, 'confirmed')",
                (kind, name),
            )

        uni = w["uni"] = org(f"Universidad Ficticia {tag}")
        vendor = w["vendor"] = org(f"Fabricante Ficticio {tag}", "company")
        # Same name stem and same mail domain: an identity shortcut would merge it in.
        other = w["other"] = org(f"Universidad Ficticia {tag} Sede Norte")
        cur.execute(
            "insert into crm.organization_domain (organization_id, domain_norm, scope) "
            "values (%s, %s, 'exclusive')",
            (uni, domain),
        )
        product = w["product"] = _insert(
            cur,
            "insert into catalog.product (manufacturer_organization_id, model_number, name) "
            "values (%s, %s, 'Centrífuga de prueba')",
            (vendor, f"CX-{tag}"),
        )

        def person(name: str) -> str:
            return _insert(
                cur,
                "insert into crm.person (display_name, confirmation, confirmed_by_operator_id) "
                "values (%s, 'confirmed', %s)",
                (name, op_id),
            )

        ana = w["ana"] = person("Ana Ficticia")
        beto = w["beto"] = person("Beto Ficticio")
        cur.execute(
            "insert into crm.affiliation (person_id, organization_id, role_title, valid_from, "
            "confirmation, confirmed_by_operator_id) "
            "values (%s, %s, 'Jefa de laboratorio', '2026-01-01', 'confirmed', %s)",
            (ana, uni, op_id),
        )
        cur.execute(
            "insert into crm.affiliation (person_id, organization_id, valid_from, confirmation) "
            "values (%s, %s, '2026-01-01', 'machine_proposed')",
            (beto, uni),
        )

        def channel(local: str, usage: str, person_id, organization_id, dom: str = domain) -> str:
            # The real columns only: crm.contact_point has no confirmed_by_operator_id.
            address = f"{local}@{dom}"
            return _insert(
                cur,
                "insert into crm.contact_point (kind, value_norm, value_display, person_id, "
                "organization_id, usage, confirmation, origin_source_record_id) "
                "values ('email', %s, %s, %s, %s, %s, 'confirmed', %s)",
                (address, address, person_id, organization_id, usage, sr),
            )

        w["cp_ana"] = channel("ana", "work", ana, uni)
        w["cp_desk"] = channel("compras", "shared_mailbox", None, uni)
        # Shares the institution's domain but records no organization: must stay unconnected.
        w["cp_decoy"] = channel("otro", "unattributed", None, None)

        def case(title: str, organization_id=None) -> str:
            return _insert(
                cur,
                "insert into crm.opportunity (organization_id, title, stage, owner_operator_id, "
                "origin_source_record_id) values (%s, %s, 'lead', %s, %s)",
                (organization_id, title, op_id, sr),
            )

        def part(case_id, organization_id, role, *, valid_to=None, machine=False) -> None:
            cur.execute(
                "insert into crm.opportunity_organization (opportunity_id, organization_id, role, "
                "valid_from, valid_to, confirmation, confirmed_by_operator_id) "
                "values (%s, %s, %s, '2026-02-01', %s, %s, %s)",
                (
                    case_id, organization_id, role, valid_to,
                    "machine_proposed" if machine else "confirmed",
                    None if machine else op_id,
                ),
            )

        # Case 1 — the institution asks: the column and the current part row agree.
        c1 = w["case_requesting"] = case(f"Centrífuga para {tag}", uni)
        part(c1, uni, "requesting_institution")
        # Case 2 — two current parts on one case, no requesting column: counted once.
        c2 = w["case_end_user"] = case(f"Reactivos {tag}")
        part(c2, uni, "end_user_institution")
        part(c2, uni, "mentioned", machine=True)
        # Case 3 — a part that was closed: history, not a connection.
        c3 = w["case_closed_part"] = case(f"Financiamiento {tag}")
        part(c3, uni, "funder", valid_to="2026-03-01")
        # Case 4 — the decoy institution's own case.
        c4 = w["case_other"] = case(f"Sede Norte {tag}", other)
        part(c4, other, "requesting_institution")

        def participant(case_id, *, person_id=None, contact_point_id=None, role="technical"):
            cur.execute(
                "insert into crm.opportunity_participant (opportunity_id, person_id, "
                "contact_point_id, role, valid_from, confirmation, confirmed_by_operator_id) "
                "values (%s, %s, %s, %s, '2026-02-01', 'confirmed', %s)",
                (case_id, person_id, contact_point_id, role, op_id),
            )

        participant(c1, contact_point_id=w["cp_ana"])  # through the channel itself
        participant(c2, person_id=ana, role="end_user")  # through the durable person
        participant(c4, contact_point_id=w["cp_decoy"])

        def interest(case_id, *, product_id=None, manufacturer=None, model_text=None,
                     withdrawn=False) -> str:
            return _insert(
                cur,
                "insert into crm.opportunity_interest (opportunity_id, product_id, "
                "manufacturer_organization_id, model_text, quantity, confirmation, "
                "withdrawn_at, withdraw_reason) values (%s, %s, %s, %s, 2, 'machine_proposed', "
                "%s, %s)",
                (
                    case_id, product_id, manufacturer, model_text,
                    "2026-03-02T00:00:00Z" if withdrawn else None,
                    "ya no lo buscan" if withdrawn else None,
                ),
            )

        w["interest_product"] = interest(
            c1, product_id=product, manufacturer=vendor, model_text=f"CX-{tag} 230V"
        )
        w["interest_withdrawn"] = interest(c1, model_text="Modelo retirado", withdrawn=True)
        w["interest_text"] = interest(c2, model_text=f"Kit {tag}")
        interest(c4, model_text="Ruido")

        quote = w["quote"] = _insert(
            cur,
            "insert into crm.quote (opportunity_id, quote_number, created_by_operator_id) "
            "values (%s, %s, %s)",
            (c1, f"PT-{tag}", op_id),
        )
        for revision_no, status in ((1, "void"), (2, "draft")):
            cur.execute(
                "insert into crm.quote_revision (quote_id, revision_no, status, quote_currency, "
                "price_decimals, created_by_operator_id) values (%s, %s, %s, 'CLP', 0, %s)",
                (quote, revision_no, status, op_id),
            )
        cur.execute(
            "insert into crm.quote (opportunity_id, quote_number) values (%s, %s)",
            (c4, f"PX-{tag}"),
        )

        def activity(case_id, kind: str, when: str, summary: str) -> str:
            return _insert(
                cur,
                "insert into crm.activity (opportunity_id, kind, occurred_at, summary, "
                "recorded_by_operator_id) values (%s, %s, %s, %s, %s)",
                (case_id, kind, when, summary, op_id),
            )

        activity(c1, "note", "2026-03-01T10:00:00Z", "Pidió ficha técnica")
        activity(c1, "call", "2026-03-05T10:00:00Z", "Llamada de seguimiento")
        w["latest_activity"] = activity(c2, "meeting", "2026-03-09T15:00:00Z", "Reunión")
        activity(c4, "note", "2026-04-01T10:00:00Z", "Ruido posterior")

        cur.execute(
            "insert into crm.opportunity_evidence (opportunity_id, source_record_id, relation, "
            "linked_by_operator_id) values (%s, %s, 'origin', %s)",
            (c1, sr, op_id),
        )

        def control(scope: str, value: str, kind: str = "block") -> None:
            cur.execute(
                "insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, "
                "source) values (%s, %s, %s, 'all', 'prueba', 'operator_command')",
                (scope, value, kind),
            )

        control("address", f"compras@{domain}")
        control("domain", domain)
        control("address", f"otro@{domain}")  # the decoy's address: not the institution's

        # A draft campaign with a frozen-audience row for the institution's desk and one for the
        # decoy. Real columns only; `draft` needs no content, criteria or approval facts.
        mailbox = _insert(
            cur,
            "insert into comms.mailbox (address_norm) values (%s)",
            (f"envios-{tag}@example.test",),
        )
        campaign = w["campaign"] = _insert(
            cur,
            "insert into outbound.campaign (name, mailbox_id, max_sends, recontact_interval_days, "
            "created_by_operator_id) values (%s, %s, 10, 30, %s)",
            (f"Boletín {tag}", mailbox, op_id),
        )
        for cp_key, local in (("cp_desk", "compras"), ("cp_decoy", "otro")):
            cur.execute(
                "insert into outbound.campaign_recipient (campaign_id, address_norm, "
                "contact_point_id) values (%s, %s, %s)",
                (campaign, f"{local}@{domain}", w[cp_key]),
            )
        conn.commit()
    return w


@pytest.fixture(scope="module")
def repo(disposable_database):
    import psycopg

    from origenlab_api.v2.repository import V2Repository

    return V2Repository(psycopg.connect, runtime_dsn(disposable_database))


def _snapshot(disposable_database) -> dict[str, str]:
    """A content digest of every table the read repository can see."""
    import psycopg

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "select table_schema, table_name from information_schema.tables "
            "where table_schema in ('crm', 'catalog', 'evidence', 'outbound', 'platform', 'comms') "
            "and table_type = 'BASE TABLE' order by 1, 2"
        )
        tables = cur.fetchall()
        digest = {}
        for schema, table in tables:
            cur.execute(
                f'select count(*), md5(coalesce(string_agg(t::text, \'|\' order by t::text), \'\')) '
                f'from "{schema}"."{table}" t'
            )
            count, md5 = cur.fetchone()
            digest[f"{schema}.{table}"] = f"{count}:{md5}"
        conn.rollback()
    return digest


# ------------------------------------------------------------------ organization list


def test_the_most_connected_organization_comes_first_with_truthful_counts(repo, world) -> None:
    page = repo.organizations(q=None, limit=50, offset=0)
    first = page.items[0]
    assert first["organization_id"] == world["uni"]
    assert first["contact_point_count"] == 2  # the decoy address is not recorded against it
    assert first["confirmed_people_count"] == 1  # the machine-proposed affiliation is not
    assert first["case_count"] == 2  # requesting + end user; the closed funder part is not
    assert first["open_case_count"] == 2
    assert first["interest_count"] == 2  # the withdrawn interest is not
    assert first["quote_count"] == 1
    assert first["activity_count"] == 3
    assert first["last_activity_at"].isoformat().startswith("2026-03-09T15:00")
    for key in ("interest_count", "quote_count", "activity_count"):
        assert type(first[key]) is int

    by_id = {row["organization_id"]: row for row in page.items}
    assert by_id[world["other"]]["case_count"] == 1
    assert by_id[world["vendor"]]["case_count"] == 0  # a product's maker is not a case part


# ------------------------------------------------------------------ organization card


def test_the_organization_card_reaches_every_durable_connection(repo, world) -> None:
    card = repo.organization_card(world["uni"])
    assert card is not None

    assert {cp["contact_point_id"] for cp in card["contact_points"]} == {
        world["cp_ana"], world["cp_desk"],
    }
    assert {p["person_id"] for p in card["people"]} == {world["ana"], world["beto"]}
    assert card["counts"]["people"] == 2

    summary = card["connection_summary"]
    assert summary["cases"] == 2
    assert summary["open_cases"] == 2
    assert summary["interests"] == 2
    assert summary["quotes"] == 1
    assert summary["activities"] == 3
    assert summary["case_evidence"] == 1
    assert summary["confirmed_people"] == 1
    assert summary["last_activity_at"].isoformat().startswith("2026-03-09T15:00")

    cases = {c["opportunity_id"]: c for c in card["cases"]}
    assert set(cases) == {world["case_requesting"], world["case_end_user"]}
    assert cases[world["case_requesting"]]["roles"] == ["requesting_institution"]
    assert cases[world["case_requesting"]]["requesting_organization_id"] == world["uni"]
    # Two parts on one case: one case, both parts, in CASE_ROLE_ORDER.
    assert cases[world["case_end_user"]]["roles"] == ["end_user_institution", "mentioned"]
    assert cases[world["case_end_user"]]["requesting_organization_id"] is None

    interests = {i["opportunity_interest_id"]: i for i in card["interests"]}
    assert set(interests) == {world["interest_product"], world["interest_text"]}
    catalogued = interests[world["interest_product"]]
    assert catalogued["product_id"] == world["product"]
    assert catalogued["product_name"] == "Centrífuga de prueba"
    assert catalogued["product_model_number"] == f"CX-{world['tag']}"
    assert catalogued["manufacturer_organization_id"] == world["vendor"]
    assert catalogued["model_text"] == f"CX-{world['tag']} 230V"
    assert interests[world["interest_text"]]["product_id"] is None

    [quote] = card["quotes"]
    assert quote["quote_id"] == world["quote"]
    assert quote["latest_revision_no"] == 2
    assert quote["latest_status"] == "draft"  # not the void revision 1
    assert quote["revision_count"] == 2

    assert [a["kind"] for a in card["activities"]] == ["meeting", "call", "note"]
    assert card["activities"][0]["activity_id"] == world["latest_activity"]

    [link] = card["case_evidence"]
    assert link["relation"] == "origin"
    assert link["subject_id"] == world["source_record_id"]


def test_the_organization_card_reports_marketing_controls_it_owns_and_no_others(
    repo, world
) -> None:
    card = repo.organization_card(world["uni"])
    assert [c["value_norm"] for c in card["address_controls"]] == [f"compras@{world['domain']}"]
    assert [c["value_norm"] for c in card["domain_controls"]] == [world["domain"]]
    # The desk's audience row is the institution's; the decoy's, on the same domain, is not.
    [row] = card["marketing"]
    assert row["contact_point_id"] == world["cp_desk"]
    assert row["campaign_name"] == f"Boletín {world['tag']}"
    assert row["campaign_status"] == "draft"
    assert row["recipient_state"] == "snapshotted"
    assert row["attempt_count"] == 0
    assert card["counts"]["address_controls"] == 1
    assert card["counts"]["domain_controls"] == 1
    assert card["counts"]["marketing"] == 1


def test_a_decoy_sharing_name_and_domain_reaches_only_its_own_case(repo, world) -> None:
    card = repo.organization_card(world["other"])
    assert [c["opportunity_id"] for c in card["cases"]] == [world["case_other"]]
    assert card["connection_summary"]["quotes"] == 1
    assert card["contact_points"] == []
    assert card["domain_controls"] == []  # it recorded no domain, so no domain is its
    assert card["marketing"] == []  # the decoy address's audience row is not its either
    assert card["counts"]["marketing"] == 0


def test_every_bounded_list_sits_beside_a_count_at_least_as_large(repo, world) -> None:
    from origenlab_api.v2.repository import V2Repository

    card = repo.organization_card(world["uni"])
    for key in ("cases", "interests", "quotes", "activities", "case_evidence"):
        assert len(card[key]) <= V2Repository.CARD_CHILD_LIMIT
        assert card["connection_summary"][key] >= len(card[key])


# ----------------------------------------------------------------------- contact card


def test_a_contact_reaches_cases_through_its_participant_rows_and_its_person(repo, world) -> None:
    card = repo.contact_card(world["cp_ana"])
    assert card is not None
    assert card["person_id"] == world["ana"]
    assert card["organization_id"] == world["uni"]
    assert [a["organization_id"] for a in card["affiliations"]] == [world["uni"]]

    cases = {c["opportunity_id"]: c for c in card["cases"]}
    assert set(cases) == {world["case_requesting"], world["case_end_user"]}
    assert cases[world["case_requesting"]]["roles"] == ["technical"]
    assert cases[world["case_end_user"]]["roles"] == ["end_user"]

    summary = card["connection_summary"]
    assert summary["cases"] == 2
    assert summary["interests"] == 2
    assert summary["quotes"] == 1
    assert summary["activities"] == 3
    assert card["quotes"][0]["latest_status"] == "draft"


def test_a_shared_desk_with_no_participant_row_reaches_no_case(repo, world) -> None:
    card = repo.contact_card(world["cp_desk"])
    assert card["person_id"] is None
    assert card["organization_id"] == world["uni"]
    for key in ("cases", "interests", "quotes", "activities", "case_evidence"):
        assert card[key] == []
        assert card["connection_summary"][key] == 0
    assert [(c["scope"], c["control_kind"]) for c in card["address_controls"]] == [
        ("address", "block")
    ]
    assert card["counts"]["address_controls"] == 1
    assert [(m["campaign_name"], m["recipient_state"]) for m in card["marketing"]] == [
        (f"Boletín {world['tag']}", "snapshotted")
    ]
    assert card["counts"]["marketing"] == 1


def test_an_address_on_the_institutions_domain_is_not_the_institutions(repo, world) -> None:
    card = repo.contact_card(world["cp_decoy"])
    assert card["person_id"] is None
    assert card["organization_id"] is None  # nothing inferred from the domain
    assert card["affiliations"] == []
    assert [c["opportunity_id"] for c in card["cases"]] == [world["case_other"]]


# --------------------------------------------------------------------- absence and writes


def test_a_missing_organization_or_contact_is_none(repo, world) -> None:
    assert repo.organization_card(ABSENT) is None
    assert repo.contact_card(ABSENT) is None
    assert repo.organization_cases(ABSENT, limit=10, offset=0) is None


def test_reading_every_card_writes_nothing(disposable_database, repo, world) -> None:
    before = _snapshot(disposable_database)
    repo.organizations(q=None, limit=50, offset=0)
    repo.organizations(q="ficticia", limit=50, offset=0)
    for org in (world["uni"], world["vendor"], world["other"]):
        repo.organization_card(org)
    for cp in (world["cp_ana"], world["cp_desk"], world["cp_decoy"]):
        repo.contact_card(cp)
    assert _snapshot(disposable_database) == before


# ------------------------------------------------------------------ list filters


def test_organization_filters_keep_only_rows_whose_own_metrics_qualify(repo, world) -> None:
    def ids(**kwargs) -> set[str]:
        page = repo.organizations(q=f"{world['tag']}", limit=50, offset=0, **kwargs)
        assert page.total == len(page.items)
        return {row["organization_id"] for row in page.items}

    everyone = {world["uni"], world["other"], world["vendor"]}
    assert ids() == everyone
    assert ids(having=("cases",)) == {world["uni"], world["other"]}
    assert ids(having=("contacts",)) == {world["uni"]}  # the decoy address is nobody's
    assert ids(having=("people",)) == {world["uni"]}
    assert ids(having=("interests",)) == {world["uni"], world["other"]}
    assert ids(having=("quotes", "contacts")) == {world["uni"]}
    # A product's manufacturer holds no case part, so no filter reaches it through cases.
    assert world["vendor"] not in ids(having=("open_cases",))
    # The fixture's activities are dated 2026-03/04, so a window reaching back to them keeps
    # both institutions with cases and one of a day keeps none.
    assert ids(active_within_days=3650) == {world["uni"], world["other"]}
    assert ids(active_within_days=1) == set()


@pytest.fixture(scope="module")
def segments_world(disposable_database, world):
    """A customer, a supplier-and-manufacturer, a funder, a recorded supplier and a distributor
    that is both — each on its own tag so the counts below are the whole segment."""
    import psycopg

    tag = f"seg{uuid.uuid4().hex[:8]}"
    w: dict[str, str] = {"tag": tag}
    op_id, sr = world["operator_id"], world["source_record_id"]
    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")

        def org(name: str) -> str:
            return _insert(
                cur,
                "insert into crm.organization (kind, name, confirmation) "
                "values ('unknown', %s, 'confirmed')",
                (f"{name} {tag}",),
            )

        def part(case_id, organization_id, role, **extra) -> None:
            columns = ", ".join(extra)
            cur.execute(
                "insert into crm.opportunity_organization (opportunity_id, organization_id, "
                f"role, valid_from, confirmation, confirmed_by_operator_id"
                f"{', ' + columns if columns else ''}) "
                f"values (%s, %s, %s, '2026-02-01', 'confirmed', %s"
                f"{', %s' * len(extra)})",
                (case_id, organization_id, role, op_id, *extra.values()),
            )

        customer = w["customer"] = org("Universidad Solicitante")
        maker = w["maker"] = org("Fabricante Ultrasonidos")
        funder = w["funder"] = org("Fondo Concursable")
        recorded = w["recorded_supplier"] = org("Distribuidora Registrada")
        both = w["both"] = org("Distribuidora que también compra")

        case = _insert(
            cur,
            "insert into crm.opportunity (organization_id, title, stage, owner_operator_id, "
            "origin_source_record_id) values (%s, %s, 'lead', %s, %s)",
            (customer, f"Sonicador {tag}", op_id, sr),
        )
        part(case, customer, "requesting_institution")
        part(case, maker, "supplier")
        part(case, maker, "manufacturer")
        part(case, funder, "funder")
        # Only a relationship to OrigenLab, no case part: still a supplier, never a customer.
        cur.execute(
            "insert into crm.organization_relationship (organization_id, role, valid_from) "
            "values (%s, 'supplier', '2025-01-01')",
            (recorded,),
        )
        # A recorded supplier that is also a recorded customer sits in both segments.
        for role in ("supplier", "customer"):
            cur.execute(
                "insert into crm.organization_relationship (organization_id, role, valid_from) "
                "values (%s, %s, '2025-01-01')",
                (both, role),
            )
        conn.commit()
    return w


def test_a_supplier_on_a_case_is_never_listed_as_a_customer(repo, segments_world) -> None:
    w = segments_world

    def page(segment=None):
        return repo.organizations(q=w["tag"], limit=50, offset=0, segment=segment)

    def ids(segment=None) -> set[str]:
        return {row["organization_id"] for row in page(segment).items}

    assert ids("customers") == {w["customer"], w["both"]}
    assert ids("suppliers") == {w["maker"], w["recorded_supplier"], w["both"]}
    assert ids("others") == {w["funder"]}
    assert page().facets == {"all": 5, "customers": 2, "suppliers": 3, "others": 1}
    # Facets are counted under the same filters as the list, whichever segment is open.
    assert page("suppliers").facets == page().facets

    rows = {row["organization_id"]: row for row in page().items}
    maker = rows[w["maker"]]
    assert maker["case_count"] == 1  # two parts on one case is one case
    assert maker["cases_as_supplier"] == 1
    assert maker["cases_as_manufacturer"] == 1
    assert maker["cases_as_requesting_institution"] == 0
    assert rows[w["customer"]]["cases_as_requesting_institution"] == 1
    assert rows[w["funder"]]["cases_as_funder"] == 1
    assert rows[w["recorded_supplier"]]["relationship_roles"] == ["supplier"]
    assert rows[w["both"]]["relationship_roles"] == ["customer", "supplier"]

    # The default order puts the institution that asks first, not whoever holds most parts.
    assert page().items[0]["organization_id"] == w["customer"]
    assert page("suppliers").items[0]["organization_id"] == w["maker"]


def test_role_counts_read_only_current_parts(repo, world) -> None:
    rows = {
        row["organization_id"]: row
        for row in repo.organizations(q=world["tag"], limit=50, offset=0).items
    }
    # Case 2 is the university's as end user and (machine-proposed) mention; case 3's funder
    # part is closed, so it is history and counts nowhere.
    uni = rows[world["uni"]]
    assert uni["cases_as_requesting_institution"] == 1
    assert uni["cases_as_end_user_institution"] == 1
    assert uni["cases_as_mentioned"] == 1
    assert uni["cases_as_funder"] == 0


def test_contact_search_reaches_recorded_names_and_never_a_shared_domain(repo, world) -> None:
    def ids(**kwargs) -> set[str]:
        page = repo.contacts(limit=50, offset=0, **kwargs)
        assert page.total == len(page.items)
        return {row["contact_point_id"] for row in page.items}

    # The institution's name finds the channels recorded against it, not the decoy address
    # that merely sits on its domain.
    assert ids(q=f"Universidad Ficticia {world['tag']}") == {world["cp_ana"], world["cp_desk"]}
    assert ids(q="ana ficticia") == {world["cp_ana"]}
    assert ids(q=world["domain"]) == {world["cp_ana"], world["cp_desk"], world["cp_decoy"]}

    assert ids(q=world["domain"], identity="person") == {world["cp_ana"]}
    assert ids(q=world["domain"], identity="organization_mailbox") == {world["cp_desk"]}
    assert ids(q=world["domain"], identity="unattributed") == {world["cp_decoy"]}
    assert ids(q=world["domain"], with_cases=True) == {world["cp_ana"], world["cp_decoy"]}


def test_contact_rows_carry_participation_and_control_counts(repo, world) -> None:
    page = repo.contacts(q=world["domain"], limit=50, offset=0)
    rows = {row["contact_point_id"]: row for row in page.items}
    assert rows[world["cp_ana"]]["case_count"] == 2  # the channel's row and its person's
    assert rows[world["cp_desk"]]["case_count"] == 0
    assert rows[world["cp_desk"]]["address_control_count"] == 1
    assert rows[world["cp_decoy"]]["case_count"] == 1
    # Recorded identities first: the person, then the institution's mailbox, then the rest.
    assert [row["contact_point_id"] for row in page.items] == [
        world["cp_ana"], world["cp_desk"], world["cp_decoy"],
    ]


def test_case_rows_carry_every_current_part_quote_state_and_last_activity(repo, world) -> None:
    page = repo.cases(stage=None, open_only=False, limit=50, offset=0,
                      organization_q=world["tag"])
    rows = {row["opportunity_id"]: row for row in page.items}

    c1 = rows[world["case_requesting"]]
    assert [(p["organization_id"], p["role"]) for p in c1["participants"]] == [
        (world["uni"], "requesting_institution"),
    ]
    assert c1["interest_labels"] == ["Centrífuga de prueba"]  # the withdrawn one is not
    assert c1["quote_count"] == 1
    assert c1["latest_quote_status"] == "draft"
    assert c1["last_activity_at"].isoformat().startswith("2026-03-05T10:00")

    c2 = rows[world["case_end_user"]]
    assert [p["role"] for p in c2["participants"]] == ["end_user_institution", "mentioned"]
    assert c2["requesting_organization_id"] is None
    assert c2["quote_count"] == 0 and c2["latest_quote_status"] is None

    # The closed funder part is history: the case is listed, holding no current part.
    everything = repo.cases(stage=None, open_only=False, limit=50, offset=0)
    closed = {row["opportunity_id"]: row for row in everything.items}[world["case_closed_part"]]
    assert closed["participants"] == []
    assert world["case_closed_part"] not in rows  # organization_q reads current parts only


def test_case_filters(repo, world) -> None:
    def ids(**kwargs) -> set[str]:
        base = {"stage": None, "open_only": False, "limit": 50, "offset": 0}
        page = repo.cases(**{**base, **kwargs})
        assert page.total == len(page.items)
        return {row["opportunity_id"] for row in page.items}

    mine = {world["case_requesting"], world["case_end_user"], world["case_closed_part"],
            world["case_other"]}
    assert mine <= ids(stage=("lead", "qualifying"))
    assert ids(organization_id=world["uni"]) == {world["case_requesting"], world["case_end_user"]}
    assert ids(organization_id=world["vendor"]) == set()
    assert ids(organization_q="Sede Norte") >= {world["case_other"]}
    assert ids(interest_q=f"kit {world['tag']}") == {world["case_end_user"]}
    assert ids(interest_q="modelo retirado") & mine == set()  # withdrawn interests don't match
    assert ids(interest_q=f"cx-{world['tag']}") == {world["case_requesting"]}
    assert ids(quote_state="draft") & mine == {world["case_requesting"]}
    assert ids(quote_state="void") & mine == set()  # revision 1 is not the latest
    assert ids(quote_state="any") & mine == {world["case_requesting"], world["case_other"]}
    assert world["case_requesting"] not in ids(quote_state="none")
    assert ids(active_within_days=1) & mine == set()


def test_filtered_reads_write_nothing(disposable_database, repo, world) -> None:
    before = _snapshot(disposable_database)
    repo.organizations(q=None, limit=50, offset=0, having=("cases", "quotes"),
                       active_within_days=30)
    repo.organizations(q=None, limit=50, offset=0, segment="suppliers")
    repo.contacts(q=world["domain"], limit=50, offset=0, identity="person", with_cases=True)
    repo.cases(stage=("lead",), open_only=True, limit=50, offset=0,
               organization_id=world["uni"], interest_q="kit", quote_state="any",
               active_within_days=30)
    assert _snapshot(disposable_database) == before

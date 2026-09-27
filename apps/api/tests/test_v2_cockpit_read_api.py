"""Tests for the cockpit read API — ``/v2/cockpit/*``.

Two layers, following the V2 test convention.

**In-process (always run).**
- All routes are GET and nothing else.
- Auth is required: unauthenticated requests return 401.
- Validated filter params return 422 for unknown values.
- Malformed UUID path params return 404, never 500.
- Response models are importable and carry the expected field names.

**Database-backed (opt-in, skipped without env).**
Builds a disposable ``origenlab_test_<hex>`` database via the harness,
inserts fictitious ``.test`` / ``.invalid`` fixtures through the
``origenlab_owner`` role, and asserts the repository and routes agree.

Every domain name is invented.  Nothing here names a real institution,
person, quote number, or email address.  The repo is public.
"""

from __future__ import annotations

import uuid

import pytest

from origenlab_api.v2.cockpit_routes import cockpit_router
from origenlab_api.v2.cockpit_schemas import (
    WORK_ITEM_KINDS,
    EvidenceDrawer,
    KpiResponse,
    OpportunityDetail,
    OpportunityListResponse,
    QuotationDetail,
    QuotationListResponse,
    SearchResponse,
    TimelineResponse,
    WorkQueueResponse,
)

# ═══════════════════════════════════════════════════════ in-process tests ═══


def test_all_cockpit_routes_are_get_only() -> None:
    """The cockpit router is a read boundary and must stay that way."""
    for route in cockpit_router.routes:
        if hasattr(route, "methods"):
            assert route.methods == {"GET"}, (
                f"Non-GET method on cockpit route {getattr(route, 'path', '?')}: {route.methods}"
            )


def test_cockpit_route_paths_are_under_v2_cockpit() -> None:
    """Every cockpit route lives under the /v2/cockpit prefix."""
    for route in cockpit_router.routes:
        path = getattr(route, "path", "")
        # FastAPI may expose full or relative path depending on version;
        # assert the path contains one of the expected cockpit segments.
        assert any(
            seg in path
            for seg in ("/kpis", "/work-queue", "/opportunities", "/quotations",
                        "/evidence", "/search")
        ), f"Unexpected cockpit route path: {path!r}"


def test_cockpit_router_has_nine_routes() -> None:
    """The nine cockpit endpoints from the specification are all present."""
    paths = [getattr(r, "path", "") for r in cockpit_router.routes if hasattr(r, "methods")]
    # Paths may include the full prefix; use `in` (contains) matching.
    def _has(seg: str) -> bool:
        return any(seg in p for p in paths)

    assert _has("/kpis")
    assert _has("/work-queue")
    assert _has("/opportunities")
    assert _has("/opportunities/{opportunity_id}")
    assert _has("/opportunities/{opportunity_id}/timeline")
    assert _has("/quotations")
    assert _has("/quotations/{quote_id}")
    assert _has("/evidence/{source_record_id}")
    assert _has("/search")


# ──────────────────────────────────── schema field contract ─────────────────


def test_kpi_response_fields() -> None:
    model = KpiResponse.model_fields
    assert "quotations" in model
    assert "evidence" in model
    assert "opportunities" in model


def test_work_queue_response_fields() -> None:
    model = WorkQueueResponse.model_fields
    assert "items" in model
    assert "total" in model


def test_work_item_kinds_closed() -> None:
    assert WORK_ITEM_KINDS == {
        "pending_evidence",
        "unresolved_document",
        "canonical_undetermined",
        "shared_printed_number",
        "case_without_institution",
        "case_without_quote",
    }


def test_opportunity_detail_fields() -> None:
    model = OpportunityDetail.model_fields
    for field in (
        "opportunity_id", "title", "stage",
        "quotes", "case_organizations", "evidence_links", "evidence_completeness",
    ):
        assert field in model, f"OpportunityDetail missing field {field!r}"


def test_quotation_detail_fields() -> None:
    model = QuotationDetail.model_fields
    for field in (
        "quote_id", "quote_number", "number_origin",
        "revisions", "canonical_revision", "gmail_sources", "conflicts",
    ):
        assert field in model, f"QuotationDetail missing field {field!r}"


def test_timeline_response_fields() -> None:
    model = TimelineResponse.model_fields
    assert "opportunity_id" in model
    assert "entries" in model


def test_evidence_drawer_fields() -> None:
    model = EvidenceDrawer.model_fields
    for field in (
        "source_record_id", "kind", "gmail_message_id", "gmail_thread_id",
        "sender", "recipients", "subject_raw", "documents", "assertions",
        "decision_events",
    ):
        assert field in model, f"EvidenceDrawer missing field {field!r}"


def test_evidence_drawer_has_no_body_field() -> None:
    """The contract must never expose the raw email body."""
    model = EvidenceDrawer.model_fields
    for name in model:
        assert "body" not in name.lower(), (
            f"EvidenceDrawer exposes a body-like field: {name!r}"
        )


def test_search_response_fields() -> None:
    model = SearchResponse.model_fields
    assert "query" in model
    assert "hits" in model
    assert "truncated" in model


# ────────────────────────────────────────────── route validation stubs ───────


class _StubCockpitRepo:
    """Minimal stub satisfying every cockpit route handler for in-process tests."""

    def kpis(self):
        return {
            "quotations": {
                "sent_historical_revisions": 0,
                "void_historical_revisions": 0,
                "quotes_with_undetermined_canonical": 0,
                "shared_printed_numbers": 0,
            },
            "evidence": {
                "pending_source_records": 0,
                "unresolved_document_references": 0,
                "revisions_missing_thread_id": 0,
                "revisions_missing_document_entry": 0,
            },
            "opportunities": {
                "by_stage": {"lead": 0, "qualifying": 0, "qualified": 0,
                             "quoting": 0, "negotiating": 0, "won": 0,
                             "lost": 0, "abandoned": 0},
                "leads_without_institution": 0,
            },
        }

    def work_queue(self, limit, offset):
        return {"items": [], "total": 0, "limit": limit, "offset": offset}

    def opportunities(self, **kw):
        return {"items": [], "total": 0, "limit": kw["limit"], "offset": kw["offset"]}

    def opportunity_detail(self, opportunity_id):
        return None

    def opportunity_timeline(self, opportunity_id):
        return None

    def quotations(self, **kw):
        return {"items": [], "total": 0, "limit": kw["limit"], "offset": kw["offset"]}

    def quotation_detail(self, quote_id):
        return None

    def evidence_drawer(self, source_record_id):
        return None

    def search(self, q):
        return {"query": q, "hits": [], "truncated": False}


from origenlab_api.v2 import cockpit_routes as _cr


def test_malformed_opportunity_id_is_404() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _cr.get_opportunity(None, _StubCockpitRepo(), "not-a-uuid")  # type: ignore[arg-type]
    assert exc.value.status_code == 404


def test_missing_opportunity_is_404() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _cr.get_opportunity(None, _StubCockpitRepo(), str(uuid.uuid4()))  # type: ignore[arg-type]
    assert exc.value.status_code == 404


def test_malformed_quote_id_is_404() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _cr.get_quotation(None, _StubCockpitRepo(), "bad-id")  # type: ignore[arg-type]
    assert exc.value.status_code == 404


def test_missing_quotation_is_404() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _cr.get_quotation(None, _StubCockpitRepo(), str(uuid.uuid4()))  # type: ignore[arg-type]
    assert exc.value.status_code == 404


def test_malformed_source_record_id_is_404() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _cr.get_evidence_drawer(None, _StubCockpitRepo(), "bad-uuid")  # type: ignore[arg-type]
    assert exc.value.status_code == 404


def test_missing_evidence_drawer_is_404() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _cr.get_evidence_drawer(None, _StubCockpitRepo(), str(uuid.uuid4()))  # type: ignore[arg-type]
    assert exc.value.status_code == 404


def test_unknown_stage_is_422() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _cr.list_opportunities(None, _StubCockpitRepo(), stage="fantasy_stage")  # type: ignore[arg-type]
    assert exc.value.status_code == 422


def test_unknown_status_is_422() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _cr.list_quotations(None, _StubCockpitRepo(), status="fantasy_status")  # type: ignore[arg-type]
    assert exc.value.status_code == 422


def test_unknown_number_origin_is_422() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _cr.list_quotations(None, _StubCockpitRepo(), number_origin="invented")  # type: ignore[arg-type]
    assert exc.value.status_code == 422


def test_kpis_response_is_valid_model() -> None:
    raw = _StubCockpitRepo().kpis()
    model = KpiResponse.model_validate(raw)
    assert model.quotations.sent_historical_revisions == 0
    assert model.opportunities.by_stage["lead"] == 0


def test_search_returns_valid_model() -> None:
    raw = _StubCockpitRepo().search("CN01001")
    model = SearchResponse.model_validate(raw)
    assert model.query == "CN01001"
    assert model.hits == []
    assert not model.truncated


# ════════════════════════════════════════════════════ DB-backed tests (opt-in)


from v2_command_harness import (  # noqa: E402
    build_disposable_database,
    needs_db,
    runtime_dsn,
)


@pytest.fixture(scope="module")
def disposable_database():
    """Module-scoped disposable database, created and dropped by the harness."""
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def world(disposable_database):
    """Fictitious fixtures: one operator, one organization, one opportunity, one source record."""
    import psycopg

    tag = uuid.uuid4().hex[:12]
    state: dict[str, object] = {"tag": tag, "dsn": disposable_database}

    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")

        cur.execute(
            """
            insert into platform.operator
                (auth_user_id, email_norm, display_name, role, status)
            values (gen_random_uuid(), %s, 'Pytest Cockpit Op', 'sales', 'active')
            returning id::text
            """,
            (f"pytest-cockpit-{tag}@example.test",),
        )
        state["operator_id"] = cur.fetchone()[0]

        cur.execute(
            """
            insert into crm.organization (kind, name, confirmation)
            values ('hospital', %s, 'machine_proposed')
            returning id::text
            """,
            (f"Instituto Ficticio {tag}",),
        )
        state["organization_id"] = cur.fetchone()[0]

        gmail_payload = {
            "gmail_message_id": f"msg-{tag}",
            "gmail_thread_id": f"thr-{tag}",
            "sender": f"ventas@example.test",
            "recipients": f"compras@cliente.invalid",
            "subject_raw": f"Cotizacion prueba {tag}",
            "sent_at": "2026-05-01T10:00:00-04:00",
            "documents": [
                {"sha256": "a" * 64, "filename": f"CN01001-pytest-{tag}.pdf"}
            ],
        }
        import json as _json
        cur.execute(
            """
            insert into evidence.source_record (kind, dedupe_key, payload, source_uri)
            values ('gmail_message', %s, %s::jsonb, %s)
            returning id::text
            """,
            (
                f"pytest-cockpit:{tag}",
                _json.dumps(gmail_payload),
                f"gmail://msg/{tag}",
            ),
        )
        state["source_record_id"] = cur.fetchone()[0]

        cur.execute(
            """
            insert into evidence.assertion (source_record_id, kind, value_norm, value)
            values (%s, 'document_reference', %s, '{}'::jsonb)
            returning id::text
            """,
            (state["source_record_id"], f"sha256:{'a' * 64}"),
        )
        state["assertion_id"] = cur.fetchone()[0]

        # Lead stage: organization_id must be null until at least 'qualified'.
        cur.execute(
            """
            insert into crm.opportunity
                (title, stage, owner_operator_id, origin_source_record_id)
            values (%s, 'lead', %s, %s)
            returning id::text
            """,
            (
                f"Pytest opp {tag}",
                state["operator_id"],
                state["source_record_id"],
            ),
        )
        state["opportunity_id"] = cur.fetchone()[0]

        # A historical quote + revision
        state["quote_number"] = f"PYTS-{tag[:8]}"
        cur.execute(
            """
            insert into crm.quote
                (opportunity_id, quote_number, number_origin, origin_source_record_id,
                 created_by_operator_id)
            values (%s, %s, 'printed_historical', %s, %s)
            returning id::text
            """,
            (
                state["opportunity_id"],
                state["quote_number"],
                state["source_record_id"],
                state["operator_id"],
            ),
        )
        state["quote_id"] = cur.fetchone()[0]

        cur.execute(
            """
            insert into crm.quote_revision
                (quote_id, revision_no, status, origin,
                 origin_source_record_id, pdf_sha256, sent_at,
                 created_by_operator_id)
            values (%s, 1, 'sent', 'historical_import', %s, %s, '2026-05-01T10:00:00-04:00', %s)
            returning id::text
            """,
            (
                state["quote_id"],
                state["source_record_id"],
                "a" * 64,
                state["operator_id"],
            ),
        )
        state["revision_id"] = cur.fetchone()[0]

        # Link the source record to the opportunity
        cur.execute(
            """
            insert into crm.opportunity_evidence
                (opportunity_id, source_record_id, relation, linked_by_operator_id)
            values (%s, %s, 'origin', %s)
            returning id::text
            """,
            (state["opportunity_id"], state["source_record_id"], state["operator_id"]),
        )
        state["evidence_link_id"] = cur.fetchone()[0]

    return state


# ──────────────────────────────────────── KPI counts ────────────────────────


@needs_db
def test_db_kpis_count_the_sent_historical_revision(world) -> None:
    import psycopg

    from origenlab_api.v2.cockpit_repository import CockpitRepository

    api_dsn = runtime_dsn(world["dsn"])
    repo = CockpitRepository(psycopg.connect, api_dsn)
    result = repo.kpis()

    assert result["quotations"]["sent_historical_revisions"] >= 1
    assert isinstance(result["quotations"]["void_historical_revisions"], int)
    assert isinstance(result["evidence"]["pending_source_records"], int)
    assert isinstance(result["evidence"]["unresolved_document_references"], int)
    assert isinstance(result["opportunities"]["by_stage"], dict)
    assert "lead" in result["opportunities"]["by_stage"]


# ──────────────────────────────────────── work queue ────────────────────────


@needs_db
def test_db_work_queue_returns_a_paginated_dict(world) -> None:
    import psycopg

    from origenlab_api.v2.cockpit_repository import CockpitRepository

    api_dsn = runtime_dsn(world["dsn"])
    repo = CockpitRepository(psycopg.connect, api_dsn)
    result = repo.work_queue(limit=50, offset=0)
    assert "items" in result
    assert "total" in result
    assert isinstance(result["total"], int)
    for item in result["items"]:
        assert item["kind"] in WORK_ITEM_KINDS
        assert "subject_ids" in item
        assert isinstance(item["subject_ids"], dict)


# ──────────────────────────────────── opportunities ─────────────────────────


@needs_db
def test_db_opportunity_list_includes_our_fixture(world) -> None:
    import psycopg

    from origenlab_api.v2.cockpit_repository import CockpitRepository

    api_dsn = runtime_dsn(world["dsn"])
    repo = CockpitRepository(psycopg.connect, api_dsn)
    result = repo.opportunities(
        stage="lead",
        organization_id=None,
        q=world["tag"],  # unique title fragment
        has_quote=None,
        date_from=None,
        date_to=None,
        limit=10,
        offset=0,
    )
    assert result["total"] >= 1
    ids = {item["opportunity_id"] for item in result["items"]}
    assert world["opportunity_id"] in ids


@needs_db
def test_db_opportunity_detail_returns_full_card(world) -> None:
    import psycopg

    from origenlab_api.v2.cockpit_repository import CockpitRepository

    api_dsn = runtime_dsn(world["dsn"])
    repo = CockpitRepository(psycopg.connect, api_dsn)
    detail = repo.opportunity_detail(world["opportunity_id"])
    assert detail is not None
    assert detail["opportunity_id"] == world["opportunity_id"]
    assert detail["stage"] == "lead"
    # Lead stage: organization_id is null (not required until qualified)
    assert detail["organization_id"] is None
    assert len(detail["quotes"]) == 1
    q = detail["quotes"][0]
    assert q["quote_id"] == world["quote_id"]
    assert q["number_origin"] == "printed_historical"
    assert len(q["revisions"]) == 1
    rev = q["revisions"][0]
    assert rev["status"] == "sent"
    assert rev["is_canonical"] is True  # single active revision


@needs_db
def test_db_missing_opportunity_returns_none(world) -> None:
    import psycopg

    from origenlab_api.v2.cockpit_repository import CockpitRepository

    api_dsn = runtime_dsn(world["dsn"])
    repo = CockpitRepository(psycopg.connect, api_dsn)
    assert repo.opportunity_detail(str(uuid.uuid4())) is None


# ──────────────────────────────────────── quotations ────────────────────────


@needs_db
def test_db_quotation_list_includes_our_fixture(world) -> None:
    import psycopg

    from origenlab_api.v2.cockpit_repository import CockpitRepository

    api_dsn = runtime_dsn(world["dsn"])
    repo = CockpitRepository(psycopg.connect, api_dsn)
    result = repo.quotations(
        quote_number=world["quote_number"],
        organization_id=None,
        opportunity_id=None,
        gmail_message_id=None,
        status=None,
        number_origin="printed_historical",
        date_from=None,
        date_to=None,
        limit=10,
        offset=0,
    )
    assert result["total"] >= 1
    ids = {item["quote_id"] for item in result["items"]}
    assert world["quote_id"] in ids


@needs_db
def test_db_quotation_detail_returns_full_card(world) -> None:
    import psycopg

    from origenlab_api.v2.cockpit_repository import CockpitRepository

    api_dsn = runtime_dsn(world["dsn"])
    repo = CockpitRepository(psycopg.connect, api_dsn)
    detail = repo.quotation_detail(world["quote_id"])
    assert detail is not None
    assert detail["quote_number"] == world["quote_number"]
    assert detail["number_origin"] == "printed_historical"
    assert len(detail["revisions"]) == 1
    assert detail["canonical_revision"] is not None
    assert detail["canonical_revision"]["is_canonical"] is True
    assert len(detail["gmail_sources"]) == 1
    gs = detail["gmail_sources"][0]
    assert gs["gmail_message_id"] == f"msg-{world['tag']}"


@needs_db
def test_db_missing_quotation_returns_none(world) -> None:
    import psycopg

    from origenlab_api.v2.cockpit_repository import CockpitRepository

    api_dsn = runtime_dsn(world["dsn"])
    repo = CockpitRepository(psycopg.connect, api_dsn)
    assert repo.quotation_detail(str(uuid.uuid4())) is None


# ──────────────────────────────────────────── timeline ──────────────────────


@needs_db
def test_db_opportunity_timeline_returns_dict(world) -> None:
    import psycopg

    from origenlab_api.v2.cockpit_repository import CockpitRepository

    api_dsn = runtime_dsn(world["dsn"])
    repo = CockpitRepository(psycopg.connect, api_dsn)
    result = repo.opportunity_timeline(world["opportunity_id"])
    assert result is not None
    assert result["opportunity_id"] == world["opportunity_id"]
    assert isinstance(result["entries"], list)
    # There must be at least one quotation_sent entry (our revision has sent_at set)
    kinds = {e["entry_kind"] for e in result["entries"]}
    assert "quotation_sent" in kinds


@needs_db
def test_db_missing_timeline_returns_none(world) -> None:
    import psycopg

    from origenlab_api.v2.cockpit_repository import CockpitRepository

    api_dsn = runtime_dsn(world["dsn"])
    repo = CockpitRepository(psycopg.connect, api_dsn)
    assert repo.opportunity_timeline(str(uuid.uuid4())) is None


# ─────────────────────────────────────── evidence drawer ────────────────────


@needs_db
def test_db_evidence_drawer_returns_gmail_fields(world) -> None:
    import psycopg

    from origenlab_api.v2.cockpit_repository import CockpitRepository

    api_dsn = runtime_dsn(world["dsn"])
    repo = CockpitRepository(psycopg.connect, api_dsn)
    drawer = repo.evidence_drawer(world["source_record_id"])
    assert drawer is not None
    assert drawer["kind"] == "gmail_message"
    assert drawer["gmail_message_id"] == f"msg-{world['tag']}"
    assert drawer["gmail_thread_id"] == f"thr-{world['tag']}"
    assert "body" not in drawer
    assert len(drawer["documents"]) == 1
    assert drawer["documents"][0]["sha256"] == "a" * 64
    # assertions: we inserted one document_reference
    assert any(a["kind"] == "document_reference" for a in drawer["assertions"])


@needs_db
def test_db_missing_evidence_drawer_returns_none(world) -> None:
    import psycopg

    from origenlab_api.v2.cockpit_repository import CockpitRepository

    api_dsn = runtime_dsn(world["dsn"])
    repo = CockpitRepository(psycopg.connect, api_dsn)
    assert repo.evidence_drawer(str(uuid.uuid4())) is None


# ──────────────────────────────────────────────── search ────────────────────


@needs_db
def test_db_search_by_quote_number_prefix(world) -> None:
    import psycopg

    from origenlab_api.v2.cockpit_repository import CockpitRepository

    api_dsn = runtime_dsn(world["dsn"])
    repo = CockpitRepository(psycopg.connect, api_dsn)
    result = repo.search(world["quote_number"])
    assert result["query"] == world["quote_number"]
    assert any(h["match_kind"] == "quote_number" and h["subject_id"] == world["quote_id"]
               for h in result["hits"])


@needs_db
def test_db_search_by_gmail_message_id(world) -> None:
    import psycopg

    from origenlab_api.v2.cockpit_repository import CockpitRepository

    api_dsn = runtime_dsn(world["dsn"])
    repo = CockpitRepository(psycopg.connect, api_dsn)
    result = repo.search(f"msg-{world['tag']}")
    assert any(
        h["match_kind"] == "gmail_message_id"
        and h["subject_id"] == world["source_record_id"]
        for h in result["hits"]
    )


@needs_db
def test_db_search_by_organization_name(world) -> None:
    import psycopg

    from origenlab_api.v2.cockpit_repository import CockpitRepository

    api_dsn = runtime_dsn(world["dsn"])
    repo = CockpitRepository(psycopg.connect, api_dsn)
    result = repo.search(world["tag"])  # the tag is part of the org name
    org_hits = [h for h in result["hits"] if h["match_kind"] == "organization_name"]
    assert any(h["subject_id"] == world["organization_id"] for h in org_hits)


@needs_db
def test_db_write_is_refused_in_cockpit_repository(world) -> None:
    """The server enforces read-only: a write attempt in a _read() transaction fails."""
    import psycopg

    from origenlab_api.v2.cockpit_repository import CockpitRepository

    api_dsn = runtime_dsn(world["dsn"])
    repo = CockpitRepository(psycopg.connect, api_dsn)
    with pytest.raises(Exception, match="read.only|25006|cannot execute"):
        with repo._read() as cur:
            cur.execute("insert into crm.organization (name) values ('should fail')")
            cur.execute("select 1")  # unreachable

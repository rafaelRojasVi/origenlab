"""Real-Postgres integration coverage for CRM-4A identity reconciliation.

Requires a disposable Postgres migrated to Alembic head:

    cd apps/email-pipeline
    ALEMBIC_DATABASE_URL=$ORIGENLAB_TEST_POSTGRES_URL uv run alembic upgrade head

Then set ORIGENLAB_TEST_POSTGRES_URL and run pytest directly (this file is
intentionally excluded from apps/api/scripts/validate.sh's default run, which
disables Postgres).

No mocks: every test exercises PostgresCommercialOperationsRepository against
a real connection so FK/constraint enforcement and the *_source unique-based
race-safety are actually proven, not merely asserted against scripted stubs.
"""

from __future__ import annotations

import os
import threading
import uuid
from typing import Iterator

import pytest

from origenlab_api.repositories.postgres.commercial_operations import (
    CommercialOperationConflictError,
    PostgresCommercialOperationsRepository,
)
from origenlab_api.repositories.postgres.commercial_operations_read import (
    PostgresCommercialOperationsReadRepository,
)
from origenlab_api.repositories.postgres.common import normalize_postgres_url
from origenlab_api.settings import Settings


def _postgres_test_url_ready() -> str | None:
    url = (os.environ.get("ORIGENLAB_TEST_POSTGRES_URL") or "").strip()
    if not url:
        return None
    try:
        import psycopg

        with psycopg.connect(normalize_postgres_url(url), connect_timeout=2):
            pass
        return url
    except Exception:
        return None


pytestmark = pytest.mark.skipif(
    _postgres_test_url_ready() is None,
    reason=(
        "Set ORIGENLAB_TEST_POSTGRES_URL to a disposable Postgres migrated to "
        "Alembic head to run CRM-4A reconciliation integration tests."
    ),
)


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _settings(url: str) -> Settings:
    return Settings(
        postgres_write_url=url,
        postgres_url=url,
        commercial_operations_writes_enabled=True,
    )


@pytest.fixture
def admin_conn() -> Iterator[object]:
    import psycopg

    url = _postgres_test_url_ready()
    assert url is not None
    conn = psycopg.connect(
        normalize_postgres_url(url),
        autocommit=True,
        row_factory=psycopg.rows.dict_row,
    )
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def repo() -> PostgresCommercialOperationsRepository:
    url = _postgres_test_url_ready()
    assert url is not None
    return PostgresCommercialOperationsRepository(_settings(url))


@pytest.fixture
def read_repo() -> PostgresCommercialOperationsReadRepository:
    url = _postgres_test_url_ready()
    assert url is not None
    return PostgresCommercialOperationsReadRepository(_settings(url))


def _seed_opportunity(
    admin_conn: object,
    *,
    opportunity_id: str,
    account_id: str | None,
    primary_contact_id: str | None,
    account_display_domain: str | None,
    contact_display_email: str | None,
) -> None:
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO commercial.opportunity (
              opportunity_id, record_kind, account_id, primary_contact_id,
              contact_display_email, account_display_domain,
              source_kind, source_key,
              canonical_stage, source_stage, stage_reason_code, stage_confidence,
              stage_is_current, stage_is_terminal,
              identity_link_status, review_status
            ) VALUES (
              %(opportunity_id)s, 'explicit_opportunity',
              %(account_id)s, %(primary_contact_id)s,
              %(contact_display_email)s, %(account_display_domain)s,
              'pytest', %(opportunity_id)s,
              'open', 'open', 'test_fixture', 'high',
              TRUE, FALSE,
              'linked', 'reviewed'
            )
            """,
            {
                "opportunity_id": opportunity_id,
                "account_id": account_id,
                "primary_contact_id": primary_contact_id,
                "contact_display_email": contact_display_email,
                "account_display_domain": account_display_domain,
            },
        )


def _seed_organization(
    admin_conn: object,
    *,
    organization_id: str,
    account_id: str,
    display_name: str,
    primary_domain: str | None,
) -> None:
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO commercial.organization (
              organization_id, display_name, primary_domain,
              version, created_by, updated_by
            ) VALUES (
              %(organization_id)s, %(display_name)s, %(primary_domain)s,
              1, 'pytest', 'pytest'
            )
            """,
            {
                "organization_id": organization_id,
                "display_name": display_name,
                "primary_domain": primary_domain,
            },
        )
        cur.execute(
            """
            INSERT INTO commercial.organization_source (
              organization_id, source_kind, source_id, created_by
            ) VALUES (%(organization_id)s, 'pr2_account', %(account_id)s, 'pytest')
            """,
            {"organization_id": organization_id, "account_id": account_id},
        )


def _seed_contact(
    admin_conn: object,
    *,
    contact_id: str,
    organization_id: str,
    primary_contact_id: str,
    primary_email: str | None,
) -> None:
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO commercial.contact (
              contact_id, organization_id, display_name, primary_email,
              version, created_by, updated_by
            ) VALUES (
              %(contact_id)s, %(organization_id)s, NULL, %(primary_email)s,
              1, 'pytest', 'pytest'
            )
            """,
            {
                "contact_id": contact_id,
                "organization_id": organization_id,
                "primary_email": primary_email,
            },
        )
        cur.execute(
            """
            INSERT INTO commercial.contact_source (
              contact_id, source_kind, source_id, created_by
            ) VALUES (%(contact_id)s, 'pr2_contact', %(primary_contact_id)s, 'pytest')
            """,
            {"contact_id": contact_id, "primary_contact_id": primary_contact_id},
        )


def _cleanup(
    admin_conn: object,
    *,
    opportunity_ids: tuple[str, ...] = (),
    sales_opportunity_ids: tuple[str, ...] = (),
    organization_ids: tuple[str, ...] = (),
    contact_ids: tuple[str, ...] = (),
) -> None:
    with admin_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM commercial.sales_opportunity_event "
            "WHERE sales_opportunity_id = ANY(%s)",
            (list(sales_opportunity_ids),),
        )
        cur.execute(
            "DELETE FROM commercial.command_idempotency WHERE result_id = ANY(%s)",
            (list(sales_opportunity_ids),),
        )
        cur.execute(
            "DELETE FROM commercial.sales_opportunity "
            "WHERE sales_opportunity_id = ANY(%s)",
            (list(sales_opportunity_ids),),
        )
        cur.execute(
            "DELETE FROM commercial.contact_source WHERE contact_id = ANY(%s)",
            (list(contact_ids),),
        )
        cur.execute(
            "DELETE FROM commercial.contact WHERE contact_id = ANY(%s)",
            (list(contact_ids),),
        )
        cur.execute(
            "DELETE FROM commercial.organization_source "
            "WHERE organization_id = ANY(%s)",
            (list(organization_ids),),
        )
        cur.execute(
            "DELETE FROM commercial.organization WHERE organization_id = ANY(%s)",
            (list(organization_ids),),
        )
        cur.execute(
            "DELETE FROM commercial.opportunity WHERE opportunity_id = ANY(%s)",
            (list(opportunity_ids),),
        )


def _promote(
    repo: PostgresCommercialOperationsRepository,
    *,
    sales_opportunity_id: str,
    source_opportunity_id: str,
    idempotency_key: str | None = None,
):
    return repo.promote_sales_opportunity(
        sales_opportunity_id=sales_opportunity_id,
        source_opportunity_id=source_opportunity_id,
        title="Integration test opportunity",
        owner_key="pytest-operator@origenlab.cl",
        operator="pytest-operator@origenlab.cl",
        idempotency_key=idempotency_key or _uid("idem"),
        request_fingerprint="a" * 64,
    )


# ---------------------------------------------------------------------------
# 1. New organization + new contact
# ---------------------------------------------------------------------------


def test_new_account_and_contact_evidence_creates_organization_and_contact(
    admin_conn: object,
    repo: PostgresCommercialOperationsRepository,
) -> None:
    account_id = _uid("a")
    contact_id_evidence = _uid("c")
    opportunity_id = _uid("o")
    sales_id = _uid("sales")
    domain = f"{_uid('newco')}.cl"
    email = f"buyer@{domain}"

    _seed_opportunity(
        admin_conn,
        opportunity_id=opportunity_id,
        account_id=account_id,
        primary_contact_id=contact_id_evidence,
        account_display_domain=domain,
        contact_display_email=email,
    )

    try:
        result = _promote(
            repo, sales_opportunity_id=sales_id, source_opportunity_id=opportunity_id
        )

        assert result.organization_id is not None
        assert result.primary_crm_contact_id is not None

        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT display_name, primary_domain FROM commercial.organization "
                "WHERE organization_id = %s",
                (result.organization_id,),
            )
            org_row = cur.fetchone()
            assert org_row is not None
            assert org_row["primary_domain"] == domain

            cur.execute(
                "SELECT organization_id, primary_email FROM commercial.contact "
                "WHERE contact_id = %s",
                (result.primary_crm_contact_id,),
            )
            contact_row = cur.fetchone()
            assert contact_row is not None
            assert contact_row["organization_id"] == result.organization_id
            assert contact_row["primary_email"] == email
    finally:
        result_org = None
        result_contact = None
        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT organization_id FROM commercial.organization_source "
                "WHERE source_id = %s",
                (account_id,),
            )
            row = cur.fetchone()
            result_org = row["organization_id"] if row else None
            cur.execute(
                "SELECT contact_id FROM commercial.contact_source WHERE source_id = %s",
                (contact_id_evidence,),
            )
            row = cur.fetchone()
            result_contact = row["contact_id"] if row else None
        _cleanup(
            admin_conn,
            opportunity_ids=(opportunity_id,),
            sales_opportunity_ids=(sales_id,),
            organization_ids=(result_org,) if result_org else (),
            contact_ids=(result_contact,) if result_contact else (),
        )


# ---------------------------------------------------------------------------
# 2. Existing organization reused
# ---------------------------------------------------------------------------


def test_existing_account_id_reuses_organization_without_duplicate(
    admin_conn: object,
    repo: PostgresCommercialOperationsRepository,
) -> None:
    account_id = _uid("a")
    organization_id = _uid("org")
    opportunity_id = _uid("o")
    sales_id = _uid("sales")
    domain = f"{_uid('existing')}.cl"

    _seed_organization(
        admin_conn,
        organization_id=organization_id,
        account_id=account_id,
        display_name=domain,
        primary_domain=domain,
    )
    _seed_opportunity(
        admin_conn,
        opportunity_id=opportunity_id,
        account_id=account_id,
        primary_contact_id=None,
        account_display_domain=domain,
        contact_display_email=None,
    )

    try:
        result = _promote(
            repo, sales_opportunity_id=sales_id, source_opportunity_id=opportunity_id
        )

        assert result.organization_id == organization_id

        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM commercial.organization "
                "WHERE primary_domain = %s",
                (domain,),
            )
            assert cur.fetchone()["n"] == 1
    finally:
        _cleanup(
            admin_conn,
            opportunity_ids=(opportunity_id,),
            sales_opportunity_ids=(sales_id,),
            organization_ids=(organization_id,),
        )


# ---------------------------------------------------------------------------
# 3. Existing contact reused
# ---------------------------------------------------------------------------


def test_existing_primary_contact_id_reuses_contact_without_duplicate(
    admin_conn: object,
    repo: PostgresCommercialOperationsRepository,
) -> None:
    account_id = _uid("a")
    contact_evidence_id = _uid("c")
    organization_id = _uid("org")
    contact_id = _uid("contact")
    opportunity_id = _uid("o")
    sales_id = _uid("sales")
    domain = f"{_uid('reuse')}.cl"
    email = f"person@{domain}"

    _seed_organization(
        admin_conn,
        organization_id=organization_id,
        account_id=account_id,
        display_name=domain,
        primary_domain=domain,
    )
    _seed_contact(
        admin_conn,
        contact_id=contact_id,
        organization_id=organization_id,
        primary_contact_id=contact_evidence_id,
        primary_email=email,
    )
    _seed_opportunity(
        admin_conn,
        opportunity_id=opportunity_id,
        account_id=account_id,
        primary_contact_id=contact_evidence_id,
        account_display_domain=domain,
        contact_display_email=email,
    )

    try:
        result = _promote(
            repo, sales_opportunity_id=sales_id, source_opportunity_id=opportunity_id
        )

        assert result.organization_id == organization_id
        assert result.primary_crm_contact_id == contact_id

        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM commercial.contact WHERE primary_email = %s",
                (email,),
            )
            assert cur.fetchone()["n"] == 1
    finally:
        _cleanup(
            admin_conn,
            opportunity_ids=(opportunity_id,),
            sales_opportunity_ids=(sales_id,),
            organization_ids=(organization_id,),
            contact_ids=(contact_id,),
        )


# ---------------------------------------------------------------------------
# 4/5. Concurrent promotion sharing account_id / primary_contact_id
# ---------------------------------------------------------------------------


def test_concurrent_promotions_same_account_id_create_only_one_organization(
    admin_conn: object,
    repo: PostgresCommercialOperationsRepository,
) -> None:
    account_id = _uid("a")
    domain = f"{_uid('race')}.cl"
    opp_a = _uid("o")
    opp_b = _uid("o")
    sales_a = _uid("sales")
    sales_b = _uid("sales")

    _seed_opportunity(
        admin_conn,
        opportunity_id=opp_a,
        account_id=account_id,
        primary_contact_id=None,
        account_display_domain=domain,
        contact_display_email=None,
    )
    _seed_opportunity(
        admin_conn,
        opportunity_id=opp_b,
        account_id=account_id,
        primary_contact_id=None,
        account_display_domain=domain,
        contact_display_email=None,
    )

    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def _run(sales_id: str, opportunity_id: str, key: str) -> None:
        try:
            results[key] = _promote(
                repo,
                sales_opportunity_id=sales_id,
                source_opportunity_id=opportunity_id,
            )
        except BaseException as exc:  # noqa: BLE001 - captured for assertion
            errors.append(exc)

    t1 = threading.Thread(target=_run, args=(sales_a, opp_a, "a"))
    t2 = threading.Thread(target=_run, args=(sales_b, opp_b, "b"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    try:
        assert not errors, errors
        org_a = results["a"].organization_id
        org_b = results["b"].organization_id
        assert org_a is not None and org_b is not None
        assert org_a == org_b

        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM commercial.organization_source "
                "WHERE source_id = %s",
                (account_id,),
            )
            assert cur.fetchone()["n"] == 1
            cur.execute(
                "SELECT count(*) AS n FROM commercial.organization "
                "WHERE primary_domain = %s",
                (domain,),
            )
            assert cur.fetchone()["n"] == 1
    finally:
        _cleanup(
            admin_conn,
            opportunity_ids=(opp_a, opp_b),
            sales_opportunity_ids=(sales_a, sales_b),
            organization_ids=(results["a"].organization_id,)
            if "a" in results and results["a"].organization_id
            else (),
        )


def test_concurrent_promotions_same_primary_contact_id_create_only_one_contact(
    admin_conn: object,
    repo: PostgresCommercialOperationsRepository,
) -> None:
    account_id_a = _uid("a")
    account_id_b = _uid("a")
    contact_evidence_id = _uid("c")
    domain_a = f"{_uid('sharedcontact')}.cl"
    email = f"shared@{domain_a}"
    opp_a = _uid("o")
    opp_b = _uid("o")
    sales_a = _uid("sales")
    sales_b = _uid("sales")

    # Same organization for both, so both promotions resolve the same org
    # and only the contact-resolution race is under test.
    organization_id = _uid("org")
    _seed_organization(
        admin_conn,
        organization_id=organization_id,
        account_id=account_id_a,
        display_name=domain_a,
        primary_domain=domain_a,
    )
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO commercial.organization_source (
              organization_id, source_kind, source_id, created_by
            ) VALUES (%(organization_id)s, 'pr2_account', %(account_id)s, 'pytest')
            """,
            {"organization_id": organization_id, "account_id": account_id_b},
        )

    _seed_opportunity(
        admin_conn,
        opportunity_id=opp_a,
        account_id=account_id_a,
        primary_contact_id=contact_evidence_id,
        account_display_domain=domain_a,
        contact_display_email=email,
    )
    _seed_opportunity(
        admin_conn,
        opportunity_id=opp_b,
        account_id=account_id_b,
        primary_contact_id=contact_evidence_id,
        account_display_domain=domain_a,
        contact_display_email=email,
    )

    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def _run(sales_id: str, opportunity_id: str, key: str) -> None:
        try:
            results[key] = _promote(
                repo,
                sales_opportunity_id=sales_id,
                source_opportunity_id=opportunity_id,
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=_run, args=(sales_a, opp_a, "a"))
    t2 = threading.Thread(target=_run, args=(sales_b, opp_b, "b"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    try:
        assert not errors, errors
        contact_a = results["a"].primary_crm_contact_id
        contact_b = results["b"].primary_crm_contact_id
        assert contact_a is not None and contact_a == contact_b

        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM commercial.contact_source WHERE source_id = %s",
                (contact_evidence_id,),
            )
            assert cur.fetchone()["n"] == 1
    finally:
        _cleanup(
            admin_conn,
            opportunity_ids=(opp_a, opp_b),
            sales_opportunity_ids=(sales_a, sales_b),
            organization_ids=(organization_id,),
            contact_ids=(results["a"].primary_crm_contact_id,)
            if "a" in results and results["a"].primary_crm_contact_id
            else (),
        )


# ---------------------------------------------------------------------------
# 6. Idempotent replay does no new reconciliation work
# ---------------------------------------------------------------------------


def test_idempotent_replay_does_not_rerun_reconciliation_or_duplicate(
    admin_conn: object,
    repo: PostgresCommercialOperationsRepository,
) -> None:
    account_id = _uid("a")
    contact_evidence_id = _uid("c")
    domain = f"{_uid('replay')}.cl"
    email = f"buyer@{domain}"
    opportunity_id = _uid("o")
    sales_id = _uid("sales")
    idem_key = _uid("idem")

    _seed_opportunity(
        admin_conn,
        opportunity_id=opportunity_id,
        account_id=account_id,
        primary_contact_id=contact_evidence_id,
        account_display_domain=domain,
        contact_display_email=email,
    )

    try:
        first = _promote(
            repo,
            sales_opportunity_id=sales_id,
            source_opportunity_id=opportunity_id,
            idempotency_key=idem_key,
        )
        second = _promote(
            repo,
            sales_opportunity_id=sales_id,
            source_opportunity_id=opportunity_id,
            idempotency_key=idem_key,
        )

        assert second.sales_opportunity_id == first.sales_opportunity_id
        assert second.organization_id == first.organization_id
        assert second.primary_crm_contact_id == first.primary_crm_contact_id

        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM commercial.organization_source "
                "WHERE source_id = %s",
                (account_id,),
            )
            assert cur.fetchone()["n"] == 1
    finally:
        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT organization_id FROM commercial.organization_source "
                "WHERE source_id = %s",
                (account_id,),
            )
            row = cur.fetchone()
            org_id = row["organization_id"] if row else None
            cur.execute(
                "SELECT contact_id FROM commercial.contact_source WHERE source_id = %s",
                (contact_evidence_id,),
            )
            row = cur.fetchone()
            contact_id = row["contact_id"] if row else None
        _cleanup(
            admin_conn,
            opportunity_ids=(opportunity_id,),
            sales_opportunity_ids=(sales_id,),
            organization_ids=(org_id,) if org_id else (),
            contact_ids=(contact_id,) if contact_id else (),
        )


# ---------------------------------------------------------------------------
# 7/8. organization_id / primary_crm_contact_id persisted
# ---------------------------------------------------------------------------


def test_promoted_sales_opportunity_persists_organization_id(
    admin_conn: object,
    repo: PostgresCommercialOperationsRepository,
) -> None:
    account_id = _uid("a")
    domain = f"{_uid('persist-org')}.cl"
    opportunity_id = _uid("o")
    sales_id = _uid("sales")

    _seed_opportunity(
        admin_conn,
        opportunity_id=opportunity_id,
        account_id=account_id,
        primary_contact_id=None,
        account_display_domain=domain,
        contact_display_email=None,
    )

    try:
        result = _promote(
            repo, sales_opportunity_id=sales_id, source_opportunity_id=opportunity_id
        )

        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT organization_id FROM commercial.sales_opportunity "
                "WHERE sales_opportunity_id = %s",
                (sales_id,),
            )
            row = cur.fetchone()
            assert row is not None
            assert row["organization_id"] == result.organization_id
            assert row["organization_id"] is not None
    finally:
        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT organization_id FROM commercial.organization_source "
                "WHERE source_id = %s",
                (account_id,),
            )
            row = cur.fetchone()
            org_id = row["organization_id"] if row else None
        _cleanup(
            admin_conn,
            opportunity_ids=(opportunity_id,),
            sales_opportunity_ids=(sales_id,),
            organization_ids=(org_id,) if org_id else (),
        )


def test_promoted_sales_opportunity_persists_primary_crm_contact_id(
    admin_conn: object,
    repo: PostgresCommercialOperationsRepository,
) -> None:
    account_id = _uid("a")
    contact_evidence_id = _uid("c")
    domain = f"{_uid('persist-contact')}.cl"
    email = f"buyer@{domain}"
    opportunity_id = _uid("o")
    sales_id = _uid("sales")

    _seed_opportunity(
        admin_conn,
        opportunity_id=opportunity_id,
        account_id=account_id,
        primary_contact_id=contact_evidence_id,
        account_display_domain=domain,
        contact_display_email=email,
    )

    try:
        result = _promote(
            repo, sales_opportunity_id=sales_id, source_opportunity_id=opportunity_id
        )

        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT primary_crm_contact_id FROM commercial.sales_opportunity "
                "WHERE sales_opportunity_id = %s",
                (sales_id,),
            )
            row = cur.fetchone()
            assert row is not None
            assert row["primary_crm_contact_id"] == result.primary_crm_contact_id
            assert row["primary_crm_contact_id"] is not None
    finally:
        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT organization_id FROM commercial.organization_source "
                "WHERE source_id = %s",
                (account_id,),
            )
            row = cur.fetchone()
            org_id = row["organization_id"] if row else None
            cur.execute(
                "SELECT contact_id FROM commercial.contact_source WHERE source_id = %s",
                (contact_evidence_id,),
            )
            row = cur.fetchone()
            contact_id = row["contact_id"] if row else None
        _cleanup(
            admin_conn,
            opportunity_ids=(opportunity_id,),
            sales_opportunity_ids=(sales_id,),
            organization_ids=(org_id,) if org_id else (),
            contact_ids=(contact_id,) if contact_id else (),
        )


# ---------------------------------------------------------------------------
# 9. Missing evidence leaves links null (promotion still succeeds)
# ---------------------------------------------------------------------------


def test_missing_account_evidence_leaves_links_null(
    admin_conn: object,
    repo: PostgresCommercialOperationsRepository,
) -> None:
    opportunity_id = _uid("o")
    sales_id = _uid("sales")

    _seed_opportunity(
        admin_conn,
        opportunity_id=opportunity_id,
        account_id=None,
        primary_contact_id=None,
        account_display_domain=None,
        contact_display_email=None,
    )

    try:
        result = _promote(
            repo, sales_opportunity_id=sales_id, source_opportunity_id=opportunity_id
        )

        assert result.organization_id is None
        assert result.primary_crm_contact_id is None
    finally:
        _cleanup(
            admin_conn,
            opportunity_ids=(opportunity_id,),
            sales_opportunity_ids=(sales_id,),
        )


# ---------------------------------------------------------------------------
# 10/11. Malformed evidence does not fabricate identity; promotion succeeds
# ---------------------------------------------------------------------------


def test_blank_domain_evidence_does_not_fabricate_organization(
    admin_conn: object,
    repo: PostgresCommercialOperationsRepository,
) -> None:
    account_id = _uid("a")
    opportunity_id = _uid("o")
    sales_id = _uid("sales")

    _seed_opportunity(
        admin_conn,
        opportunity_id=opportunity_id,
        account_id=account_id,
        primary_contact_id=None,
        account_display_domain="   ",
        contact_display_email=None,
    )

    try:
        result = _promote(
            repo, sales_opportunity_id=sales_id, source_opportunity_id=opportunity_id
        )

        assert result.organization_id is None
        assert result.primary_crm_contact_id is None

        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM commercial.organization_source "
                "WHERE source_id = %s",
                (account_id,),
            )
            assert cur.fetchone()["n"] == 0
    finally:
        _cleanup(
            admin_conn,
            opportunity_ids=(opportunity_id,),
            sales_opportunity_ids=(sales_id,),
        )


def test_oversized_email_evidence_does_not_fabricate_but_contact_still_created(
    admin_conn: object,
    repo: PostgresCommercialOperationsRepository,
) -> None:
    account_id = _uid("a")
    contact_evidence_id = _uid("c")
    domain = f"{_uid('oversized')}.cl"
    oversized_email = "x" * 400 + "@" + domain
    opportunity_id = _uid("o")
    sales_id = _uid("sales")

    _seed_opportunity(
        admin_conn,
        opportunity_id=opportunity_id,
        account_id=account_id,
        primary_contact_id=contact_evidence_id,
        account_display_domain=domain,
        contact_display_email=oversized_email,
    )

    try:
        result = _promote(
            repo, sales_opportunity_id=sales_id, source_opportunity_id=opportunity_id
        )

        assert result.organization_id is not None
        assert result.primary_crm_contact_id is not None

        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT primary_email FROM commercial.contact WHERE contact_id = %s",
                (result.primary_crm_contact_id,),
            )
            row = cur.fetchone()
            assert row is not None
            assert row["primary_email"] is None
    finally:
        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT organization_id FROM commercial.organization_source "
                "WHERE source_id = %s",
                (account_id,),
            )
            row = cur.fetchone()
            org_id = row["organization_id"] if row else None
            cur.execute(
                "SELECT contact_id FROM commercial.contact_source WHERE source_id = %s",
                (contact_evidence_id,),
            )
            row = cur.fetchone()
            contact_id = row["contact_id"] if row else None
        _cleanup(
            admin_conn,
            opportunity_ids=(opportunity_id,),
            sales_opportunity_ids=(sales_id,),
            organization_ids=(org_id,) if org_id else (),
            contact_ids=(contact_id,) if contact_id else (),
        )


# ---------------------------------------------------------------------------
# 12/13. Rollback leaves no partial org/contact; repeated conflict still errors
# ---------------------------------------------------------------------------


def test_sales_opportunity_conflict_rolls_back_newly_created_organization_and_contact(
    admin_conn: object,
    repo: PostgresCommercialOperationsRepository,
) -> None:
    account_id = _uid("a")
    contact_evidence_id = _uid("c")
    domain = f"{_uid('conflict')}.cl"
    email = f"buyer@{domain}"
    opportunity_id = _uid("o")
    sales_id = _uid("sales")
    competing_sales_id = _uid("sales")

    _seed_opportunity(
        admin_conn,
        opportunity_id=opportunity_id,
        account_id=account_id,
        primary_contact_id=contact_evidence_id,
        account_display_domain=domain,
        contact_display_email=email,
    )

    # Pre-existing promotion for the same PR3 source opportunity forces the
    # ON CONFLICT (source_kind, source_opportunity_id) DO NOTHING path.
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO commercial.sales_opportunity (
              sales_opportunity_id, source_kind, source_opportunity_id,
              account_id, primary_contact_id, title, stage, owner_key,
              version, created_by, updated_by, created_at, updated_at
            ) VALUES (
              %(id)s, 'pr3', %(source_opportunity_id)s,
              NULL, NULL, 'Existing', 'new', 'pytest',
              1, 'pytest', 'pytest', now(), now()
            )
            """,
            {"id": competing_sales_id, "source_opportunity_id": opportunity_id},
        )

    try:
        with pytest.raises(CommercialOperationConflictError):
            _promote(
                repo,
                sales_opportunity_id=sales_id,
                source_opportunity_id=opportunity_id,
            )

        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM commercial.organization_source "
                "WHERE source_id = %s",
                (account_id,),
            )
            assert cur.fetchone()["n"] == 0
            cur.execute(
                "SELECT count(*) AS n FROM commercial.contact_source WHERE source_id = %s",
                (contact_evidence_id,),
            )
            assert cur.fetchone()["n"] == 0
            cur.execute(
                "SELECT count(*) AS n FROM commercial.organization "
                "WHERE primary_domain = %s",
                (domain,),
            )
            assert cur.fetchone()["n"] == 0
    finally:
        _cleanup(
            admin_conn,
            opportunity_ids=(opportunity_id,),
            sales_opportunity_ids=(sales_id, competing_sales_id),
        )


def test_repeated_promotion_after_conflict_respects_existing_conflict_error(
    admin_conn: object,
    repo: PostgresCommercialOperationsRepository,
) -> None:
    account_id = _uid("a")
    domain = f"{_uid('repeat-conflict')}.cl"
    opportunity_id = _uid("o")
    sales_id_1 = _uid("sales")
    sales_id_2 = _uid("sales")

    _seed_opportunity(
        admin_conn,
        opportunity_id=opportunity_id,
        account_id=account_id,
        primary_contact_id=None,
        account_display_domain=domain,
        contact_display_email=None,
    )

    try:
        first = _promote(
            repo, sales_opportunity_id=sales_id_1, source_opportunity_id=opportunity_id
        )

        with pytest.raises(CommercialOperationConflictError):
            _promote(
                repo,
                sales_opportunity_id=sales_id_2,
                source_opportunity_id=opportunity_id,
            )
        with pytest.raises(CommercialOperationConflictError):
            _promote(
                repo,
                sales_opportunity_id=sales_id_2,
                source_opportunity_id=opportunity_id,
            )

        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM commercial.organization_source "
                "WHERE source_id = %s",
                (account_id,),
            )
            assert cur.fetchone()["n"] == 1
    finally:
        _cleanup(
            admin_conn,
            opportunity_ids=(opportunity_id,),
            sales_opportunity_ids=(sales_id_1, sales_id_2),
            organization_ids=(first.organization_id,) if first.organization_id else (),
        )


# ---------------------------------------------------------------------------
# 14. Contact bound to a different organization is not linked (org still is)
# ---------------------------------------------------------------------------


def test_contact_reused_only_when_existing_organization_matches_resolved_organization(
    admin_conn: object,
    repo: PostgresCommercialOperationsRepository,
) -> None:
    account_id_existing = _uid("a")
    account_id_new = _uid("a")
    contact_evidence_id = _uid("c")
    domain_existing = f"{_uid('org-a')}.cl"
    domain_new = f"{_uid('org-b')}.cl"
    email = f"person@{domain_existing}"

    organization_id_existing = _uid("org")
    contact_id_existing = _uid("contact")
    _seed_organization(
        admin_conn,
        organization_id=organization_id_existing,
        account_id=account_id_existing,
        display_name=domain_existing,
        primary_domain=domain_existing,
    )
    _seed_contact(
        admin_conn,
        contact_id=contact_id_existing,
        organization_id=organization_id_existing,
        primary_contact_id=contact_evidence_id,
        primary_email=email,
    )

    opportunity_id = _uid("o")
    sales_id = _uid("sales")
    _seed_opportunity(
        admin_conn,
        opportunity_id=opportunity_id,
        account_id=account_id_new,
        primary_contact_id=contact_evidence_id,
        account_display_domain=domain_new,
        contact_display_email=email,
    )

    result = None
    try:
        result = _promote(
            repo, sales_opportunity_id=sales_id, source_opportunity_id=opportunity_id
        )

        assert result.organization_id is not None
        assert result.organization_id != organization_id_existing
        assert result.primary_crm_contact_id is None

        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM commercial.contact WHERE primary_email = %s",
                (email,),
            )
            assert cur.fetchone()["n"] == 1
    finally:
        new_org_ids = (
            (organization_id_existing, result.organization_id)
            if result is not None and result.organization_id
            else (organization_id_existing,)
        )
        _cleanup(
            admin_conn,
            opportunity_ids=(opportunity_id,),
            sales_opportunity_ids=(sales_id,),
            organization_ids=new_org_ids,
            contact_ids=(contact_id_existing,),
        )


# ---------------------------------------------------------------------------
# 15. Read model exposes the resolved organization/contact display identity
# ---------------------------------------------------------------------------


def test_list_sales_opportunities_exposes_resolved_identity_display_names(
    admin_conn: object,
    repo: PostgresCommercialOperationsRepository,
    read_repo: PostgresCommercialOperationsReadRepository,
) -> None:
    account_id = _uid("a")
    contact_evidence_id = _uid("c")
    domain = f"{_uid('readmodel')}.cl"
    email = f"buyer@{domain}"
    opportunity_id = _uid("o")
    sales_id = _uid("sales")

    _seed_opportunity(
        admin_conn,
        opportunity_id=opportunity_id,
        account_id=account_id,
        primary_contact_id=contact_evidence_id,
        account_display_domain=domain,
        contact_display_email=email,
    )

    try:
        result = _promote(
            repo, sales_opportunity_id=sales_id, source_opportunity_id=opportunity_id
        )
        assert result.organization_id is not None
        assert result.primary_crm_contact_id is not None

        items, _total = read_repo.list_sales_opportunities(
            source_opportunity_ids=[opportunity_id]
        )

        assert len(items) == 1
        item = items[0]
        assert item.organization_display_name == domain
        assert item.contact_primary_email == email
        # No durable contact display_name was ever set (evidence had no
        # name), so it must stay None rather than fabricate one.
        assert item.contact_display_name is None
    finally:
        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT organization_id FROM commercial.organization_source "
                "WHERE source_id = %s",
                (account_id,),
            )
            row = cur.fetchone()
            org_id = row["organization_id"] if row else None
            cur.execute(
                "SELECT contact_id FROM commercial.contact_source WHERE source_id = %s",
                (contact_evidence_id,),
            )
            row = cur.fetchone()
            contact_id = row["contact_id"] if row else None
        _cleanup(
            admin_conn,
            opportunity_ids=(opportunity_id,),
            sales_opportunity_ids=(sales_id,),
            organization_ids=(org_id,) if org_id else (),
            contact_ids=(contact_id,) if contact_id else (),
        )

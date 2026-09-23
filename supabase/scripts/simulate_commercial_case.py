#!/usr/bin/env python3
"""Run one whole commercial case, end to end, in a database that does not survive it.

**What this is for.** The unit tests prove each command's rules one at a time. What they do
not show is the thing an operator actually cares about: what a case *looks like* once the six
commands have been used the way they are meant to be used together. This runs that case and
prints it, so the shape of the result can be read and argued with before anybody decides
anything real.

**The case it runs** is the one `docs/DOMAIN.md` §3.6.1 uses as its worked example, with a
fictitious customer:

* a requesting institution — a university that wants to buy;
* **Hielscher** as supplier *and* manufacturer on the same case, which it may legitimately be
  at once, and which it is **never** made the customer of;
* one piece of evidence — the message the case was opened from, linked as its `origin`;
* one interest — the equipment being sought, with a quantity and no price.

**How it stays a simulation.**

1. Every row it writes goes into a **disposable database it creates and drops** —
   `origenlab_test_<hex>`, migrated from `supabase/migrations/`. The commands run there as the
   unprivileged `origenlab_api` role, exactly as they would in a deployment.
2. Every fixture is **invented**. No real institution, person, address or message appears; the
   domains are reserved `.invalid` names. Nothing is read from any real database, so nothing
   real can leak into a public repository through this file's output.
3. `origenlab_clean` is **fingerprinted before and after**, read only, inside `begin read
   only`, and the transaction is rolled back. It is never written, and this proves it rather
   than promising it. `origenlab_dev` is refused by name before any connection is opened.

Nothing here connects to the hosted project, Gmail, Drive or any campaign surface, and no
command it runs could reach them if it tried.

Usage:

    supabase/scripts/simulate_commercial_case.py
    supabase/scripts/simulate_commercial_case.py --keep    # leave the database for inspection

Procedure: docs/OPERATIONS.md §4.1.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import sys
import uuid
from decimal import Decimal
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(REPO_ROOT / "apps" / "email-pipeline" / "tests"))

from protected_databases import (  # noqa: E402
    PROTECTED_DATABASES,
    assert_connection_is_disposable,
)

from origenlab_api.v2.case_command_repository import V2CaseCommandRepository  # noqa: E402
from origenlab_api.v2.case_commands import (  # noqa: E402
    ADD_CASE_ORGANIZATION,
    ADVANCE_CASE_STAGE,
    LINK_CASE_EVIDENCE,
    OPEN_COMMERCIAL_CASE,
    RECORD_CASE_INTEREST,
    SET_CASE_ORGANIZATION_ROLE,
    AddCaseOrganizationBody,
    AdvanceCaseStageBody,
    LinkCaseEvidenceBody,
    OpenCommercialCaseBody,
    RecordCaseInterestBody,
    SetCaseOrganizationRoleBody,
    validated_case,
)
from origenlab_api.v2.commands import CommandRefused, request_digest  # noqa: E402
from origenlab_api.v2.identity import OperatorIdentity  # noqa: E402

MIGRATIONS = REPO_ROOT / "supabase" / "migrations"

#: The clean room, and the maintenance login that may create a database beside it. Both are
#: the constants the shell tooling uses; neither is taken from the environment.
CLEAN_DSN = "postgresql://postgres:postgres@127.0.0.1:54332/origenlab_clean"
MAINTENANCE_DSN = "postgresql://postgres:postgres@127.0.0.1:54332/postgres"

#: The runtime role's login, written outside Git by `cleanroom_db.sh api-login`. Only its
#: credentials are used; the database it names is replaced by the disposable one.
API_ENV = pathlib.Path(
    os.environ.get("OL_LOCAL_PRIVATE_ROOT", str(pathlib.Path.home() / "data/origenlab-v2-local"))
) / "api.cleanroom.env"

BOOTSTRAP = """
create schema if not exists extensions authorization postgres;
create extension if not exists btree_gist with schema extensions;
"""

#: Every name below is invented. `Hielscher Ultrasonics` is a real manufacturer and one of the
#: six approved brands (`apps/web/src/data/brands.ts`), which is exactly why it is here: the
#: rule being demonstrated is that an approved supplier brand is never turned into a customer
#: by appearing on a case. It is a brand on a public list, not a private commercial fact.
UNIVERSITY = "Universidad Ficticia del Norte"
MANUFACTURER = "Hielscher Ultrasonics"
PURCHASING_AGENT = "Central de Abastecimiento Ficticia"
MODEL = "UP200Ht"
MAILBOX = "compras@universidad-ficticia.invalid"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def api_dsn(database: str) -> str:
    if not API_ENV.is_file():
        fail(
            f"{API_ENV} is missing. Run `supabase/scripts/cleanroom_db.sh api-login` first: "
            "the simulation runs as the unprivileged runtime role, not as postgres."
        )
    for line in API_ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("ORIGENLAB_V2_DATABASE_URL="):
            base = line.partition("=")[2].strip().rpartition("/")[0]
            return f"{base}/{database}"
    fail(f"{API_ENV} carries no ORIGENLAB_V2_DATABASE_URL")
    raise AssertionError  # unreachable


# --------------------------------------------------------------------- the clean room, read only


def clean_room_fingerprint() -> str:
    """A digest over everything a case command could move, taken without writing anything.

    Deliberately not a row count. A command that opened a case *and* linked its evidence would
    leave some counts unchanged; this hashes identifiers and states, so any movement at all
    changes the digest.
    """
    import psycopg

    with psycopg.connect(CLEAN_DSN) as conn:
        reached = conn.execute("select current_database()").fetchone()[0]
        if reached != "origenlab_clean":
            fail(f"expected to read origenlab_clean, reached {reached!r}")
        with conn.transaction():
            conn.execute("set transaction read only")
            rows = conn.execute(
                """
                select coalesce(string_agg(line, E'\\n' order by line), '') from (
                    select 'op:' || id::text || ':' || stage || ':' || version::text as line
                      from crm.opportunity
                    union all
                    select 'oo:' || id::text || ':' || role || ':' || confirmation
                      from crm.opportunity_organization
                    union all
                    select 'oi:' || id::text || ':' || confirmation from crm.opportunity_interest
                    union all
                    select 'oe:' || id::text || ':' || relation from crm.opportunity_evidence
                    union all
                    select 'or:' || id::text || ':' || confirmation from crm.organization
                    union all
                    select 'ev:' || id::text || ':' || event_type from crm.domain_event
                    union all
                    select 'rc:' || id::text || ':' || status from platform.command_receipt
                ) s
                """
            ).fetchone()[0]
        conn.rollback()
    return hashlib.sha256(rows.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------- the disposable world


def create_database() -> str:
    import psycopg

    name = f"origenlab_test_{uuid.uuid4().hex[:8]}"
    if name in PROTECTED_DATABASES:  # pragma: no cover - a hex name is never one of these
        fail("refusing to create a protected database")
    with psycopg.connect(MAINTENANCE_DSN, autocommit=True) as conn:
        conn.execute(f"create database {name}")
    dsn = f"{MAINTENANCE_DSN.rpartition('/')[0]}/{name}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        reached = assert_connection_is_disposable(conn)
        if reached != name:
            fail(f"expected to reach {name!r}, reached {reached!r}")
        conn.execute(BOOTSTRAP)
        for path in sorted(MIGRATIONS.glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))
    return dsn


def drop_database(dsn: str) -> None:
    import psycopg

    name = dsn.rpartition("/")[2]
    with psycopg.connect(MAINTENANCE_DSN, autocommit=True) as conn:
        conn.execute(
            "select pg_terminate_backend(pid) from pg_stat_activity where datname = %s", (name,)
        )
        conn.execute(f"drop database if exists {name}")


def seed(dsn: str) -> dict[str, Any]:
    """The invented world the case happens in. Nothing here is read from anywhere."""
    import psycopg

    state: dict[str, Any] = {}
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            """
            insert into platform.operator (auth_user_id, email_norm, display_name, role, status)
            values (gen_random_uuid(), 'operador.local@example.invalid', 'Operadora local',
                    'sales', 'active')
            returning id::text
            """
        )
        state["operator_id"] = cur.fetchone()[0]
        cur.execute(
            """
            insert into evidence.source_record (kind, dedupe_key, payload, source_uri)
            values ('gmail_message', 'simulacion-caso-1',
                    '{"subject": "Consulta por homogeneizador ultrasónico"}'::jsonb,
                    'gmail://msg/simulacion-1')
            returning id::text
            """
        )
        state["source_record_id"] = cur.fetchone()[0]
        for key, kind, name in (
            ("university_id", "university", UNIVERSITY),
            ("manufacturer_id", "company", MANUFACTURER),
            ("agent_id", "institution", PURCHASING_AGENT),
        ):
            cur.execute(
                "insert into crm.organization (kind, name, confirmation) "
                "values (%s, %s, 'confirmed') returning id::text, version",
                (kind, name),
            )
            state[key], state[f"{key}_version"] = cur.fetchone()
        # Hielscher is a registered supplier AND manufacturer of OrigenLab's, today, with no
        # end date. This is the fact that makes it refusable as a customer.
        cur.execute(
            "insert into crm.organization_relationship (organization_id, role, valid_from) "
            "values (%s, 'supplier', '2024-01-01'), (%s, 'manufacturer', '2024-01-01')",
            (state["manufacturer_id"], state["manufacturer_id"]),
        )
        cur.execute(
            "insert into catalog.product (manufacturer_organization_id, model_number) "
            "values (%s, %s) returning id::text",
            (state["manufacturer_id"], MODEL),
        )
        state["product_id"] = cur.fetchone()[0]
        cur.execute(
            "insert into comms.mailbox (address_norm) values (%s) returning id::text", (MAILBOX,)
        )
        mailbox_id = cur.fetchone()[0]
        cur.execute(
            "insert into comms.message (mailbox_id, provider_message_id, direction, "
            "internal_date) values (%s, 'simulacion-1', 'inbound', now()) returning id::text",
            (mailbox_id,),
        )
        state["message_id"] = cur.fetchone()[0]
    return state


# --------------------------------------------------------------------- the case


class Runner:
    def __init__(self, repo: V2CaseCommandRepository, operator: OperatorIdentity) -> None:
        self.repo = repo
        self.operator = operator
        self.n = 0

    def run(self, command: str, body: Any) -> dict[str, Any]:
        self.n += 1
        result = self.repo.execute(
            command_name=command,
            operator=self.operator,
            fields=validated_case(command, body),
            idempotency_key=f"simulacion-{self.n}",
            digest=request_digest(command, body),
        )
        print(f"  ✓ {command}")
        return result

    def expect_refusal(self, command: str, body: Any, label: str) -> None:
        self.n += 1
        try:
            self.repo.execute(
                command_name=command,
                operator=self.operator,
                fields=validated_case(command, body),
                idempotency_key=f"simulacion-{self.n}",
                digest=request_digest(command, body),
            )
        except CommandRefused as exc:
            print(f"  ✗ {command} — {exc.code} ({label})")
            return
        fail(f"{command} was accepted and should have been refused: {label}")


def run_case(dsn: str, world: dict[str, Any]) -> str:
    import psycopg

    repo = V2CaseCommandRepository(psycopg.connect, api_dsn(dsn.rpartition("/")[2]))
    operator = OperatorIdentity(
        operator_id=world["operator_id"],
        email_norm="operador.local@example.invalid",
        display_name="Operadora local",
        role="sales",
        status="active",
    )
    runner = Runner(repo, operator)

    print("\nComandos")
    opened = runner.run(
        OPEN_COMMERCIAL_CASE,
        OpenCommercialCaseBody(
            title="Homogeneizador ultrasónico para laboratorio de alimentos",
            origin_source_record_id=world["source_record_id"],
            note="Correo entrante pidiendo cotización por un equipo Hielscher.",
        ),
    )
    case_id = opened["opportunity_id"]
    version = opened["opportunity_version"]

    # The refusal the whole rule exists for, run before the decision it protects: Hielscher is
    # named in the message, and naming it is not the same as it being the customer.
    runner.expect_refusal(
        ADD_CASE_ORGANIZATION,
        AddCaseOrganizationBody(
            opportunity_id=case_id, opportunity_version=version,
            organization_id=world["manufacturer_id"],
            organization_version=world["manufacturer_id_version"],
            role="requesting_institution",
            note="La marca aparece en el asunto del correo.",
        ),
        "un proveedor/fabricante registrado no se vuelve cliente por aparecer en un correo",
    )

    added = runner.run(
        ADD_CASE_ORGANIZATION,
        AddCaseOrganizationBody(
            opportunity_id=case_id, opportunity_version=version,
            organization_id=world["university_id"],
            organization_version=world["university_id_version"],
            role="requesting_institution",
            note="Firma el correo y dice que el equipo es para su laboratorio.",
        ),
    )
    version = added["opportunity_version"]

    for role, note in (
        ("manufacturer", "Fabrica el equipo que se busca."),
        ("supplier", "Es a quien OrigenLab le compraría para atender este caso."),
    ):
        runner.run(
            ADD_CASE_ORGANIZATION,
            AddCaseOrganizationBody(
                opportunity_id=case_id, opportunity_version=version,
                organization_id=world["manufacturer_id"],
                organization_version=world["manufacturer_id_version"],
                role=role, note=note,
            ),
        )

    # An institution whose part nobody has worked out yet. `mentioned` is a value, not a shrug.
    mentioned = runner.run(
        ADD_CASE_ORGANIZATION,
        AddCaseOrganizationBody(
            opportunity_id=case_id, opportunity_version=version,
            organization_id=world["agent_id"],
            organization_version=world["agent_id_version"],
            role="mentioned",
            note="El correo la nombra y todavía no sé qué papel tiene en la compra.",
        ),
    )
    # ...and then it is worked out. The old reading closes and stays readable.
    runner.run(
        SET_CASE_ORGANIZATION_ROLE,
        SetCaseOrganizationRoleBody(
            opportunity_id=case_id, opportunity_version=version,
            opportunity_organization_id=mentioned["opportunity_organization_id"],
            role="purchasing_agent",
            note="Llamé: compra ella por encargo de la universidad.",
        ),
    )

    runner.run(
        RECORD_CASE_INTEREST,
        RecordCaseInterestBody(
            opportunity_id=case_id, opportunity_version=version,
            product_id=world["product_id"],
            manufacturer_organization_id=world["manufacturer_id"],
            model_text=MODEL, quantity=Decimal("1"), quantity_unit="unidad",
            description="Homogeneizador ultrasónico de sobremesa para muestras de alimentos.",
            note="Lo pide por modelo en el cuerpo del correo.",
        ),
    )

    runner.run(
        LINK_CASE_EVIDENCE,
        LinkCaseEvidenceBody(
            opportunity_id=case_id, opportunity_version=version,
            relation="supports_interest", message_id=world["message_id"],
            note="El mensaje describe el equipo y el uso que le van a dar.",
        ),
    )

    moved = runner.run(
        ADVANCE_CASE_STAGE,
        AdvanceCaseStageBody(
            opportunity_id=case_id, opportunity_version=version, stage="qualifying",
            note="Hay institución solicitante confirmada y un equipo identificado.",
        ),
    )
    version = moved["opportunity_version"]
    runner.run(
        ADVANCE_CASE_STAGE,
        AdvanceCaseStageBody(
            opportunity_id=case_id, opportunity_version=version, stage="qualified",
            note="Confirmado por teléfono: tienen presupuesto y plazo.",
        ),
    )
    return case_id


# --------------------------------------------------------------------- reading it back


def show(dsn: str, case_id: str) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "select title, stage, version, organization_id::text, closed_at "
            "from crm.opportunity where id = %s",
            (case_id,),
        )
        title, stage, version, organization_id, closed_at = cur.fetchone()
        print("\nEl caso")
        print(f"  {title}")
        print(f"  etapa {stage} · versión {version} · {'cerrado' if closed_at else 'abierto'}")

        cur.execute(
            """
            select o.name, oo.role, oo.confirmation, oo.valid_to,
                   oo.supplier_exception_reason,
                   (oo.organization_id = (select organization_id from crm.opportunity where id = %s))
              from crm.opportunity_organization oo
              join crm.organization o on o.id = oo.organization_id
             where oo.opportunity_id = %s
             order by oo.valid_to nulls first, o.name, oo.role
            """,
            (case_id, case_id),
        )
        print("\n  Instituciones")
        for name, role, confirmation, valid_to, exception, is_customer in cur.fetchall():
            mark = "  (solicitante del caso)" if is_customer and valid_to is None else ""
            state = "vigente" if valid_to is None else f"cerrada {valid_to}"
            print(f"    · {name}: {role} [{confirmation}, {state}]{mark}")
            if exception:
                print(f"        excepción justificada: {exception}")

        # Both readings at once, as §3.6.1 requires of every surface that shows a case.
        cur.execute(
            """
            select o.name, string_agg(distinct r.role, ' + ' order by r.role)
              from crm.opportunity_organization oo
              join crm.organization o on o.id = oo.organization_id
              join crm.organization_relationship r on r.organization_id = o.id
             where oo.opportunity_id = %s and (r.valid_to is null or r.valid_to > current_date)
             group by o.name
            """,
            (case_id,),
        )
        relationships = cur.fetchall()
        if relationships:
            print("\n  Lo que estas instituciones son para OrigenLab (no para este caso)")
            for name, roles in relationships:
                print(f"    · {name}: {roles} registrado")

        cur.execute(
            """
            select coalesce(p.model_number, i.model_text), m.name, i.quantity, i.quantity_unit,
                   i.confirmation, i.description
              from crm.opportunity_interest i
              left join catalog.product p on p.id = i.product_id
              left join crm.organization m on m.id = i.manufacturer_organization_id
             where i.opportunity_id = %s
            """,
            (case_id,),
        )
        print("\n  Lo que se busca")
        for model, maker, quantity, unit, confirmation, description in cur.fetchall():
            amount = f"{quantity:g} {unit}" if quantity is not None else "cantidad no dicha"
            print(f"    · {model} de {maker} — {amount} [{confirmation}]")
            if description:
                print(f"        {description}")
        print("      (sin monto, sin moneda y sin margen: un interés no es una cotización)")

        cur.execute(
            """
            select e.relation,
                   case when e.source_record_id is not null then 'registro de evidencia'
                        when e.assertion_id is not null then 'aserción'
                        when e.message_id is not null then 'mensaje'
                        else 'aviso de licitación' end,
                   op.display_name
              from crm.opportunity_evidence e
              join platform.operator op on op.id = e.linked_by_operator_id
             where e.opportunity_id = %s and e.unlinked_at is null
             order by e.linked_at
            """,
            (case_id,),
        )
        print("\n  Por qué el caso cree lo que cree")
        for relation, subject, who in cur.fetchall():
            print(f"    · {subject} — {relation} (adjuntada por {who})")

        cur.execute(
            "select event_type, actor_kind, count(*) from crm.domain_event "
            "where command_receipt_id in (select id from platform.command_receipt) "
            "group by 1, 2 order by 1",
        )
        print("\n  Rastro de auditoría")
        for event_type, actor, n in cur.fetchall():
            print(f"    · {event_type} × {n} ({actor})")

        cur.execute(
            "select count(*) from platform.command_receipt where status = 'completed'"
        )
        print(f"    · recibos de comando completados: {cur.fetchone()[0]}")

        # The absences, counted rather than asserted.
        print("\n  Lo que no se tocó")
        for table in (
            "outbound.campaign", "outbound.campaign_recipient", "outbound.contact_control",
            "outbound.send_attempt", "crm.person", "crm.affiliation", "crm.quote",
            "crm.task", "crm.activity", "crm.contact_point",
        ):
            cur.execute(f"select count(*) from {table}")  # noqa: S608 - literal list above
            print(f"    · {table}: {cur.fetchone()[0]} filas")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep", action="store_true",
        help="leave the disposable database in place for inspection (it is still not the clean room)",
    )
    args = parser.parse_args()

    before = clean_room_fingerprint()
    print(f"Sala limpia, huella antes: {before[:16]}…  (leída en solo lectura, nunca escrita)")

    dsn = create_database()
    print(f"Base desechable: {dsn.rpartition('/')[2]}")
    try:
        world = seed(dsn)
        case_id = run_case(dsn, world)
        show(dsn, case_id)
    finally:
        if not args.keep:
            drop_database(dsn)
            print(f"\nBase desechable eliminada: {dsn.rpartition('/')[2]}")
        else:
            print(f"\nBase desechable conservada: {dsn.rpartition('/')[2]}")

    after = clean_room_fingerprint()
    print(f"Sala limpia, huella después: {after[:16]}…")
    if before != after:
        fail("the clean room changed during a simulation; that must never happen")
    print("Sala limpia sin cambios. Nada de esto ocurrió en origenlab_clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

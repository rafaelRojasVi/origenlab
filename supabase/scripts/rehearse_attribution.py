#!/usr/bin/env python3
"""Rehearse `attribute_sender_organization` against the clean room's **real** records.

The unit tests prove the command on fictitious evidence, because this repository is public
and the senders in the queue are real people. What they cannot prove is that the real rows
have the shape the command expects: the real assertion values, the real existing
organization, the real sender addresses. This script closes that gap without recording a
single durable decision.

**How it stays a rehearsal.**

1. `origenlab_clean` is opened **read only**, inside `begin read only`, and every statement
   against it is a `select`. The transaction is rolled back, not committed.
2. The rows it read are **replayed into a disposable database** that this script creates and
   drops -- `origenlab_test_<hex>`, migrated from `supabase/migrations/`. The commands run
   there, as the unprivileged `origenlab_api` role, exactly as they would in a deployment.
3. The clean room is fingerprinted **before and after**. A rehearsal that changed it fails
   loudly rather than being discovered by the next `verify`.

It therefore answers "would this decision work, on these rows, as written?" and never
"decide it". Nothing here connects to the hosted project, Gmail or Drive, and
`origenlab_dev` is refused by name before any connection is opened.

Usage:

    supabase/scripts/rehearse_attribution.py            # rehearse every eligible record
    supabase/scripts/rehearse_attribution.py --verbose  # print each decision's shape

Procedure: docs/OPERATIONS.md §4.1.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import sys
import uuid
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(REPO_ROOT / "apps" / "email-pipeline" / "tests"))

from protected_databases import (  # noqa: E402
    PROTECTED_DATABASES,
    ProtectedDatabaseRefused,
    assert_connection_is_disposable,
)

from origenlab_api.v2.command_repository import V2CommandRepository  # noqa: E402
from origenlab_api.v2.commands import (  # noqa: E402
    ATTACHABLE_USAGE,
    ATTRIBUTE_SENDER_ORGANIZATION,
    CommandRefused,
)
from origenlab_api.v2.identity import OperatorIdentity  # noqa: E402

MIGRATIONS = REPO_ROOT / "supabase" / "migrations"

#: The clean room, and the maintenance login that may create a database beside it. Both are
#: the constants the shell tooling uses; neither is taken from the environment, for the same
#: reason `OL_CLEAN_DBNAME` is not.
CLEAN_DSN = "postgresql://postgres:postgres@127.0.0.1:54332/origenlab_clean"
MAINTENANCE_DSN = "postgresql://postgres:postgres@127.0.0.1:54332/postgres"

#: The runtime role's login, written outside Git by `cleanroom_db.sh api-login`. Only its
#: credentials are used; the database it names is replaced by the disposable one.
API_ENV = pathlib.Path(
    os.environ.get("OL_LOCAL_PRIVATE_ROOT", str(pathlib.Path.home() / "data/origenlab-v2-local"))
) / "api.cleanroom.env"

#: The one refusal a rehearsal expects to see: `shared_mailbox` on an address whose local
#: part is not a recognised desk. It is the rule under test, not a defect in the rows.
EXPECTED_REFUSAL = "named_address_is_not_a_shared_mailbox_by_default"

BOOTSTRAP = """
create schema if not exists extensions authorization postgres;
create extension if not exists btree_gist with schema extensions;
"""


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def api_dsn(database: str) -> str:
    """The `origenlab_api` DSN, pointed at a database of our own."""
    if not API_ENV.is_file():
        fail(
            f"{API_ENV} is missing. Run `supabase/scripts/cleanroom_db.sh api-login` first: "
            "the rehearsal runs as the unprivileged runtime role, not as postgres."
        )
    for line in API_ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("ORIGENLAB_V2_DATABASE_URL="):
            base = line.partition("=")[2].strip().rpartition("/")[0]
            return f"{base}/{database}"
    fail(f"{API_ENV} carries no ORIGENLAB_V2_DATABASE_URL")
    raise AssertionError  # unreachable


# --------------------------------------------------------------------- the clean room, read only


def clean_room_fingerprint(cur: Any) -> str:
    """A digest over everything a decision would move, and nothing that drifts on its own.

    Deliberately not a row count: a command that created an organization *and* resolved an
    assertion would leave some counts unchanged. This hashes the identifiers and the states
    themselves, so any movement at all changes the digest.
    """
    cur.execute(
        """
        select coalesce(string_agg(line, E'\\n' order by line), '') from (
            select 'sr:' || id::text || ':' || review_status || ':' || is_quarantined::text as line
              from evidence.source_record
            union all
            select 'as:' || id::text || ':' || resolution || ':' ||
                   coalesce(resolved_id::text, '-') from evidence.assertion
            union all
            select 'og:' || id::text || ':' || confirmation || ':' || version::text
              from crm.organization
            union all
            select 'cp:' || id::text || ':' || coalesce(organization_id::text, '-') || ':' ||
                   coalesce(person_id::text, '-') || ':' || usage from crm.contact_point
            union all
            select 'rc:' || id::text from platform.command_receipt
            union all
            select 'ev:' || id::text from crm.domain_event
        ) t
        """
    )
    return hashlib.sha256(cur.fetchone()[0].encode("utf-8")).hexdigest()


def read_cases(cur: Any) -> list[dict[str, Any]]:
    """Every pending record that names an institution and a sender address.

    Selected by **shape**, never by a hard-coded id or address: a public script that named
    the real senders would be the leak the privacy guard exists to stop. The shape is the
    thing under test anyway — "a message naming one or more institutions, with one sender
    address still unresolved" is exactly what this command is for.
    """
    cur.execute(
        """
        select sr.id::text as source_record_id,
               sr.payload->>'from_domain' as from_domain,
               count(*) filter (where a.kind = 'organization_name'
                                  and a.resolution = 'unresolved') as names,
               count(*) filter (where a.kind = 'contact_address'
                                  and a.resolution = 'unresolved') as addresses
          from evidence.source_record sr
          join evidence.assertion a on a.source_record_id = sr.id
         where sr.kind = 'gmail_message'
           and sr.review_status = 'pending'
           and not sr.is_quarantined
         group by sr.id
        having count(*) filter (where a.kind = 'organization_name'
                                 and a.resolution = 'unresolved') > 0
           and count(*) filter (where a.kind = 'contact_address'
                                 and a.resolution = 'unresolved') = 1
         order by names desc, sr.id
        """
    )
    records = [dict(zip([d[0] for d in cur.description], row, strict=True)) for row in cur]

    cases: list[dict[str, Any]] = []
    for record in records:
        cur.execute(
            "select id::text as id, kind, value_norm, value->>'observed_value' as observed "
            "from evidence.assertion where source_record_id = %s and resolution = 'unresolved' "
            "order by kind, value_norm",
            (record["source_record_id"],),
        )
        assertions = [dict(zip([d[0] for d in cur.description], row, strict=True)) for row in cur]
        names = [a for a in assertions if a["kind"] == "organization_name"]
        address = next(a for a in assertions if a["kind"] == "contact_address")

        for name in names:
            cur.execute(
                "select id::text as id, name, kind, confirmation, version from crm.organization "
                "where lower(name) = %s and merged_into_organization_id is null",
                (name["value_norm"],),
            )
            row = cur.fetchone()
            name["existing"] = (
                dict(zip([d[0] for d in cur.description], row, strict=True)) if row else None
            )

        # The sender's address may already be a channel the CRM proposed, unattributed. That
        # is the common case in this queue and the command's `linked` path, so it is part of
        # the shape being rehearsed.
        cur.execute(
            "select id::text as id, organization_id::text as organization_id, "
            "person_id::text as person_id, usage from crm.contact_point "
            "where kind = 'email' and value_norm = %s",
            (address["value_norm"],),
        )
        row = cur.fetchone()
        existing_contact = (
            dict(zip([d[0] for d in cur.description], row, strict=True)) if row else None
        )
        cases.append(
            {
                "source_record_id": record["source_record_id"],
                "from_domain": record["from_domain"],
                "names": names,
                "address": address,
                "existing_contact": existing_contact,
            }
        )
    return cases


# ----------------------------------------------------------------- the disposable rehearsal room


def build_disposable(psycopg: Any) -> str:
    name = f"origenlab_test_{uuid.uuid4().hex[:8]}"
    if name.split("/")[-1] in PROTECTED_DATABASES:  # pragma: no cover - impossible by construction
        raise ProtectedDatabaseRefused(name)
    with psycopg.connect(MAINTENANCE_DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f"create database {name}")
    dsn = f"{MAINTENANCE_DSN.rpartition('/')[0]}/{name}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert_connection_is_disposable(conn)
        with conn.cursor() as cur:
            cur.execute(BOOTSTRAP)
            for path in sorted(MIGRATIONS.glob("*.sql")):
                cur.execute(path.read_text(encoding="utf-8"))
    return name


def drop_disposable(psycopg: Any, name: str) -> None:
    with psycopg.connect(MAINTENANCE_DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "select pg_terminate_backend(pid) from pg_stat_activity where datname = %s", (name,)
        )
        cur.execute(f"drop database if exists {name}")


def replay_case(psycopg: Any, dsn: str, case: dict[str, Any]) -> dict[str, Any]:
    """Recreate one clean-room record, its assertions and its surroundings, verbatim."""
    state: dict[str, Any] = {"names": []}
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert_connection_is_disposable(conn)
        with conn.cursor() as cur:
            cur.execute("set role origenlab_owner")
            cur.execute(
                "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                "values (gen_random_uuid(), %s, 'Rehearsal Operator', 'sales', 'active') "
                "returning id::text",
                (f"rehearsal-{uuid.uuid4().hex[:8]}@example.invalid",),
            )
            state["operator_id"] = cur.fetchone()[0]

            cur.execute(
                "insert into evidence.source_record (kind, dedupe_key, payload) "
                "values ('gmail_message', %s, %s::jsonb) returning id::text",
                (f"rehearsal:{case['source_record_id']}", '{"rehearsal": true}'),
            )
            state["source_record_id"] = cur.fetchone()[0]

            for name in case["names"]:
                cur.execute(
                    "insert into evidence.assertion "
                    "(source_record_id, kind, value_norm, value, resolution) "
                    "values (%s, 'organization_name', %s, %s::jsonb, 'unresolved') "
                    "returning id::text",
                    (state["source_record_id"], name["value_norm"],
                     f'{{"observed_value": {_json_string(name["observed"] or name["value_norm"])}}}'),
                )
                entry = {
                    "assertion_id": cur.fetchone()[0],
                    "value_norm": name["value_norm"],
                    "observed": name["observed"],
                    "existing": None,
                }
                if name["existing"]:
                    cur.execute(
                        "insert into crm.organization (kind, name, confirmation) "
                        "values (%s, %s, %s) returning id::text, version",
                        (name["existing"]["kind"], name["existing"]["name"],
                         name["existing"]["confirmation"]),
                    )
                    organization_id, version = cur.fetchone()
                    entry["existing"] = {"id": organization_id, "version": version,
                                         "name": name["existing"]["name"],
                                         "confirmation": name["existing"]["confirmation"]}
                state["names"].append(entry)

            address = case["address"]
            cur.execute(
                "insert into evidence.assertion "
                "(source_record_id, kind, value_norm, value, resolution) "
                "values (%s, 'contact_address', %s, %s::jsonb, 'unresolved') returning id::text",
                (state["source_record_id"], address["value_norm"],
                 f'{{"observed_value": {_json_string(address["observed"] or address["value_norm"])}}}'),
            )
            state["address_assertion_id"] = cur.fetchone()[0]
            state["address"] = address["value_norm"]

            if case["existing_contact"]:
                cur.execute(
                    "insert into crm.contact_point (kind, value_norm, value_display, usage, confirmation) "
                    "values ('email', %s, %s, %s, 'machine_proposed')",
                    (address["value_norm"], address["observed"] or address["value_norm"],
                     case["existing_contact"]["usage"]),
                )
    return state


def _json_string(value: str) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def rehearse(psycopg: Any, case: dict[str, Any], verbose: bool) -> list[str]:
    """Run the real command once per institution the message names, **and per relationship**.

    **Each rehearsal gets its own database.** The sender belongs to exactly one of the named
    institutions, so rehearsing the second name after the first has already been applied
    would be rehearsing a sequence no operator will ever perform — and it would trip over
    the command's own refusals for reasons that say nothing about the real decision. A fresh
    room per decision is the only way each one is measured against the state it will really
    meet: the clean room's, as it is now.

    **Why both relationships, and why the script picks neither.** `usage` has no default any
    more, because `shared_mailbox` and `individual_owner_unknown` make opposite claims about
    a human being and only an operator can say which is true. A rehearsal that hard-coded one
    would be rehearsing this script's opinion. So it runs both and reports what the boundary
    answers to each — which is the useful thing anyway: it shows the operator, before they
    decide anything, that a named sender address is refused as a shared mailbox and accepted
    as one whose owner is not recorded.
    """
    lines: list[str] = []
    for index, source_name in enumerate(case["names"]):
        for relationship in ATTACHABLE_USAGE:
            database = build_disposable(psycopg)
            dsn = f"{MAINTENANCE_DSN.rpartition('/')[0]}/{database}"
            try:
                state = replay_case(psycopg, dsn, case)
                target = state["names"][index]
                repo = V2CommandRepository(psycopg.connect, api_dsn(database))
                operator = OperatorIdentity(
                    operator_id=state["operator_id"],
                    email_norm="rehearsal@example.invalid",
                    display_name="Rehearsal Operator",
                    role="sales",
                    status="active",
                )
                fields: dict[str, Any] = {
                    "source_record_id": state["source_record_id"],
                    "note": "ensayo: el remitente pertenece a esta institución",
                    "organization_assertion_id": target["assertion_id"],
                    "address_assertion_id": state["address_assertion_id"],
                    "usage": relationship,
                    # No override. An override is an operator saying how they know a named
                    # address is a desk, and this script knows nothing of the sort.
                    "shared_mailbox_override_note": None,
                }
                if target["existing"]:
                    fields |= {
                        "target": "existing",
                        "organization_id": target["existing"]["id"],
                        "organization_version": target["existing"]["version"],
                    }
                    route = f"confirmar la existente «{target['existing']['name']}»"
                else:
                    fields |= {"target": "new", "kind": "unknown", "name_display": None}
                    route = f"crear nueva «{target['observed'] or target['value_norm']}»"

                try:
                    result = repo.execute(
                        command_name=ATTRIBUTE_SENDER_ORGANIZATION,
                        operator=operator,
                        fields=fields,
                        idempotency_key=f"rehearsal-{uuid.uuid4().hex}",
                        digest="0" * 64,
                    )
                except CommandRefused as exc:
                    lines.append(
                        f"    RECHAZADO  {route} · {relationship}\n"
                        f"               {exc.code} — {exc.message}"
                    )
                    continue

                lines.append(
                    f"    OK         {route} · {relationship}\n"
                    f"               creó {result['created'] or '[]'}; "
                    f"aserciones que quedan sin resolver: "
                    f"{result['assertions_left_unresolved']}; "
                    f"registro queda '{result['review_status']}'"
                )
                if verbose:
                    lines.append(
                        f"               organization_id={result['organization_id']} "
                        f"contact_point_id={result['contact_point_id']}"
                    )
                _assert_nothing_extra(psycopg, dsn, lines)
            finally:
                drop_disposable(psycopg, database)
        del source_name
    return lines


def _assert_nothing_extra(psycopg: Any, dsn: str, lines: list[str]) -> None:
    """The five tables a domain-shaped guess would touch, all still empty."""
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "select (select count(*) from crm.person), "
            "(select count(*) from crm.organization_domain), "
            "(select count(*) from crm.affiliation), "
            "(select count(*) from crm.opportunity), "
            "(select count(*) from crm.quote)"
        )
        counts = cur.fetchone()
    if any(counts):
        lines.append(f"    !! rehearsal produced {counts} in person/domain/affiliation/opp/quote")
        raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="print ids of what was created")
    args = parser.parse_args()

    try:
        import psycopg
    except ModuleNotFoundError:
        fail("psycopg is required; run this from apps/api's virtualenv")
        return 1

    # Read the clean room, in a transaction that can only read.
    with psycopg.connect(CLEAN_DSN) as conn, conn.cursor() as cur:
        cur.execute("begin read only")
        before = clean_room_fingerprint(cur)
        cases = read_cases(cur)
        conn.rollback()

    if not cases:
        print("no pending record names an institution and one unresolved sender address")
        return 0

    print(f"clean room: {len(cases)} record(s) eligible; fingerprint {before[:16]}…")
    print("rehearsal rooms: one disposable database per decision, each dropped after it\n")
    failures = 0
    for case in cases:
        names = ", ".join(
            f"«{n['observed'] or n['value_norm']}»"
            + (" [ya existe]" if n["existing"] else " [nueva]")
            for n in case["names"]
        )
        channel = (
            "ya es canal propuesto, sin institución"
            if case["existing_contact"] and not case["existing_contact"]["organization_id"]
            else "dirección nueva"
            if not case["existing_contact"]
            else "canal ya atribuido"
        )
        print(f"  registro {case['source_record_id'][:8]} · dominio {case['from_domain']}")
        print(f"    nombra: {names}")
        print(f"    remitente: {channel}")
        for line in rehearse(psycopg, case, args.verbose):
            print(line)
            # `named_address_...` is the refusal the boundary is *supposed* to produce here,
            # and a rehearsal that exited non-zero for it would be reporting the rule working
            # as a failure. Every other refusal still is one: it means the real rows do not
            # have the shape the command expects.
            if line.strip().startswith("RECHAZADO") and EXPECTED_REFUSAL not in line:
                failures += 1
        print("")

    with psycopg.connect(CLEAN_DSN) as conn, conn.cursor() as cur:
        cur.execute("begin read only")
        after = clean_room_fingerprint(cur)
        conn.rollback()

    if after != before:
        fail(f"the clean room changed during the rehearsal: {before[:16]}… -> {after[:16]}…")
    print(f"clean room unchanged: fingerprint {after[:16]}… (nothing was decided)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

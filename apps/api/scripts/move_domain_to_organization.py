#!/usr/bin/env python3
"""Give an email domain to the organization it really belongs to, and fix the cases a rule
opened under a stray organization named after that domain (owner 2026-10-12: «uc.cl» is the
Pontificia Universidad Católica de Chile we already have; it should be assigned at once).

    uv run python scripts/move_domain_to_organization.py --domain uc.cl --to <org id> --operator <id or name>
    uv run python scripts/move_domain_to_organization.py … --apply

With --apply, through the dashboard's own commands as the named operator: the domain leaves
every other organization holding it (`remove-organization-domain`) and joins the target
(`add-organization-domain`), so the email → cases rules match it from now on; on every case
where a stray organization holding the domain is the requesting institution, that reading
becomes «mentioned» and the target is named requester (`set-case-organization-role`, then
`add-case-organization`); a stray left with no case is archived. Keys derived from the domain,
the case and the step: a second --apply replays.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from origenlab_api.v2.case_commands import (
    ADD_CASE_ORGANIZATION,
    ADVANCE_CASE_STAGE,
    AdvanceCaseStageBody,
    SET_CASE_ORGANIZATION_ROLE,
    AddCaseOrganizationBody,
    SetCaseOrganizationRoleBody,
    validated_case,
)
from origenlab_api.v2.commands import CommandRefused, request_digest

NOTE = "El dominio pertenece a esta institución (dueño, 2026-10-12)"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--domain", required=True)
    p.add_argument("--to", required=True, help="the organization id the domain belongs to")
    p.add_argument("--operator", required=True)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args(argv)
    domain = args.domain.strip().lower()

    import psycopg

    from origenlab_api.settings import get_settings
    from origenlab_api.v2.case_command_repository import V2CaseCommandRepository
    from origenlab_api.v2.crm_authoring import V2CrmAuthoringRepository
    from origenlab_api.v2.crm_authoring_routes import (
        AddOrganizationDomainBody,
        ArchiveOrganizationBody,
        RemoveOrganizationDomainBody,
    )

    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    from load_missing_cases import _one, _operator  # noqa: E402

    settings = get_settings()
    target = settings.v2_database_target()

    def connect(dsn: str, **kw: Any) -> Any:
        return psycopg.connect(dsn, **{**target.connect_options, **kw})

    t = settings.v2_statement_timeout_ms
    cases = V2CaseCommandRepository(connect, target.dsn, statement_timeout_ms=t)
    authoring = V2CrmAuthoringRepository(connect, target.dsn, statement_timeout_ms=t)
    op = _operator(cases, args.operator)
    key = lambda step: f"domain-move:{domain}:{step}"[:200]  # noqa: E731

    def org(oid: str) -> dict[str, Any]:
        row = _one(cases, "select id::text as id, name, version, confirmation, status from crm.organization where id = %s", (oid,))
        if row is None:
            raise SystemExit(f"no organization {oid}")
        return row

    def author(name: str, body: Any, step: str) -> dict[str, Any]:
        return authoring.execute(command_name=name, operator=op, fields=body.model_dump(),
                                 idempotency_key=key(step), digest=request_digest(name, body))

    def case_cmd(name: str, body: Any, step: str) -> dict[str, Any]:
        return cases.execute(command_name=name, operator=op, fields=validated_case(name, body),
                             idempotency_key=key(step), digest=request_digest(name, body))

    goal = org(args.to)
    with cases._write() as cur:
        cur.execute("""select d.id::text as domain_id, d.organization_id::text as organization_id, d.scope
                         from crm.organization_domain d
                        where d.domain_norm = %s and d.removed_at is null""", (domain,))
        holders = [dict(zip([c.name for c in cur.description], r)) for r in cur.fetchall()]
        # Strays: whoever holds the domain now, or held it before (a rerun after the domain moved).
        cur.execute("""select distinct organization_id::text from crm.organization_domain
                        where domain_norm = %s and organization_id <> %s""", (domain, goal["id"]))
        strays = sorted({r[0] for r in cur.fetchall()})
        cur.execute("""select oo.id::text as row_id, oo.opportunity_id::text as opportunity_id,
                              oo.organization_id::text as organization_id, o.stage
                         from crm.opportunity_organization oo join crm.opportunity o on o.id = oo.opportunity_id
                        where oo.organization_id = any(%s::uuid[]) and oo.role = 'requesting_institution'
                          and oo.valid_to is null""", (strays,))
        moves = [dict(zip([c.name for c in cur.description], r)) for r in cur.fetchall()]
    held = any(h["organization_id"] == goal["id"] for h in holders)
    print(f"target: {goal['name']} ({goal['id'][:8]}, {goal['confirmation']}); domain already there: {held}")
    for s in strays:
        print(f"stray holder: {org(s)['name']} ({s[:8]})")
    for m in moves:
        print(f"case {m['opportunity_id'][:8]} ({m['stage']}): requester {m['organization_id'][:8]} → {goal['id'][:8]}")
    if not args.apply:
        return 0

    failures = 0
    try:
        for h in holders:
            if h["organization_id"] == goal["id"]:
                continue
            o = org(h["organization_id"])
            author("remove-organization-domain", RemoveOrganizationDomainBody(
                organization_id=o["id"], expected_version=o["version"], domain_id=h["domain_id"], note=NOTE),
                f"remove-{o['id']}")
            print(f"OK   domain removed from {o['name']}")
        if not held:
            g = org(goal["id"])
            scope = holders[0]["scope"] if holders else "shared"
            author("add-organization-domain", AddOrganizationDomainBody(
                organization_id=g["id"], expected_version=g["version"], domain=domain, scope=scope, note=NOTE), "add")
            print(f"OK   domain added to {g['name']}")
    except CommandRefused as exc:
        print(f"FAIL domain: {exc.code}")
        return 1
    # A case from `qualified` on must keep a requester, so the swap happens at `qualifying`: step
    # back, swap, and walk back to the stage it was at — each step its own recorded command.
    back = {"negotiating": ["quoting", "qualified", "qualifying"], "quoting": ["qualified", "qualifying"],
            "qualified": ["qualifying"]}
    for m in moves:
        oid = m["opportunity_id"]
        try:
            if m["stage"] in ("won", "lost", "abandoned"):
                print(f"SKIP case {oid[:8]}: closed ({m['stage']})"); continue
            version = _one(cases, "select version from crm.opportunity where id = %s", (oid,))["version"]  # type: ignore[index]
            path = back.get(m["stage"], [])
            for stage in path:
                version = case_cmd(ADVANCE_CASE_STAGE, AdvanceCaseStageBody(
                    opportunity_id=oid, opportunity_version=version, stage=stage, note=NOTE), f"back-{oid}-{stage}")["opportunity_version"]
            version = case_cmd(SET_CASE_ORGANIZATION_ROLE, SetCaseOrganizationRoleBody(
                opportunity_id=oid, opportunity_version=version,
                opportunity_organization_id=m["row_id"], role="mentioned", note=NOTE), f"demote-{m['row_id']}")["opportunity_version"]
            g = org(goal["id"])
            version = case_cmd(ADD_CASE_ORGANIZATION, AddCaseOrganizationBody(
                opportunity_id=oid, opportunity_version=version,
                organization_id=g["id"], organization_version=g["version"], role="requesting_institution",
                note=NOTE), f"name-{oid}")["opportunity_version"]
            for stage in list(reversed(path[:-1])) + ([m["stage"]] if path else []):
                version = case_cmd(ADVANCE_CASE_STAGE, AdvanceCaseStageBody(
                    opportunity_id=oid, opportunity_version=version, stage=stage, note=NOTE), f"forward-{oid}-{stage}")["opportunity_version"]
            print(f"OK   case {oid[:8]}: requester is now {g['name']}, back at {m['stage']}")
        except CommandRefused as exc:
            print(f"FAIL case {m['opportunity_id'][:8]}: {exc.code} — {exc}")
            failures += 1
    for s in strays:
        live = _one(cases, """select count(*) as n from crm.opportunity_organization
                               where organization_id = %s and valid_to is null and role <> 'mentioned'""", (s,))
        o = org(s)
        if live and live["n"] == 0 and o["status"] == "active":
            try:
                author("archive-organization", ArchiveOrganizationBody(
                    organization_id=o["id"], expected_version=o["version"], note=NOTE), f"archive-{o['id']}")
                print(f"OK   archived {o['name']}")
            except CommandRefused as exc:
                print(f"FAIL archive {o['name']}: {exc.code}")
                failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

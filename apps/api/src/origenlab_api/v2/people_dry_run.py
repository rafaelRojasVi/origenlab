"""What the people rules would do right now — read once by the owner before switching
`ORIGENLAB_V2_AUTO_PEOPLE_ENABLED` on (people first, Task 10).

    cd apps/api && uv run python -m origenlab_api.v2.people_dry_run

Reads the same snapshot the automatic run reads (`PeopleRulesRepository.read_snapshot`) inside a
`set transaction read only` transaction that is rolled back, plans it with `people_rules.plan_people`,
and prints counts per rule and tier and up to 20 sample actions — up to 4 of each rule that changes
something (P5, P3, P2, P4, P1b) and of the W1 guesses first, P1 only to fill the rest. **Never writes**: no command, no
receipt, no row. Addresses are masked (`p***@hotmail…`) and person names cut to initials, so the
output can be read with someone else in the room; institutions are named.

The database is the API's own (`ORIGENLAB_V2_DATABASE_URL` and, for the hosted one,
`ORIGENLAB_V2_DATABASE_REMOTE` / `_EXPECTED_HOST` / `_SSLROOTCERT`, as `settings.v2_database_target`).
"""

from __future__ import annotations

import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from origenlab_api.v2.command_core import DEFAULT_COMMAND_TIMEOUT_MS
from origenlab_api.v2.people_rules import RULE_IDS, PeopleAction, PeopleSnapshot, plan_people

SAMPLE_ACTIONS = 20
#: Up to this many samples of each rule before P1 fills the rest: after a backfill almost every
#: action is a P1, and the owner must see the merges, new persons and guesses.
SAMPLES_PER_RULE = 4
#: The order the samples are shown in: the costliest mistakes first.
SAMPLE_ORDER = ("P5", "P3", "P2", "P4", "P1b", "W1")


def mask_address(address: str | None) -> str:
    """`pjsoto@hotmail.invalid` → `p***@hotmail…`: the first letter and the domain's first label."""
    address = (address or "").strip().lower()
    if not address:
        return "—"
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain.split('.', 1)[0]}…"


def mask_name(name: str | None) -> str:
    """`Juan Pedro Soto` → `J. P. S.`."""
    words = [w for w in (name or "").split() if w[:1].isalpha()]
    return " ".join(f"{w[0].upper()}." for w in words) or "—"


def _who(action: PeopleAction, snap: PeopleSnapshot) -> str:
    fields = action.fields
    if action.rule_id == "P5":
        def name(pid: Any) -> str:
            p = snap.persons.get(pid)
            return mask_name(p.display_name if p else None)
        return f"{name(fields.get('winner_person_id'))} ← {name(fields.get('loser_person_id'))}"
    if action.rule_id == "P3":
        return mask_name(fields.get("display_name"))
    p = snap.persons.get(action.person_id or "")
    return mask_name(p.display_name if p else None)


def _what(action: PeopleAction, snap: PeopleSnapshot) -> str:
    fields = action.fields
    org = snap.organizations.get(fields.get("organization_id") or "")
    at = f" en {org.name}" if org else ""
    return {
        "P1": "dirección ya conocida",
        "P1b": f"nombre nuevo ({mask_name(fields.get('name'))}, de {fields.get('source')})",
        "P2": f"dirección nueva para la persona{at}",
        "P3": f"persona nueva{at}",
        "P4": f"institución nueva{at}",
        "P5": "fusión de dos fichas",
        "W1": "pregunta «¿Es …?» con Sí/No",
    }.get(action.rule_id, action.rule_id)


def samples_by_rule(actions: Sequence[PeopleAction], samples: int = SAMPLE_ACTIONS) -> list[PeopleAction]:
    """Up to `SAMPLES_PER_RULE` actions of each rule in `SAMPLE_ORDER`, then P1 (and any rule not
    yet shown) to fill up to `samples`."""
    chosen: list[int] = []
    for rule in SAMPLE_ORDER:
        chosen += [i for i, a in enumerate(actions) if a.rule_id == rule][:SAMPLES_PER_RULE]
    taken = set(chosen)
    chosen += [i for i in range(len(actions)) if i not in taken]
    return [actions[i] for i in chosen[:samples]]


def report(snap: PeopleSnapshot, actions: Sequence[PeopleAction], *, samples: int = SAMPLE_ACTIONS) -> str:
    """The dry run's text: readings, counts per rule and tier, then sample actions — masked."""
    counts = Counter((a.rule_id, a.tier) for a in actions)
    lines = [
        "Reglas de personas — simulación (no escribe nada)",
        f"readings: {len(snap.readings)}",
        f"actions: {len(actions)} (strong {sum(1 for a in actions if a.tier == 'strong')}, "
        f"weak {sum(1 for a in actions if a.tier == 'weak')})",
        "per rule:",
    ]
    order = {r: i for i, r in enumerate(RULE_IDS)}
    for (rule, tier), n in sorted(counts.items(), key=lambda kv: (order.get(kv[0][0], 99), kv[0][1])):
        lines.append(f"  {rule} {tier}: {n}")
    shown = samples_by_rule(actions, samples)
    lines.append(f"sample actions ({len(shown)}, up to {SAMPLES_PER_RULE} per rule, P1 last):")
    for a in shown:
        lines.append(f"  - {a.rule_id} {a.tier} {mask_address(a.address)} · {_who(a, snap)} · {_what(a, snap)}")
    return "\n".join(lines)


@contextmanager
def read_only(connect: Any, dsn: str, statement_timeout_ms: int) -> Iterator[Any]:
    """One cursor in a `set transaction read only` transaction that is always rolled back: any
    write attempted in it fails (`ReadOnlySqlTransaction`)."""
    with connect(dsn, autocommit=False) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("set transaction read only")
                cur.execute(f"set local statement_timeout = {int(statement_timeout_ms)}")
                yield cur
        finally:
            conn.rollback()


def dry_run(connect: Any, dsn: str, statement_timeout_ms: int = DEFAULT_COMMAND_TIMEOUT_MS) -> str:
    """Read the snapshot read-only, plan it, roll back, and return the report. The statement timeout
    is four times the command one, as the repository's own snapshot read."""
    from origenlab_api.v2.people_rules_repository import PeopleRulesRepository

    repo = PeopleRulesRepository(connect, dsn, statement_timeout_ms)
    with read_only(connect, dsn, int(statement_timeout_ms) * 4) as cur:
        snap = repo.read_snapshot(cur)
    return report(snap, plan_people(snap))


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
                            ).parse_args(list(argv) if argv is not None else None)
    from origenlab_api.settings import get_settings

    try:
        settings = get_settings()
        target = settings.v2_database_target()
    except ValueError as exc:
        print(f"people_dry_run: {exc}", file=sys.stderr)
        return 2

    import psycopg

    def connect(dsn: str, **kw: Any) -> Any:
        return psycopg.connect(dsn, **{**target.connect_options, **kw})

    print(dry_run(connect, target.dsn, settings.v2_statement_timeout_ms))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Operator roster: only named, approved @domain accounts become dashboard operators.

Database proofs run in a disposable `origenlab_test_<hex>` database as the `origenlab_api`
login, never against a real database. Every address here is fictitious.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from origenlab_api.v2.operator_roster import (
    RosterRefused,
    apply,
    compute_plan,
    describe,
    load_roster,
    mask_email,
    parse_roster,
    plan,
)
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

DOMAIN = "example-workspace.test"
OTHER = "second-workspace.test"
REPO_ROOT = Path(__file__).resolve().parents[3]


def people() -> list[dict[str, str]]:
    return [
        {"email": f"Admin.One@{DOMAIN}", "display_name": "Admin One", "role": "admin"},
        {"email": f"admin.two@{DOMAIN}", "display_name": "Admin Two", "role": "admin"},
        {"email": f"sales.one@{DOMAIN}", "display_name": "Sales One", "role": "sales"},
    ]


# ------------------------------------------------------------------ validation (no database)


def test_a_valid_roster_is_normalised() -> None:
    entries = parse_roster(people(), workspace_domain=DOMAIN)
    assert [e.email_norm for e in entries][0] == f"admin.one@{DOMAIN}"
    assert {e.status for e in entries} == {"active"}


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (lambda p: p[0].update(email="someone@gmail.com"), "is not an @"),
        (lambda p: p[0].update(email=f"x@sub.{DOMAIN}"), "is not an @"),
        (lambda p: p[0].update(email="not-an-address"), "not an email"),
        (lambda p: p[1].update(email=p[0]["email"].lower()), "appears twice"),
        (lambda p: p[0].update(role="owner"), "role must be"),
        (lambda p: p[0].pop("role"), "role must be"),
        (lambda p: p[0].update(status="pending"), "status must be"),
        (lambda p: p[0].update(display_name="  "), "display_name"),
        (lambda p: p[0].update(password="x"), "unknown field"),
    ],
)
def test_an_unsafe_or_malformed_roster_is_refused(mutate, reason) -> None:
    roster = people()
    mutate(roster)
    with pytest.raises(RosterRefused, match=reason):
        parse_roster(roster, workspace_domain=DOMAIN)


def test_an_empty_roster_is_refused() -> None:
    with pytest.raises(RosterRefused):
        parse_roster([], workspace_domain=DOMAIN)


def test_a_roster_inside_the_public_repository_is_refused(tmp_path: Path) -> None:
    inside = REPO_ROOT / "apps" / "api" / "operator_roster.json"
    with pytest.raises(RosterRefused, match="outside the repository"):
        load_roster(inside, workspace_domain=DOMAIN, repo_root=REPO_ROOT)
    outside = tmp_path / "roster.json"
    outside.write_text(json.dumps(people()), encoding="utf-8")
    assert len(load_roster(outside, workspace_domain=DOMAIN, repo_root=REPO_ROOT)) == 3


def test_output_masks_every_address() -> None:
    assert mask_email(f"admin.one@{DOMAIN}") == f"a***@{DOMAIN}"


def test_the_confirmation_count_comes_from_the_plan_not_a_fixed_number() -> None:
    entries = parse_roster(people(), workspace_domain=DOMAIN)
    existing = {
        entries[0].email_norm: {"id": "1", "email_norm": entries[0].email_norm,
                                "display_name": "Admin One", "role": "admin", "status": "active"},
        entries[1].email_norm: {"id": "2", "email_norm": entries[1].email_norm,
                                "display_name": "Admin Two", "role": "sales", "status": "active"},
    }
    result = compute_plan(entries, existing)
    assert [c.action for c in result.changes] == ["unchanged", "update", "insert"]
    assert "to apply this plan: --apply --confirm-changes 2" in describe(result)
    settled = compute_plan(entries[:1], existing)
    assert "nothing to apply" in describe(settled)


# ------------------------------------------------------------------ database (disposable)


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


@needs_db
def test_plan_reads_only_and_apply_creates_exactly_the_named_operators(db) -> None:
    import psycopg

    entries = parse_roster(people(), workspace_domain=DOMAIN)
    with psycopg.connect(runtime_dsn(db)) as conn:
        before = operator_count(conn)
        first = plan(conn, entries)
        assert [c.action for c in first.changes] == ["insert"] * 3
        assert operator_count(conn) == before  # the plan wrote nothing

        with pytest.raises(RosterRefused, match="not the 2"):
            apply(conn, entries, expected_changes=2)
        assert operator_count(conn) == before  # a mismatched confirmation writes nothing

        apply(conn, entries, expected_changes=3)
        with conn.cursor() as cur:
            cur.execute(
                "select email_norm, role, status from platform.operator where email_norm like %s order by 1",
                (f"%@{DOMAIN}",),
            )
            assert cur.fetchall() == [
                (f"admin.one@{DOMAIN}", "admin", "active"),
                (f"admin.two@{DOMAIN}", "admin", "active"),
                (f"sales.one@{DOMAIN}", "sales", "active"),
            ]

        again = plan(conn, entries)
        assert again.pending == ()
        output = "\n".join(describe(again))
        for local in ("admin.one", "admin.two", "sales.one"):
            assert f"{local}@" not in output  # every address is masked


@needs_db
def test_a_role_change_updates_one_row_and_leaves_everyone_else_alone(db) -> None:
    import psycopg

    # Own addresses, so this proof does not depend on the test above having run first.
    team = [
        {"email": f"lead@{OTHER}", "display_name": "Lead", "role": "admin"},
        {"email": f"seller@{OTHER}", "display_name": "Seller", "role": "sales"},
    ]
    with psycopg.connect(runtime_dsn(db)) as conn:
        apply(conn, parse_roster(team, workspace_domain=OTHER), expected_changes=2)
        with conn.cursor() as cur:
            cur.execute(
                "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                "values (gen_random_uuid(), %s, 'Outside Roster', 'viewer', 'active')",
                (f"outsider@{OTHER}",),
            )
        conn.commit()

        team[1]["role"] = "viewer"
        team[0]["status"] = "disabled"
        entries = parse_roster(team, workspace_domain=OTHER)
        result = plan(conn, entries)
        assert [(c.action, c.fields) for c in result.pending] == [
            ("update", ("status",)),
            ("update", ("role",)),
        ]
        applied = apply(conn, entries, expected_changes=2)
        assert applied.left_alone >= 1
        with conn.cursor() as cur:
            cur.execute(
                "select email_norm, role, status, version from platform.operator "
                "where email_norm like %s order by 1",
                (f"%@{OTHER}",),
            )
            assert cur.fetchall() == [
                (f"lead@{OTHER}", "admin", "disabled", 2),
                (f"outsider@{OTHER}", "viewer", "active", 1),  # never touched
                (f"seller@{OTHER}", "viewer", "active", 2),
            ]


def operator_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("select count(*) from platform.operator")
        return cur.fetchone()[0]


# ------------------------------------------------------------------ command line (no database)


def cli(argv: list[str], capsys) -> tuple[int, str]:
    import importlib.util

    # scripts/ isn't a package; load it by path.
    path = Path(__file__).resolve().parents[1] / "scripts" / "operator_roster.py"
    spec = importlib.util.spec_from_file_location("operator_roster_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    code = module.main(argv)
    captured = capsys.readouterr()
    return code, captured.err


@pytest.fixture
def roster_file(tmp_path: Path) -> Path:
    path = tmp_path / "roster.json"
    path.write_text(json.dumps(people()), encoding="utf-8")
    return path


def test_cli_apply_without_a_confirmed_count_is_refused(roster_file, capsys, monkeypatch) -> None:
    monkeypatch.setenv("ROSTER_TEST_DSN", "postgresql://unused.invalid/none")
    code, err = cli(["--roster", str(roster_file), "--domain", DOMAIN, "--dsn-env", "ROSTER_TEST_DSN", "--apply"], capsys)
    assert code == 2 and "--confirm-changes" in err


def test_cli_without_a_dsn_is_refused(roster_file, capsys, monkeypatch) -> None:
    monkeypatch.delenv("ROSTER_TEST_DSN", raising=False)
    code, err = cli(["--roster", str(roster_file), "--domain", DOMAIN, "--dsn-env", "ROSTER_TEST_DSN"], capsys)
    assert code == 2 and "ROSTER_TEST_DSN is not set" in err


def test_cli_refuses_a_roster_from_another_domain(roster_file, capsys) -> None:
    code, err = cli(["--roster", str(roster_file)], capsys)  # default domain origenlab.cl
    assert code == 2 and "is not an @origenlab.cl account" in err
    assert "admin.one@" not in err.lower()

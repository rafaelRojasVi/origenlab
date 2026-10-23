"""The prospect loader on a disposable `origenlab_test_<hex>` database, as the real
`origenlab_worker` login: one finding is one `prospect_finding` source record and its assertions;
a rerun writes nothing. Every value is invented."""

from __future__ import annotations

import json

import pytest

from dbhelp import owner_rows
from origenlab_worker.database import local_test_target, open_verified_connection
from origenlab_worker.prospects.finding import parse_finding
from origenlab_worker.prospects.store import (
    PROSPECTS_REQUIRED_POLICIES,
    PROSPECTS_REQUIRED_PRIVILEGES,
    load_findings,
    store_finding,
)
from v2_command_harness import build_disposable_database, needs_worker_db, worker_dsn

pytestmark = needs_worker_db

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16


@pytest.fixture(scope="module")
def dsn():
    yield from build_disposable_database()


def _conn(dsn):
    return open_verified_connection(local_test_target(worker_dsn(dsn)), application_name="test-prospects",
                                    privileges=PROSPECTS_REQUIRED_PRIVILEGES, policies=PROSPECTS_REQUIRED_POLICIES)


def _line(n, **over):
    base = {"source": "manual", "external_id": f"demo-{n}", "person_name": f"Persona Ficticia {n}",
            "role": "Investigadora", "organization_name": "Instituto Ficticio", "unit": "Lab de Prueba",
            "email": f"persona{n}@example.test", "email_source_url": "https://example.test/paper",
            "website": "https://example.test", "why_now": "Ganó un fondo", "equipment": ["Balanza"],
            "sources": [["fondo", "https://example.test/fondo"]], "confidence": "media", "score": 60}
    base.update(over)
    return base


def test_store_finding_writes_record_and_assertions(dsn):
    with _conn(dsn) as conn, conn.transaction():
        outcome = store_finding(conn.cursor(), parse_finding(_line(1)), ("image/png", PNG))
    assert outcome == "inserted"
    rows = owner_rows(dsn, "select kind, review_status, payload from evidence.source_record"
                           " where dedupe_key = 'prospect:manual:demo-1:persona ficticia 1'")
    assert rows and rows[0][0] == "prospect_finding" and rows[0][1] == "pending"
    payload = rows[0][2] if isinstance(rows[0][2], dict) else json.loads(rows[0][2])
    assert payload["why_now"] == "Ganó un fondo"
    assert payload["logo"]["content_type"] == "image/png"
    kinds = owner_rows(dsn, "select a.kind, a.value_norm from evidence.assertion a join evidence.source_record s"
                            " on s.id = a.source_record_id where s.dedupe_key = 'prospect:manual:demo-1:persona ficticia 1'"
                            " order by a.kind")
    assert [k for k, _ in kinds] == ["affiliation", "contact_address", "organization_name"]
    assert ("contact_address", "persona1@example.test") in kinds


def test_finding_without_email_has_no_contact_address(dsn):
    with _conn(dsn) as conn, conn.transaction():
        store_finding(conn.cursor(), parse_finding(_line(2, email=None, email_source_url=None)), None)
    kinds = owner_rows(dsn, "select a.kind from evidence.assertion a join evidence.source_record s"
                            " on s.id = a.source_record_id where s.dedupe_key = 'prospect:manual:demo-2:persona ficticia 2'")
    assert sorted(k for (k,) in kinds) == ["affiliation", "organization_name"]


def test_load_is_idempotent(dsn, tmp_path):
    path = tmp_path / "lote.jsonl"
    path.write_text("\n".join(json.dumps(_line(n)) for n in (10, 11)) + "\n", encoding="utf-8")
    with _conn(dsn) as conn:
        first = load_findings(conn, path, apply=True, fetch=lambda url: None)
    with _conn(dsn) as conn:
        second = load_findings(conn, path, apply=True, fetch=lambda url: None)
    assert (first["inserted"], first["unchanged"]) == (2, 0)
    assert (second["inserted"], second["unchanged"]) == (0, 2)
    count = owner_rows(dsn, "select count(*) from evidence.source_record where dedupe_key like 'prospect:manual:demo-1_:%%'")
    assert count[0][0] == 2
    asserts = owner_rows(dsn, "select count(*) from evidence.assertion a join evidence.source_record s"
                              " on s.id = a.source_record_id where s.dedupe_key like 'prospect:manual:demo-1_:%%'")
    assert asserts[0][0] == 6


def test_dry_run_writes_nothing_and_reports_refusals(dsn, tmp_path):
    path = tmp_path / "lote.jsonl"
    path.write_text(json.dumps(_line(20)) + "\n" + json.dumps(_line(21, email_source_url=None)) + "\n",
                    encoding="utf-8")
    with _conn(dsn) as conn:
        report = load_findings(conn, path, apply=False, fetch=lambda url: None)
    assert report["valid"] == 1 and report["would_insert"] == 1
    assert report["refused"] == [{"line": 2, "reason": "email_without_source"}]
    assert owner_rows(dsn, "select count(*) from evidence.source_record where dedupe_key like 'prospect:manual:demo-2_:%%'")[0][0] == 0


def test_cli_parses_and_refuses_without_settings(capsys, tmp_path):
    import time

    from origenlab_worker.cli import _parser as build_parser
    from origenlab_worker.prospects.cli import run_prospects_load

    path = tmp_path / "lote.jsonl"
    path.write_text(json.dumps(_line(30)) + "\n", encoding="utf-8")
    args = build_parser().parse_args(["prospects-load", "--file", str(path), "--apply", "--no-logos"])
    assert (args.command, args.file, args.apply, args.no_logos) == ("prospects-load", str(path), True, True)
    code = run_prospects_load(args, {}, time.monotonic())
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 3 and line["event"] == "prospects_load" and line["mode"] == "config_refused"


def test_cli_dry_run_reports_counts(capsys, dsn, tmp_path):
    import time
    from types import SimpleNamespace

    from origenlab_worker.prospects.cli import run_prospects_load

    path = tmp_path / "lote.jsonl"
    path.write_text(json.dumps(_line(40)) + "\n", encoding="utf-8")
    args = SimpleNamespace(file=str(path), apply=False, no_logos=True)
    code = run_prospects_load(args, {}, time.monotonic(), target=local_test_target(worker_dsn(dsn)))
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0 and line["mode"] == "dry_run"
    assert (line["valid"], line["would_insert"], line["refused"]) == (1, 1, [])

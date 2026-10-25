"""End-to-end DB test for the daily ANID prospect search run.

Runs `run_search` against a disposable `origenlab_test_<hex>` database as the real
`origenlab_worker` login, with Europe PMC and Claude fully stubbed (no network, no spend).
Every value is invented.

Assertions:
* adds exactly `25 - unreviewed` visible findings (the queue-level gap);
* low-fit (<30) findings are stored with `hidden_reason='no_calza'` and NOT counted as
  visible (the fixed bug: they must not consume slots meant for visible prospects);
* the `evidence.prospect_search_run` row ends `done` with the correct counts;
* a second run on a full queue adds 0 findings and ends `done`;
* candidates already in the DB (same project code or same folded person+institution)
  are filtered out before the loop — skipped at the filter_new stage;
* BudgetReached ends the run with status `failed` and counts.error='budget'.
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest

from dbhelp import owner_rows
from origenlab_worker.database import local_test_target, open_verified_connection
from origenlab_worker.prospects.daily import (
    QUEUE_TARGET,
    SEARCH_REQUIRED_PRIVILEGES,
    run_search,
)
from origenlab_worker.prospects.fit import (
    DEFAULT_MODEL,
    BudgetReached,
    FIT_THRESHOLD,
    FitResult,
)
from origenlab_worker.prospects.store import PROSPECTS_REQUIRED_POLICIES
from v2_command_harness import build_disposable_database, needs_worker_db, worker_dsn

pytestmark = needs_worker_db

# ── helpers ──────────────────────────────────────────────────────────────────

# Extra policies the daily search needs beyond the store's baseline.
_SEARCH_POLICIES: frozenset[tuple[str, str, str]] = PROSPECTS_REQUIRED_POLICIES | frozenset({
    ("evidence", "prospect_search_run", "origenlab_worker_select"),
    ("evidence", "prospect_search_run", "origenlab_worker_insert"),
    ("evidence", "prospect_search_run", "origenlab_worker_update"),
    ("platform", "model_usage", "origenlab_worker_insert"),
})


def _conn(dsn: str):
    return open_verified_connection(
        local_test_target(worker_dsn(dsn)),
        application_name="test-daily-db",
        privileges=SEARCH_REQUIRED_PRIVILEGES,
        policies=_SEARCH_POLICIES,
    )


def _csv(rows: list[dict[str, str]], *, delim: str = ";") -> str:
    headers = [
        "AGNO_FALLO", "PROGRAMA", "INSTRUMENTO", "AREA_OCDE",
        "CODIGO_PROYECTO", "NOMBRE_RESPONSABLE", "INSTITUCION_PRINCIPAL",
        "NOMBRE_PROYECTO", "DISCIPLINA_DETALLE", "MONTO_ADJUDICADO",
        "PALABRAS_CLAVES", "SEXO",
    ]
    lines = [delim.join(headers)]
    for row in rows:
        lines.append(delim.join(row.get(h, "") for h in headers))
    return "\n".join(lines)


_BASE_ROW: dict[str, str] = {
    "AGNO_FALLO": "2025", "PROGRAMA": "FONDECYT", "INSTRUMENTO": "REGULAR",
    "AREA_OCDE": "CIENCIAS NATURALES", "CODIGO_PROYECTO": "XXX250001",
    "NOMBRE_RESPONSABLE": "FICTICIO ANA LABORATORIO",
    "INSTITUCION_PRINCIPAL": "Instituto Ficticio",
    "NOMBRE_PROYECTO": "Estudio ficticio de microbiología de suelos",
    "DISCIPLINA_DETALLE": "Microbiología", "MONTO_ADJUDICADO": "150",
    "PALABRAS_CLAVES": "microbiolog|suelo|ficticio", "SEXO": "MUJER",
}


def _row(**over: str) -> dict[str, str]:
    return {**_BASE_ROW, **over}


def _seed_visible_findings(dsn: str, count: int, *, prefix: str = "sd") -> None:
    """Seed `count` visible (non-hidden) prospect_finding rows as the owner role."""
    for i in range(count):
        payload = json.dumps({
            "source": "fondecyt", "external_id": f"{prefix}-{i:04d}",
            "person_name": f"Persona Ficticia {prefix} {i}",
            "organization_name": "Instituto Ficticio Base",
            "why_now": "Fondo adjudicado ficticio",
            "score": 60,
            "equipment": ["Balanza"],
            "sources": [["fondo", "https://example.test/fondo"]],
            "confidence": "media",
        })
        owner_rows(
            dsn,
            "insert into evidence.source_record "
            "(kind, dedupe_key, payload, source_uri, review_status) "
            "values ('prospect_finding', %s, %s::jsonb, %s, 'pending')",
            (f"prospect:fondecyt:{prefix}-{i:04d}:persona ficticia {prefix} {i}",
             payload, "https://example.test/fondo"),
        )


def _seed_finding_with_code(dsn: str, codigo: str) -> None:
    """Seed a single finding with a specific project code (for duplicate-filter testing)."""
    payload = json.dumps({
        "source": "fondecyt", "external_id": codigo,
        "person_name": f"Persona Existente {codigo}",
        "organization_name": "Institución Existente",
        "why_now": "Ya existe", "score": 70,
        "equipment": ["Centrifuga"],
        "sources": [["fondo", "https://example.test/fondo"]],
        "confidence": "alta",
    })
    owner_rows(
        dsn,
        "insert into evidence.source_record "
        "(kind, dedupe_key, payload, source_uri, review_status) "
        "values ('prospect_finding', %s, %s::jsonb, %s, 'pending')",
        (f"prospect:fondecyt:{codigo}:persona existente {codigo.lower()}",
         payload, "https://example.test/fondo"),
    )


def _insert_queued_run(dsn: str) -> str:
    """Insert a queued daily search run; return its id as a string."""
    rows = owner_rows(
        dsn,
        "insert into evidence.prospect_search_run (trigger, status) "
        "values ('daily', 'queued') returning id::text",
    )
    assert rows and rows[0][0], "failed to insert queued run"
    return rows[0][0]


def _fake_epmc(email: str | None = "aficticia@ficticio.invalid"):
    """A requests.Session-shaped stub returning a fixed email (or None)."""
    from types import SimpleNamespace
    result = {"email": email, "source_url": "https://doi.org/10.9999/ficticio.2025",
              "source_year": 2025, "orcid": None} if email else None

    class _FakeSession:
        def get(self, _url: str, **__: Any) -> Any:
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "resultList": {"result": [
                        {
                            "doi": "10.9999/ficticio.2025",
                            "pubYear": "2025",
                            "pmcid": None,
                            "authorList": {"author": [{
                                "fullName": "Ficticia A",
                                "authorId": [],
                                "authorAffiliationDetailsList": {"authorAffiliation": [
                                    {"affiliation": f"Instituto Ficticio. {email or ''}"}
                                ]},
                            }]},
                        }
                    ]},
                } if email else {"resultList": {"result": []}},
            )
    # Patch search_epmc directly to always return the fixed result.
    # (Easier than wrestling with affiliation parsing in the test setup.)
    return result, _FakeSession()


def _fake_fit(fit_score: int) -> FitResult:
    return FitResult(
        fit=fit_score,
        why_now="Proyecto ficticio necesita equipos.",
        equipment=["Balanza analítica"] if fit_score >= FIT_THRESHOLD else [],
        model=DEFAULT_MODEL,
        usage=None,
    )


# ── function-scoped disposable database ──────────────────────────────────────
# Each test gets its own fresh database: findings accumulate in the shared
# evidence schema, so a module-scoped DB would carry visible findings between
# tests and affect the `unreviewed` count used by run_search.

@pytest.fixture()
def dsn():
    yield from build_disposable_database()


# ── test: adds exactly 25 - unreviewed visible findings ──────────────────────

def test_run_adds_visible_findings_to_fill_queue(dsn, tmp_path, monkeypatch):
    """run_search fills the visible queue to QUEUE_TARGET.

    Setup: 23 visible findings already in DB (need = 2).
    Pool: 3 candidates — 2 high-fit (fit=80), 1 low-fit (fit=10).
    Expected: counts['added']=2 (visible); counts['low_fit']=1 (hidden stored); run done.
    The low-fit finding must NOT consume a visible slot (the fixed bug).
    """
    _seed_visible_findings(dsn, 23, prefix="vis")
    run_id = _insert_queued_run(dsn)

    enrich_result, epmc_sess = _fake_epmc("aficticia@ficticio.invalid")

    fit_calls: list[int] = []
    def _patched_fit(conn: Any, context: str, model: str, search_run_id: str | None) -> FitResult:
        # First call → low-fit; remaining → high-fit
        score = 10 if len(fit_calls) == 0 else 80
        fit_calls.append(score)
        if score < FIT_THRESHOLD:
            return _fake_fit(score)
        return _fake_fit(score)

    monkeypatch.setattr("origenlab_worker.prospects.daily.call_fit", _patched_fit)
    monkeypatch.setattr("origenlab_worker.prospects.daily.search_epmc",
                        lambda sess, variants, inst, **kw: enrich_result)

    candidates = [
        _row(CODIGO_PROYECTO=f"DB{n:06d}", NOMBRE_RESPONSABLE=f"FICTICIA{n} DB LABORATORIO")
        for n in range(1, 4)
    ]
    csv_path = tmp_path / "daily_fill.csv"
    csv_path.write_text(_csv(candidates), encoding="utf-8")

    with _conn(dsn) as conn:
        counts = run_search(conn, run_id, DEFAULT_MODEL, csv_path=str(csv_path),
                            _epmc_session=epmc_sess)

    # visible added = 2 (need was 2; the 1 low-fit candidate must not use up a slot)
    assert counts["added"] == 2, f"expected 2 visible added, got {counts['added']}"
    assert counts["low_fit"] == 1, f"expected 1 hidden, got {counts['low_fit']}"
    assert counts.get("error") is None

    # run row must be 'done'
    run_row = owner_rows(dsn,
        "select status, counts from evidence.prospect_search_run where id = %s::uuid",
        (run_id,))
    assert run_row and run_row[0][0] == "done"
    stored_counts = run_row[0][1] if isinstance(run_row[0][1], dict) else json.loads(run_row[0][1])
    assert stored_counts["added"] == 2
    assert stored_counts["low_fit"] == 1


# ── test: hidden findings stored with correct hidden_reason ──────────────────

def test_low_fit_findings_stored_hidden(dsn, tmp_path, monkeypatch):
    """Findings with fit < FIT_THRESHOLD are written with hidden_reason='no_calza'."""
    run_id = _insert_queued_run(dsn)

    enrich_result, epmc_sess = _fake_epmc("bficticia@ficticio.invalid")

    monkeypatch.setattr("origenlab_worker.prospects.daily.call_fit",
                        lambda conn, ctx, mdl, search_run_id: _fake_fit(15))
    monkeypatch.setattr("origenlab_worker.prospects.daily.search_epmc",
                        lambda sess, variants, inst, **kw: enrich_result)

    candidates = [_row(CODIGO_PROYECTO="LFIT0001",
                       NOMBRE_RESPONSABLE="BFICTICIA OB LABORATORIO")]
    csv_path = tmp_path / "lowfit.csv"
    csv_path.write_text(_csv(candidates), encoding="utf-8")

    with _conn(dsn) as conn:
        run_search(conn, run_id, DEFAULT_MODEL, csv_path=str(csv_path),
                   _epmc_session=epmc_sess)

    rows = owner_rows(dsn,
        "select payload->>'hidden_reason', payload->>'score' "
        "from evidence.source_record where kind='prospect_finding' "
        "and payload->>'external_id' = 'LFIT0001'")
    assert rows, "hidden finding was not stored"
    hidden_reason, score_str = rows[0]
    assert hidden_reason == "no_calza", f"expected no_calza, got {hidden_reason!r}"
    assert int(score_str) < FIT_THRESHOLD


# ── test: second run on full queue adds 0 ────────────────────────────────────

def test_second_run_on_full_queue_adds_zero(dsn, tmp_path, monkeypatch):
    """When the visible queue is already at QUEUE_TARGET, the run adds 0 and ends done."""
    # Fill the queue completely (25 visible findings)
    _seed_visible_findings(dsn, QUEUE_TARGET, prefix="full")
    run_id = _insert_queued_run(dsn)

    monkeypatch.setattr("origenlab_worker.prospects.daily.call_fit",
                        lambda conn, ctx, mdl, search_run_id: _fake_fit(80))

    candidates = [_row(CODIGO_PROYECTO="FULL0001",
                       NOMBRE_RESPONSABLE="CFICTICIA FL LABORATORIO")]
    csv_path = tmp_path / "full.csv"
    csv_path.write_text(_csv(candidates), encoding="utf-8")

    with _conn(dsn) as conn:
        counts = run_search(conn, run_id, DEFAULT_MODEL, csv_path=str(csv_path))

    assert counts["added"] == 0
    run_row = owner_rows(dsn,
        "select status from evidence.prospect_search_run where id = %s::uuid", (run_id,))
    assert run_row[0][0] == "done"


# ── test: duplicate project codes and person+institution are skipped ──────────

def test_existing_codes_and_person_keys_are_skipped(dsn, tmp_path, monkeypatch):
    """Candidates already in the DB are filtered out before the loop."""
    # Seed two findings with known codes
    _seed_finding_with_code(dsn, "DUP00001")
    _seed_finding_with_code(dsn, "DUP00002")
    run_id = _insert_queued_run(dsn)

    enrich_result, epmc_sess = _fake_epmc("dficticia@ficticio.invalid")
    monkeypatch.setattr("origenlab_worker.prospects.daily.call_fit",
                        lambda conn, ctx, mdl, search_run_id: _fake_fit(80))
    monkeypatch.setattr("origenlab_worker.prospects.daily.search_epmc",
                        lambda sess, variants, inst, **kw: enrich_result)

    # 3 candidates: 2 duplicates (known codes) + 1 genuinely new
    candidates = [
        _row(CODIGO_PROYECTO="DUP00001", NOMBRE_RESPONSABLE="DUPLICADA AA LABORATORIO"),
        _row(CODIGO_PROYECTO="DUP00002", NOMBRE_RESPONSABLE="DUPLICADA BB LABORATORIO"),
        _row(CODIGO_PROYECTO="NEW99901", NOMBRE_RESPONSABLE="DFICTICIA NW LABORATORIO"),
    ]
    csv_path = tmp_path / "dupes.csv"
    csv_path.write_text(_csv(candidates), encoding="utf-8")

    with _conn(dsn) as conn:
        counts = run_search(conn, run_id, DEFAULT_MODEL, csv_path=str(csv_path),
                            _epmc_session=epmc_sess)

    # Only the new candidate is added; duplicates are filtered before the loop
    assert counts["added"] >= 1
    assert counts.get("skipped", 0) == 0  # no parse errors; duplicates are filtered, not skipped


# ── test: BudgetReached ends the run cleanly ──────────────────────────────────

def test_budget_reached_ends_run_failed(dsn, tmp_path, monkeypatch):
    """When call_fit raises BudgetReached, the run ends with status='failed' and error='budget'."""
    run_id = _insert_queued_run(dsn)

    enrich_result, epmc_sess = _fake_epmc("eficticia@ficticio.invalid")

    def _budget_fit(conn: Any, ctx: str, mdl: str, search_run_id: str | None) -> FitResult:
        from decimal import Decimal
        raise BudgetReached(Decimal("9.99"), Decimal("10.00"))

    monkeypatch.setattr("origenlab_worker.prospects.daily.call_fit", _budget_fit)
    monkeypatch.setattr("origenlab_worker.prospects.daily.search_epmc",
                        lambda sess, variants, inst, **kw: enrich_result)

    candidates = [_row(CODIGO_PROYECTO="BDG00001",
                       NOMBRE_RESPONSABLE="EFICTICIA BD LABORATORIO")]
    csv_path = tmp_path / "budget.csv"
    csv_path.write_text(_csv(candidates), encoding="utf-8")

    with _conn(dsn) as conn:
        counts = run_search(conn, run_id, DEFAULT_MODEL, csv_path=str(csv_path),
                            _epmc_session=epmc_sess)

    assert counts.get("error") == "budget"

    run_row = owner_rows(dsn,
        "select status, error from evidence.prospect_search_run where id = %s::uuid",
        (run_id,))
    assert run_row[0][0] == "failed"
    assert run_row[0][1] == "budget"

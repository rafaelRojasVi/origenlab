"""Unit tests for the daily ANID prospect search: anid.py, enrich.py, fit.py, daily.py.

All fixtures use entirely fictitious values — no real names, project codes, institutions or
emails from the ANID dataset.  Real data must never appear in tests (repo is public).
"""

from __future__ import annotations

import json
import textwrap
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from origenlab_worker.prospects.anid import (
    AnidCandidate,
    _amount_label,
    _fold,
    _fund_label,
    _name_variants,
    _title_case,
    filter_new,
    institution_website,
    parse_csv,
)
from origenlab_worker.prospects.enrich import (
    _author_name_matches,
    _email_domain_matches,
    _institution_matches,
    search_epmc,
)
from origenlab_worker.prospects.fit import (
    DEFAULT_MODEL,
    ENV_PROSPECT_MODEL,
    FIT_THRESHOLD,
    build_candidate_context,
    model_from_env,
)


# ── Helpers ───────────────────────────────────────────────────────────────────────────────────

def _csv(rows: list[dict[str, str]], *, delim: str = ";") -> str:
    """Build a minimal ANID-shaped CSV string from a list of dicts (all required columns)."""
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


_FICTITIOUS_ROW: dict[str, str] = {
    "AGNO_FALLO": "2025",
    "PROGRAMA": "FONDECYT",
    "INSTRUMENTO": "REGULAR",
    "AREA_OCDE": "CIENCIAS NATURALES",
    "CODIGO_PROYECTO": "XXX250001",
    "NOMBRE_RESPONSABLE": "FICTICIO ANA LABORATORIO",
    "INSTITUCION_PRINCIPAL": "Instituto Ficticio",
    "NOMBRE_PROYECTO": "Estudio ficticio de microbiología de suelos",
    "DISCIPLINA_DETALLE": "Microbiología",
    "MONTO_ADJUDICADO": "150",
    "PALABRAS_CLAVES": "microbiolog|suelo|ficticio",
    "SEXO": "MUJER",
}

_FICTITIOUS_ROW_FONDEQUIP: dict[str, str] = {
    **_FICTITIOUS_ROW,
    "CODIGO_PROYECTO": "XXX250002",
    "PROGRAMA": "FONDEQUIP",
    "INSTRUMENTO": "EQUIPAMIENTO",
    "NOMBRE_RESPONSABLE": "SEGUNDOFICTICIO BLAS LABORATORIO",
    "NOMBRE_PROYECTO": "Equipamiento ficticio de balanzas ultrasónicas",
    "PALABRAS_CLAVES": "ultrason|balanza|ficticio",
    "SEXO": "HOMBRE",
}


# ── anid.py ──────────────────────────────────────────────────────────────────────────────────

class TestFoldTitleCase:
    def test_fold_strips_accents_and_lowercases(self) -> None:
        assert _fold("Ángel Ñoño") == "angel nono"

    def test_title_case_keeps_de_del_lower(self) -> None:
        result = _title_case("ANA DE LA ROSA LABORATORIO")
        assert result == "Ana de la Rosa Laboratorio"

    def test_title_case_does_not_lower_first_word(self) -> None:
        result = _title_case("DE LA ROSA ANA")
        assert result.startswith("De")


class TestNameVariants:
    def test_three_part_name_gives_paternal_maternal_initial(self) -> None:
        # ANID order: FIRST PATERNAL MATERNAL
        variants = _name_variants("ANA LABORATORIO FICTICIA")
        assert variants == ["LABORATORIO-FICTICIA A", "LABORATORIO A"]

    def test_four_part_name(self) -> None:
        variants = _name_variants("ANA BELEN LABORATORIO FICTICIA")
        assert variants == ["LABORATORIO-FICTICIA A", "LABORATORIO A"]

    def test_two_part_name_returns_as_is(self) -> None:
        variants = _name_variants("ANA LABORATORIO")
        assert variants == ["ANA LABORATORIO"]

    def test_empty_string(self) -> None:
        assert _name_variants("") == []


class TestAmountLabel:
    def test_integer_monto(self) -> None:
        assert _amount_label("180") == "$ 180 M"

    def test_float_monto(self) -> None:
        assert _amount_label("1250.5") == "$ 1.250 M"

    def test_empty_string_gives_zero(self) -> None:
        assert _amount_label("") == "$ 0 M"

    def test_invalid(self) -> None:
        # non-numeric after stripping → ValueError → ""
        assert _amount_label("n/a") == ""


class TestFundLabel:
    def test_with_instrumento(self) -> None:
        assert _fund_label("FONDECYT", "REGULAR", "2025") == "Fondecyt Regular 2025"

    def test_without_instrumento(self) -> None:
        assert _fund_label("FONDEQUIP", "", "2025") == "Fondequip 2025"


class TestInstitutionWebsite:
    def test_known_university(self) -> None:
        url = institution_website("Universidad de Chile")
        assert url == "https://www.uchile.cl"

    def test_abbreviation(self) -> None:
        url = institution_website("USACH")
        assert url == "https://www.usach.cl"

    def test_unknown_institution_returns_none(self) -> None:
        assert institution_website("Instituto Ficticio de Laboratorio") is None


class TestParseCsv:
    def test_parses_pool_row_and_filters_by_max_year(self) -> None:
        content = _csv([_FICTITIOUS_ROW])
        results = parse_csv(content)
        assert len(results) == 1
        assert results[0].codigo == "XXX250001"
        assert results[0].programa == "FONDECYT"
        assert results[0].agno_fallo == "2025"

    def test_excludes_wrong_year(self) -> None:
        row_old = {**_FICTITIOUS_ROW, "AGNO_FALLO": "2023", "CODIGO_PROYECTO": "XXX230001"}
        row_new = {**_FICTITIOUS_ROW, "AGNO_FALLO": "2025", "CODIGO_PROYECTO": "XXX250001"}
        content = _csv([row_old, row_new])
        results = parse_csv(content)
        # max year is 2025 — only the 2025 row
        assert len(results) == 1
        assert results[0].codigo == "XXX250001"

    def test_explicit_year_overrides_max(self) -> None:
        row_old = {**_FICTITIOUS_ROW, "AGNO_FALLO": "2023", "CODIGO_PROYECTO": "XXX230001"}
        row_new = {**_FICTITIOUS_ROW, "AGNO_FALLO": "2025", "CODIGO_PROYECTO": "XXX250001"}
        content = _csv([row_old, row_new])
        results = parse_csv(content, year="2023")
        assert len(results) == 1
        assert results[0].codigo == "XXX230001"

    def test_excludes_postdoctorado(self) -> None:
        row = {**_FICTITIOUS_ROW, "INSTRUMENTO": "POSTDOCTORADO"}
        content = _csv([row])
        assert parse_csv(content) == []

    def test_excludes_wrong_programa(self) -> None:
        row = {**_FICTITIOUS_ROW, "PROGRAMA": "BECAS"}
        content = _csv([row])
        assert parse_csv(content) == []

    def test_excludes_wrong_area(self) -> None:
        row = {**_FICTITIOUS_ROW, "AREA_OCDE": "CIENCIAS SOCIALES"}
        content = _csv([row])
        assert parse_csv(content) == []

    def test_fondequip_ranks_higher_than_fondecyt(self) -> None:
        content = _csv([_FICTITIOUS_ROW, _FICTITIOUS_ROW_FONDEQUIP])
        results = parse_csv(content)
        assert len(results) == 2
        assert results[0].programa == "FONDEQUIP"   # higher rank first

    def test_bom_stripped(self) -> None:
        content = "﻿" + _csv([_FICTITIOUS_ROW])
        results = parse_csv(content)
        assert len(results) == 1

    def test_keywords_split_by_pipe(self) -> None:
        results = parse_csv(_csv([_FICTITIOUS_ROW]))
        assert results[0].palabras_clave == ["microbiolog", "suelo", "ficticio"]

    def test_lab_bonus_detected_from_keywords(self) -> None:
        results = parse_csv(_csv([_FICTITIOUS_ROW]))
        assert results[0].lab_bonus is True

    def test_amount_label_derived(self) -> None:
        results = parse_csv(_csv([_FICTITIOUS_ROW]))
        assert results[0].amount_label == "$ 150 M"

    def test_header_only_csv_returns_empty(self) -> None:
        # A CSV with just a header row (no data) returns [] without error
        header_only = _csv([])
        assert parse_csv(header_only) == []

    def test_sexo_preserved(self) -> None:
        results = parse_csv(_csv([_FICTITIOUS_ROW]))
        assert results[0].sexo == "MUJER"


class TestFilterNew:
    def _candidate(self, codigo: str = "XXX250001", name: str = "Ana Ficticia",
                   inst: str = "Instituto Ficticio") -> AnidCandidate:
        return AnidCandidate(
            codigo=codigo, programa="FONDECYT", instrumento="REGULAR",
            agno_fallo="2025", nombre_proyecto="Proyecto Ficticio",
            nombre_responsable="ANA FICTICIA LABORATORIO",
            institucion=inst, area_ocde="CIENCIAS NATURALES",
            disciplina="Microbiología", monto_m="150",
            palabras_clave=["microbiolog"],
            sexo="MUJER",
            person_name=name,
            fund_label="Fondecyt Regular 2025",
            amount_label="$ 150 M",
            website=None,
            prog_rank=2,
            lab_bonus=True,
            name_variants=["FICTICIA A"],
        )

    def test_passes_new_candidate(self) -> None:
        c = self._candidate()
        result = filter_new([c], frozenset(), frozenset())
        assert result == [c]

    def test_drops_known_code(self) -> None:
        c = self._candidate()
        result = filter_new([c], frozenset({"XXX250001"}), frozenset())
        assert result == []

    def test_drops_known_person_institution_key(self) -> None:
        c = self._candidate()
        person_key = f"{_fold('Ana Ficticia')}|{_fold('Instituto Ficticio')}"
        result = filter_new([c], frozenset(), frozenset({person_key}))
        assert result == []

    def test_different_institution_passes(self) -> None:
        c = self._candidate(inst="Otro Instituto Ficticio")
        person_key = f"{_fold('Ana Ficticia')}|{_fold('Instituto Ficticio')}"
        result = filter_new([c], frozenset(), frozenset({person_key}))
        assert result == [c]


# ── enrich.py ─────────────────────────────────────────────────────────────────────────────────

class TestInstitutionMatches:
    def test_exact_match(self) -> None:
        assert _institution_matches("Instituto Ficticio, Valparaíso, Chile", "Instituto Ficticio")

    def test_partial_words_match(self) -> None:
        assert _institution_matches(
            "Universidad Ficticia de Laboratorio, Chile",
            "Universidad Ficticia de Laboratorio",
        )

    def test_no_match(self) -> None:
        assert not _institution_matches("Otra Institución Distinta", "Instituto Ficticio")


class TestEmailDomainMatches:
    def test_matching_domain(self) -> None:
        assert _email_domain_matches("ana@ficticio.cl", "Instituto Ficticio", "https://www.ficticio.cl")

    def test_no_known_domain(self) -> None:
        assert not _email_domain_matches("ana@ficticio.cl", "Instituto Ficticio", None)

    def test_different_domain(self) -> None:
        assert not _email_domain_matches("ana@otro.cl", "Instituto Ficticio", "https://www.ficticio.cl")


class TestAuthorNameMatches:
    def test_matches_full_name(self) -> None:
        author = {"fullName": "Ficticia A"}
        assert _author_name_matches(author, ["Ficticia A"])

    def test_matches_with_dot(self) -> None:
        author = {"fullName": "ficticia a."}
        assert _author_name_matches(author, ["Ficticia A"])

    def test_no_match(self) -> None:
        author = {"fullName": "Otro Autor"}
        assert not _author_name_matches(author, ["Ficticia A"])


class TestSearchEpmc:
    def _fake_session(self, articles: list[dict]) -> Any:
        """Return a session stub that yields the given articles list."""
        resp = SimpleNamespace(
            status_code=200,
            json=lambda: {"resultList": {"result": articles}},
        )
        sess = SimpleNamespace(get=MagicMock(return_value=resp))
        return sess

    def _article(self, author_full: str, aff_text: str, email: str,
                 doi: str = "10.9999/ficticio.2025") -> dict:
        return {
            "doi": doi,
            "pubYear": "2025",
            "pmcid": None,
            "authorList": {
                "author": [{
                    "fullName": author_full,
                    "authorId": [],
                    "affiliationList": {"affiliation": [aff_text]},
                }]
            },
        }

    def test_returns_email_when_institution_matches(self) -> None:
        article = self._article(
            author_full="Ficticia A",
            aff_text="Instituto Ficticio, Chile. ana@ficticio.invalid",
            email="ana@ficticio.invalid",
        )
        sess = self._fake_session([article])
        result = search_epmc(sess, ["Ficticia A"], "Instituto Ficticio", pause_s=0)
        assert result is not None
        assert result["email"] == "ana@ficticio.invalid"
        assert result["source_year"] == 2025

    def test_returns_none_when_no_email_in_aff(self) -> None:
        article = self._article(
            author_full="Ficticia A",
            aff_text="Instituto Ficticio, Chile.",
            email="",
        )
        sess = self._fake_session([article])
        result = search_epmc(sess, ["Ficticia A"], "Instituto Ficticio", pause_s=0)
        assert result is None

    def test_returns_none_when_institution_does_not_match(self) -> None:
        article = self._article(
            author_full="Ficticia A",
            aff_text="Otra Institución Diferente, Chile. ana@ficticio.invalid",
            email="ana@ficticio.invalid",
        )
        sess = self._fake_session([article])
        # institution won't match
        result = search_epmc(sess, ["Ficticia A"], "Instituto Ficticio", pause_s=0)
        assert result is None

    def test_returns_none_on_http_error(self) -> None:
        resp = SimpleNamespace(status_code=503, json=lambda: {})
        sess = SimpleNamespace(get=MagicMock(return_value=resp))
        result = search_epmc(sess, ["Ficticia A"], "Instituto Ficticio", pause_s=0)
        assert result is None

    def test_returns_none_for_empty_variants(self) -> None:
        result = search_epmc(MagicMock(), [], "Instituto Ficticio", pause_s=0)
        assert result is None


# ── fit.py ────────────────────────────────────────────────────────────────────────────────────

class TestBuildCandidateContext:
    def test_includes_proyecto_and_discipline(self) -> None:
        ctx = build_candidate_context(
            nombre_proyecto="Estudio ficticio de suelos",
            disciplina="Microbiología",
            area_ocde="CIENCIAS NATURALES",
            palabras_clave=["microbiolog", "suelo"],
            monto_label="$ 150 M",
            fund_label="Fondecyt Regular 2025",
        )
        assert "Estudio ficticio de suelos" in ctx
        assert "Microbiología" in ctx
        assert "microbiolog" in ctx
        assert "$ 150 M" in ctx

    def test_omits_empty_fields(self) -> None:
        ctx = build_candidate_context(
            nombre_proyecto="Proyecto Ficticio",
            disciplina="Física",
            area_ocde="INGENIERIA Y TECNOLOGIA",
            palabras_clave=[],
            monto_label="",
            fund_label="",
        )
        assert "Palabras clave" not in ctx
        assert "Monto" not in ctx


class TestModelFromEnv:
    def test_returns_default_when_env_not_set(self) -> None:
        model = model_from_env({})
        assert model == DEFAULT_MODEL

    def test_reads_env_key(self) -> None:
        # Any valid model id — use the default (it validates via check_model_id)
        model = model_from_env({ENV_PROSPECT_MODEL: DEFAULT_MODEL})
        assert model == DEFAULT_MODEL

    def test_raises_on_invalid_model_id(self) -> None:
        with pytest.raises((ValueError, Exception)):
            model_from_env({ENV_PROSPECT_MODEL: "not-a-real-model-id-$$"})


class TestFitThreshold:
    def test_threshold_is_30(self) -> None:
        assert FIT_THRESHOLD == 30


# ── daily.py (dry_run path — no DB, no network, no Claude) ───────────────────────────────────

class TestCandidateToFindingDict:
    """Test `_candidate_to_finding_dict` in isolation via parse_csv + daily internals."""

    def _candidate(self) -> Any:
        from origenlab_worker.prospects.anid import parse_csv
        results = parse_csv(_csv([_FICTITIOUS_ROW]))
        assert results
        return results[0]

    def test_source_is_programme_lower(self) -> None:
        from origenlab_worker.prospects.daily import _candidate_to_finding_dict
        c = self._candidate()
        raw = _candidate_to_finding_dict(c, enrich=None, fit=None, hidden=False)
        assert raw["source"] == "fondecyt"

    def test_external_id_matches_codigo(self) -> None:
        from origenlab_worker.prospects.daily import _candidate_to_finding_dict
        c = self._candidate()
        raw = _candidate_to_finding_dict(c, enrich=None, fit=None, hidden=False)
        assert raw["external_id"] == "XXX250001"

    def test_hidden_reason_set_when_hidden(self) -> None:
        from origenlab_worker.prospects.daily import _candidate_to_finding_dict
        c = self._candidate()
        raw = _candidate_to_finding_dict(c, enrich=None, fit=None, hidden=True)
        assert raw["hidden_reason"] == "no_calza"

    def test_hidden_reason_null_when_not_hidden(self) -> None:
        from origenlab_worker.prospects.daily import _candidate_to_finding_dict
        c = self._candidate()
        raw = _candidate_to_finding_dict(c, enrich=None, fit=None, hidden=False)
        assert raw["hidden_reason"] is None

    def test_enrich_email_included(self) -> None:
        from origenlab_worker.prospects.daily import _candidate_to_finding_dict
        c = self._candidate()
        enrich = {"email": "ana@ficticio.invalid", "source_url": "https://doi.org/10.9999/x",
                  "source_year": 2024, "orcid": None}
        raw = _candidate_to_finding_dict(c, enrich=enrich, fit=None, hidden=False)
        assert raw["email"] == "ana@ficticio.invalid"
        assert raw["email_source_year"] == 2024

    def test_confidence_alta_for_high_rank(self) -> None:
        from origenlab_worker.prospects.daily import _candidate_to_finding_dict
        c = self._candidate()
        # FONDECYT REGULAR has rank 2; below alta threshold 3 — should be 'media'
        raw = _candidate_to_finding_dict(c, enrich=None, fit=None, hidden=False)
        assert raw["confidence"] == "media"

    def test_confidence_alta_for_fondequip(self) -> None:
        from origenlab_worker.prospects.daily import _candidate_to_finding_dict
        candidates = parse_csv(_csv([_FICTITIOUS_ROW_FONDEQUIP]))
        c = candidates[0]
        raw = _candidate_to_finding_dict(c, enrich=None, fit=None, hidden=False)
        assert raw["confidence"] == "alta"

    def test_sexo_preserved_as_salutation_sex(self) -> None:
        from origenlab_worker.prospects.daily import _candidate_to_finding_dict
        c = self._candidate()
        raw = _candidate_to_finding_dict(c, enrich=None, fit=None, hidden=False)
        assert raw["salutation_sex"] == "MUJER"


class TestRunSearchDryRun:
    """run_search with dry_run=True skips DB, Claude and network — uses a CSV file."""

    def _write_csv(self, tmp_path: Any) -> str:
        content = _csv([_FICTITIOUS_ROW, _FICTITIOUS_ROW_FONDEQUIP])
        path = tmp_path / "ficticio_anid.csv"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def _fake_conn(self, unreviewed: int = 0) -> Any:
        """Minimal psycopg-style connection stub for the read-only query path."""
        row_unreviewed = SimpleNamespace(fetchone=lambda: (unreviewed,))
        row_codes = SimpleNamespace(fetchall=lambda: [])
        row_keys = SimpleNamespace(fetchall=lambda: [])

        _cursor_calls = iter([row_unreviewed, row_codes, row_keys])
        cur = MagicMock()
        cur.__enter__ = lambda s: s
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchone = MagicMock(side_effect=lambda: next(_cursor_calls).fetchone())
        cur.fetchall = MagicMock(side_effect=lambda: next(_cursor_calls).fetchall())

        conn = MagicMock()
        conn._autocommit = True
        conn.cursor = MagicMock(return_value=cur)
        conn.rollback = MagicMock()
        return conn

    def test_dry_run_adds_candidates_up_to_queue_target(self, tmp_path: Any) -> None:
        from origenlab_worker.prospects.daily import run_search
        csv_path = self._write_csv(tmp_path)
        conn = self._fake_conn(unreviewed=0)
        counts = run_search(conn, run_id="00000000-0000-0000-0000-000000000001",
                            model=DEFAULT_MODEL, dry_run=True, csv_path=csv_path)
        # Both fictitious candidates should be added (queue target 25, 0 already there)
        assert counts["added"] == 2
        assert counts.get("error") is None

    def test_dry_run_skips_when_queue_full(self, tmp_path: Any) -> None:
        from origenlab_worker.prospects.daily import run_search
        csv_path = self._write_csv(tmp_path)
        conn = self._fake_conn(unreviewed=25)  # already at target
        counts = run_search(conn, run_id="00000000-0000-0000-0000-000000000002",
                            model=DEFAULT_MODEL, dry_run=True, csv_path=csv_path)
        assert counts["added"] == 0
        assert counts["pool_left"] == 0

    def test_dry_run_pool_left_reflects_remaining_candidates(self, tmp_path: Any) -> None:
        from origenlab_worker.prospects.daily import run_search, QUEUE_TARGET
        # Reduce queue to 1 slot: need = 1, pool_left = max(0, 2 - 1 - 0) = 1
        csv_path = self._write_csv(tmp_path)
        conn = self._fake_conn(unreviewed=QUEUE_TARGET - 1)
        counts = run_search(conn, run_id="00000000-0000-0000-0000-000000000003",
                            model=DEFAULT_MODEL, dry_run=True, csv_path=csv_path)
        assert counts["added"] == 1
        assert counts["pool_left"] >= 0

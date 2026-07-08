"""Unit tests for Prospectos CSV export helpers (mirror read model)."""

from __future__ import annotations

import csv
import io

import pytest

from origenlab_email_pipeline.lead_research.prospect_action_queue_export import (
    CSV_UTF8_BOM,
    EXPORT_FIELDNAMES,
    EXPORT_QUEUE_ALL_VISIBLE,
    EXPORT_QUEUE_READY_TO_CONTACT,
    EXPORT_QUEUE_FOLLOWUP_REVIEW,
    export_filename_for_queue,
    filter_prospects_for_export,
    matches_export_queue,
    prospect_to_export_row,
    render_prospects_csv,
    sanitize_csv_cell,
)


def _fixture_prospects() -> list[dict]:
    return [
        {
            "organization_name": "Acme Labs",
            "contact_name": None,
            "email": "contacto@acme.cl",
            "domain": "acme.cl",
            "sector": "Laboratorios privados",
            "region": "RM",
            "final_score": 95,
            "classification": "net_new_safe_review",
            "status": "net_new_safe_review",
            "is_blocked": False,
            "gmail_sent_count": 0,
            "gmail_received_count": 0,
            "recommended_next_action": "Redactar correo inicial",
            "evidence_url": "https://www.acme.cl/",
            "product_angle": "centrífugas",
            "source_type": "deepsearch",
        },
        {
            "organization_name": "RedSalud",
            "contact_name": "Compras",
            "email": "compras@redsalud.gob.cl",
            "domain": "redsalud.gob.cl",
            "sector": "Salud",
            "region": "RM",
            "final_score": 10,
            "classification": "old_gmail_prospect_review",
            "status": "revision_individual",
            "is_blocked": False,
            "gmail_sent_count": 2,
            "gmail_received_count": 0,
            "recommended_next_action": "Revisar historial",
            "evidence_url": "https://www.redsalud.gob.cl/",
            "product_angle": "centrífugas",
            "source_type": "gmail_historico",
        },
        {
            "organization_name": "Hospital Demo",
            "contact_name": None,
            "email": None,
            "domain": "hospitaldemo.cl",
            "sector": "Licitaciones",
            "region": "Valparaíso",
            "final_score": 88,
            "classification": "public_tender_review",
            "status": "public_tender_review",
            "is_blocked": False,
            "gmail_sent_count": 0,
            "gmail_received_count": 0,
            "recommended_next_action": "Revisar bases",
            "evidence_url": "https://www.mercadopublico.cl/",
            "product_angle": "incubadoras",
            "source_type": "deepsearch",
        },
    ]


def _parse_csv_text(text: str) -> csv.DictReader:
    normalized = text.encode("utf-8").decode("utf-8-sig")
    return csv.DictReader(io.StringIO(normalized))


def test_filter_ready_to_contact_queue() -> None:
    rows = filter_prospects_for_export(
        _fixture_prospects(),
        export_queue=EXPORT_QUEUE_READY_TO_CONTACT,
    )
    assert rows
    assert all(row["commercial_action_bucket"] == "ready_to_contact" for row in rows)
    assert {row["organization_name"] for row in rows} == {"Acme Labs"}


def test_filter_followup_queue() -> None:
    rows = filter_prospects_for_export(
        _fixture_prospects(),
        export_queue=EXPORT_QUEUE_FOLLOWUP_REVIEW,
    )
    assert rows
    assert all(row["commercial_action_bucket"] == "already_contacted" for row in rows)
    assert {row["organization_name"] for row in rows} == {"RedSalud"}


def test_filter_bucket_export_preserves_score_then_name_priority() -> None:
    base = _fixture_prospects()[0]
    prospects = [
        {**base, "organization_name": "Zulu Labs", "final_score": 50},
        {**base, "organization_name": "Alpha Labs", "final_score": 50},
        {**base, "organization_name": "Beta Labs", "final_score": 90},
        _fixture_prospects()[1],
    ]
    rows = filter_prospects_for_export(
        prospects,
        export_queue=EXPORT_QUEUE_ALL_VISIBLE,
        commercial_action_bucket="ready_to_contact",
    )
    assert [row["organization_name"] for row in rows] == ["Beta Labs", "Alpha Labs", "Zulu Labs"]
    assert all(row["commercial_action_bucket"] == "ready_to_contact" for row in rows)


def test_render_prospects_csv_header_and_columns() -> None:
    rows = filter_prospects_for_export(
        _fixture_prospects(),
        export_queue=EXPORT_QUEUE_READY_TO_CONTACT,
    )
    text = render_prospects_csv(rows)
    reader = _parse_csv_text(text)
    assert list(reader.fieldnames) == list(EXPORT_FIELDNAMES)
    parsed = list(reader)
    assert parsed
    assert "gmail_url" not in text
    assert "source_file" not in text


def test_export_filename_for_queue_is_constant() -> None:
    assert export_filename_for_queue("ready_to_contact") == "prospectos-ready-to-contact.csv"
    assert export_filename_for_queue("all_visible") == "prospectos-filtered-export.csv"


def test_matches_export_queue_all_visible() -> None:
    prospect = _fixture_prospects()[0]
    assert matches_export_queue(prospect, "all_visible") is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('=HYPERLINK("https://evil.example","click")', '\'=HYPERLINK("https://evil.example","click")'),
        ("+SUM(1,1)", "'+SUM(1,1)"),
        ("-1+2", "'-1+2"),
        ("@cmd", "'@cmd"),
        ("\t=cmd", "'\t=cmd"),
        ("\r=cmd", "'\r=cmd"),
        ("Acme Labs", "Acme Labs"),
        ("contacto@acme.cl", "contacto@acme.cl"),
        ("https://www.acme.cl/", "https://www.acme.cl/"),
        (None, ""),
    ],
)
def test_sanitize_csv_cell_formula_prefixes(raw: object, expected: str) -> None:
    assert sanitize_csv_cell(raw) == expected


def test_exported_csv_does_not_contain_unescaped_formula_cell() -> None:
    malicious = {
        **_fixture_prospects()[0],
        "organization_name": '=HYPERLINK("https://evil.example","click")',
        "contact_name": "+SUM(1,1)",
        "recommended_next_action": "-open portal",
    }
    row = prospect_to_export_row(malicious)
    text = render_prospects_csv([row])
    reader = _parse_csv_text(text)
    parsed = next(reader)
    assert parsed["organization_name"].startswith("'=")
    assert parsed["contact_name"].startswith("'+")
    assert parsed["recommended_next_action"].startswith("'-")
    assert ',=HYPERLINK("https://evil.example","click")' not in text
    assert ",+SUM(1,1)" not in text


def test_render_prospects_csv_includes_utf8_bom() -> None:
    text = render_prospects_csv([])
    assert text.startswith(CSV_UTF8_BOM)
    assert text.encode("utf-8").startswith(b"\xef\xbb\xbf")


def test_render_prospects_csv_preserves_spanish_accents() -> None:
    accented = {
        **_fixture_prospects()[0],
        "organization_name": "Laboratorio Peña",
        "contact_name": "María",
        "region": "Región Metropolitana",
        "sector": "Biobío",
        "product_angle": "centrífugas",
    }
    text = render_prospects_csv([prospect_to_export_row(accented)])
    assert text.startswith(CSV_UTF8_BOM)
    parsed = next(_parse_csv_text(text))
    assert parsed["organization_name"] == "Laboratorio Peña"
    assert parsed["region"] == "Región Metropolitana"
    assert parsed["sector"] == "Biobío"
    assert "PeÃ±a" not in text
    assert "RegiÃ³n" not in text

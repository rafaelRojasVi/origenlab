"""Unit tests for Prospectos CSV export helpers (mirror read model)."""

from __future__ import annotations

import csv
import io

from origenlab_email_pipeline.lead_research.prospect_action_queue_export import (
    EXPORT_FIELDNAMES,
    EXPORT_QUEUE_READY_TO_CONTACT,
    EXPORT_QUEUE_FOLLOWUP_REVIEW,
    export_filename_for_queue,
    filter_prospects_for_export,
    matches_export_queue,
    render_prospects_csv,
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


def test_render_prospects_csv_header_and_columns() -> None:
    rows = filter_prospects_for_export(
        _fixture_prospects(),
        export_queue=EXPORT_QUEUE_READY_TO_CONTACT,
    )
    text = render_prospects_csv(rows)
    reader = csv.DictReader(io.StringIO(text))
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

"""Tests for publishing ChileCompra API queue to canonical dashboard CSV."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from origenlab_email_pipeline.chilecompra_api import (
    VALIDITY_STATUS_EXPIRED,
    VALIDITY_STATUS_MISSING_CLOSE_DATE,
    VALIDITY_STATUS_NOT_PUBLICADA,
    VALIDITY_STATUS_OPEN,
    classify_chilecompra_validity_status,
)
from origenlab_email_pipeline.equipment_first_chilecompra_publish import (
    attach_item_metadata_to_queue_rows,
    CHILECOMPRA_CONTACT_STATUS,
    CHILECOMPRA_CONTACT_STATUS_MISSING_CLOSE_DATE,
    CHILECOMPRA_REVIEW_NOTE,
    CHILECOMPRA_SAFE_CHANNEL,
    PUBLISHED_DASHBOARD_FIELDS,
    coalesce_dashboard_rows_by_codigo,
    enrich_chilecompra_row_for_dashboard,
    is_dashboard_active_chilecompra_row,
    publish_chilecompra_equipment_queue_for_dashboard,
    publish_chilecompra_equipment_rows,
    sort_chilecompra_dashboard_rows,
    update_active_manifest_canonical_queue,
)

_SECRET_TICKET = "00000000-0000-0000-0000-000000000099"


def _source_row(
    *,
    codigo: str,
    next_action: str,
    fit_score: str,
    close_date: str = "20/06/2026 17:00:00",
    title: str = "Adquisición centrifuga laboratorio",
    validity_status: str = VALIDITY_STATUS_OPEN,
    chilecompra_status_code: str = "5",
    chilecompra_status: str = "Publicada",
    equipment_category: str = "centrifuge",
    item_description: str = "Centrifuga refrigerada",
    reason: str = "source:chilecompra_api; equipment:centrifuge",
) -> dict[str, str]:
    return {
        "codigo_licitacion": codigo,
        "buyer": "Hospital Demo",
        "region": "RM",
        "close_date": close_date,
        "title": title,
        "item_description": item_description,
        "equipment_category": equipment_category,
        "fit_score": fit_score,
        "reason": reason,
        "next_action": next_action,
        "api_checked_at_utc": "2026-06-14T12:00:00+00:00",
        "validity_status": validity_status,
        "chilecompra_status_code": chilecompra_status_code,
        "chilecompra_status": chilecompra_status,
        "source": "chilecompra_api",
    }


def test_enrich_adds_missing_dashboard_columns_without_buyer_email() -> None:
    enriched = enrich_chilecompra_row_for_dashboard(
        _source_row(codigo="1051-1-LP26", next_action="needs_supplier_quote", fit_score="80")
    )

    assert enriched["safe_channel"] == CHILECOMPRA_SAFE_CHANNEL
    assert enriched["contact_status"] == CHILECOMPRA_CONTACT_STATUS
    assert enriched["supplier_needed"] == "yes"
    assert enriched["contact_email"] == ""
    assert CHILECOMPRA_REVIEW_NOTE in enriched["operator_note"]
    assert enriched["gmail_prior_thread"] == "none"


def test_needs_supplier_quote_gets_supplier_needed_yes() -> None:
    enriched = enrich_chilecompra_row_for_dashboard(
        _source_row(codigo="1-1-LP26", next_action="needs_supplier_quote", fit_score="70")
    )
    assert enriched["supplier_needed"] == "yes"
    assert enriched["supplier_contact"] != ""


def test_account_intelligence_only_stays_review_only_and_sorts_lower() -> None:
    rows = sort_chilecompra_dashboard_rows(
        publish_chilecompra_equipment_rows(
            [
                _source_row(
                    codigo="LOW-1-LP26",
                    next_action="account_intelligence_only",
                    fit_score="80",
                ),
                _source_row(
                    codigo="HIGH-1-LP26",
                    next_action="quote_now",
                    fit_score="80",
                ),
            ]
        )
    )

    assert rows[0]["codigo_licitacion"] == "HIGH-1-LP26"
    assert rows[1]["codigo_licitacion"] == "LOW-1-LP26"
    low = next(row for row in rows if row["codigo_licitacion"] == "LOW-1-LP26")
    assert low["next_action"] == "account_intelligence_only"
    assert low["safe_channel"] == CHILECOMPRA_SAFE_CHANNEL
    assert low["contact_status"] == CHILECOMPRA_CONTACT_STATUS


def test_publish_writes_stable_dashboard_columns(tmp_path: Path) -> None:
    source_csv = tmp_path / "equipment_first_operator_queue_chilecompra_api_20260614.csv"
    out_csv = tmp_path / "equipment_first_operator_queue_20260614.csv"
    with source_csv.open("w", newline="", encoding="utf-8") as handle:
        sample = _source_row(codigo="1051-1-LP26", next_action="quote_now", fit_score="85")
        writer = csv.DictWriter(handle, fieldnames=list(sample.keys()))
        writer.writeheader()
        writer.writerow(sample)

    result = publish_chilecompra_equipment_queue_for_dashboard(
        source_csv=source_csv,
        out_csv=out_csv,
    )

    assert result["output_rows"] == 1
    with out_csv.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == list(PUBLISHED_DASHBOARD_FIELDS)
        row = next(reader)
    assert row["codigo_licitacion"] == "1051-1-LP26"
    assert row["priority_rank"] == "1"
    assert row["contact_email"] == ""
    assert row["close_date"] == "20/06/2026 17:00:00"
    assert row["validity_status"] == VALIDITY_STATUS_OPEN
    assert row["chilecompra_status_code"] == "5"
    assert row["source"] == "chilecompra_api"
    assert _SECRET_TICKET not in out_csv.read_text(encoding="utf-8")


def test_update_manifest_prepends_canonical_queue(tmp_path: Path) -> None:
    active = tmp_path / "active" / "current"
    active.mkdir(parents=True)
    manifest_path = active / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "campaign_mode": "equipment_first",
                "canonical_files": [
                    "equipment_first_operator_queue_chilecompra_api_20260614.csv",
                    "equipment_first_operator_queue_20260518.csv",
                ],
            }
        ),
        encoding="utf-8",
    )
    source_manifest = active / "equipment_first_operator_queue_chilecompra_api_20260614.manifest.json"
    source_manifest.write_text(
        json.dumps({"output_rows": 2, "detail_error_count": 0}),
        encoding="utf-8",
    )

    result = update_active_manifest_canonical_queue(
        active,
        queue_filename="equipment_first_operator_queue_20260614.csv",
        source_manifest=source_manifest,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["canonical_files"][0] == "equipment_first_operator_queue_20260614.csv"
    assert "equipment_first_operator_queue_chilecompra_api_20260614.csv" not in manifest["canonical_files"]
    assert result["chilecompra_api_publish"]["source_output_rows"] == 2
    assert _SECRET_TICKET not in manifest_path.read_text(encoding="utf-8")


def test_sort_uses_close_date_when_fit_and_action_tie() -> None:
    rows = sort_chilecompra_dashboard_rows(
        [
            enrich_chilecompra_row_for_dashboard(
                _source_row(
                    codigo="LATE-1-LP26",
                    next_action="quote_now",
                    fit_score="80",
                    close_date="30/06/2026 17:00:00",
                )
            ),
            enrich_chilecompra_row_for_dashboard(
                _source_row(
                    codigo="SOON-1-LP26",
                    next_action="quote_now",
                    fit_score="80",
                    close_date="10/06/2026 17:00:00",
                )
            ),
        ]
    )
    assert rows[0]["codigo_licitacion"] == "SOON-1-LP26"


def test_publish_excludes_expired_and_inactive_status_rows(tmp_path: Path) -> None:
    source_csv = tmp_path / "equipment_first_operator_queue_chilecompra_api_20260614.csv"
    out_csv = tmp_path / "equipment_first_operator_queue_20260614.csv"
    rows = [
        _source_row(codigo="OPEN-1-LP26", next_action="quote_now", fit_score="85"),
        _source_row(
            codigo="EXPIRED-1-LP26",
            next_action="quote_now",
            fit_score="90",
            validity_status=VALIDITY_STATUS_EXPIRED,
            close_date="10/06/2026 17:00:00",
        ),
        _source_row(
            codigo="CLOSED-1-LP26",
            next_action="quote_now",
            fit_score="95",
            validity_status=VALIDITY_STATUS_NOT_PUBLICADA,
            chilecompra_status_code="6",
            chilecompra_status="Cerrada",
        ),
    ]
    with source_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    result = publish_chilecompra_equipment_queue_for_dashboard(
        source_csv=source_csv,
        out_csv=out_csv,
    )

    assert result["input_rows"] == 3
    assert result["excluded_rows"] == 2
    assert result["output_rows"] == 1
    with out_csv.open(encoding="utf-8") as handle:
        published = list(csv.DictReader(handle))
    assert len(published) == 1
    assert published[0]["codigo_licitacion"] == "OPEN-1-LP26"


def test_missing_close_date_stays_review_only_in_dashboard_publish() -> None:
    source = _source_row(
        codigo="MISSING-1-LP26",
        next_action="needs_supplier_quote",
        fit_score="70",
        close_date="",
        validity_status=VALIDITY_STATUS_MISSING_CLOSE_DATE,
    )
    assert is_dashboard_active_chilecompra_row(source)
    enriched = enrich_chilecompra_row_for_dashboard(source)
    assert enriched["contact_status"] == CHILECOMPRA_CONTACT_STATUS_MISSING_CLOSE_DATE
    assert enriched["close_date"] == ""
    assert "missing_close_date" in enriched["operator_note"]


def test_iso_future_close_date_does_not_get_missing_close_date_review_status(tmp_path: Path) -> None:
    iso_close_date = "2026-06-17T19:00:00"
    validity = classify_chilecompra_validity_status(
        chilecompra_status_code="5",
        chilecompra_status="Publicada",
        close_date=iso_close_date,
        now=datetime(2026, 6, 14, 12, 0, 0),
    )
    assert validity == VALIDITY_STATUS_OPEN

    source_csv = tmp_path / "equipment_first_operator_queue_chilecompra_api_20260614.csv"
    out_csv = tmp_path / "equipment_first_operator_queue_20260614.csv"
    source = _source_row(
        codigo="ISO-1-LP26",
        next_action="quote_now",
        fit_score="85",
        close_date=iso_close_date,
        validity_status=validity,
    )
    with source_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(source.keys()))
        writer.writeheader()
        writer.writerow(source)

    publish_chilecompra_equipment_queue_for_dashboard(
        source_csv=source_csv,
        out_csv=out_csv,
    )

    with out_csv.open(encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["close_date"] == iso_close_date
    assert row["validity_status"] == VALIDITY_STATUS_OPEN
    assert row["contact_status"] == CHILECOMPRA_CONTACT_STATUS
    assert row["contact_status"] != CHILECOMPRA_CONTACT_STATUS_MISSING_CLOSE_DATE


def test_coalesce_same_codigo_with_different_categories_publishes_one_row() -> None:
    rows = publish_chilecompra_equipment_rows(
        [
            _source_row(
                codigo="1051-1-LP26",
                next_action="needs_supplier_quote",
                fit_score="70",
                equipment_category="centrifuge",
                item_description="Centrifuga refrigerada",
                reason="source:chilecompra_api; equipment:centrifuge",
            ),
            _source_row(
                codigo="1051-1-LP26",
                next_action="account_intelligence_only",
                fit_score="60",
                equipment_category="homogenizer",
                item_description="Homogeneizador ultrasónico",
                reason="source:chilecompra_api; equipment:homogenizer",
            ),
        ]
    )

    assert len(rows) == 1
    assert rows[0]["codigo_licitacion"] == "1051-1-LP26"
    assert "centrifuge" in rows[0]["equipment_category"]
    assert "homogenizer" in rows[0]["equipment_category"]
    assert "Centrifuga refrigerada" in rows[0]["item_description"]
    assert "Homogeneizador ultrasónico" in rows[0]["item_description"]
    assert "equipment:centrifuge" in rows[0]["reason"]
    assert "equipment:homogenizer" in rows[0]["reason"]


def test_coalesce_highest_priority_next_action_and_supplier_needed_wins() -> None:
    enriched = coalesce_dashboard_rows_by_codigo(
        [
            enrich_chilecompra_row_for_dashboard(
                _source_row(
                    codigo="1051-1-LP26",
                    next_action="account_intelligence_only",
                    fit_score="90",
                    equipment_category="homogenizer",
                )
            ),
            enrich_chilecompra_row_for_dashboard(
                _source_row(
                    codigo="1051-1-LP26",
                    next_action="needs_supplier_quote",
                    fit_score="70",
                    equipment_category="centrifuge",
                )
            ),
        ]
    )

    assert len(enriched) == 1
    assert enriched[0]["next_action"] == "needs_supplier_quote"
    assert enriched[0]["supplier_needed"] == "yes"


def test_publish_output_has_unique_codigo_licitacion(tmp_path: Path) -> None:
    source_csv = tmp_path / "equipment_first_operator_queue_chilecompra_api_20260614.csv"
    out_csv = tmp_path / "equipment_first_operator_queue_20260614.csv"
    rows = [
        _source_row(
            codigo="1051-1-LP26",
            next_action="quote_now",
            fit_score="85",
            equipment_category="centrifuge",
        ),
        _source_row(
            codigo="1051-1-LP26",
            next_action="needs_supplier_quote",
            fit_score="75",
            equipment_category="homogenizer",
            item_description="Homogeneizador",
            reason="source:chilecompra_api; equipment:homogenizer",
        ),
        _source_row(codigo="2000-1-LP26", next_action="quote_now", fit_score="80"),
    ]
    with source_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    result = publish_chilecompra_equipment_queue_for_dashboard(
        source_csv=source_csv,
        out_csv=out_csv,
    )

    assert result["output_rows"] == 2
    assert result["coalesced_duplicate_rows"] == 1
    assert result["unique_codigo_count"] == 2
    with out_csv.open(encoding="utf-8") as handle:
        published = list(csv.DictReader(handle))
    codigos = [row["codigo_licitacion"] for row in published]
    assert len(codigos) == len(set(codigos))


def test_coalesce_does_not_bypass_expired_and_inactive_filtering(tmp_path: Path) -> None:
    source_csv = tmp_path / "equipment_first_operator_queue_chilecompra_api_20260614.csv"
    out_csv = tmp_path / "equipment_first_operator_queue_20260614.csv"
    rows = [
        _source_row(codigo="OPEN-1-LP26", next_action="quote_now", fit_score="85"),
        _source_row(
            codigo="OPEN-1-LP26",
            next_action="needs_supplier_quote",
            fit_score="70",
            equipment_category="homogenizer",
        ),
        _source_row(
            codigo="EXPIRED-1-LP26",
            next_action="quote_now",
            fit_score="90",
            validity_status=VALIDITY_STATUS_EXPIRED,
        ),
    ]
    with source_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    result = publish_chilecompra_equipment_queue_for_dashboard(
        source_csv=source_csv,
        out_csv=out_csv,
    )

    assert result["output_rows"] == 1
    assert result["coalesced_duplicate_rows"] == 1
    with out_csv.open(encoding="utf-8") as handle:
        published = list(csv.DictReader(handle))
    assert published[0]["codigo_licitacion"] == "OPEN-1-LP26"
    assert "homogenizer" in published[0]["equipment_category"]


def test_publish_includes_fecha_publicacion_and_item_metadata(tmp_path: Path) -> None:
    source_csv = tmp_path / "equipment_first_operator_queue_chilecompra_api_20260614.csv"
    out_csv = tmp_path / "equipment_first_operator_queue_20260614.csv"
    source = _source_row(codigo="META-1-LP26", next_action="quote_now", fit_score="85")
    source.update(
        {
            "fecha_publicacion": "15/06/2026 10:00:00",
            "unspsc_code": "41100000",
            "unidad": "Unidad",
            "cantidad": "2",
            "producto": "Centrifuga",
            "nivel_1": "Equipamiento",
            "nivel_2": "Laboratorio",
            "nivel_3": "Centrifugas",
        }
    )
    with source_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(source.keys()))
        writer.writeheader()
        writer.writerow(source)

    publish_chilecompra_equipment_queue_for_dashboard(
        source_csv=source_csv,
        out_csv=out_csv,
    )

    with out_csv.open(encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["fecha_publicacion"] == "15/06/2026 10:00:00"
    assert row["unspsc_code"] == "41100000"
    assert row["cantidad"] == "2"
    assert row["producto"] == "Centrifuga"
    assert "mercadopublico.cl" in row["mercado_publico_url"]
    assert "META-1-LP26" in row["mercado_publico_url"]
    assert _SECRET_TICKET not in row["mercado_publico_url"]
    assert _SECRET_TICKET not in out_csv.read_text(encoding="utf-8")


def test_coalesce_combines_item_metadata_for_same_codigo() -> None:
    rows = publish_chilecompra_equipment_rows(
        [
            {
                **_source_row(
                    codigo="1051-1-LP26",
                    next_action="needs_supplier_quote",
                    fit_score="70",
                    equipment_category="centrifuge",
                ),
                "unspsc_code": "41100000",
                "cantidad": "1",
                "producto": "Centrifuga",
                "nivel_1": "Equipamiento",
            },
            {
                **_source_row(
                    codigo="1051-1-LP26",
                    next_action="account_intelligence_only",
                    fit_score="60",
                    equipment_category="homogenizer",
                ),
                "unspsc_code": "42200000",
                "cantidad": "2",
                "producto": "Homogeneizador",
                "nivel_2": "Laboratorio",
            },
        ]
    )

    assert len(rows) == 1
    merged = rows[0]
    assert "41100000" in merged["unspsc_code"]
    assert "42200000" in merged["unspsc_code"]
    assert "Centrifuga" in merged["producto"]
    assert "Homogeneizador" in merged["producto"]
    assert merged["cantidad"] == "3"


def test_enrich_carries_anexos_json_to_published_row() -> None:
    enriched = enrich_chilecompra_row_for_dashboard(
        {
            **_source_row(codigo="1051-1-LP26", next_action="quote_now", fit_score="90"),
            "anexos_json": json.dumps(
                [{"nombre": "Bases.pdf", "tipo": "Bases", "url": "https://www.mercadopublico.cl/bases.pdf"}]
            ),
        }
    )
    assert "anexos_json" in enriched
    assert "Bases.pdf" in enriched["anexos_json"]
    assert "anexos_json" in PUBLISHED_DASHBOARD_FIELDS

def test_item_metadata_quantity_is_scoped_to_detected_category() -> None:
    queue_rows = [
        {
            "codigo_licitacion": "1093303-5-CO26",
            "equipment_category": "centrifuge",
        }
    ]
    normalized_rows = [
        {
            "codigo": "1093303-5-CO26",
            "line_description": (
                "2.- EQUIPO DETECTOR DE FUGA/FILTRACIÓN EN PRODUCTOS "
                "FARMACÉUTICOS Y DISPOSITIVOS MÉDICOS"
            ),
            "producto": "Unidades de laboratorio",
            "cantidad": "1",
            "unidad": "Unidad",
        },
        {
            "codigo": "1093303-5-CO26",
            "line_description": "6.- CENTRÍFUGA REFRIGERADA DE SOBREMESA",
            "producto": "Unidades de laboratorio",
            "cantidad": "1",
            "unidad": "Unidad",
        },
    ]

    attached = attach_item_metadata_to_queue_rows(queue_rows, normalized_rows)

    assert len(attached) == 1
    assert attached[0]["cantidad"] == "1"
    assert "CENTRÍFUGA REFRIGERADA" in attached[0]["line_description"]
    assert "DETECTOR DE FUGA" not in attached[0]["line_description"]


def test_item_metadata_aggregates_only_lines_within_each_category() -> None:
    queue_rows = [
        {
            "codigo_licitacion": "745712-19-LP26",
            "equipment_category": "balance",
        },
        {
            "codigo_licitacion": "745712-19-LP26",
            "equipment_category": "centrifuge",
        },
    ]
    normalized_rows = [
        {
            "codigo": "745712-19-LP26",
            "line_description": "Ítem N°4 Balanza analítica para laboratorio",
            "producto": "Balanzas electrónicas de carga superior",
            "cantidad": "1",
            "unidad": "Unidad",
        },
        {
            "codigo": "745712-19-LP26",
            "line_description": (
                "Ítem N°12 Centrífuga para tubos eppendorf de 1,5 a 2 ml"
            ),
            "producto": "Centrífugos de laboratorio",
            "cantidad": "1",
            "unidad": "Unidad",
        },
        {
            "codigo": "745712-19-LP26",
            "line_description": (
                "Ítem N°15 Centrífuga de sobremesa con rotor de ángulo fijo"
            ),
            "producto": "Centrífugos de laboratorio",
            "cantidad": "1",
            "unidad": "Unidad",
        },
    ]

    attached = attach_item_metadata_to_queue_rows(queue_rows, normalized_rows)
    by_category = {
        row["equipment_category"]: row
        for row in attached
    }

    assert by_category["balance"]["cantidad"] == "1"
    assert by_category["centrifuge"]["cantidad"] == "2"
    assert "Balanza analítica" in by_category["balance"]["line_description"]
    assert "Centrífuga para tubos" in by_category["centrifuge"]["line_description"]
    assert "Centrífuga de sobremesa" in by_category["centrifuge"]["line_description"]


def test_category_without_item_level_support_does_not_inherit_tender_quantity() -> None:
    queue_rows = [
        {
            "codigo_licitacion": "TEST-1-LP26",
            "equipment_category": "centrifuge",
        }
    ]
    normalized_rows = [
        {
            "codigo": "TEST-1-LP26",
            "line_description": "Equipo detector de fuga",
            "producto": "Unidades de laboratorio",
            "cantidad": "2",
            "unidad": "Unidad",
        }
    ]

    attached = attach_item_metadata_to_queue_rows(queue_rows, normalized_rows)

    assert attached[0]["cantidad"] == ""


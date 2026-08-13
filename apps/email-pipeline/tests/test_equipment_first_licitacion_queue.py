"""Tests for equipment-first Licitacion_Publicada queue builder."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from origenlab_email_pipeline.equipment_first_licitacion_queue import (
    STOP_CONSUMABLES_OUTREACH_CODES,
    build_equipment_queue_rows,
    build_equipment_queue_rows_from_normalized_rows,
    classify_next_action,
    detect_equipment_categories,
    is_maintenance_only_service,
    parse_close_date,
)


def test_detect_centrifuge_excludes_water_pump_context() -> None:
    blob = "Construcción sala de bombas, 2 centrifugas sumergible para pozo"
    assert detect_equipment_categories(blob) == []


def test_detect_centrifuge_in_lab_context() -> None:
    blob = "Centrifuga para laboratorio clinico refrigerada"
    cats = detect_equipment_categories(blob)
    assert any(c == "centrifuge" for c, _ in cats)


def test_ultrasonido_skipped_for_ecografo() -> None:
    blob = "MANTENCIÓN EQUIPOS DE ECOGRAFÍA ultrasonido cardiaco"
    assert not any(
        c == "lab_ultrasonic_processor" for c, _ in detect_equipment_categories(blob)
    )


def test_tubos_microcentrifuga_excluded_without_equipment_purchase() -> None:
    assert (
        detect_equipment_categories("INSUMOS DE LABORATORIO TUBOS DE MICROCENTRIFUGA")
        == []
    )
    assert (
        detect_equipment_categories("Tubos para centrifuga, transparente 50 ml") == []
    )
    blob = (
        "ADQUISICIÓN DE MATERIAL DE LABORATORIO PARA PROYECTO | "
        "Tubos para centrifuga, transparente 50 ml"
    )
    assert detect_equipment_categories(blob) == []


def test_tubos_conicos_accented_excluded_as_consumable_via_line_description() -> None:
    """A concrete line_description alone must carry enough consumable evidence to
    exclude it, without relying on producto/taxonomy — accented "cónicos" must be
    recognized the same as unaccented "conicos".
    """
    rows = build_equipment_queue_rows_from_normalized_rows(
        [
            {
                "codigo": "GOLD-101-LP26",
                "title": "Adquisición de material de laboratorio",
                "line_description": "Tubos cónicos para centrífuga 50 ml",
                "producto": "Tubos para centrífuga",
            }
        ],
        now=datetime(2026, 8, 13, 12, 0, 0),
    )

    assert rows == []


def test_generic_balanza_digital_without_lab_context_excluded() -> None:
    assert detect_equipment_categories("BALANZA DIGITAL insumos no clinicos") == []


def test_stop_codes_classified_skip_consumables() -> None:
    for code in STOP_CONSUMABLES_OUTREACH_CODES:
        assert (
            classify_next_action(
                codigo=code,
                category="centrifuge",
                close_dt=datetime(2026, 6, 1),
                maintenance=False,
                arriendo=False,
                convenio_reactivos=False,
                now=datetime(2026, 5, 18),
            )
            == "skip_consumables"
        )


def test_opportunity_truth_excludes_plural_maintenance_service() -> None:
    rows = build_equipment_queue_rows_from_normalized_rows(
        [
            {
                "codigo": "5067-119-L126",
                "buyer": "UNIVERSIDAD DE SANTIAGO DE CHILE",
                "close_date": "14/08/2026 15:00:00",
                "title": ("Mantenciones preventivas de los equipos del laboratorio"),
                "descripcion": "Servicio de mantenciones preventivas",
                "line_description": ("Mantención de Balanza Analítica modelo FA 2004G"),
                "producto": "Kits de reparación",
            }
        ],
        now=datetime(2026, 8, 13, 12, 0, 0),
    )

    assert rows == []
    assert is_maintenance_only_service(
        "Mantenciones preventivas de dos balanzas analíticas"
    )


def test_opportunity_truth_detects_plural_balanzas_and_basculas() -> None:
    rows = build_equipment_queue_rows_from_normalized_rows(
        [
            {
                "codigo": "2981-205-LE26",
                "buyer": "JENACO",
                "close_date": "14/08/2026 15:00:00",
                "title": "Adquisición de equipos",
                "line_description": "10 BALANZAS DE PRECISIÓN Y ACCESORIOS",
                "producto": "Básculas analíticas",
            }
        ],
        now=datetime(2026, 8, 13, 12, 0, 0),
    )

    assert len(rows) == 1
    assert rows[0]["equipment_category"] == "balance"
    assert rows[0]["next_action"] == "quote_now"


def test_opportunity_truth_excludes_clinical_and_body_composition_scales() -> None:
    rows = build_equipment_queue_rows_from_normalized_rows(
        [
            {
                "codigo": "2410-66-LP26",
                "buyer": "I MUNICIPALIDAD DE LOS ANGELES",
                "close_date": "19/08/2026 10:00:00",
                "title": "Equipamiento clínico",
                "line_description": ("BALANZA MECÁNICA CON TALLÍMETRO INCORPORADO"),
                "producto": "Balanzas electrónicas de carga superior",
            },
            {
                "codigo": "2548-70-L126",
                "buyer": "I MUNICIPALIDAD DE PADRE LAS CASAS",
                "close_date": "20/08/2026 10:00:00",
                "title": "Equipamiento clínico",
                "line_description": (
                    "Balanza de bioimpedancia para composición corporal"
                ),
                "producto": "Balanzas de uso comercial",
            },
        ],
        now=datetime(2026, 8, 13, 12, 0, 0),
    )

    assert rows == []


def test_opportunity_truth_prefers_concrete_line_evidence() -> None:
    rows = build_equipment_queue_rows_from_normalized_rows(
        [
            {
                "codigo": "4291-46-LE26",
                "buyer": "UNIVERSIDAD DE ANTOFAGASTA",
                "close_date": "20/08/2026 15:01:00",
                "title": "Adquisición de equipos de laboratorio",
                "line_description": "Centrífuga Refrigerada Universal de sobremesa",
                "producto": "Bombas centrífugas de laboratorio",
            },
            {
                "codigo": "4291-46-LE26",
                "buyer": "UNIVERSIDAD DE ANTOFAGASTA",
                "close_date": "20/08/2026 15:01:00",
                "title": "Adquisición de equipos de laboratorio",
                "line_description": "Campana de Flujo Laminar Vertical 800 mm",
                "producto": "Bombas centrífugas de laboratorio",
            },
            {
                "codigo": "4291-46-LE26",
                "buyer": "UNIVERSIDAD DE ANTOFAGASTA",
                "close_date": "20/08/2026 15:01:00",
                "title": "Adquisición de equipos de laboratorio",
                "line_description": "Termociclador PCR",
                "producto": "Bombas centrífugas de laboratorio",
            },
        ],
        now=datetime(2026, 8, 13, 12, 0, 0),
    )

    assert len(rows) == 1
    assert rows[0]["equipment_category"] == "centrifuge"
    assert "Centrífuga Refrigerada" in rows[0]["item_description"]
    assert "Campana" not in rows[0]["item_description"]
    assert "Termociclador" not in rows[0]["item_description"]


def test_build_queue_from_minimal_csv(tmp_path: Path) -> None:
    src = tmp_path / "Licitacion_Publicada.csv"
    with src.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Textbox69", "ReportTitle"])
        w.writerow(["header", "x"])
        w.writerow(
            [
                "Textbox36",
                "TipoLC",
                "Textbox37",
                "Textbox38",
                "Textbox39",
                "citName",
                "Textbox40",
                "FechaCierre1",
                "rbiDescription",
                "rbiGoodAndService",
                "Unidad_de_Medida",
                "Cantidad",
                "ProductoName",
                "Level1",
                "Level2",
                "Level3",
            ]
        )
        w.writerow(
            [
                "1057898-51-LP26",
                "LP",
                "ADQUISICIÓN DE CENTRIFUGAS UMT",
                "Hospital regional",
                "SERVICIO DE SALUD NUBLE",
                "Región del Ñuble",
                "15/05/2026 14:16:11",
                "04/06/2026 17:00:00",
                "FAMILIA N°1: EQUIPOS UMT 1",
                "41105103",
                "Global",
                "1",
                "Bombas centrífugas de laboratorio",
                "Equipamiento para laboratorios",
                "Equipos e insumos para laboratorio",
                "Bombas y conductos de laboratorio",
            ]
        )
        w.writerow(
            [
                "1497-6-LE26",
                "LE",
                "INSUMOS PARA ANALISIS",
                "SEREMI",
                "SUBSECRETARIA DE SALUD PUBLICA",
                "Región de Los Ríos",
                "01/05/2026 10:00:00",
                "22/05/2026 15:01:00",
                "Reactivos microbiológicos",
                "x",
                "Unidad",
                "1",
                "Medios de cultivo",
                "Lab",
                "Lab",
                "Lab",
            ]
        )

    rows = build_equipment_queue_rows(src, now=datetime(2026, 5, 18, 12, 0, 0))
    codes = {r["codigo_licitacion"] for r in rows}
    assert "1057898-51-LP26" in codes
    assert "1497-6-LE26" not in codes  # consumables only, no equipment line
    nuble = next(r for r in rows if r["codigo_licitacion"] == "1057898-51-LP26")
    assert nuble["equipment_category"] == "centrifuge"
    assert nuble["next_action"] == "quote_now"


def test_parse_close_date_supports_chilean_datetime_formats() -> None:
    assert parse_close_date("20/06/2026 17:00:00") == datetime(2026, 6, 20, 17, 0, 0)
    assert parse_close_date("20/06/2026 17:00") == datetime(2026, 6, 20, 17, 0, 0)


def test_parse_close_date_supports_iso_datetime_formats() -> None:
    assert parse_close_date("2026-06-17T19:00:00") == datetime(2026, 6, 17, 19, 0, 0)
    assert parse_close_date("2026-06-17T19:00") == datetime(2026, 6, 17, 19, 0, 0)
    assert parse_close_date("2026-06-17 19:00:00") == datetime(2026, 6, 17, 19, 0, 0)


def test_parse_close_date_supports_iso_timezone_suffix_as_naive_wall_time() -> None:
    assert parse_close_date("2026-06-17T19:00:00+00:00") == datetime(
        2026, 6, 17, 19, 0, 0
    )
    assert parse_close_date("2026-06-17T19:00:00Z") == datetime(2026, 6, 17, 19, 0, 0)


def test_build_equipment_queue_rows_accepts_aware_now_with_iso_close_date() -> None:
    rows = build_equipment_queue_rows_from_normalized_rows(
        [
            {
                "codigo": "1051-1-LP26",
                "buyer": "Hospital Demo",
                "region": "RM",
                "close_date": "2026-06-17T19:00:00",
                "title": "Adquisición centrifuga laboratorio",
                "line_description": "Centrifuga refrigerada para laboratorio clínico",
                "producto": "Centrifuga",
            }
        ],
        now=datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc),
    )

    assert len(rows) == 1
    assert rows[0]["codigo_licitacion"] == "1051-1-LP26"
    assert rows[0]["close_date"] == "2026-06-17T19:00:00"
    assert rows[0]["next_action"] == "quote_now"


def test_build_equipment_queue_rows_naive_now_still_works() -> None:
    rows = build_equipment_queue_rows_from_normalized_rows(
        [
            {
                "codigo": "2000-1-LP26",
                "buyer": "Hospital Demo",
                "region": "RM",
                "close_date": "20/06/2026 17:00:00",
                "title": "Balanza analítica laboratorio",
                "line_description": "Balanza analítica 0.1 mg para laboratorio clínico",
                "producto": "Balanza",
            }
        ],
        now=datetime(2026, 6, 14, 12, 0, 0),
    )

    assert len(rows) == 1
    assert rows[0]["equipment_category"] == "balance"
    assert rows[0]["next_action"] in {"quote_now", "needs_supplier_quote"}


def test_maintenance_only_centrifuge_tender_1702_excluded_from_queue() -> None:
    rows = build_equipment_queue_rows_from_normalized_rows(
        [
            {
                "codigo": "1702-20-L126",
                "buyer": "SERVICIO DE SALUD",
                "region": "RM",
                "close_date": "30/06/2026 17:00:00",
                "title": "SERVICIO DE MANTENIMIENTO DE EQUIPOS DE LABORATORIO",
                "descripcion": "Contratar el servicio de mantenimiento preventivo y correctivo",
                "line_description": (
                    "MANTENIMIENTO PREVENTIVO Y CORRECTIVOS DE CENTRIFUGA DE LABORATORIO"
                ),
                "producto": "Mantenimiento centrífuga",
            },
            {
                "codigo": "1702-20-L126",
                "buyer": "SERVICIO DE SALUD",
                "region": "RM",
                "close_date": "30/06/2026 17:00:00",
                "title": "SERVICIO DE MANTENIMIENTO DE EQUIPOS DE LABORATORIO",
                "line_description": (
                    "MANTENIMIENTO PREVENTIVO Y CORRECTIVO DE INCUBADORA DE LABORATORIO"
                ),
                "producto": "Mantenimiento incubadora",
            },
        ],
        now=datetime(2026, 6, 14, 12, 0, 0),
    )
    assert rows == []
    assert is_maintenance_only_service(
        "SERVICIO DE MANTENIMIENTO | MANTENIMIENTO PREVENTIVO Y CORRECTIVOS DE CENTRIFUGA"
    )


def test_true_centrifuge_acquisition_still_appears_with_maintenance_mention() -> None:
    rows = build_equipment_queue_rows_from_normalized_rows(
        [
            {
                "codigo": "1051-1-LP26",
                "buyer": "Hospital Demo",
                "region": "RM",
                "close_date": "20/06/2026 17:00:00",
                "title": "ADQUISICIÓN DE CENTRÍFUGA DE LABORATORIO",
                "descripcion": (
                    "Compra de equipo nuevo; incluye mantenimiento preventivo año 1 como garantía"
                ),
                "line_description": "Centrífuga refrigerada para laboratorio clínico",
                "producto": "Centrífuga",
            }
        ],
        now=datetime(2026, 6, 14, 12, 0, 0),
    )
    assert len(rows) == 1
    assert rows[0]["codigo_licitacion"] == "1051-1-LP26"
    assert rows[0]["equipment_category"] == "centrifuge"
    assert rows[0]["next_action"] in {"quote_now", "needs_supplier_quote"}


def test_incubator_replacement_parts_not_removed_when_not_maintenance_only() -> None:
    rows = build_equipment_queue_rows_from_normalized_rows(
        [
            {
                "codigo": "2200-3-LP26",
                "buyer": "Laboratorio Regional",
                "region": "Biobío",
                "close_date": "25/06/2026 17:00:00",
                "title": "Repuestos incubadora laboratorio",
                "line_description": "Motor ventilador para incubadora de laboratorio clínico",
                "producto": "Repuesto incubadora",
            }
        ],
        now=datetime(2026, 6, 14, 12, 0, 0),
    )
    assert len(rows) == 1
    assert rows[0]["equipment_category"] == "incubator"
    assert rows[0]["next_action"] != "skip_maintenance_service"


def test_classify_next_action_maintenance_service_only_returns_skip() -> None:
    assert (
        classify_next_action(
            codigo="1702-20-L126",
            category="centrifuge",
            close_dt=datetime(2026, 6, 30, 17, 0, 0),
            maintenance=True,
            arriendo=False,
            convenio_reactivos=False,
            now=datetime(2026, 6, 14, 12, 0, 0),
            service_only=True,
        )
        == "skip_maintenance_service"
    )

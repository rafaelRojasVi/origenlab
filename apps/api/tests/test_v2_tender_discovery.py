"""«En Mercado Público»'s matching, without a database: which titles the sweep keeps, how a stored
head is classified (catalog family, laboratory equipment, or not shown), and Chilean dates. Every
value is invented."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from origenlab_api.v2.tenders.discovery import chile_time, classify, families_in, fold, title_candidate


def _detail(*items: dict, **head) -> dict:
    return {"Comprador": {"NombreOrganismo": "Hospital Ejemplo"}, "Items": {"Listado": list(items)}, **head}


@pytest.mark.parametrize(("title", "family"), [
    ("ADQUISICIÓN DE BALANZA ANALÍTICA", "pesaje"),
    ("Compra de básculas para bodega", "pesaje"),
    ("Analizador de humedad para laboratorio de suelos", "pesaje"),
    ("Microcentrífuga refrigerada", "centrifugas"),
    ("Homogeneizador de tejidos", "homogeneizacion"),
    ("Sonicador para extracción", "ultrasonido"),
    ("Baño ultrasónico 10 L", "ultrasonido"),
    ("Agitador magnético con calefacción", "agitacion"),
    ("Vórtex de tubos", "agitacion"),
    ("Osmómetro de punto de congelación", "osmometria"),
    ("Cámara de electroforesis horizontal", "electroforesis"),
])
def test_each_catalog_family_is_found_in_a_title_and_kept_by_the_sweep(title, family) -> None:
    assert family in families_in(title)
    assert title_candidate(title)


@pytest.mark.parametrize("title", ["Servicio de aseo", "Pintura de fachada", "Arriendo de vehículos", "", None])
def test_unrelated_titles_are_not_kept(title) -> None:
    assert not title_candidate(title)


def test_fold_drops_accents_case_and_extra_spaces() -> None:
    assert fold("  CENTRÍFUGA   de  Mesa ") == "centrifuga de mesa"


def test_a_family_in_an_item_puts_the_notice_in_the_catalog_tier_with_that_item() -> None:
    head = _detail({"CodigoProducto": 41111500, "NombreProducto": "Balanzas", "Descripcion": "Balanza 0,1 mg",
                    "Cantidad": 2, "UnidadMedida": "Unidad"})
    match = classify("Equipamiento para laboratorio de química", head)
    assert match["tier"] == "catalogo" and match["families"] == ["pesaje"]
    assert match["items"] == [{"name": "Balanzas", "description": "Balanza 0,1 mg", "quantity": 2, "unit": "Unidad",
                               "unspsc": "41111500"}]


def test_families_come_in_the_page_order_without_repeats() -> None:
    match = classify("Centrífuga y balanza", _detail({"NombreProducto": "Balanza"}, {"NombreProducto": "Centrífuga"}))
    assert match["families"] == ["pesaje", "centrifugas"]


def test_a_laboratory_equipment_item_without_a_family_is_the_second_tier() -> None:
    head = _detail({"CodigoProducto": 41104900, "NombreProducto": "Incubadoras de laboratorio"})
    assert classify("Equipos para laboratorio clínico", head)["tier"] == "laboratorio"


def test_laboratory_services_are_not_shown_once_the_detail_says_so() -> None:
    head = _detail({"CodigoProducto": 85121803, "NombreProducto": "Laboratorios biológicos",
                    "Descripcion": "CONVENIO SUMINISTRO DE EXAMENES"})
    assert classify("EXAMENES DE LABORATORIO", head)["tier"] is None


def test_before_its_detail_a_laboratorio_title_is_shown_unless_it_reads_as_a_service() -> None:
    assert classify("Equipamiento laboratorio liceo", {"CodigoExterno": "x"})["tier"] == "laboratorio"
    assert classify("Exámenes de laboratorio", {"CodigoExterno": "x"})["tier"] is None


def test_chile_time_reads_both_api_shapes_as_santiago_time() -> None:
    assert chile_time("30/11/2026 15:00:00") == datetime(2026, 11, 30, 18, 0, tzinfo=UTC)
    assert chile_time("2026-07-15T10:00:00") == datetime(2026, 7, 15, 14, 0, tzinfo=UTC)
    assert chile_time("2026-07-15T10:00:00+00:00") == datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    assert chile_time("") is None and chile_time(None) is None and chile_time("mañana") is None

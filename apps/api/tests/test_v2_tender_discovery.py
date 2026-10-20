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
    (item,) = match["items"]
    assert {k: item[k] for k in ("name", "description", "quantity", "unit", "unspsc")} == {
        "name": "Balanzas", "description": "Balanza 0,1 mg", "quantity": 2, "unit": "Unidad", "unspsc": "41111500"}
    assert item["families"] == [{"key": "pesaje", "label": "Balanzas y humedad"}] and item["lab_equipment"] is True


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


def _vocabulary():
    from origenlab_api.v2.tenders.discovery import build_vocabulary

    return build_vocabulary([
        ("Homogeneizador manual D1000, incluye sondas", "Homogenizer", "Homogeneizadores", "Benchmark Scientific", "D1000"),
        ("Procesador ultrasónico UP200St", None, None, "Hielscher Ultrasonics", "UP200St"),
        ("Termociclador de gradiente", None, "Termocicladores", "Bio-Rad", "T100"),
        ("Kit de sondas", None, None, "IKA", "S25N"),
        ("Baño termostático 20 L", None, None, "Löser Messtechnik", "OM 807"),
    ])


def test_the_catalog_vocabulary_takes_types_makers_and_models_and_skips_parts_and_vague_words() -> None:
    v = _vocabulary()
    assert v.hits("Compra de termocicladores") == ["termocicladores"]
    assert v.hits("Procesador ultrasónico Hielscher modelo UP200St") == ["up200st", "hielscher", "procesador ultrasonico"]
    assert v.hits("Baño termostático para laboratorio") == ["bano termostatico"]
    assert v.hits("Equipos IKA y Löser") == ["ika", "loser"]
    # A part is not a type, «Bio» is too vague for a maker, a spaced model is not a model.
    assert v.hits("kit de sondas") == [] and v.hits("bio") == [] and v.hits("om 807") == []


def test_a_catalog_word_without_a_curated_family_is_the_catalog_tier_with_its_terms() -> None:
    match = classify("Compra equipo Hielscher UP200St", {}, _vocabulary())
    assert match["tier"] == "catalogo" and match["families"] == ["catalogo"] and match["terms"] == ["up200st", "hielscher"]
    assert title_candidate("Compra equipo Hielscher UP200St", _vocabulary())
    assert not title_candidate("Compra equipo Hielscher UP200St")  # the curated families alone miss it


def test_an_everyday_first_word_needs_the_next_one() -> None:
    from origenlab_api.v2.tenders.discovery import build_vocabulary

    v = build_vocabulary([("Contador de colonias digital", None, None, "Fabricante", "CC100"),
                          ("Mesa antivibratoria", None, None, "Fabricante", "MA1")])
    assert v.hits("Servicio de contador auditor") == []
    assert v.hits("Contador de colonias") == ["contador de colonias"]
    assert v.hits("Mesa de reuniones") == []


def test_all_items_keeps_every_item_and_says_what_each_matched() -> None:
    head = _detail({"NombreProducto": "Termocicladores", "Descripcion": "T100 o equivalente"},
                   {"NombreProducto": "Mesa de trabajo"})
    match = classify("Equipamiento", head, _vocabulary(), all_items=True)
    assert [i["name"] for i in match["items"]] == ["Termocicladores", "Mesa de trabajo"]
    assert match["items"][0]["terms"] == ["t100", "termocicladores"] and match["items"][1]["terms"] == []


def _junji_vocabulary():
    from origenlab_api.v2.tenders.discovery import build_vocabulary

    return build_vocabulary([
        ("Material de referencia certificado", None, None, "Fabricante Uno", "MR100"),
        ("Programas de calibración", None, None, "Fabricante Dos", "PC200"),
        ("Centrífuga refrigerada", None, None, "Ortoalresa", "DIGI22"),
        ("Agitador magnético", None, None, "IKA", "RCT5"),
        ("Contador de colonias digital", None, None, "Fabricante Tres", "CC300"),
    ])


def test_a_kindergartens_teaching_material_is_not_a_centrifuge() -> None:
    """2026-10-20, as seen live: «Material de enseñanza no fungible» for kindergartens matched
    «Centrífugas» and «Agitadores» through toy sets, and «material» / «programas» through the catalog."""
    head = _detail(
        {"CodigoProducto": 60101500, "NombreProducto": "Juegos educativos", "Descripcion": "SET DE CIENCIAS CON CENTRIFUGA Y AGITADOR"},
        {"CodigoProducto": 60101501, "NombreProducto": "Juegos educativos", "Descripcion": "SET DE 3 CANASTOS DE MIMBRE CON ASAS"},
    )
    match = classify("ADQUISICIÓN DE MATERIAL DE ENSEÑANZA NO FUNGIBLE", head, _junji_vocabulary())
    assert match == {"tier": None, "families": [], "terms": [], "items": []}


def test_an_item_coded_as_equipment_or_not_coded_is_read_one_coded_otherwise_is_not() -> None:
    v = _junji_vocabulary()
    coded = classify("Compra", _detail({"CodigoProducto": 41103800, "NombreProducto": "Centrífugas",
                                        "Descripcion": "centrífuga refrigerada"}), v)
    assert coded["families"] == ["centrifugas"] and coded["terms"] == ["centrifugas"]
    medical = classify("Compra", _detail({"CodigoProducto": 42181500, "NombreProducto": "Centrífuga clínica"}), v)
    assert medical["families"] == ["centrifugas"]
    uncoded = classify("Compra", _detail({"NombreProducto": "Centrífuga de mesa"}), v)
    assert uncoded["families"] == ["centrifugas"]
    furniture = classify("Compra", _detail({"CodigoProducto": 56101500, "NombreProducto": "Mueble para centrífuga"}), v)
    assert furniture["families"] == [] and furniture["tier"] is None


def test_common_words_are_never_catalog_types_and_a_title_type_needs_laboratory_context() -> None:
    v = _junji_vocabulary()
    assert v.hits("Programas de material didáctico") == []
    # A type in the title alone counts with laboratory context, a model or maker always.
    assert classify("Contador de colonias", {}, v)["terms"] == []
    assert classify("Contador de colonias para laboratorio", {}, v)["terms"] == ["contador de colonias"]
    # A curated family is context enough: «agitador» is one.
    assert classify("Agitador magnético", {}, v)["terms"] == ["agitador"]
    assert classify("Equipo IKA RCT5", {}, v)["terms"] == ["rct5", "ika"]

from __future__ import annotations

from origenlab_email_pipeline.equipment_first_chilecompra_publish import (
    aggregate_chilecompra_item_metadata_by_category,
    attach_item_metadata_to_queue_rows,
)

# Real cached ChileCompra detail-cache evidence (SAG multi-item tender,
# chilecompra_detail_cache/745712-19-LP26.json). Several items have a
# NombreProducto/Categoria of "Centrífugos de laboratorio" copied across
# unrelated line items by the buyer's catalog tagging, while the concrete
# Descripcion names a different, unrelated instrument.
SAG_CODIGO = "745712-19-LP26"
SAG_SPECTROPHOTOMETER_ROW = {
    "codigo": SAG_CODIGO,
    "line_description": (
        "Ítem N°3 Espectrofotómetro UVvisible. Detalles en bases técnicas. "
        "Para Valparaíso."
    ),
    "producto": "Centrífugos de laboratorio",
    "nivel_1": (
        "Equipamiento para el acondicionamiento, distribución y filtrado de "
        "fluidos / Filtros y purificación industrial / Separadores"
    ),
    "unspsc_code": "40161701",
    "unidad": "Unidad",
    "cantidad": "1.0",
}
SAG_MAGNETIC_STIRRER_ROW = {
    "codigo": SAG_CODIGO,
    "line_description": (
        "Ítem N°5 Agitador magnético. Detalles en bases técnicas. Para Maule/ "
        "Curicó - Talca (1 unidad a cada laboratorio)"
    ),
    "producto": "Centrífugos de laboratorio",
    "nivel_1": (
        "Equipamiento para el acondicionamiento, distribución y filtrado de "
        "fluidos / Filtros y purificación industrial / Separadores"
    ),
    "unspsc_code": "40161701",
    "unidad": "Unidad",
    "cantidad": "2.0",
}
SAG_BALANCE_ROW = {
    "codigo": SAG_CODIGO,
    "line_description": "Ítem N°4 Balanza analítica. Detalles en bases técnicas. Para Maule /Talca.",
    "producto": "Balanzas electrónicas de carga superior",
    "nivel_1": (
        "Equipamiento para laboratorios / Instrumentos de medida y "
        "experimentación / Instrumentos de medición de peso"
    ),
    "unspsc_code": "41111501",
    "unidad": "Unidad",
    "cantidad": "1.0",
}
SAG_CENTRIFUGE_EPPENDORF_ROW = {
    "codigo": SAG_CODIGO,
    "line_description": (
        "Ítem N°12 Centrífuga para tubos eppendorf de 1,5 a 2ml + UPS. "
        "Detalles en bases técnicas. Para Los Lagos / Osorno."
    ),
    "producto": "Centrífugos de laboratorio",
    "nivel_1": (
        "Equipamiento para el acondicionamiento, distribución y filtrado de "
        "fluidos / Filtros y purificación industrial / Separadores"
    ),
    "unspsc_code": "40161701",
    "unidad": "Unidad",
    "cantidad": "1.0",
}
SAG_CENTRIFUGE_SOBREMESA_ROW = {
    "codigo": SAG_CODIGO,
    "line_description": (
        "Ítem N°15 Centrífuga de Sobremesa con rotor de ángulo fijo + UPS. "
        "Detalles en bases técnicas. Para Aysén / Coyahique."
    ),
    "producto": "Centrífugos de laboratorio",
    "nivel_1": (
        "Equipamiento para el acondicionamiento, distribución y filtrado de "
        "fluidos / Filtros y purificación industrial / Separadores"
    ),
    "unspsc_code": "40161701",
    "unidad": "Unidad",
    "cantidad": "1.0",
}

# Real cached ChileCompra detail-cache evidence (PDI, single-item tender,
# chilecompra_detail_cache/2981-205-LE26.json).
PDI_CODIGO = "2981-205-LE26"
PDI_BALANZAS_ROW = {
    "codigo": PDI_CODIGO,
    "line_description": (
        "10 BALANZAS DE PRECISION Y ACCESORIOS, DETALLADO EN ESPECIFICACIONES "
        "TECNICAS DE LA RESOLEX N°594 DE FECHA 4.AGO.026"
    ),
    "producto": "Básculas analíticas",
    "nivel_1": (
        "Equipamiento para laboratorios / Instrumentos de medida y "
        "experimentación / Instrumentos de medición de peso"
    ),
    "unspsc_code": "41111517",
    "unidad": "Unidad",
    "cantidad": "10.0",
}


def test_sag_unrelated_concrete_lines_are_not_categorized_as_centrifuge_by_taxonomy() -> None:
    """A concrete, unrelated line_description must not be swept into a category
    merely because the buyer's producto/taxonomy field says so.
    """
    rows = [SAG_SPECTROPHOTOMETER_ROW, SAG_MAGNETIC_STIRRER_ROW]
    aggregated = aggregate_chilecompra_item_metadata_by_category(rows)

    assert (SAG_CODIGO, "centrifuge") not in aggregated
    assert (SAG_CODIGO, "balance") not in aggregated


def test_sag_real_centrifuge_lines_remain_in_centrifuge_category() -> None:
    """The legitimate centrifuge purchase lines must still be recognized."""
    rows = [SAG_CENTRIFUGE_EPPENDORF_ROW, SAG_CENTRIFUGE_SOBREMESA_ROW]
    aggregated = aggregate_chilecompra_item_metadata_by_category(rows)

    entry = aggregated[(SAG_CODIGO, "centrifuge")]
    assert entry["cantidad"] == "2"
    assert "Centrífuga para tubos eppendorf" in entry["line_description"]
    assert "Centrífuga de Sobremesa" in entry["line_description"]


def test_sag_quantity_and_description_include_only_concrete_matching_lines() -> None:
    """Mixing real centrifuge lines with mislabeled unrelated lines in the same
    tender must not contaminate the centrifuge category's quantity or
    description — the exact Antofagasta/SAG contamination pattern.
    """
    rows = [
        SAG_SPECTROPHOTOMETER_ROW,
        SAG_MAGNETIC_STIRRER_ROW,
        SAG_BALANCE_ROW,
        SAG_CENTRIFUGE_EPPENDORF_ROW,
        SAG_CENTRIFUGE_SOBREMESA_ROW,
    ]
    aggregated = aggregate_chilecompra_item_metadata_by_category(rows)

    centrifuge = aggregated[(SAG_CODIGO, "centrifuge")]
    assert centrifuge["cantidad"] == "2"
    assert "Espectrofotómetro" not in centrifuge["line_description"]
    assert "Agitador magnético" not in centrifuge["line_description"]

    balance = aggregated[(SAG_CODIGO, "balance")]
    assert balance["cantidad"] == "1"
    assert "Balanza analítica" in balance["line_description"]


def test_sag_legitimate_purchase_remains_attached_to_queue_row() -> None:
    """End-to-end: attach_item_metadata_to_queue_rows must still surface the real
    centrifuge purchase evidence on the queue row, uncontaminated by the
    mislabeled unrelated lines sharing the same tender.
    """
    rows = [
        SAG_SPECTROPHOTOMETER_ROW,
        SAG_MAGNETIC_STIRRER_ROW,
        SAG_CENTRIFUGE_EPPENDORF_ROW,
        SAG_CENTRIFUGE_SOBREMESA_ROW,
    ]
    queue_rows = [{"codigo_licitacion": SAG_CODIGO, "equipment_category": "centrifuge"}]

    attached = attach_item_metadata_to_queue_rows(queue_rows, rows)

    assert len(attached) == 1
    row = attached[0]
    assert row["cantidad"] == "2"
    assert "Centrífuga para tubos eppendorf" in row["line_description"]
    assert "Centrífuga de Sobremesa" in row["line_description"]
    assert "Espectrofotómetro" not in row["line_description"]
    assert "Agitador magnético" not in row["line_description"]


def test_generic_line_description_falls_back_to_item_taxonomy() -> None:
    """A genuinely empty or generic line_description may still fall back to
    producto/taxonomy — the guard only rejects taxonomy when it conflicts with
    a concrete, informative line_description.
    """
    generic_row = {
        "codigo": "9999-1-LP26",
        "line_description": "Ítem N° 1: Equipos",
        "producto": "Centrífugos de laboratorio",
        "nivel_1": "Equipamiento para laboratorios",
        "unspsc_code": "40161701",
        "unidad": "Unidad",
        "cantidad": "3.0",
    }
    empty_row = {
        "codigo": "9999-1-LP26",
        "line_description": "",
        "producto": "Centrífugos de laboratorio",
        "nivel_1": "",
        "unspsc_code": "40161701",
        "unidad": "Unidad",
        "cantidad": "3.0",
    }

    for row in (generic_row, empty_row):
        aggregated = aggregate_chilecompra_item_metadata_by_category([row])
        assert ("9999-1-LP26", "centrifuge") in aggregated


def test_pdi_plural_balanzas_basculas_detected() -> None:
    """PDI JENACO tender: plural 'balanzas' in a concrete line_description, with
    producto naming the plural synonym 'básculas', must be detected as balance
    category evidence with its full quantity.
    """
    aggregated = aggregate_chilecompra_item_metadata_by_category([PDI_BALANZAS_ROW])

    entry = aggregated[(PDI_CODIGO, "balance")]
    assert entry["cantidad"] == "10"
    assert "BALANZAS" in entry["line_description"]

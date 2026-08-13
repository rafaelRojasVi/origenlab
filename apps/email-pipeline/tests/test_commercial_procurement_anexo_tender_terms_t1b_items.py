"""T1-B budgeted procurement-item roster extraction tests."""

from dataclasses import replace

from origenlab_email_pipeline.chilecompra_anexo_evidence.models import (
    AttachmentExtraction,
    AttachmentRecord,
    EvidenceChunk,
    TenderAttachmentBundle,
    sha256_text,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms import (
    FACT_STATE_CONFLICTING,
    FACT_STATE_EXPLICIT,
    FACT_STATE_UNKNOWN,
    extract_tender_terms,
)


def _bundle(*texts: str) -> TenderAttachmentBundle:
    chunks = tuple(
        EvidenceChunk(
            chunk_id=f"chunk-{index:02d}",
            attachment_id="att-1",
            tender_id="1057890-1-LE26",
            source_format="pdf",
            locator_type="pdf_page",
            locator={
                "page": index + 4,
                "page_count": 102,
                "part": 1,
            },
            text=text,
            text_sha256=sha256_text(text),
            char_count=len(text),
            ordinal=index,
        )
        for index, text in enumerate(texts, start=1)
    )

    record = AttachmentRecord(
        attachment_id="att-1",
        tender_id="1057890-1-LE26",
        listing_ordinal=1,
        row_ordinal=1,
        portal_filename="2-Res._1756_Ap._Bases.pdf",
        safe_filename="2-Res._1756_Ap._Bases.pdf",
        tipo="Bases",
        descripcion="Bases administrativas y técnicas",
        fecha_adjunto="2026-07-24",
        content_type="application/pdf",
        detected_format="pdf",
        extension="pdf",
        byte_count=10_000,
        sha256="a" * 64,
        download_status="downloaded",
        outcome="extraction_success",
        role_tag="administrative_basis",
        evidence_attachment_id="att-1",
        extraction=AttachmentExtraction(
            outcome="extraction_success",
            detected_format="pdf",
            chunk_count=len(chunks),
            char_count=sum(len(text) for text in texts),
            page_count=102,
            extractor="test",
        ),
    )

    return TenderAttachmentBundle(
        tender_id="1057890-1-LE26",
        source_kind="test",
        listing_page_count=1,
        attachments_discovered=1,
        attachments_downloaded=1,
        attachments=(record,),
        chunks=chunks,
        bytes_downloaded=10_000,
        incomplete_reason_codes=(),
        semantic_digest="gold-source-digest",
        outcome_counts={"extraction_success": 1},
    )


_GOLD_ROSTER = """\
Presupuesto Disponible $ 28.419.400.- CLP impuestos incluidos
Desglose:
ITEMS 1: Cabina de Seguridad Biológica
Clase II–A2 (Cantidad:1) $7.400.000.-
ITEMS 2: Centrifuga universal (24-36
capachos) (Cantidad:1) $10.455.000.-
ITEMS 3: Equipo de refrigeración combinado
(refrigerador y freezer) (Cantidad:1)
$1.064.400.-
ITEM 4: Freezer clínico (Cantidad:1)
$9.500.000.-
Artículo 6°: Plazos.
"""


def _facts(item):
    return {
        fact.field_name: fact
        for fact in item.facts
    }


def test_gold_article_5_extracts_four_budgeted_items() -> None:
    result = extract_tender_terms(
        _bundle(_GOLD_ROSTER)
    )

    assert [item.item_number for item in result.items] == [
        "1",
        "2",
        "3",
        "4",
    ]

    expected = {
        "1": (
            "Cabina de Seguridad Biológica Clase II–A2",
            1,
            7_400_000,
        ),
        "2": (
            "Centrifuga universal (24-36 capachos)",
            1,
            10_455_000,
        ),
        "3": (
            "Equipo de refrigeración combinado "
            "(refrigerador y freezer)",
            1,
            1_064_400,
        ),
        "4": (
            "Freezer clínico",
            1,
            9_500_000,
        ),
    }

    for item in result.items:
        label, quantity, budget = expected[item.item_number]
        facts = _facts(item)

        assert item.item_label == label

        assert facts["item_label"].state == FACT_STATE_EXPLICIT
        assert facts["item_label"].value == label

        assert facts["item_quantity"].state == FACT_STATE_EXPLICIT
        assert facts["item_quantity"].value == quantity
        assert facts["item_quantity"].unit == "count"

        assert facts["item_budget"].state == FACT_STATE_EXPLICIT
        assert facts["item_budget"].value == budget
        assert facts["item_budget"].currency == "CLP"
        assert facts["item_budget"].tax_basis == "taxes_included"

        assert facts["item_budget"].evidence


def test_gold_centrifuge_item_is_addressable() -> None:
    result = extract_tender_terms(
        _bundle(_GOLD_ROSTER)
    )

    centrifuge = next(
        item
        for item in result.items
        if item.item_number == "2"
    )
    facts = _facts(centrifuge)

    assert centrifuge.item_label == (
        "Centrifuga universal (24-36 capachos)"
    )
    assert facts["item_quantity"].value == 1
    assert facts["item_budget"].value == 10_455_000
    assert "Centrifuga universal" in (
        facts["item_budget"]
        .evidence[0]
        .evidence_excerpt
    )


def test_non_budgeted_contract_item_list_does_not_create_roster() -> None:
    text = """\
Provisión de los siguientes equipos:
1. Cabina de Seguridad Biológica Clase II–A2. (CANTIDAD: 1)
2. Centrifuga universal (24-36 capachos). (CANTIDAD: 1)
3. Equipo de refrigeración combinado. (CANTIDAD: 1)
4. Freezer clínico. (CANTIDAD: 1)
"""

    result = extract_tender_terms(
        _bundle(text)
    )

    assert result.items == ()


def test_repeated_identical_item_row_coalesces_evidence() -> None:
    row = """\
Presupuesto Disponible $ 10.455.000.- CLP impuestos incluidos
Desglose:
ITEMS 2: Centrifuga universal (24-36 capachos)
(Cantidad:1) $10.455.000.-
"""

    result = extract_tender_terms(
        _bundle(row, row)
    )

    assert len(result.items) == 1

    facts = _facts(result.items[0])

    assert facts["item_budget"].value == 10_455_000

    # Each repeated budget observation preserves both:
    # - the concrete item row proving the amount; and
    # - the authority header proving CLP + taxes-included metadata.
    budget_evidence = facts["item_budget"].evidence

    assert len(budget_evidence) == 4

    excerpts = [
        evidence.evidence_excerpt
        for evidence in budget_evidence
    ]

    assert sum(
        "Presupuesto Disponible" in excerpt
        for excerpt in excerpts
    ) == 2

    assert sum(
        "Centrifuga universal" in excerpt
        for excerpt in excerpts
    ) == 2

    # Label and quantity remain row-backed only.
    assert len(facts["item_label"].evidence) == 2
    assert len(facts["item_quantity"].evidence) == 2


def test_conflicting_item_budget_is_preserved() -> None:
    one = """\
ITEMS 2: Centrifuga universal (24-36 capachos)
(Cantidad:1) $10.455.000.-
"""
    two = """\
ITEMS 2: Centrifuga universal (24-36 capachos)
(Cantidad:1) $11.000.000.-
"""

    result = extract_tender_terms(
        _bundle(one, two)
    )

    facts = _facts(result.items[0])
    budget = facts["item_budget"]

    assert budget.state == FACT_STATE_CONFLICTING
    assert budget.value is None
    assert sorted(
        candidate.value
        for candidate in budget.candidates
    ) == [
        10_455_000,
        11_000_000,
    ]


def test_conflicting_item_label_does_not_select_one_label() -> None:
    one = """\
ITEMS 2: Centrifuga universal
(Cantidad:1) $10.455.000.-
"""
    two = """\
ITEMS 2: Centrifuga refrigerada
(Cantidad:1) $10.455.000.-
"""

    result = extract_tender_terms(
        _bundle(one, two)
    )

    item = result.items[0]
    label = _facts(item)["item_label"]

    assert item.item_label is None
    assert label.state == FACT_STATE_CONFLICTING
    assert sorted(
        candidate.value
        for candidate in label.candidates
    ) == [
        "Centrifuga refrigerada",
        "Centrifuga universal",
    ]


def test_budget_without_explicit_clp_tax_context_does_not_invent_metadata() -> None:
    text = """\
ITEMS 2: Centrifuga universal (24-36 capachos)
(Cantidad:1) $10.455.000.-
"""

    result = extract_tender_terms(
        _bundle(text)
    )

    budget = _facts(result.items[0])["item_budget"]

    assert budget.value == 10_455_000
    assert budget.currency is None
    assert budget.tax_basis is None



def test_authority_equipment_roster_continues_across_pdf_pages() -> None:
    first_page = """\
3.1 Equipos solicitados
El detalle con los equipos, cantidades, lugar de entrega y presupuesto
estimado para cada uno, es el que se detalla a continuación:
Ítem
Producto
Cantidad
Región / Ciudad
Monto total
estimado (IVA y costo de despacho incluido)
1
Balanza analítica
1
Maule / Talca
$2.023.000
2
Agitador magnético
2
Maule / Curicó - Talca
$1.904.000
"""

    second_page = """\
3
Centrífuga para tubos eppendorf de 1,5 a 2ml + UPS
1
Los Lagos / Osorno
$5.360.000
3.2 Características técnicas de los equipos solicitados
ITEM 1: Balanza analítica
Capacidad 200 - 300 g
"""

    result = extract_tender_terms(
        _bundle(
            first_page,
            second_page,
        )
    )

    assert [
        item.item_number
        for item in result.items
    ] == [
        "1",
        "2",
        "3",
    ]

    expected = {
        "1": (
            "Balanza analítica",
            1,
            2_023_000,
        ),
        "2": (
            "Agitador magnético",
            2,
            1_904_000,
        ),
        "3": (
            "Centrífuga para tubos eppendorf de 1,5 a 2ml + UPS",
            1,
            5_360_000,
        ),
    }

    for item in result.items:
        label, quantity, budget = (
            expected[item.item_number]
        )
        facts = _facts(item)

        assert item.item_label == label
        assert (
            facts["item_quantity"].value
            == quantity
        )
        assert (
            facts["item_budget"].value
            == budget
        )

        # "$" alone does not establish CLP.
        assert (
            facts["item_budget"].currency
            is None
        )

        # The authority table header does
        # explicitly establish IVA-inclusive
        # estimated amounts.
        assert (
            facts["item_budget"].tax_basis
            == "taxes_included"
        )

        # Budget provenance includes the
        # concrete item row plus the authority
        # header that establishes tax basis.
        assert len(
            facts["item_budget"].evidence
        ) == 2

        excerpts = {
            evidence.evidence_excerpt
            for evidence in (
                facts["item_budget"].evidence
            )
        }

        assert any(
            "Monto total" in excerpt
            for excerpt in excerpts
        )
        assert any(
            label in excerpt
            for excerpt in excerpts
        )



def test_prefilled_acquisition_form_creates_partial_item_without_budget() -> None:
    text = """\
[r1] | ANEXO N°4 LICITACIÓN PÚBLICA “ADQUISICION DE AUTOCLAVE LABORATORIO DE TNS EN PODOLOGÍA CLÍNICA DEL CFT DE LA REGIÓN DE LA ARAUCANÍA” OFERTA ECONÓMICA, GARANTÍA TÉCNICA Y PLAZO DE ENTREGA
[r22] | Descripción | Cantidad | Precio Unitario Neto (valor por cada producto) | Precio Total Neto (considerando todas las cantidades) | Precio Total Con IVA (considerando todas las cantidades, incluyendo IVA) | Plazo de entrega. Indicar en dias hábiles.
[r23] | Autoclave 50 litros + capacitación presencial (ambas sedes) | 2 | | 0 | 0 |
"""

    result = extract_tender_terms(
        _bundle(text)
    )

    assert len(result.items) == 1

    item = result.items[0]
    facts = _facts(item)

    assert item.item_number is None
    assert item.item_label == (
        "Autoclave 50 litros + capacitación presencial "
        "(ambas sedes)"
    )

    assert facts["item_label"].state == FACT_STATE_EXPLICIT
    assert facts["item_quantity"].state == FACT_STATE_EXPLICIT
    assert facts["item_quantity"].value == 2

    budget = facts["item_budget"]

    assert budget.state == FACT_STATE_UNKNOWN
    assert budget.value is None
    assert budget.currency is None
    assert budget.tax_basis is None
    assert budget.reason_codes == (
        "bidder_price_fields_not_authority_budget",
    )

    excerpts = [
        evidence.evidence_excerpt
        for evidence in budget.evidence
    ]

    assert any(
        "Precio Unitario Neto" in excerpt
        for excerpt in excerpts
    )

    assert any(
        "Autoclave 50 litros" in excerpt
        for excerpt in excerpts
    )


def test_economic_form_without_acquisition_subject_does_not_create_item() -> None:
    text = """\
[r1] ANEXO OFERTA ECONÓMICA
[r2] Descripción | Cantidad | Precio Unitario Neto | Precio Total Neto
[r3] Prueba de laboratorio | 2 | | 0
"""

    result = extract_tender_terms(
        _bundle(text)
    )

    assert result.items == ()


def test_numbered_item_identity_remains_stable_after_partial_item_support() -> None:
    result = extract_tender_terms(
        _bundle(_GOLD_ROSTER)
    )

    item = next(
        row
        for row in result.items
        if row.item_number == "2"
    )

    facts = _facts(item)

    assert (
        item.item_id
        == "aff55fa62ab1adb81c2f7a1209968f27"
    )
    assert (
        facts["item_label"].fact_id
        == "2cb57162b56c5be9392fd708d3bebd43"
    )
    assert (
        facts["item_quantity"].fact_id
        == "b71fb7e318ddab0a80cc7f182febe235"
    )
    assert (
        facts["item_budget"].fact_id
        == "a603508b1b61f3c427f9d97fc3935f01"
    )



def test_repeated_prefilled_acquisition_item_coalesces_and_is_deterministic() -> None:
    text = """\
[r1] | ANEXO N°4 LICITACIÓN PÚBLICA “ADQUISICION DE AUTOCLAVE LABORATORIO” OFERTA ECONÓMICA, GARANTÍA TÉCNICA Y PLAZO DE ENTREGA
[r22] | Descripción | Cantidad | Precio Unitario Neto | Precio Total Neto
[r23] | Autoclave 50 litros + capacitación presencial | 2 | | 0
"""

    bundle = _bundle(
        text,
        text,
    )

    forward = extract_tender_terms(bundle)

    reverse = extract_tender_terms(
        replace(
            bundle,
            chunks=tuple(
                reversed(bundle.chunks)
            ),
        )
    )

    assert len(forward.items) == 1

    item = forward.items[0]
    facts = _facts(item)

    assert item.item_number is None
    assert facts["item_quantity"].value == 2

    # Per source chunk:
    # label/quantity = subject + header + row
    # budget unknown = header + row
    assert len(
        facts["item_label"].evidence
    ) == 6

    assert len(
        facts["item_quantity"].evidence
    ) == 6

    assert len(
        facts["item_budget"].evidence
    ) == 4

    assert (
        facts["item_budget"].state
        == FACT_STATE_UNKNOWN
    )

    assert (
        forward.to_dict()
        == reverse.to_dict()
    )

    assert (
        forward.semantic_digest
        == reverse.semantic_digest
    )


def test_prefilled_bidder_prices_never_become_authority_item_budget() -> None:
    price_pairs = (
        ("", "0"),
        ("0", "0"),
        ("0.0", "0.0"),
        ("$0", "$0"),
        ("1250000", "2500000"),
    )

    for unit_price, total_net in price_pairs:
        text = f"""\
[r1] | LICITACIÓN “ADQUISICION DE AUTOCLAVE PARA LABORATORIO” OFERTA ECONÓMICA
[r22] | Descripción | Cantidad | Precio Unitario Neto | Precio Total Neto
[r23] | Autoclave 50 litros | 2 | {unit_price} | {total_net}
"""

        result = extract_tender_terms(
            _bundle(text)
        )

        assert len(result.items) == 1

        budget = _facts(
            result.items[0]
        )["item_budget"]

        assert (
            budget.state
            == FACT_STATE_UNKNOWN
        )

        assert budget.value is None

        assert budget.reason_codes == (
            "bidder_price_fields_not_authority_budget",
        )


def test_prefilled_form_row_must_match_acquisition_subject() -> None:
    text = """\
[r1] | LICITACIÓN “ADQUISICION DE CENTRIFUGA PARA LABORATORIO” OFERTA ECONÓMICA
[r22] | Descripción | Cantidad | Precio Unitario Neto | Precio Total Neto
[r23] | Autoclave 50 litros | 2 | | 0
"""

    result = extract_tender_terms(
        _bundle(text)
    )

    assert result.items == ()


def test_item_output_is_deterministic_across_chunk_input_order() -> None:
    first = """\
ITEMS 1: Cabina de Seguridad Biológica
(Cantidad:1) $7.400.000.-
"""
    second = """\
ITEMS 2: Centrifuga universal
(Cantidad:1) $10.455.000.-
"""

    bundle = _bundle(first, second)

    forward = extract_tender_terms(bundle)
    reverse = extract_tender_terms(
        replace(
            bundle,
            chunks=tuple(reversed(bundle.chunks)),
        )
    )

    assert forward.to_dict() == reverse.to_dict()
    assert forward.semantic_digest == reverse.semantic_digest

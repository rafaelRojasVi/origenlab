"""Extract explicit procurement-item terms from verified tender evidence.

B3.1 recognizes two intentionally narrow item-roster morphologies:

1. the gold inline budget roster, where item number, label, quantity and
   monetary amount appear together; and
2. an authority equipment-budget table introduced by an explicit
   ``Equipos solicitados`` / ``presupuesto estimado para cada uno`` header.

The authority table may continue across adjacent evidence chunks belonging to
the same attachment, for example across consecutive PDF pages.

Bidder economic forms, reagent/service tables and technical-specification item
headings do not create authority item budgets here.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)

from .constants import (
    FACT_STATE_CONFLICTING,
    FACT_STATE_EXPLICIT,
    FACT_STATE_UNKNOWN,
)
from .models import (
    FactCandidate,
    TenderItemTerms,
    TenderTermFact,
    TermEvidence,
)
from .source import (
    ResolvedEvidenceChunk,
    term_evidence_from_span,
)

_FLAGS = re.IGNORECASE | re.DOTALL


# ---------------------------------------------------------------------------
# Gold inline roster
# ---------------------------------------------------------------------------

_INLINE_ITEM_ROW_RE = re.compile(
    r"\b(?:ITEMS?|[ÍI]TEMS?)\s*"
    r"(?P<number>\d+)\s*:\s*"
    r"(?P<label>"
    r"(?:(?!\b(?:ITEMS?|[ÍI]TEMS?)\s*\d+\s*:).){1,500}?"
    r")"
    r"\(\s*Cantidad\s*:\s*(?P<quantity>\d+)\s*\)"
    r"\s*\$\s*(?P<budget>\d[\d.\s]*)"
    r"\s*(?:\.-)?",
    _FLAGS,
)

_INLINE_TAX_INCLUSIVE_CLP_HEADER_RE = re.compile(
    r"\bPresupuesto\s+Disponible\b"
    r".{0,120}?"
    r"\bCLP\b"
    r".{0,80}?"
    r"\bimpuestos\s+incluidos\b"
    r".{0,80}?"
    r"\bDesglose\s*:",
    _FLAGS,
)

_MAX_INLINE_HEADER_DISTANCE = 2_000


# ---------------------------------------------------------------------------
# Real authority equipment roster
# ---------------------------------------------------------------------------

_AUTHORITY_EQUIPMENT_HEADER_RE = re.compile(
    r"\b3\.1\s+Equipos\s+solicitados\b"
    r".{0,700}?"
    r"\bpresupuesto\s+estimado\s+para\s+cada\s+uno\b"
    r".{0,500}?"
    r"\b[ÍI]tem\b"
    r"\s+Producto"
    r"\s+Cantidad"
    r"\s+Regi[oó]n\s*/\s*Ciudad"
    r"\s+Monto\s+total"
    r"\s+estimado"
    r"\s*\(\s*IVA\s+y\s+costo\s+de\s+despacho\s+incluido\s*\)",
    _FLAGS,
)

_AUTHORITY_EQUIPMENT_STOP_RE = re.compile(
    r"\b3\.2\s+Caracter[ií]sticas\s+t[eé]cnicas\b",
    re.IGNORECASE,
)

_AUTHORITY_EQUIPMENT_ROW_RE = re.compile(
    r"^[ \t]*(?P<number>\d{1,3})[ \t]*\r?\n"
    r"(?P<label>.*?)"
    r"^[ \t]*(?P<quantity>\d{1,3})[ \t]*\r?\n"
    r"(?P<location>.*?)"
    r"^[ \t]*\$\s*"
    r"(?P<budget>\d{1,3}(?:\.\d{3})*)"
    r"[ \t]*(?:\r?\n|$)",
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)


_STRUCTURED_ROW_RE = re.compile(
    r"^\[r(?P<number>\d+)\]\s*(?P<body>.*)$",
    re.MULTILINE,
)

_PREFILLED_ACQUISITION_SUBJECT_RE = re.compile(
    r"\bADQUISICI[ÓO]N\s+DE\s+"
    r"(?P<subject>.{3,250}?)"
    r"\s*(?:[”\"])?\s+"
    r"OFERTA\s+ECON[ÓO]MICA\b",
    re.IGNORECASE | re.DOTALL,
)

_ITEM_IDENTITY_STOPWORDS = frozenset(
    {
        "adquisicion",
        "compra",
        "laboratorio",
        "oferta",
        "economica",
        "garantia",
        "tecnica",
        "plazo",
        "entrega",
        "para",
        "del",
        "las",
        "los",
        "una",
        "uno",
        "region",
        "equipo",
        "equipos",
        "producto",
        "productos",
    }
)


def _identity_normalize(value: str) -> str:
    normalized = unicodedata.normalize(
        "NFKD",
        value.casefold(),
    )

    normalized = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )

    return re.sub(
        r"\s+",
        " ",
        normalized,
    ).strip()


def _identity_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(
            r"[a-z0-9]+",
            _identity_normalize(value),
        )
        if (
            len(token) >= 4
            and token
            not in _ITEM_IDENTITY_STOPWORDS
        )
    }


def _prefilled_item_key(label: str) -> str:
    normalized = _identity_normalize(label)

    digest = hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()[:24]

    return f"label:{digest}"


def _row_cells(body: str) -> list[str]:
    return [
        cell.strip()
        for cell in body.split("|")
    ]


def _find_column(
    cells: list[str],
    predicate: Any,
) -> int | None:
    for index, cell in enumerate(cells):
        if predicate(
            _identity_normalize(cell)
        ):
            return index

    return None


@dataclass(frozen=True)
class _ItemObservation:
    item_key: str
    item_number: str | None
    item_label: str
    quantity: int
    budget: int | None
    row_evidence: TermEvidence
    item_context_evidence: tuple[TermEvidence, ...] = ()
    budget_context_evidence: tuple[TermEvidence, ...] = ()
    currency: str | None = None
    tax_basis: str | None = None


def _normalize_label(raw: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        raw,
    ).strip(" \t\r\n:.-")


def _parse_integer_amount(raw: str) -> int:
    digits = re.sub(r"\D", "", raw)

    if not digits:
        raise ValueError(
            f"invalid item budget amount: {raw!r}"
        )

    return int(digits)


def _item_id(
    tender_id: str,
    item_number: str,
) -> str:
    seed = (
        f"anexo_t1_item\0{tender_id}\0{item_number}"
    ).encode()

    return hashlib.sha256(seed).hexdigest()[:32]


def _fact_id(
    tender_id: str,
    item_number: str,
    field_name: str,
) -> str:
    seed = (
        f"anexo_t1_item_fact\0{tender_id}\0"
        f"{item_number}\0{field_name}"
    ).encode()

    return hashlib.sha256(seed).hexdigest()[:32]


def _candidate_id(
    tender_id: str,
    item_number: str,
    field_name: str,
    value: Any,
) -> str:
    value_digest = canonical_json_digest(
        {"value": value}
    )

    seed = (
        f"anexo_t1_item_candidate\0{tender_id}\0"
        f"{item_number}\0{field_name}\0{value_digest}"
    ).encode()

    return hashlib.sha256(seed).hexdigest()[:32]


def _evidence_tuple(
    rows: list[TermEvidence],
) -> tuple[TermEvidence, ...]:
    by_id = {
        row.evidence_id: row
        for row in rows
    }

    return tuple(
        by_id[key]
        for key in sorted(by_id)
    )


def _inline_observations(
    source: ResolvedEvidenceChunk,
) -> tuple[_ItemObservation, ...]:
    text = source.chunk.text

    header_match = (
        _INLINE_TAX_INCLUSIVE_CLP_HEADER_RE.search(
            text
        )
    )

    header_evidence = None

    if header_match is not None:
        header_evidence = term_evidence_from_span(
            source,
            char_start=header_match.start(),
            char_end=header_match.end(),
        )

    rows: list[_ItemObservation] = []

    for match in _INLINE_ITEM_ROW_RE.finditer(text):
        label = _normalize_label(
            match.group("label")
        )

        if not label:
            continue

        row_evidence = term_evidence_from_span(
            source,
            char_start=match.start(),
            char_end=match.end(),
        )

        has_header_context = (
            header_match is not None
            and header_evidence is not None
            and header_match.end() <= match.start()
            and (
                match.start() - header_match.end()
                <= _MAX_INLINE_HEADER_DISTANCE
            )
        )

        context_evidence = (
            (header_evidence,)
            if has_header_context
            else ()
        )

        rows.append(
            _ItemObservation(
                item_key=match.group("number"),
                item_number=match.group("number"),
                item_label=label,
                quantity=int(
                    match.group("quantity")
                ),
                budget=_parse_integer_amount(
                    match.group("budget")
                ),
                row_evidence=row_evidence,
                budget_context_evidence=(
                    context_evidence
                ),
                currency=(
                    "CLP"
                    if has_header_context
                    else None
                ),
                tax_basis=(
                    "taxes_included"
                    if has_header_context
                    else None
                ),
            )
        )

    return tuple(rows)


def _source_group_key(
    source: ResolvedEvidenceChunk,
) -> tuple[str, str]:
    return (
        source.chunk.attachment_id,
        source.chunk.member_path or "",
    )


def _source_order_key(
    source: ResolvedEvidenceChunk,
) -> tuple[int, str]:
    return (
        source.chunk.ordinal,
        source.chunk.chunk_id,
    )


def _authority_roster_observations(
    sources: tuple[ResolvedEvidenceChunk, ...],
) -> tuple[_ItemObservation, ...]:
    """Recognize authority budget rows across adjacent chunks.

    Context never crosses an attachment or archive-member boundary.
    """

    grouped: dict[
        tuple[str, str],
        list[ResolvedEvidenceChunk],
    ] = {}

    for source in sources:
        grouped.setdefault(
            _source_group_key(source),
            [],
        ).append(source)

    observations: list[_ItemObservation] = []

    for group_key in sorted(grouped):
        group = sorted(
            grouped[group_key],
            key=_source_order_key,
        )

        active = False
        header_evidence: TermEvidence | None = None

        for source in group:
            text = source.chunk.text
            segment_start = 0

            if not active:
                header_match = (
                    _AUTHORITY_EQUIPMENT_HEADER_RE.search(
                        text
                    )
                )

                if header_match is None:
                    continue

                active = True
                segment_start = header_match.end()

                header_evidence = (
                    term_evidence_from_span(
                        source,
                        char_start=header_match.start(),
                        char_end=header_match.end(),
                    )
                )

            segment = text[segment_start:]

            stop_match = (
                _AUTHORITY_EQUIPMENT_STOP_RE.search(
                    segment
                )
            )

            if stop_match is not None:
                segment = segment[
                    : stop_match.start()
                ]

            for match in (
                _AUTHORITY_EQUIPMENT_ROW_RE.finditer(
                    segment
                )
            ):
                label = _normalize_label(
                    match.group("label")
                )

                if not label:
                    continue

                absolute_start = (
                    segment_start + match.start()
                )
                absolute_end = (
                    segment_start + match.end()
                )

                row_evidence = (
                    term_evidence_from_span(
                        source,
                        char_start=absolute_start,
                        char_end=absolute_end,
                    )
                )

                observations.append(
                    _ItemObservation(
                        item_key=match.group(
                            "number"
                        ),
                        item_number=match.group(
                            "number"
                        ),
                        item_label=label,
                        quantity=int(
                            match.group("quantity")
                        ),
                        budget=_parse_integer_amount(
                            match.group("budget")
                        ),
                        row_evidence=row_evidence,
                        budget_context_evidence=(
                            (header_evidence,)
                            if header_evidence
                            is not None
                            else ()
                        ),
                        # "$" alone is not enough to
                        # assert CLP.
                        currency=None,
                        # The authority table header
                        # explicitly says IVA included.
                        tax_basis="taxes_included",
                    )
                )

            if stop_match is not None:
                active = False
                header_evidence = None

    return tuple(observations)


def _prefilled_acquisition_observations(
    source: ResolvedEvidenceChunk,
) -> tuple[_ItemObservation, ...]:
    """Recognize authority-prefilled item identity in bidder forms.

    Price cells in these forms are intentionally not treated as authority
    budgets. A prefilled description and quantity can still establish the
    procurement item itself.
    """

    text = source.chunk.text

    subject_match = (
        _PREFILLED_ACQUISITION_SUBJECT_RE.search(
            text
        )
    )

    if subject_match is None:
        return ()

    subject_tokens = _identity_tokens(
        subject_match.group("subject")
    )

    if not subject_tokens:
        return ()

    subject_evidence = term_evidence_from_span(
        source,
        char_start=subject_match.start(),
        char_end=subject_match.end(),
    )

    parsed_rows = [
        (
            int(match.group("number")),
            match,
            _row_cells(
                match.group("body")
            ),
        )
        for match in (
            _STRUCTURED_ROW_RE.finditer(text)
        )
    ]

    if not parsed_rows:
        return ()

    header = None

    for (
        row_number,
        match,
        cells,
    ) in parsed_rows:
        description_index = _find_column(
            cells,
            lambda value: (
                value == "descripcion"
                or value.startswith(
                    "descripcion "
                )
            ),
        )

        quantity_index = _find_column(
            cells,
            lambda value: (
                value == "cantidad"
                or value.startswith(
                    "cantidad "
                )
            ),
        )

        unit_price_index = _find_column(
            cells,
            lambda value: (
                "precio unitario neto"
                in value
            ),
        )

        total_net_index = _find_column(
            cells,
            lambda value: (
                "precio total neto"
                in value
            ),
        )

        if (
            description_index is None
            or quantity_index is None
            or unit_price_index is None
            or total_net_index is None
        ):
            continue

        header = (
            row_number,
            match,
            description_index,
            quantity_index,
            unit_price_index,
            total_net_index,
        )
        break

    if header is None:
        return ()

    (
        header_row_number,
        header_match,
        description_index,
        quantity_index,
        unit_price_index,
        total_net_index,
    ) = header

    header_evidence = term_evidence_from_span(
        source,
        char_start=header_match.start(),
        char_end=header_match.end(),
    )

    observations: list[_ItemObservation] = []

    for (
        row_number,
        row_match,
        cells,
    ) in parsed_rows:
        if row_number <= header_row_number:
            continue

        max_required_index = max(
            description_index,
            quantity_index,
            unit_price_index,
            total_net_index,
        )

        if len(cells) <= max_required_index:
            continue

        label = _normalize_label(
            cells[description_index]
        )

        quantity_raw = cells[
            quantity_index
        ].strip()

        if not label:
            continue

        if not re.fullmatch(
            r"\d+",
            quantity_raw,
        ):
            continue

        quantity = int(quantity_raw)

        if quantity <= 0:
            continue

        label_tokens = _identity_tokens(label)

        if not (
            subject_tokens
            & label_tokens
        ):
            continue

        row_evidence = term_evidence_from_span(
            source,
            char_start=row_match.start(),
            char_end=row_match.end(),
        )

        observations.append(
            _ItemObservation(
                item_key=_prefilled_item_key(
                    label
                ),
                item_number=None,
                item_label=label,
                quantity=quantity,
                # Economic-form price fields are
                # bidder inputs/formulas, not an
                # authority budget.
                budget=None,
                row_evidence=row_evidence,
                item_context_evidence=(
                    subject_evidence,
                    header_evidence,
                ),
                budget_context_evidence=(
                    header_evidence,
                ),
                currency=None,
                tax_basis=None,
            )
        )

    return tuple(observations)


def _fact_from_values(
    *,
    tender_id: str,
    item_number: str,
    field_name: str,
    rows: list[
        tuple[
            Any,
            tuple[TermEvidence, ...],
        ]
    ],
    coverage_status: str,
    value_type: str,
    unit: str | None = None,
    currency: str | None = None,
    tax_basis: str | None = None,
) -> TenderTermFact:
    grouped: dict[
        str,
        tuple[Any, list[TermEvidence]],
    ] = {}

    for value, evidence_rows in rows:
        key = canonical_json_digest(
            {"value": value}
        )

        existing = grouped.get(key)

        if existing is None:
            grouped[key] = (
                value,
                list(evidence_rows),
            )
        else:
            existing[1].extend(
                evidence_rows
            )

    ordered = [
        grouped[key]
        for key in sorted(grouped)
    ]

    fact_id = _fact_id(
        tender_id,
        item_number,
        field_name,
    )

    if len(ordered) == 1:
        value, evidence_rows = ordered[0]

        return TenderTermFact(
            fact_id=fact_id,
            field_name=field_name,
            state=FACT_STATE_EXPLICIT,
            coverage_status=coverage_status,
            value=value,
            value_type=value_type,
            unit=unit,
            currency=currency,
            tax_basis=tax_basis,
            evidence=_evidence_tuple(
                evidence_rows
            ),
        )

    candidates = tuple(
        sorted(
            (
                FactCandidate(
                    candidate_id=_candidate_id(
                        tender_id,
                        item_number,
                        field_name,
                        value,
                    ),
                    value=value,
                    evidence=_evidence_tuple(
                        evidence_rows
                    ),
                )
                for (
                    value,
                    evidence_rows,
                ) in ordered
            ),
            key=lambda row: (
                row.candidate_id
            ),
        )
    )

    return TenderTermFact(
        fact_id=fact_id,
        field_name=field_name,
        state=FACT_STATE_CONFLICTING,
        coverage_status=coverage_status,
        value_type=value_type,
        unit=unit,
        currency=currency,
        tax_basis=tax_basis,
        candidates=candidates,
        reason_codes=(
            "multiple_distinct_explicit_item_values",
        ),
    )


def _item_sort_key(
    item_number: str,
) -> tuple[int, int | str]:
    if item_number.isdigit():
        return (0, int(item_number))

    return (1, item_number)


def _common_metadata(
    observations: list[_ItemObservation],
    field_name: str,
) -> str | None:
    values = {
        getattr(row, field_name)
        for row in observations
    }

    if len(values) != 1:
        return None

    return next(iter(values))


def extract_item_terms(
    *,
    tender_id: str,
    sources: tuple[ResolvedEvidenceChunk, ...],
    coverage_status: str,
) -> tuple[TenderItemTerms, ...]:
    """Return deterministic explicit/partial item-level procurement terms."""

    observations: list[_ItemObservation] = []

    for source in sources:
        observations.extend(
            _inline_observations(source)
        )
        observations.extend(
            _prefilled_acquisition_observations(
                source
            )
        )

    observations.extend(
        _authority_roster_observations(
            sources
        )
    )

    by_key: dict[
        str,
        list[_ItemObservation],
    ] = {}

    for observation in observations:
        by_key.setdefault(
            observation.item_key,
            [],
        ).append(observation)

    items: list[TenderItemTerms] = []

    for item_key in sorted(
        by_key,
        key=_item_sort_key,
    ):
        item_observations = by_key[
            item_key
        ]

        label_fact = _fact_from_values(
            tender_id=tender_id,
            item_number=item_key,
            field_name="item_label",
            rows=[
                (
                    row.item_label,
                    (
                        row.row_evidence,
                        *row.item_context_evidence,
                    ),
                )
                for row in item_observations
            ],
            coverage_status=coverage_status,
            value_type="string",
        )

        quantity_fact = _fact_from_values(
            tender_id=tender_id,
            item_number=item_key,
            field_name="item_quantity",
            rows=[
                (
                    row.quantity,
                    (
                        row.row_evidence,
                        *row.item_context_evidence,
                    ),
                )
                for row in item_observations
            ],
            coverage_status=coverage_status,
            value_type="integer",
            unit="count",
        )

        budget_observations = [
            row
            for row in item_observations
            if row.budget is not None
        ]

        if budget_observations:
            currency = _common_metadata(
                budget_observations,
                "currency",
            )

            tax_basis = _common_metadata(
                budget_observations,
                "tax_basis",
            )

            budget_fact = _fact_from_values(
                tender_id=tender_id,
                item_number=item_key,
                field_name="item_budget",
                rows=[
                    (
                        row.budget,
                        (
                            row.row_evidence,
                            *row.budget_context_evidence,
                        ),
                    )
                    for row in (
                        budget_observations
                    )
                ],
                coverage_status=coverage_status,
                value_type="integer",
                currency=currency,
                tax_basis=tax_basis,
            )
        else:
            unknown_evidence: list[
                TermEvidence
            ] = []

            for row in item_observations:
                unknown_evidence.extend(
                    (
                        row.row_evidence,
                        *row.budget_context_evidence,
                    )
                )

            budget_fact = TenderTermFact(
                fact_id=_fact_id(
                    tender_id,
                    item_key,
                    "item_budget",
                ),
                field_name="item_budget",
                state=FACT_STATE_UNKNOWN,
                coverage_status=coverage_status,
                value_type="integer",
                evidence=_evidence_tuple(
                    unknown_evidence
                ),
                reason_codes=(
                    "bidder_price_fields_not_authority_budget",
                ),
            )

        item_label = (
            label_fact.value
            if (
                label_fact.state
                == FACT_STATE_EXPLICIT
            )
            else None
        )

        item_numbers = {
            row.item_number
            for row in item_observations
        }

        item_number = (
            next(iter(item_numbers))
            if len(item_numbers) == 1
            else None
        )

        items.append(
            TenderItemTerms(
                # Existing numbered items retain
                # exactly their prior identity seed:
                # item_key == item_number.
                item_id=_item_id(
                    tender_id,
                    item_key,
                ),
                item_number=item_number,
                item_label=item_label,
                facts=tuple(
                    sorted(
                        (
                            label_fact,
                            quantity_fact,
                            budget_fact,
                        ),
                        key=lambda fact: (
                            fact.field_name
                        ),
                    )
                ),
            )
        )

    return tuple(items)

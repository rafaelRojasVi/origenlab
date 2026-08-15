"""Extract high-value tender-wide commercial terms from canonical annex evidence.

T1-B recognition is intentionally content-driven and role-agnostic.

Every positive fact:
- comes from a B1-verified ResolvedEvidenceChunk;
- keeps exact character-span provenance;
- preserves repeated supporting evidence;
- becomes conflicting when distinct explicit values disagree.

Absence is fail-closed:
- complete coverage -> not_explicitly_found;
- incomplete coverage -> unknown.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from origenlab_email_pipeline.chilecompra_anexo_evidence.models import (
    TenderAttachmentBundle,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)

from .constants import (
    FACT_STATE_CONFLICTING,
    FACT_STATE_EXPLICIT,
    FACT_STATE_UNKNOWN,
)
from .fingerprint import finalize_bundle
from .items import extract_item_terms
from .models import (
    FactCandidate,
    TenderTermFact,
    TenderTermsBundle,
    TermEvidence,
)
from .source import (
    ResolvedEvidenceChunk,
    coverage_from_bundle,
    resolve_evidence_chunks,
    term_evidence_from_span,
)

_FLAGS = re.IGNORECASE | re.DOTALL


@dataclass(frozen=True)
class _Rule:
    field_name: str
    pattern: re.Pattern[str]
    parser: str
    value_type: str
    unit: str | None = None
    currency: str | None = None
    tax_basis: str | None = None


@dataclass(frozen=True)
class _ObservedCandidate:
    rule: _Rule
    value: Any
    evidence: TermEvidence


_RULES: tuple[_Rule, ...] = (
    _Rule(
        field_name="total_budget",
        pattern=re.compile(
            r"(?:"
            r"\bPresupuesto\s+(?:Disponible|Estimado)\b"
            r"|"
            r"\bMonto\s+estimado\s+del\s+contrato\b"
            r")"
            r".{0,180}?"
            r"\$\s*(?P<value>\d[\d.\s]{2,20})"
            r"\s*(?:\.-)?"
            r"(?:\s*CLP\b)?"
            r".{0,80}?"
            r"\b(?:impuestos|IVA)\s+incluid[oa]s?\b",
            _FLAGS,
        ),
        parser="clp_integer",
        value_type="integer",
        currency="CLP",
        tax_basis="taxes_included",
    ),
    _Rule(
        field_name="offer_validity_days",
        pattern=re.compile(
            r"(?:"
            r"\bLas\s+ofertas\s+tendr[aá]n\s+una\s+"
            r"(?:validez\s+de|vigencia\s+m[ií]nima\s+de)\s+"
            r"|"
            r"\bValidez\s+de\s+la\s+oferta\b"
            r"\s*(?:\||:|-)\s*"
            r")"
            r"(?P<value>\d{1,4})\s+d[ií]as\b"
            r"(?:\s+(?:corridos|h[aá]biles))?",
            _FLAGS,
        ),
        parser="integer",
        value_type="integer",
        unit="days",
    ),
    _Rule(
        field_name="contract_duration_months",
        pattern=re.compile(
            r"(?:"
            r"\bTiempo\s+m[aá]ximo\s+de\s+duraci[oó]n\s+del\s+contrato\b"
            r".{0,150}?"
            r"\bpor\s+"
            r"|"
            r"\bVigencia\s+(?:del\s+)?contrato\b"
            r"\s*(?:\||:|-)\s*"
            r"|"
            r"\bVigencia\s+del\s+contrato\s+"
            r"se\s+extender[aá]\s+por\s+el\s+periodo\s+de\s+"
            r"|"
            r"\bDuraci[oó]n\s+del\s+Contrato\b"
            r".{0,100}?"
            r"\bTiempo\s+de\s+Duraci[oó]n\s*:\s*"
            r")"
            r"(?P<value>\d{1,3})\s+meses\b",
            _FLAGS,
        ),
        parser="integer",
        value_type="integer",
        unit="months",
    ),
    _Rule(
        field_name="payment_deadline_days",
        pattern=re.compile(
            r"(?:"
            r"\bModalidad\s+de\s+Pago\s+del\s+contrato\b"
            r".{0,150}?"
            r"\bPago\s+m[aá]ximo\s+"
            r"|"
            r"\bEl\s+SAG\s+pagar[aá]\b"
            r".{0,220}?"
            r"\ben\s+el\s+plazo\s+m[aá]ximo\s+de\s+"
            r")"
            r"(?P<value>\d{1,3})\s+d[ií]as\b"
            r"(?:\s+(?:corridos|h[aá]biles))?"
            r"(?:\s*,?\s*contados?\s+desde\s+la\s+"
            r"recepci[oó]n\s+de\s+la\s+factura\b)?",
            _FLAGS,
        ),
        parser="integer",
        value_type="integer",
        unit="days",
    ),
    _Rule(
        field_name="maximum_delivery_days",
        pattern=re.compile(
            r"(?:"
            r"\bplazo\s+m[aá]ximo\s+"
            r"(?:de\s+entrega|para\s+la\s+entrega)\b"
            r".{0,120}?"
            r"(?:[a-záéíóúñ]+\s*\(\s*)?"
            r"(?P<delivery_max_days>\d{1,4})"
            r"\s*\)?\s*d[ií]as?"
            r"(?:\s+(?P<delivery_max_basis>h[aá]biles|corridos))?"
            r"|"
            r"\bplazo\s+de\s+entrega\s+superior\s+a\s+"
            r"(?P<delivery_threshold_days>\d{1,4})\s*d[ií]as?"
            r"(?:\s+(?P<delivery_threshold_basis>h[aá]biles|corridos))?"
            r".{0,260}?"
            r"\b(?:"
            r"inadmisible"
            r"|fuera\s+de\s+bases"
            r"|no\s+continuar[aá]\s+con\s+el\s+proceso"
            r")\b"
            r"|"
            r"\b(?:"
            r"plazo\s+de\s+entrega\s+ofertado"
            r"|plazo\s+de\s+entrega\s+de\s+los\s+equipos"
            r")\b"
            r".{0,180}?"
            r"\b(?:no\s+podr[aá]|no\s+puede)\s+"
            r"(?:superar|exceder)\s+"
            r"(?:los\s+)?"
            r"(?P<delivery_cap_days>\d{1,4})\s*d[ií]as?"
            r"(?:\s+(?P<delivery_cap_basis>h[aá]biles|corridos))?"
            r"|"
            r"\bde\s+superar\s+los\s+"
            r"(?P<delivery_superar_days>\d{1,4})\s*d[ií]as?"
            r"(?:\s+(?P<delivery_superar_basis>h[aá]biles|corridos))?"
            r".{0,260}?"
            r"\b(?:oferta|propuesta)\b"
            r".{0,120}?"
            r"\binadmisible\b"
            r")",
            _FLAGS,
        ),
        parser="duration_days",
        value_type="duration_days",
        unit="days",
    ),
    _Rule(
        field_name="faithful_performance_guarantee_percent",
        pattern=re.compile(
            r"(?:"
            r"\bGarant[ií]a\s+de\s+Fiel\s+Cumplimiento"
            r"(?:\s+del|\s+de)?\s+Contrato\b"
            r".{0,1800}?"
            r"\bmonto\s+(?:"
            r"que\s+alcance\s+el"
            r"|equivalente\s+a(?:\s+un)?"
            r")\s+"
            r"|"
            r"\b(?:Fiel|ﬁel)"
            r"(?:\s+y\s+oportuno)?\s+Cumplimiento\b"
            r".{0,1400}?"
            r"\b(?:Dicha\s+)?garant[ií]a\b"
            r".{0,500}?"
            r"\bmonto\s+equivalente\s+a(?:\s+un)?\s+"
            r")"
            r"(?P<value>\d{1,3}(?:[.,]\d+)?)\s*%"
            r"\s+del\s+precio\s+(?:final|ﬁnal)\s+neto\b",
            _FLAGS,
        ),
        parser="percentage",
        value_type="percentage",
        unit="percent",
        tax_basis="net_final_awarded_price",
    ),
)


def _parse_value(rule: _Rule, raw: str) -> Any:
    if rule.parser == "integer":
        return int(raw)

    if rule.parser == "clp_integer":
        digits = re.sub(r"\D", "", raw)
        if not digits:
            raise ValueError(f"invalid CLP amount: {raw!r}")
        return int(digits)

    if rule.parser == "percentage":
        normalized = raw.replace(",", ".")
        value = float(normalized)
        return int(value) if value.is_integer() else value

    raise ValueError(f"unsupported T1 parser: {rule.parser}")


def _parse_duration_days_match(
    match: re.Match[str],
) -> dict[str, Any]:
    group_pairs = (
        ("delivery_max_days", "delivery_max_basis"),
        (
            "delivery_threshold_days",
            "delivery_threshold_basis",
        ),
        (
            "delivery_cap_days",
            "delivery_cap_basis",
        ),
        (
            "delivery_superar_days",
            "delivery_superar_basis",
        ),
    )

    for days_group, basis_group in group_pairs:
        raw_days = match.group(days_group)

        if raw_days is None:
            continue

        raw_basis = match.group(basis_group)

        if raw_basis is None:
            day_basis = None
        else:
            normalized = (
                raw_basis.casefold()
                .replace("á", "a")
                .replace("é", "e")
                .replace("í", "i")
                .replace("ó", "o")
                .replace("ú", "u")
            )

            if normalized == "habiles":
                day_basis = "business"
            elif normalized == "corridos":
                day_basis = "calendar"
            else:
                raise ValueError(
                    f"unsupported delivery day basis: {raw_basis!r}"
                )

        return {
            "days": int(raw_days),
            "day_basis": day_basis,
        }

    raise ValueError(
        "duration-days match contains no supported value group"
    )


def _fact_id(tender_id: str, field_name: str) -> str:
    seed = f"anexo_t1_fact\0{tender_id}\0{field_name}".encode()
    return hashlib.sha256(seed).hexdigest()[:32]


def _candidate_id(
    tender_id: str,
    field_name: str,
    value: Any,
) -> str:
    value_digest = canonical_json_digest({"value": value})
    seed = (
        f"anexo_t1_candidate\0{tender_id}\0"
        f"{field_name}\0{value_digest}"
    ).encode()
    return hashlib.sha256(seed).hexdigest()[:32]


def _evidence_tuple(rows: list[TermEvidence]) -> tuple[TermEvidence, ...]:
    by_id = {row.evidence_id: row for row in rows}
    return tuple(by_id[key] for key in sorted(by_id))


def _observe_rule(
    source: ResolvedEvidenceChunk,
    rule: _Rule,
) -> tuple[_ObservedCandidate, ...]:
    observed: list[_ObservedCandidate] = []

    for match in rule.pattern.finditer(source.chunk.text):
        if rule.parser == "duration_days":
            value = _parse_duration_days_match(match)
        else:
            value = _parse_value(
                rule,
                match.group("value"),
            )

        # Preserve the whole supporting statement, not only the captured
        # numeric token, so a human reviewer can see why the value was read.
        evidence = term_evidence_from_span(
            source,
            char_start=match.start(),
            char_end=match.end(),
        )

        observed.append(
            _ObservedCandidate(
                rule=rule,
                value=value,
                evidence=evidence,
            )
        )

    return tuple(observed)


def _fact_from_observations(
    *,
    tender_id: str,
    rule: _Rule,
    observations: tuple[_ObservedCandidate, ...],
    coverage_status: str,
    coverage_complete: bool,
) -> TenderTermFact:
    fact_id = _fact_id(tender_id, rule.field_name)

    if not observations:
        # Complete extraction coverage only tells us that the source corpus was
        # readable. It does not make this intentionally narrow recognizer
        # exhaustive over every possible wording of the commercial term.
        #
        # Therefore a missing supported pattern remains unknown. A future rule
        # set may emit not_explicitly_found only once field-level recognition
        # coverage is strong enough to support an authoritative absence claim.
        reason = (
            "no_supported_explicit_pattern_match"
            if coverage_complete
            else "source_coverage_incomplete"
        )
        return TenderTermFact(
            fact_id=fact_id,
            field_name=rule.field_name,
            state=FACT_STATE_UNKNOWN,
            coverage_status=coverage_status,
            value_type=rule.value_type,
            unit=rule.unit,
            currency=rule.currency,
            tax_basis=rule.tax_basis,
            reason_codes=(reason,),
        )

    grouped: dict[str, tuple[Any, list[TermEvidence]]] = {}

    for observation in observations:
        key = canonical_json_digest({"value": observation.value})
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = (
                observation.value,
                [observation.evidence],
            )
        else:
            existing[1].append(observation.evidence)

    ordered_groups = [
        grouped[key]
        for key in sorted(grouped)
    ]

    if len(ordered_groups) == 1:
        value, evidence_rows = ordered_groups[0]
        return TenderTermFact(
            fact_id=fact_id,
            field_name=rule.field_name,
            state=FACT_STATE_EXPLICIT,
            coverage_status=coverage_status,
            value=value,
            value_type=rule.value_type,
            unit=rule.unit,
            currency=rule.currency,
            tax_basis=rule.tax_basis,
            evidence=_evidence_tuple(evidence_rows),
        )

    candidates = tuple(
        sorted(
            (
                FactCandidate(
                    candidate_id=_candidate_id(
                        tender_id,
                        rule.field_name,
                        value,
                    ),
                    value=value,
                    evidence=_evidence_tuple(evidence_rows),
                )
                for value, evidence_rows in ordered_groups
            ),
            key=lambda candidate: candidate.candidate_id,
        )
    )

    return TenderTermFact(
        fact_id=fact_id,
        field_name=rule.field_name,
        state=FACT_STATE_CONFLICTING,
        coverage_status=coverage_status,
        value_type=rule.value_type,
        unit=rule.unit,
        currency=rule.currency,
        tax_basis=rule.tax_basis,
        candidates=candidates,
        reason_codes=("multiple_distinct_explicit_values",),
    )


def extract_tender_terms(
    bundle: TenderAttachmentBundle,
) -> TenderTermsBundle:
    """Extract the initial T1-B tender-wide commercial term set."""

    if not bundle.semantic_digest:
        raise ValueError("source bundle semantic digest is required")

    coverage = coverage_from_bundle(bundle)
    sources = resolve_evidence_chunks(bundle)

    observations_by_field: dict[str, list[_ObservedCandidate]] = {
        rule.field_name: []
        for rule in _RULES
    }

    for source in sources:
        for rule in _RULES:
            observations_by_field[rule.field_name].extend(
                _observe_rule(source, rule)
            )

    items = extract_item_terms(
        tender_id=bundle.tender_id,
        sources=sources,
        coverage_status=coverage.fact_coverage_status,
    )

    facts = tuple(
        sorted(
    (
                _fact_from_observations(
                    tender_id=bundle.tender_id,
                    rule=rule,
                    observations=tuple(
                        observations_by_field[rule.field_name]
                    ),
                    coverage_status=coverage.fact_coverage_status,
                    coverage_complete=coverage.is_complete,
                )
                for rule in _RULES
            ),
            key=lambda fact: fact.field_name,
        )
    )

    return finalize_bundle(
        TenderTermsBundle(
            tender_id=bundle.tender_id,
            source_bundle_semantic_digest=bundle.semantic_digest,
            tender_facts=facts,
            items=items,
            coverage=coverage,
        )
    )

"""Claude call per ANID candidate: fit score, «Por qué ahora» and matching equipment.

One call per candidate, outside a transaction. Cap pattern exactly like tender/job.py _call_model:
  1. short transaction: with_cap_lock + ensure_within_cap (worst-case cost), then commit;
  2. the model call, with no transaction open;
  3. short transaction: record_usage.

BudgetReached → stop the run cleanly (caller catches it and sets counts.error='budget').
UnpricedModel → refuse before any paid call.

Model: from env `ORIGENLAB_WORKER_PROSPECT_MODEL`, default `claude-sonnet-5-5`, validated by
`check_model_id`. Returns FitResult; fit < 30 is below threshold (caller still stores it as
hidden so the candidate is never picked again).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from origenlab_api.v2.catalog.enrichment import check_model_id
from origenlab_api.v2.model_spend import (
    FEATURE_PROSPECTOS,
    BudgetReached,
    UnpricedModel,
    ensure_within_cap,
    record_usage,
    with_cap_lock,
    worst_case_usd,
)

_log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5-5"
ENV_PROSPECT_MODEL = "ORIGENLAB_WORKER_PROSPECT_MODEL"

# Rough upper bound: project title + discipline + keywords in Spanish
INPUT_TOKENS_ESTIMATE = 800
MAX_TOKENS = 512
FIT_THRESHOLD = 30  # below this the finding is stored but hidden

_SYSTEM = """\
Eres el asistente de ventas de OrigenLab, distribuidora de equipos de laboratorio en Chile.
Solo vendemos estas marcas y categorías:
- Balanzas y analizadores de humedad: OHAUS, Adam Equipment
- Agitadores, dispersores y homogeneizadores ultrasónicos de laboratorio: IKA
- Procesadores ultrasónicos de sonda (sonicadores): Hielscher Ultrasonics
- Centrífugas de laboratorio: Ortoalresa
- Osmómetros y criometros: Löser Messtechnik
- Reactivos y kits para electroforesis: SERVA Electrophoresis

Recibirás datos de un proyecto ANID adjudicado. Responde SOLO con un objeto JSON (sin markdown):
{
  "fit": <0-100>,
  "why_now": "<una frase corta en español natural, como le hablaría Tatiana al PI, explicando por qué el proyecto necesita equipos ahora>",
  "equipment": ["<categoría o marca específica que calza>", ...]
}

Reglas:
- fit = 0 si la disciplina no necesita ninguno de nuestros equipos.
- fit > 70 si el proyecto usa explícitamente uno o más de nuestros equipos o técnicas afines.
- why_now: máximo 20 palabras, sin jerga. Ej: «Su proyecto de microbiología de suelos necesitará balanzas analíticas y homogeneizador».
- equipment: solo categorías que OrigenLab vende; máximo 4 ítems; lista vacía si fit < 30.
"""


@dataclass
class FitResult:
    fit: int
    why_now: str
    equipment: list[str]
    model: str
    usage: Any


def call_fit(conn: Any, candidate_context: str, model: str,
             search_run_id: str | None) -> FitResult:
    """One Claude call for a candidate's fit. `conn` must be outside a transaction when called.
    Raises BudgetReached or UnpricedModel (both are not bugs)."""
    import anthropic
    from origenlab_api.v2.model_spend import MONTHLY_CAP_USD

    if FEATURE_PROSPECTOS not in MONTHLY_CAP_USD:  # pragma: no cover — always in the dict
        raise UnpricedModel(f"feature {FEATURE_PROSPECTOS!r} has no cap entry")

    worst = worst_case_usd(model, input_tokens_estimate=INPUT_TOKENS_ESTIMATE, max_tokens=MAX_TOKENS)

    # 1. Cap check (short tx)
    with conn.transaction(), conn.cursor() as cur:
        with_cap_lock(cur, FEATURE_PROSPECTOS)
        ensure_within_cap(cur, FEATURE_PROSPECTOS, datetime.now(timezone.utc), worst)

    # 2. Model call (no transaction open)
    client = anthropic.Anthropic()
    message = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=_SYSTEM,
        messages=[{"role": "user", "content": candidate_context}],
    )
    served_model = getattr(message, "model", None) or model
    usage = message.usage
    raw = (message.content[0].text if message.content else "{}").strip()
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        parsed = {}
    fit = max(0, min(int(parsed.get("fit") or 0), 100))
    why_now = str(parsed.get("why_now") or "").strip()[:500] or "Proyecto adjudicado por ANID."
    equipment = [str(e).strip() for e in (parsed.get("equipment") or []) if str(e).strip()][:4]

    # 3. Record usage (short tx)
    with conn.transaction(), conn.cursor() as cur:
        record_usage(cur, feature=FEATURE_PROSPECTOS, step="prospect", model=model, usage=usage,
                     bid_preparation_id=None, served_model=served_model,
                     search_run_id=search_run_id)

    return FitResult(fit=fit, why_now=why_now, equipment=equipment,
                     model=served_model, usage=usage)


def build_candidate_context(*, nombre_proyecto: str, disciplina: str,
                            area_ocde: str, palabras_clave: list[str],
                            monto_label: str, fund_label: str) -> str:
    """Plain-text context for the Claude prompt — no names, no codes (privacy + public repo)."""
    lines = [
        f"Proyecto: {nombre_proyecto}",
        f"Área OCDE: {area_ocde}",
        f"Disciplina: {disciplina}",
    ]
    if palabras_clave:
        lines.append(f"Palabras clave: {', '.join(palabras_clave[:10])}")
    if monto_label:
        lines.append(f"Monto adjudicado: {monto_label}")
    if fund_label:
        lines.append(f"Fondo: {fund_label}")
    return "\n".join(lines)


def model_from_env(env: dict[str, str]) -> str:
    """Read + validate the prospect model from the env; raises ValueError on invalid id."""
    return check_model_id((env.get(ENV_PROSPECT_MODEL) or DEFAULT_MODEL).strip())


__all__ = [
    "DEFAULT_MODEL", "ENV_PROSPECT_MODEL", "FEATURE_PROSPECTOS", "FIT_THRESHOLD",
    "FitResult", "build_candidate_context", "call_fit", "model_from_env",
]

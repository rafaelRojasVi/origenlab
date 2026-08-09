"""Advisory false-positive flags for annex-derived claims.

Annex documents interleave the equipment specification with accessory and
spare-part lists, so reading annexes amplifies an existing recognition risk:
a line like "Sonotrodo de repuesto para procesador ultrasónico" names an
accessory but still matches the equipment pattern.

These flags are **metadata only**. They never suppress a claim and never change
a recognition decision - PR5D rules are production and stay frozen. Their job is
to make the risk visible in the review packet so a later production-integration
slice can decide what to do about it.
"""

from __future__ import annotations

import re

from ..commercial_procurement_product_relevance.models import MatchedEvidenceSpan
from ..commercial_procurement_product_relevance.normalize import normalize_product_text


RISK_ACCESSORY_HEADED = "accessory_headed_line"

# Accessory / spare-part / consumable heads seen in Chilean annex line items.
_ACCESSORY_HEAD_RE = re.compile(
    r"\b("
    r"repuesto|repuestos|accesorio|accesorios|sonotrodo|sonotrodos|rotor|rotores|"
    r"sonda|sondas|punta|puntas|tubo|tubos|gradilla|gradillas|adaptador|adaptadores|"
    r"kit|set|juego|cubeta|cubetas|filtro|filtros|manguera|mangueras|"
    r"portaobjeto|portaobjetos|cubreobjeto|cubreobjetos"
    r")\b"
)


def accessory_risk_codes(
    text_raw: str, spans: tuple[MatchedEvidenceSpan, ...]
) -> tuple[str, ...]:
    """Flag lines where the equipment term only qualifies an accessory.

    Fires when an accessory noun *precedes* the equipment match and the match is
    introduced by "para" - the shape of "<accessory> ... para <equipment>". A
    line that leads with the equipment and merely mentions an accessory
    afterwards ("Centrífuga de sobremesa con rotor de ángulo fijo") is not
    flagged, and neither is equipment scoped by what it holds ("Centrífuga para
    tubos eppendorf"), because there the equipment comes first.
    """
    normalized = normalize_product_text(text_raw)
    if not normalized:
        return ()

    starts = [s.start for s in spans if s.start is not None]
    if not starts:
        return ()

    # Spans index into the normalized text the classifier matched against.
    prefix = normalized[: min(starts)]
    if not prefix.strip():
        return ()
    if not _ACCESSORY_HEAD_RE.search(prefix):
        return ()
    if not prefix.rstrip().endswith("para"):
        return ()
    return (RISK_ACCESSORY_HEADED,)

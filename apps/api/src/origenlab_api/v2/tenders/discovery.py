"""«En Mercado Público»: which open tenders fit what OrigenLab sells.

The worker's discovery sweep (`origenlab_worker.tender.discovery`) reads Mercado Público's list of
open tenders and keeps, in `procurement.notice`, every one whose title passes :func:`title_candidate`;
it then reads each kept notice's detail (buyer, items). The «Licitaciones» page reads them back
through :func:`discovered_notices`, which classifies each from its stored head:

* **`catalogo`** — the title, description or an item names one of the catalog's families
  (:data:`FAMILIES`: balanzas, centrífugas, homogeneizadores, ultrasonido, osmómetros, electroforesis,
  agitación/calefacción de laboratorio).
* **`laboratorio`** — no family, but an item is UNSPSC segment 41 (laboratory and measuring
  equipment) or the text names laboratory equipment.
* anything else (lab *services* such as «exámenes de laboratorio», reagents only) is not shown.

A notice read before its detail arrived is shown on its title alone, `detail: false`.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from origenlab_email_pipeline.equipment_first_licitacion_queue import parse_close_date

from origenlab_api.v2.tenders.codes import portal_url

CHILE = ZoneInfo("America/Santiago")

#: (key, label, pattern over the accent-free, lower-case text). Ordered as the page shows them.
FAMILIES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("pesaje", "Balanzas y humedad", re.compile(
        r"\bbalanzas?\b|\bbasculas?\b|\btermobalanzas?\b|\bpesaje\b|\bmasas? patron\b|"
        r"\b(analizador|determinador|medidor)(es)? de humedad\b")),
    ("centrifugas", "Centrífugas", re.compile(r"centrifug|citocentrifug|\bultracentrifug")),
    ("homogeneizacion", "Homogeneizadores y dispersores", re.compile(
        r"homogeneiza|\bdispersor|\bmolinos? (de|para) laboratorio|\bmolino analitico")),
    ("ultrasonido", "Ultrasonido y sonicadores", re.compile(
        r"sonicador|sonificador|ultrason|\bsonicacion")),
    ("agitacion", "Agitadores, vórtex y calefacción", re.compile(
        r"\bagitador(es)?\b|\bvortex\b|\bplacas? calefactoras?\b|\bplancha(s)? calefactora|"
        r"\brotavapor|\bevaporador(es)? rotatorio|\bbanos? (termorregulad|termostatic|de agua|maria|seco)|"
        r"\btermoreactor|\bmantas? calefactoras?")),
    ("osmometria", "Osmómetros", re.compile(r"osmometr")),
    ("electroforesis", "Electroforesis", re.compile(r"electroforesis|\btransiluminador|\bcamaras? de western")),
)
FAMILY_LABELS: dict[str, str] = {key: label for key, label, _ in FAMILIES}

#: Laboratory equipment by name, when no family matched: the second tier.
_LAB_EQUIPMENT = re.compile(
    r"\bequip(o|os|amiento)s? (de|para) (uso de )?laboratorio|\binstrumental de laboratorio|"
    r"\bequipos? (medicos|clinicos) de laboratorio|\bincubadora|\bestufas? (de|para) (cultivo|secado|laboratorio)|"
    r"\bautoclave|\bph ?metro|\bmedidor(es)? de ph\b|\bmicroscopio|\bespectrofotometr|\bcampanas? de (extraccion|gases|flujo)|"
    r"\bcabinas? de (bioseguridad|flujo)|\bmuflas?\b|\bconductivimetr|\brefractometr|\bviscosimetr|\bturbidimetr|"
    r"\btitulador|\bpipetas? automatica|\bmicropipeta|\btermociclador|\bdestilador(es)? de agua|\bliofiliz")
#: A title worth a detail lookup: a family, laboratory equipment, or the word «laboratorio».
_TITLE_ONLY = re.compile(r"laboratorio")
#: Lab services and supplies, not equipment: they never make the second tier on their own.
_SERVICE = re.compile(r"\bexamen(es)?\b|\banalisis clinicos\b|\bservicios? de laboratorio|\breactivos?\b")
LAB_UNSPSC_SEGMENT = "41"


def chile_time(raw: Any) -> datetime | None:
    """A Mercado Público date (`dd/mm/yyyy hh:mm[:ss]` or ISO) as an aware instant: an offset-less one
    is Chilean time; blank or unreadable is None."""
    text = str(raw).strip() if raw is not None else ""
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        moment = parse_close_date(text)
        if moment is None:
            return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=CHILE)


def fold(text: str | None) -> str:
    """Lower-case, accent-free, single-spaced: what every pattern here reads."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", plain).strip().lower()


def families_in(text: str | None) -> list[str]:
    folded = fold(text)
    return [key for key, _, pattern in FAMILIES if pattern.search(folded)]


def title_candidate(title: str | None) -> bool:
    """The sweep's gate before a detail lookup: broad on purpose (the read narrows it)."""
    folded = fold(title)
    return bool(folded) and (
        any(p.search(folded) for _, _, p in FAMILIES) or bool(_LAB_EQUIPMENT.search(folded))
        or bool(_TITLE_ONLY.search(folded))
    )


def head_items(head: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The notice's items as the API returns them (`Items.Listado`), tolerant of either shape."""
    items = (head or {}).get("Items")
    if isinstance(items, dict):
        items = items.get("Listado")
    if isinstance(items, dict):
        items = [items]
    return [i for i in items or [] if isinstance(i, dict)]


def has_detail(head: dict[str, Any] | None) -> bool:
    """A head from the detail endpoint carries the buyer; the list endpoint's never does."""
    return isinstance((head or {}).get("Comprador"), dict)


def classify(title: str | None, head: dict[str, Any] | None) -> dict[str, Any]:
    """`tier` (`catalogo` | `laboratorio` | None), the families matched, and the items that matched."""
    head = head or {}
    texts = [title, head.get("Nombre"), head.get("Descripcion")]
    found: list[str] = []
    for text in texts:
        found += [f for f in families_in(text) if f not in found]
    matched_items: list[dict[str, Any]] = []
    lab_item = False
    for item in head_items(head):
        text = " ".join(str(item.get(k) or "") for k in ("NombreProducto", "Descripcion", "Categoria"))
        item_families = families_in(text)
        unspsc = str(item.get("CodigoProducto") or item.get("CodigoCategoria") or "")
        is_lab = unspsc.startswith(LAB_UNSPSC_SEGMENT) or bool(_LAB_EQUIPMENT.search(fold(text)))
        lab_item = lab_item or is_lab
        found += [f for f in item_families if f not in found]
        if item_families or is_lab:
            matched_items.append({
                "name": _str(item.get("NombreProducto")) or _str(item.get("Descripcion")),
                "description": _str(item.get("Descripcion")),
                "quantity": item.get("Cantidad"),
                "unit": _str(item.get("UnidadMedida")),
                "unspsc": unspsc or None,
            })
    order = {key: n for n, (key, _, _) in enumerate(FAMILIES)}
    found.sort(key=order.__getitem__)
    if found:
        tier = "catalogo"
    elif lab_item:
        tier = "laboratorio"
    else:
        folded = " ".join(fold(t) for t in texts)
        named = bool(_LAB_EQUIPMENT.search(folded))
        # Before its detail arrives a title-only candidate is shown, unless it reads as a service.
        pending = not has_detail(head) and bool(_TITLE_ONLY.search(folded)) and not _SERVICE.search(folded)
        tier = "laboratorio" if named or pending else None
    return {"tier": tier, "families": found, "items": matched_items}


def _str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _instant(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


#: Open notices only: seen in Mercado Público's open list in the last two days (the sweep refreshes
#: `last_seen_at` every run), not withdrawn, closing in the future (or with no date yet).
DISCOVERED_SQL = """
select n.id::text as id, n.codigo_externo as code, n.title, n.buyer_name, n.closes_at, n.published_at,
       n.first_seen_at, n.head, n.source_status,
       case when o.closed_at is null then o.id::text end as opportunity_id
  from procurement.notice n
  left join crm.opportunity o on o.id = n.promoted_opportunity_id
 where n.disappeared_at is null
   and (n.closes_at is null or n.closes_at > %(now)s)
   and n.last_seen_at > %(now)s - interval '2 days'
 order by n.closes_at asc nulls last, n.codigo_externo
"""


def discovered_row(row: dict[str, Any], match: dict[str, Any]) -> dict[str, Any]:
    head = row.get("head") or {}
    buyer = head.get("Comprador") if isinstance(head.get("Comprador"), dict) else {}
    amount = _number(head.get("MontoEstimado"))
    return {
        "id": row["id"],
        "code": row["code"],
        "title": row.get("title") or row["code"],
        "buyer": row.get("buyer_name") or _str(buyer.get("NombreOrganismo")),
        "region": _str(buyer.get("RegionUnidad")),
        "closes_at": _instant(row.get("closes_at")),
        "published_at": _instant(row.get("published_at")),
        "first_seen_at": _instant(row.get("first_seen_at")),
        "estimated_amount": {"amount": amount, "currency": _str(head.get("Moneda")) or "CLP"} if amount else None,
        "tier": match["tier"],
        "families": [{"key": f, "label": FAMILY_LABELS[f]} for f in match["families"]],
        "items": match["items"][:8],
        "item_count": len(head_items(head)),
        "detail": has_detail(head),
        "portal_url": portal_url(row["code"]),
        "opportunity_id": row.get("opportunity_id"),
    }


def discovered_notices(cur: Any, now: datetime) -> dict[str, Any]:
    """Every open notice that fits, catalog families first, then by closing time."""
    cur.execute(DISCOVERED_SQL, {"now": now})
    columns = [c.name for c in cur.description]
    shown: list[dict[str, Any]] = []
    for values in cur.fetchall():
        row = dict(zip(columns, values, strict=True))
        match = classify(row.get("title"), row.get("head"))
        if match["tier"] is not None:
            shown.append(discovered_row(row, match))
    shown.sort(key=lambda r: (r["tier"] != "catalogo", r["closes_at"] is None, r["closes_at"] or ""))
    counts = {"catalogo": sum(r["tier"] == "catalogo" for r in shown),
              "laboratorio": sum(r["tier"] == "laboratorio" for r in shown)}
    cur.execute("select max(last_seen_at) from procurement.notice where promoted_opportunity_id is null")
    (last_seen,) = cur.fetchone()
    return {"notices": shown, "counts": counts, "families": [{"key": k, "label": l} for k, l, _ in FAMILIES],
            "last_checked_at": _instant(last_seen)}

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

**The catalog itself** (:class:`CatalogVocabulary`, read from `catalog.product` on every run and
read): the equipment type each live product's name starts with («Balanza …», «Homogeneizador …»), its
`category_es`, its maker («OHAUS», «IKA», «Hielscher») and its model number («UP200St»). A notice that
names one and no curated family lands in the catalog tier as «Otros de tu catálogo»; the words that
matched are returned as `terms` so the page can say why.

A notice read before its detail arrived is shown on its title alone, `detail: false`. One notice's
page (:func:`discovered_notice`) adds every date, every item with what matched, and the facts of
the head (type, contract length, delivery address, the buyer's complaint count).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
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
#: A notice that names the catalog (a type, a maker, a model) but no curated family.
CATALOG_FAMILY: tuple[str, str] = ("catalogo", "Otros de tu catálogo")
FAMILY_LABELS: dict[str, str] = {key: label for key, label, _ in FAMILIES} | {CATALOG_FAMILY[0]: CATALOG_FAMILY[1]}

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
#: An item coded outside these UNSPSC segments (41 laboratory, 42 medical equipment) is not read for
#: a match: a «centrífuga» inside a kindergarten's toy set (segment 60) says nothing. An item with no
#: code is read.
EQUIPMENT_UNSPSC_SEGMENTS = frozenset({"41", "42"})

#: A product name's first word that says nothing on its own: the next word is taken with it
#: («Procesador ultrasónico», «Baño termostático»).
_GENERIC_HEADS = frozenset({
    "equipo", "equipos", "sistema", "modulo", "unidad", "procesador", "controlador", "fuente", "bano",
    "instrumento", "aparato", "maquina", "dispositivo", "medidor", "analizador", "camara", "cabina",
    # Everyday words outside a laboratorio: «contador» is also an accountant, «mesa» a table.
    "contador", "bomba", "horno", "lampara", "mesa", "campana", "refrigerador", "congelador", "carro",
    "estante", "mueble", "lector", "agua", "termometro", "reloj", "sensor", "monitor", "impresora",
})
#: A first word that names a part or a bundle, not equipment: the name gives no type.
_NO_TYPE = frozenset({
    "kit", "juego", "set", "accesorio", "accesorios", "soporte", "adaptador", "cabezal", "repuesto",
    "tapa", "cable", "frasco", "tubo", "tubos", "vaso", "bandeja", "plato", "pack", "paquete", "servicio",
    "envio", "flete", "base", "pieza", "porta", "caja", "conjunto", "rotor", "sonda", "sondas", "filtro",
    "manguera", "electrodo", "pesa", "pesas", "papel", "funda", "cubierta",
})
#: Maker words too common to mean the maker.
_GENERIC_MAKER_WORDS = frozenset({
    "laboratorio", "laboratorios", "scientific", "instruments", "instrument", "equipment", "group",
    "gmbh", "company", "industrial", "international", "lab", "labs", "technologies", "technology",
    "the", "electronics", "medical", "systems", "chile", "sociedad", "comercial",
})
_CONNECTORS = frozenset({"de", "del", "para", "con", "y", "e", "a", "en", "la", "el", "los", "las"})
#: Words every kind of tender uses: never a catalog type, whatever a product's name starts with.
_COMMON_WORDS = frozenset({
    "material", "materiales", "programa", "programas", "producto", "productos", "insumo", "insumos",
    "articulo", "articulos", "elemento", "elementos", "herramienta", "herramientas", "implemento",
    "equipamiento", "mobiliario", "suministro", "licencia", "software", "capacitacion", "mantencion",
    "mantenimiento", "reparacion", "reactivo", "reactivos", "solucion", "soluciones", "estandar",
    "libro", "libros", "mueble", "computador", "notebook", "vehiculo", "alimento", "alimentos",
    "combustible", "uniforme", "bolsa", "guante", "guantes", "mascarilla", "jeringa", "aguja", "agujas",
    "control", "calibracion", "certificado", "manual", "curso", "proyecto", "estudio", "general",
    "standard", "estuche", "maleta", "sistema", "modelo", "version", "nuevo", "digital", "electronico",
})


@dataclass(frozen=True)
class CatalogVocabulary:
    """What the catalog sells, as patterns over folded text. Empty: matches nothing."""

    types: re.Pattern[str] | None = None
    makers: re.Pattern[str] | None = None
    models: re.Pattern[str] | None = None

    def hits(self, text: str | None, *, types: bool = True) -> list[str]:
        """The catalog words in `text`: models, makers, then types (left out with `types=False`)."""
        folded = fold(text)
        found: list[str] = []
        for pattern in (self.models, self.makers, self.types if types else None):
            if pattern is None or not folded:
                continue
            for m in pattern.finditer(folded):
                word = m.group(0).strip()
                if word not in found:
                    found.append(word)
        return found


EMPTY_VOCABULARY = CatalogVocabulary()


def _alternation(words: Iterable[str], *, stem: bool = False) -> re.Pattern[str] | None:
    """One `\b(a|b|…)` pattern, longest first; `stem` lets a type match its plural."""
    unique = sorted({w for w in words if w}, key=lambda w: (-len(w), w))
    if not unique:
        return None
    # A two-word type reads across a connector: «contador colonias» matches «contador de colonias».
    body = "|".join(re.escape(w).replace(r"\ ", r"(?: (?:de|del|para|con|a))? ") for w in unique)
    return re.compile(rf"\b(?:{body})" + (r"(?:s|es)?\b" if stem else r"\b"))


def _type_of(name: str | None) -> str | None:
    words = [w for w in re.split(r"[^a-z0-9]+", fold(name)) if w]
    if not words or words[0].isdigit():
        return None
    head = words[0]
    if head in _NO_TYPE or head in _COMMON_WORDS:
        return None
    if head in _GENERIC_HEADS:
        rest = [w for w in words[1:] if w not in _CONNECTORS]
        if not rest or len(rest[0]) < 5 or any(c.isdigit() for c in rest[0]) or rest[0] in _COMMON_WORDS:
            return None
        return f"{head} {rest[0]}"
    if len(head) < 6 or any(c.isdigit() for c in head):
        return None
    return head[:-2] if head.endswith("es") and len(head) > 7 else head[:-1] if head.endswith("s") else head


def build_vocabulary(rows: Iterable[tuple[Any, ...]]) -> CatalogVocabulary:
    """`rows`: (name_es, name, category_es, maker name, model number) per live equipment product."""
    types: set[str] = set()
    makers: set[str] = set()
    models: set[str] = set()
    for name_es, name, category, maker, model in rows:
        kind = _type_of(name_es) or _type_of(name)
        if kind:
            types.add(kind)
        category_type = _type_of(category)
        if category_type:
            types.add(category_type)
        raw_first = re.split(r"[\s\-_,.;:()/&+]+", str(maker or "").strip())[0]
        first = fold(raw_first)
        # Four letters or more, or a three-letter all-capitals brand («IKA»); never a generic word.
        if first not in _GENERIC_MAKER_WORDS and (len(first) >= 4 or (len(first) == 3 and raw_first.isupper())):
            makers.add(first)
        model_text = fold(model)
        if (len(model_text) >= 4 and any(c.isdigit() for c in model_text) and any(c.isalpha() for c in model_text)
                and " " not in model_text):
            models.add(model_text)
    return CatalogVocabulary(types=_alternation(types, stem=True), makers=_alternation(makers),
                             models=_alternation(models))


#: Live equipment of the catalog (merged-away products and services, consumables, spare parts and
#: accessories left out: they name nothing a tender buys as equipment).
CATALOG_VOCABULARY_SQL = """
select p.name_es, p.name, p.category_es, o.name as maker, p.model_number
  from catalog.product p
  join crm.organization o on o.id = p.manufacturer_organization_id
 where p.merged_into_product_id is null
   and (p.product_kind is null or p.product_kind = 'equipment')
"""


def load_vocabulary(cur: Any) -> CatalogVocabulary:
    cur.execute(CATALOG_VOCABULARY_SQL)
    return build_vocabulary(cur.fetchall())


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


def title_candidate(title: str | None, vocabulary: CatalogVocabulary = EMPTY_VOCABULARY) -> bool:
    """The sweep's gate before a detail lookup: broad on purpose (the read narrows it)."""
    folded = fold(title)
    return bool(folded) and (
        any(p.search(folded) for _, _, p in FAMILIES) or bool(_LAB_EQUIPMENT.search(folded))
        or bool(_TITLE_ONLY.search(folded)) or bool(vocabulary.hits(title))
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


def _same_word(a: str, b: str) -> bool:
    """«centrifuga» and «centrifugas» are one word for «Calza con»."""
    def stem(w: str) -> str:
        return w[:-2] if w.endswith("es") and len(w) > 7 else w[:-1] if w.endswith("s") else w
    return stem(a) == stem(b)


def _add_terms(terms: list[str], new: list[str]) -> None:
    for t in new:
        if not any(_same_word(t, seen) for seen in terms):
            terms.append(t)


def _item_row(item: dict[str, Any], unspsc: str, families: list[str], terms: list[str], *, is_lab: bool) -> dict[str, Any]:
    return {
        "name": _str(item.get("NombreProducto")) or _str(item.get("Descripcion")),
        "description": _str(item.get("Descripcion")),
        "quantity": item.get("Cantidad"),
        "unit": _str(item.get("UnidadMedida")),
        "unspsc": unspsc or None,
        "category": _str(item.get("Categoria")),
        "families": [{"key": f, "label": FAMILY_LABELS[f]} for f in families],
        "terms": terms,
        "lab_equipment": is_lab,
    }


def classify(title: str | None, head: dict[str, Any] | None,
             vocabulary: CatalogVocabulary = EMPTY_VOCABULARY, *, all_items: bool = False) -> dict[str, Any]:
    """`tier` (`catalogo` | `laboratorio` | None), the families matched, the catalog words that
    matched (`terms`) and the items that matched (every item with `all_items`)."""
    head = head or {}
    texts = [title, head.get("Nombre"), head.get("Descripcion")]
    found: list[str] = []
    terms: list[str] = []
    for text in texts:
        found += [f for f in families_in(text) if f not in found]
        # A model or a maker always counts; a type in the title waits for laboratory context (below).
        _add_terms(terms, vocabulary.hits(text, types=False))
    matched_items: list[dict[str, Any]] = []
    lab_item = False
    for item in head_items(head):
        text = " ".join(str(item.get(k) or "") for k in ("NombreProducto", "Descripcion", "Categoria"))
        unspsc = str(item.get("CodigoProducto") or item.get("CodigoCategoria") or "")
        in_scope = not unspsc or unspsc[:2] in EQUIPMENT_UNSPSC_SEGMENTS
        item_families = families_in(text) if in_scope else []
        item_terms = vocabulary.hits(text) if in_scope else []
        is_lab = unspsc.startswith(LAB_UNSPSC_SEGMENT) or (in_scope and bool(_LAB_EQUIPMENT.search(fold(text))))
        lab_item = lab_item or is_lab
        found += [f for f in item_families if f not in found]
        _add_terms(terms, item_terms)
        if all_items or item_families or item_terms or is_lab:
            matched_items.append(_item_row(item, unspsc, item_families, item_terms, is_lab=is_lab))
    folded_texts = " ".join(fold(t) for t in texts)
    if found or lab_item or _TITLE_ONLY.search(folded_texts) or _LAB_EQUIPMENT.search(folded_texts):
        for text in texts:
            _add_terms(terms, vocabulary.hits(text))
    if not found and terms:
        found = [CATALOG_FAMILY[0]]
    order = {key: n for n, key in enumerate([k for k, _, _ in FAMILIES] + [CATALOG_FAMILY[0]])}
    found.sort(key=order.__getitem__)
    if found:
        tier = "catalogo"
    elif lab_item:
        tier = "laboratorio"
    else:
        folded = folded_texts
        named = bool(_LAB_EQUIPMENT.search(folded))
        # Before its detail arrives a title-only candidate is shown, unless it reads as a service.
        pending = not has_detail(head) and bool(_TITLE_ONLY.search(folded)) and not _SERVICE.search(folded)
        tier = "laboratorio" if named or pending else None
    return {"tier": tier, "families": found, "terms": terms[:6], "items": matched_items}


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
        "terms": match["terms"],
        "items": match["items"][:8],
        "item_count": len(head_items(head)),
        "detail": has_detail(head),
        "portal_url": portal_url(row["code"]),
        "opportunity_id": row.get("opportunity_id"),
    }


def discovered_notices(cur: Any, now: datetime) -> dict[str, Any]:
    """Every open notice that fits, catalog families first, then by closing time."""
    vocabulary = load_vocabulary(cur)
    cur.execute(DISCOVERED_SQL, {"now": now})
    columns = [c.name for c in cur.description]
    shown: list[dict[str, Any]] = []
    for values in cur.fetchall():
        row = dict(zip(columns, values, strict=True))
        match = classify(row.get("title"), row.get("head"), vocabulary)
        if match["tier"] is not None:
            shown.append(discovered_row(row, match))
    shown.sort(key=lambda r: (r["tier"] != "catalogo", r["closes_at"] is None, r["closes_at"] or ""))
    counts = {"catalogo": sum(r["tier"] == "catalogo" for r in shown),
              "laboratorio": sum(r["tier"] == "laboratorio" for r in shown)}
    cur.execute("select max(last_seen_at) from procurement.notice where promoted_opportunity_id is null")
    (last_seen,) = cur.fetchone()
    families = [{"key": k, "label": l} for k, l, _ in FAMILIES] + [{"key": CATALOG_FAMILY[0], "label": CATALOG_FAMILY[1]}]
    return {"notices": shown, "counts": counts, "families": families, "last_checked_at": _instant(last_seen)}


#: The head's dates, in the order a tender lives them (`Fechas.*`; `FechaCierre` also at the top).
DATE_FIELDS: tuple[tuple[str, str], ...] = (
    ("FechaPublicacion", "Publicación"),
    ("FechaInicio", "Inicio de preguntas"),
    ("FechaFinal", "Preguntas hasta"),
    ("FechaPubRespuestas", "Respuestas"),
    ("FechaVisitaTerreno", "Visita a terreno"),
    ("FechaEntregaAntecedentes", "Entrega de antecedentes"),
    ("FechaSoporteFisico", "Entrega en soporte físico"),
    ("FechaCierre", "Cierre de ofertas"),
    ("FechaActoAperturaTecnica", "Apertura técnica"),
    ("FechaActoAperturaEconomica", "Apertura económica"),
    ("FechaEstimadaAdjudicacion", "Adjudicación estimada"),
    ("FechaAdjudicacion", "Adjudicación"),
    ("FechaEstimadaFirma", "Firma de contrato estimada"),
)


def notice_dates(head: dict[str, Any], closes_at: datetime | None) -> list[dict[str, Any]]:
    fechas = head.get("Fechas") if isinstance(head.get("Fechas"), dict) else {}
    out: list[dict[str, Any]] = []
    for key, label in DATE_FIELDS:
        at = chile_time(fechas.get(key)) or (closes_at if key == "FechaCierre" else None)
        if at is not None:
            out.append({"key": key, "label": label, "at": at.isoformat()})
    out.sort(key=lambda d: d["at"])
    return out


def _unit(value: Any) -> str | None:
    """`UnidadTiempo*` codes: 1 horas, 2 días, 3 semanas, 4 meses, 5 años."""
    return {"1": "horas", "2": "días", "3": "semanas", "4": "meses", "5": "años"}.get(str(value or "").strip())


def notice_facts(head: dict[str, Any]) -> list[dict[str, str]]:
    """The head's plain facts, labelled, the empty ones left out."""
    buyer = head.get("Comprador") if isinstance(head.get("Comprador"), dict) else {}
    duration = _str(head.get("TiempoDuracionContrato"))
    duration_unit = _unit(head.get("UnidadTiempoDuracionContrato"))
    complaints = _number(head.get("CantidadReclamos"))
    rows = [
        ("Tipo", _str(head.get("Tipo"))),
        ("Estado", _str(head.get("Estado"))),
        ("Unidad compradora", _str(buyer.get("NombreUnidad"))),
        ("Comuna", _str(buyer.get("ComunaUnidad"))),
        ("Moneda", _str(head.get("Moneda"))),
        ("Duración del contrato", f"{duration} {duration_unit}" if duration and duration_unit else duration),
        ("Dirección de entrega", _str(head.get("DireccionEntrega"))),
        ("Etapas de apertura", {"1": "una etapa", "2": "dos etapas"}.get(str(head.get("Etapas") or ""))),
        ("Contrato renovable", {"1": "sí", "0": "no"}.get(str(head.get("EsRenovable") or ""))),
        ("Permite subcontratación", {"1": "sí", "0": "no"}.get(str(head.get("SubContratacion") or ""))),
        ("Reclamos del comprador", f"{complaints} reclamos por pago a proveedores" if complaints is not None else None),
    ]
    return [{"label": label, "value": value} for label, value in rows if value]


DISCOVERED_ONE_SQL = DISCOVERED_SQL.split(" where ")[0] + " where n.id = %(id)s::uuid"


def discovered_notice(cur: Any, notice_id: str, now: datetime) -> dict[str, Any] | None:
    """One notice's page: the list row plus every date, every item and the head's facts."""
    vocabulary = load_vocabulary(cur)
    cur.execute(DISCOVERED_ONE_SQL, {"id": notice_id})
    values = cur.fetchone()
    if values is None:
        return None
    row = dict(zip([c.name for c in cur.description], values, strict=True))
    head = row.get("head") or {}
    match = classify(row.get("title"), head, vocabulary, all_items=True)
    page = discovered_row(row, {**match, "tier": match["tier"] or "laboratorio"})
    page.update(
        tier=match["tier"],
        items=match["items"],
        description=_str(head.get("Descripcion")),
        dates=notice_dates(head, row.get("closes_at")),
        facts=notice_facts(head),
        source_status=row.get("source_status"),
        open=row.get("closes_at") is None or row["closes_at"] > now,
    )
    return page

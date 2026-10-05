"""People the quote emails already name — suggestions for «Crear persona», never records.

Computed per request from the CRM, so a suggestion is gone the moment its address belongs to a
person:

* each quote revision's carrying Gmail message (`crm.quote_revision.origin_source_record_id` →
  `evidence.source_record.payload`: `recipients`, `documents[].filename`);
* the revision's case gives the institution: `crm.opportunity.organization_id`, which the
  deferred trigger `opportunity_requesting_agrees` keeps equal to the case's current confirmed
  requesting institution.

A suggestion is (name, address, institution, how many quotes, last date). The name is the
recipient's display name; otherwise the person a PDF file name names («CN00987-Ana Pérez –
Institución.pdf») when that file name's institution is the case's own and the message has exactly
one recipient without a name; otherwise there is no suggestion. A recipient's own name beats a
PDF's; among spellings, the commonest, then the latest.

Excluded: OrigenLab and Labdelivery addresses; a desk mailbox (`contacto@`, `ventas@`, …) with no
person's name; an address the CRM already holds on a person, as a shared mailbox, or deactivated
(`create-person` refuses those). An address the CRM holds with no owner (`unattributed`,
`individual_owner_unknown`) is suggested, and `create-person` claims that row.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from email.errors import HeaderParseError
from email.header import decode_header, make_header
from email.utils import getaddresses
from typing import Any

from origenlab_api.v2.marketing_audience import address_ref

logger = logging.getLogger(__name__)

#: The same two domains as `quote_crm_import_plan.OWN_DOMAINS` (pinned by a test).
OWN_DOMAINS = frozenset({"origenlab.cl", "labdelivery.cl"})

#: Local parts of a desk, not a person (folded: no accents, lower case).
GENERIC_LOCAL_PARTS = frozenset({
    "contacto", "contact", "ventas", "venta", "info", "informaciones", "administracion", "admin",
    "compras", "adquisiciones", "abastecimiento", "recepcion", "secretaria", "oficina",
    "laboratorio", "lab", "contabilidad", "finanzas", "facturacion", "pagos", "cotizaciones",
    "cotizacion", "soporte", "gerencia", "rrhh", "hola", "noreply", "postmaster", "bodega",
    "logistica", "operaciones", "calidad",
})

#: A word that makes a display name a desk's («Ventas Laboratorio X»), not a person's.
GENERIC_NAME_WORDS = frozenset({
    "ventas", "contacto", "compras", "adquisiciones", "abastecimiento", "administracion",
    "recepcion", "secretaria", "oficina", "laboratorio", "contabilidad", "finanzas",
    "facturacion", "cotizaciones", "soporte", "gerencia", "departamento", "unidad", "info", "admin",
    # An institution's words («Universidad Ficticia», «Mesa de Ayuda»), folded like `fold`.
    "universidad", "hospital", "instituto", "facultad", "centro", "clinica", "biblioteca",
    "municipalidad", "ministerio", "fundacion", "corporacion", "mesa", "equipo", "ayuda", "servicio",
})

#: `crm.contact_point.usage` values that say nobody owns the address yet (DOMAIN.md §2.5).
OWNERLESS_USAGES = frozenset({"unattributed", "individual_owner_unknown"})

_ADDRESS = re.compile(r"^[^@\s,;<>\"']+@[^@\s,;<>\"']+\.[a-z]{2,}$")
_ADDRESS_IN_TEXT = re.compile(r"[A-Za-z0-9._%+'\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_WORD = re.compile(r"^[^\W\d_]+(?:['’.\-][^\W\d_]+)*\.?$")
_FILENAME = re.compile(
    r"^(?P<number>[A-Za-z]{0,4}\s?\d{3,6}(?:-\d{2,4})?)\s*-\s*(?P<person>[^–—\-]+?)\s+[–—-]\s+"
    r"(?P<institution>[^/\\]+?)\.pdf$",
    re.IGNORECASE,
)

SOURCES_SQL = """
    select q.id::text as quote_id, qr.sent_at,
           sr.payload->>'recipients' as recipients, sr.payload->'documents' as documents,
           op.organization_id::text as organization_id
      from crm.quote_revision qr
      join crm.quote q on q.id = qr.quote_id
      join crm.opportunity op on op.id = q.opportunity_id
      join evidence.source_record sr on sr.id = qr.origin_source_record_id
"""

ORGANIZATIONS_SQL = """
    select o.id::text as organization_id, o.name, o.legal_name,
           coalesce(array_agg(d.domain_norm order by d.domain_norm)
                      filter (where d.id is not null), '{}') as domains
      from crm.organization o
      left join crm.organization_domain d on d.organization_id = o.id and d.removed_at is null
     where o.merged_into_organization_id is null and o.status = 'active'
     group by o.id, o.name, o.legal_name
"""

HELD_SQL = """
    select value_norm, person_id is not null as on_person, usage, status
      from crm.contact_point
     where kind = 'email' and value_norm = any(%s)
"""


@dataclass(frozen=True)
class SourceRow:
    quote_id: str
    sent_at: str | None
    recipients: str | None
    filenames: tuple[str, ...]
    organization_id: str | None


@dataclass(frozen=True)
class OrgNames:
    name: str
    legal_name: str | None
    domains: tuple[str, ...]

    def matches(self, institution: str) -> bool:
        """The PDF's institution is this one: its name, legal name or a domain's first label."""
        key = fold(institution)
        keys = {fold(self.name), fold(self.legal_name or "")} | {fold(d.split(".")[0]) for d in self.domains}
        return bool(key) and key in keys - {""}


@dataclass(frozen=True)
class HeldAddress:
    on_person: bool
    usage: str
    status: str

    @property
    def blocks(self) -> bool:
        return self.on_person or self.status != "active" or self.usage not in OWNERLESS_USAGES


def fold(value: str) -> str:
    stripped = "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", stripped.lower())


def parse_recipients(raw: str | None) -> list[tuple[str | None, str]]:
    """(display name or None, lower-cased address) for each address in a recipients header.

    `getaddresses` keeps «"Pérez, Ana" <ana@…>» whole where a split on commas would not; a header
    it cannot parse (spaces instead of commas) still gives its bare addresses, without names.
    """
    text = (raw or "").replace(";", ",")
    out: list[tuple[str | None, str]] = []
    seen: set[str] = set()
    for name, address in getaddresses([text]):
        address = address.strip().lower()
        if _ADDRESS.match(address) and address not in seen:
            seen.add(address)
            out.append((name.strip() or None, address))
    for found in _ADDRESS_IN_TEXT.findall(text):
        address = found.lower().strip(".")
        if _ADDRESS.match(address) and address not in seen:
            seen.add(address)
            out.append((None, address))
    return out


def clean_name(raw: str | None, *, local_part: str, institutions: Iterable[str | None] = ()) -> str | None:
    """A person's name as a display name gives it, or None when it is not one.

    `institutions` are names the display name must not equal (the case institution's name and
    legal name, ignoring case and accents).
    """
    if not raw:
        return None
    name = raw
    if "=?" in name:
        try:
            name = str(make_header(decode_header(name)))
        except (ValueError, LookupError, UnicodeDecodeError, HeaderParseError):
            return None
    name = " ".join(name.replace('"', " ").strip(" '").split())
    if name.count(",") == 1:
        last, first = (part.strip() for part in name.split(","))
        name = f"{first} {last}".strip()
    words = name.split()
    if not 1 <= len(words) <= 6:
        return None
    if any(not _WORD.match(w) for w in words) or any(fold(w) in GENERIC_NAME_WORDS for w in words):
        return None
    if fold(name) == fold(local_part):
        return None
    if fold(name) in {fold(i) for i in institutions if i} - {""}:
        return None
    return name.title() if name.isupper() or name.islower() else name


def person_from_filename(filename: str) -> tuple[str, str] | None:
    """(person, institution) from «CN00987-Ana Pérez – Institución.pdf», or None."""
    base = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    m = _FILENAME.match(base)
    return (m.group("person").strip(), m.group("institution").strip()) if m else None


def _generic(address: str) -> bool:
    return fold(address.partition("@")[0]) in {fold(g) for g in GENERIC_LOCAL_PARTS}


def _filename_person(row: SourceRow, organizations: Mapping[str, OrgNames]) -> str | None:
    org = organizations.get(row.organization_id or "")
    if org is None:
        return None
    institutions = (org.name, org.legal_name)
    found: str | None = None
    for filename in row.filenames:
        parsed = person_from_filename(filename)
        if parsed is None or not org.matches(parsed[1]):
            continue
        name = clean_name(parsed[0], local_part="", institutions=institutions)
        if name is None:
            continue
        if found is not None and found != name:
            return None  # two people named: no guess
        found = name
    return found


def candidate_addresses(rows: Iterable[SourceRow]) -> set[str]:
    return {a for r in rows for _, a in parse_recipients(r.recipients) if a.rpartition("@")[2] not in OWN_DOMAINS}


def compute_person_suggestions(
    rows: Iterable[SourceRow],
    organizations: Mapping[str, OrgNames],
    held: Mapping[str, HeldAddress],
) -> list[dict[str, Any]]:
    names: dict[str, list[tuple[int, str, str]]] = defaultdict(list)  # (2 recipient | 1 pdf, sent, name)
    quotes: dict[str, set[str]] = defaultdict(set)
    orgs: dict[str, Counter[str]] = defaultdict(Counter)
    org_last: dict[tuple[str, str], str] = {}
    last: dict[str, str] = {}
    for row in rows:
        sent = row.sent_at or ""
        # Subdomains are deliberately not excluded (same rule as `quote_crm_import_plan.OWN_DOMAINS`).
        recipients = [
            (raw, address) for raw, address in parse_recipients(row.recipients)
            if address.rpartition("@")[2] not in OWN_DOMAINS
        ]
        org_names = organizations.get(row.organization_id or "")
        institutions = (org_names.name, org_names.legal_name) if org_names else ()
        named = {
            address: clean_name(raw, local_part=address.partition("@")[0], institutions=institutions)
            for raw, address in recipients
        }
        # Held addresses still count as nameless recipients: the PDF may be theirs. Held only
        # suppresses that address's own suggestion.
        nameless = [a for a, n in named.items() if n is None and not _generic(a)]
        from_pdf = _filename_person(row, organizations) if len(nameless) == 1 else None
        for address, name in named.items():
            if address in held and held[address].blocks:
                continue
            if name is not None:
                names[address].append((2, sent, name))
            elif from_pdf is not None and nameless == [address]:
                names[address].append((1, sent, from_pdf))
            quotes[address].add(row.quote_id)
            last[address] = max(last.get(address, ""), sent)
            if row.organization_id in organizations:
                orgs[address][row.organization_id] += 1
                key = (address, row.organization_id)
                org_last[key] = max(org_last.get(key, ""), sent)

    out: list[dict[str, Any]] = []
    for address, candidates in names.items():
        best = max(c[0] for c in candidates)
        pool = [c for c in candidates if c[0] == best]
        spelled = Counter(c[2] for c in pool)
        name = max(pool, key=lambda c: (spelled[c[2]], c[1]))[2]
        org_id = (
            max(orgs[address], key=lambda o: (orgs[address][o], org_last[(address, o)]))
            if orgs[address] else None
        )
        out.append({
            "suggestion_ref": address_ref(address),
            "email": address,
            "display_name": name,
            "name_source": "recipient" if best == 2 else "filename",
            "organization_id": org_id,
            "organization_name": organizations[org_id].name if org_id else None,
            "quotes": len(quotes[address]),
            "last_sent_at": last[address] or None,
            "existing_contact_point": held[address].usage if address in held else None,
        })
    out.sort(key=lambda s: s["email"])
    out.sort(key=lambda s: s["last_sent_at"] or "", reverse=True)
    return out


def _dicts(cur: Any) -> list[dict[str, Any]]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _source_row(r: Mapping[str, Any]) -> SourceRow:
    docs = r.get("documents")
    if isinstance(docs, str):
        docs = json.loads(docs)
    sent = r.get("sent_at")
    return SourceRow(
        quote_id=r["quote_id"],
        sent_at=sent.isoformat() if isinstance(sent, datetime) else sent,
        recipients=r.get("recipients"),
        filenames=tuple(str(d["filename"]) for d in docs or [] if isinstance(d, dict) and d.get("filename")),
        organization_id=r.get("organization_id"),
    )


def read_person_suggestions(cur: Any) -> list[dict[str, Any]]:
    """Every suggestion, from inside a read-only transaction the caller opened."""
    cur.execute(SOURCES_SQL)
    rows = [_source_row(r) for r in _dicts(cur)]
    cur.execute(ORGANIZATIONS_SQL)
    organizations = {
        r["organization_id"]: OrgNames(r["name"], r["legal_name"], tuple(r["domains"] or ()))
        for r in _dicts(cur)
    }
    held: dict[str, HeldAddress] = {}
    addresses = sorted(candidate_addresses(rows))
    if addresses:
        cur.execute(HELD_SQL, (addresses,))
        held = {r["value_norm"]: HeldAddress(bool(r["on_person"]), r["usage"], r["status"]) for r in _dicts(cur)}
    return compute_person_suggestions(rows, organizations, held)


def safe_person_suggestions(cur: Any) -> list[dict[str, Any]]:
    """`read_person_suggestions`, or [] with one warning: suggestions never fail a card.

    Only the exception's class name is logged — a header's text names people.
    """
    try:
        return read_person_suggestions(cur)
    except Exception as exc:  # noqa: BLE001 - one bad sender header must not 500 every institution card
        logger.warning("person_suggestions: no suggestions — %s", exc.__class__.__name__)
        return []

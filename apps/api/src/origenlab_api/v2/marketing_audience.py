"""Equipment-interest audiences: who has shown interest in what, and who may be written to.

Two questions, answered separately and never folded into one number:

1. **Relevance** — which people and which institutions have an evidenced interest in an
   equipment family, brand or model. Every interest carries its *basis* (what happened), its
   *source* (where that is recorded) and its *date*. There is no score. A person or institution
   with no evidence is not "low interest": it is absent, and the UI says «Sin información».
2. **Sending eligibility** — whether an address may receive a campaign at all: suppliers,
   suppressed addresses and domains, and invalid destinations are excluded, and recipients are
   deduplicated by address. Relevance never overrides it; selecting someone does not either.

Where interests come from — the existing structures only, no parallel contact database:

* `crm.opportunity_interest` — recorded on a commercial case (`recorded_in_crm`), confirmed by
  an operator or proposed by the machine.
* Quotation evidence — the Gmail message a historical quotation revision was recorded from
  (`crm.quote_revision.origin_source_record_id`): its subject and attachment names are read
  against the catalogue. Not yet an interest in the CRM; shown as such.
* The case title — a CRM field an operator or the importer wrote; read as text, so it counts as
  not recorded (`recorded_in_crm` false), like quotation evidence.

Person-level and institution-level interests stay distinct: a quotation *sent to an address*
is that contact's interest; the *case's requesting institution* holds the institution's.

Bases, in the order they are decided for a case:

* `purchased` — the case is `won`.
* `requested_quotation` — a quotation revision on the case was sent.
* `requested_information` — an operator-confirmed interest on a case with no quotation sent.
* `inferred_relevance` — a machine-proposed interest or a mention in a case title, with no
  quotation and no purchase behind it.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping

from origenlab_api.v2.equipment_taxonomy import EquipmentTaxonomy

BASES = ("purchased", "requested_quotation", "requested_information", "inferred_relevance")
BASIS_LABEL = {
    "purchased": "Compró",
    "requested_quotation": "Pidió cotización",
    "requested_information": "Pidió información",
    "inferred_relevance": "Relevancia inferida",
}
SOURCE_LABEL = {
    "crm_interest": "Interés registrado en el caso",
    "quotation_evidence": "Evidencia de cotización (correo enviado)",
    "case_title": "Título del caso",
}

#: Reasons an address may not receive a campaign. Order is display order.
EXCLUSION_LABEL = {
    "invalid_address": "Destino no válido",
    "blocked_address": "Dirección bloqueada",
    "blocked_domain": "Dominio bloqueado",
    "cooldown": "En período de espera",
    "supplier": "Proveedor o fabricante",
    "possible_supplier_unreviewed": "Posible proveedor (sin revisar)",
}
#: Facts shown beside eligibility that do not by themselves exclude.
NOTE_LABEL = {
    "prior_contact": "Contacto previo registrado (se respeta el intervalo al congelar la audiencia)",
    "shared_mailbox": "Buzón compartido",
    "no_contact_point": "Dirección sin punto de contacto en el CRM (identidad sin resolver)",
}

_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ADDRESS_IN_TEXT = re.compile(r"[A-Za-z0-9._%+'\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def addresses_in(raw: str | None) -> list[str]:
    """Every address in a recipients header, lower-cased, in order, without duplicates."""
    seen: dict[str, None] = {}
    for a in _ADDRESS_IN_TEXT.findall(raw or ""):
        seen.setdefault(a.lower().strip("."), None)
    return list(seen)


def domain_of(address: str) -> str:
    return address.rpartition("@")[2]


#: The key `address_ref` is computed under. A random per-process key until
#: `configure_address_ref_key` pins one derived from the API's own secret, so refs stay stable
#: across workers and restarts. Never an unkeyed digest: a plain (or truncated) SHA-256 of an
#: address can be recomputed offline from a guessed address and used to correlate it.
_address_ref_key: bytes = secrets.token_bytes(32)


def configure_address_ref_key(secret: str) -> None:
    """Derive the `address_ref` key from an API secret, domain-separated from its other uses."""
    global _address_ref_key
    _address_ref_key = hmac.new(secret.encode("utf-8"), b"origenlab-api/address-ref/v1", hashlib.sha256).digest()


def address_ref(address: str) -> str:
    """An opaque reference to one address that survives redaction: a keyed HMAC scoped to this
    API, so two reads that each carry it can be joined by a `viewer`, who never sees the address
    and cannot recompute the ref from a guessed one."""
    normalized = address.strip().lower().encode("utf-8")
    return hmac.new(_address_ref_key, normalized, hashlib.sha256).hexdigest()[:24]


def destination_key(address: str, contact_point_id: str | None) -> str:
    """A stable id for one destination that survives address redaction in the response: the CRM
    contact point's UUID when there is one, otherwise the keyed `address_ref`."""
    if contact_point_id:
        return f"cp:{contact_point_id}"
    return "addr:" + address_ref(address)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


# --------------------------------------------------------------------------- inputs


@dataclass
class CaseFacts:
    """What the audience needs to know about one commercial case."""

    opportunity_id: str
    title: str
    stage: str
    created_at: Any
    closed_at: Any = None
    organization_id: str | None = None
    organization_name: str | None = None
    first_sent_at: Any = None  # earliest sent quotation revision on the case
    quote_numbers: list[str] = field(default_factory=list)

    @property
    def won(self) -> bool:
        return self.stage == "won"

    @property
    def quoted(self) -> bool:
        return self.first_sent_at is not None


@dataclass
class EligibilityFacts:
    """Everything that can exclude an address, looked up once for the whole candidate set."""

    blocked_addresses: set[str] = field(default_factory=set)
    blocked_domains: set[str] = field(default_factory=set)
    cooldown_addresses: set[str] = field(default_factory=set)
    prior_contact_addresses: set[str] = field(default_factory=set)
    #: W12 display facts per prior-contact address: sources, last contact, campaign.
    prior_contact_details: dict[str, dict[str, Any]] = field(default_factory=dict)
    invalid_addresses: set[str] = field(default_factory=set)
    supplier_domains: set[str] = field(default_factory=set)
    supplier_organization_ids: set[str] = field(default_factory=set)
    candidate_supplier_domains: set[str] = field(default_factory=set)
    contact_points: dict[str, dict[str, Any]] = field(default_factory=dict)  # by address


def eligibility(address: str, facts: EligibilityFacts, organization_id: str | None = None) -> dict[str, Any]:
    """Whether one address may receive a campaign, with every reason it may not."""
    address = address.lower()
    domain = domain_of(address)
    cp = facts.contact_points.get(address)
    reasons: list[str] = []
    if not _ADDRESS.match(address) or address in facts.invalid_addresses:
        reasons.append("invalid_address")
    if address in facts.blocked_addresses:
        reasons.append("blocked_address")
    if domain in facts.blocked_domains:
        reasons.append("blocked_domain")
    if address in facts.cooldown_addresses:
        reasons.append("cooldown")
    org_ids = {organization_id, (cp or {}).get("organization_id")} - {None}
    if domain in facts.supplier_domains or org_ids & facts.supplier_organization_ids:
        reasons.append("supplier")
    elif domain in facts.candidate_supplier_domains:
        reasons.append("possible_supplier_unreviewed")
    notes: list[str] = []
    if address in facts.prior_contact_addresses:
        notes.append("prior_contact")
    if cp is None:
        notes.append("no_contact_point")
    elif cp.get("usage") == "shared_mailbox":
        notes.append("shared_mailbox")
    return {
        "eligible": not reasons,
        "reasons": [{"code": r, "label": EXCLUSION_LABEL[r]} for r in reasons],
        "notes": [{"code": n, "label": NOTE_LABEL[n]} for n in notes],
    }


# --------------------------------------------------------------------------- composition


def _interest(
    match: Mapping[str, Any],
    *,
    basis: str,
    source: str,
    date: Any,
    recorded_in_crm: bool,
    confirmation: str | None,
    case: CaseFacts | None,
    source_record_id: str | None = None,
    interest_id: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    return {
        **match,
        "basis": basis,
        "basis_label": BASIS_LABEL[basis],
        "source": {
            "kind": source,
            "label": SOURCE_LABEL[source],
            "opportunity_id": case.opportunity_id if case else None,
            "case_title": case.title if case else None,
            "quote_numbers": case.quote_numbers if case else [],
            "source_record_id": source_record_id,
            "interest_id": interest_id,
            "detail": detail,
        },
        "date": _iso(date),
        "recorded_in_crm": recorded_in_crm,
        "confirmation": confirmation,
    }


def _most_specific(matches: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One entry per catalogue item; a brand-only match yields to a model of the same brand.

    One record that names "Löser" in its subject and "i Osmometer basic" in its attachment is
    one interest in that model, not two interests.
    """
    unique = {(m["brand_id"], m["model_id"]): dict(m) for m in matches}
    with_model = {b for b, model in unique if model is not None}
    return [m for (b, model), m in unique.items() if model is not None or b not in with_model]


def _case_basis(case: CaseFacts, *, confirmed: bool) -> tuple[str, Any]:
    if case.won:
        return "purchased", case.closed_at
    if case.quoted:
        return "requested_quotation", case.first_sent_at
    if confirmed:
        return "requested_information", case.created_at
    return "inferred_relevance", case.created_at


@dataclass
class AudienceInputs:
    cases: dict[str, CaseFacts]
    #: crm.opportunity_interest rows: interest_id, opportunity_id, confirmation, created_at,
    #: texts (model_text, description, product name/model, manufacturer name), participants.
    interests: list[dict[str, Any]]
    #: quotation evidence rows: source_record_id, opportunity_id, subject, filenames, sent_at,
    #: recipients (raw header).
    quotation_evidence: list[dict[str, Any]]
    eligibility: EligibilityFacts
    #: open identity questions an operator settles in «Revisión».
    review_queue: dict[str, int] = field(default_factory=dict)


def compose(taxonomy: EquipmentTaxonomy, inputs: AudienceInputs) -> dict[str, Any]:
    """People and institutions with evidenced equipment interest, plus coverage figures."""
    persons: dict[str, dict[str, Any]] = {}
    institutions: dict[str, dict[str, Any]] = {}
    coverage = {
        "crm_interest_rows": len(inputs.interests),
        "crm_interests_matched": 0,
        "crm_interests_unmatched": 0,
        "quotation_evidence_records": len(inputs.quotation_evidence),
        "quotation_evidence_with_mentions": 0,
        "case_titles_with_mentions": 0,
        "recipients_without_contact_point": 0,
    }
    brand_linkage: dict[str, dict[str, int]] = defaultdict(lambda: {"crm": 0, "evidence_only": 0})

    def institution(case: CaseFacts) -> dict[str, Any] | None:
        if case.organization_id is None:
            return None
        return institutions.setdefault(
            case.organization_id,
            {
                "organization_id": case.organization_id,
                "name": case.organization_name,
                "interests": [],
                "destinations": {},
            },
        )

    def person(address: str, via_case: CaseFacts | None) -> dict[str, Any]:
        cp = inputs.eligibility.contact_points.get(address)
        key = destination_key(address, cp["id"] if cp else None)
        row = persons.setdefault(
            key,
            {
                "key": key,
                "address": address,
                "address_ref": address_ref(address),
                "contact_point_id": cp["id"] if cp else None,
                "person_id": cp.get("person_id") if cp else None,
                "display_name": cp.get("person_name") if cp else None,
                "organization_ids": [],
                "interests": [],
            },
        )
        if via_case and via_case.organization_id and via_case.organization_id not in row["organization_ids"]:
            row["organization_ids"].append(via_case.organization_id)
        return row

    # 1. interests recorded on a case
    for row in inputs.interests:
        case = inputs.cases.get(row["opportunity_id"])
        if case is None:
            continue
        matches = []
        for text in row.get("texts") or []:
            matches.extend(m.as_dict() for m in taxonomy.match(text))
        matches = _most_specific(matches)
        if not matches:
            coverage["crm_interests_unmatched"] += 1
            continue
        coverage["crm_interests_matched"] += 1
        basis, date = _case_basis(case, confirmed=row["confirmation"] == "confirmed")
        if basis == "inferred_relevance" or basis == "requested_information":
            date = row.get("created_at") or date
        for m in matches:
            brand_linkage[m["brand_id"]]["crm"] += 1
            item = _interest(
                m, basis=basis, source="crm_interest", date=date, recorded_in_crm=True,
                confirmation=row["confirmation"], case=case, interest_id=row["interest_id"],
            )
            inst = institution(case)
            if inst is not None:
                inst["interests"].append(item)
            for p in row.get("participants") or []:
                if p.get("address"):
                    person(p["address"], case)["interests"].append(item)

    # 2. quotation evidence not (yet) recorded as an interest. A record that already is the
    # origin of a recorded interest is shown once, as that interest.
    promoted = {r["origin_source_record_id"] for r in inputs.interests if r.get("origin_source_record_id")}
    coverage["quotation_evidence_already_recorded"] = 0
    for ev in inputs.quotation_evidence:
        if ev["source_record_id"] in promoted:
            coverage["quotation_evidence_already_recorded"] += 1
            continue
        case = inputs.cases.get(ev["opportunity_id"]) if ev.get("opportunity_id") else None
        texts = [ev.get("subject")] + list(ev.get("filenames") or [])
        matches = _most_specific({**m.as_dict(), "_in": t} for t in texts for m in taxonomy.match(t))
        if not matches:
            continue
        coverage["quotation_evidence_with_mentions"] += 1
        basis = "purchased" if case and case.won else "requested_quotation"
        date = case.closed_at if basis == "purchased" and case else ev.get("sent_at")
        recipients = addresses_in(ev.get("recipients"))
        for m in matches:
            brand_linkage[m["brand_id"]]["evidence_only"] += 1
            matched_in = m.pop("_in", None)
            item = _interest(
                m, basis=basis, source="quotation_evidence", date=date, recorded_in_crm=False,
                confirmation=None, case=case, source_record_id=ev["source_record_id"],
                detail=matched_in,  # the subject line or the attachment name that named it
            )
            if case is not None:
                inst = institution(case)
                if inst is not None:
                    inst["interests"].append(item)
            for address in recipients:
                p = person(address, case)
                p["interests"].append(item)
        coverage["recipients_without_contact_point"] += sum(
            1 for a in recipients if a not in inputs.eligibility.contact_points
        )

    # 3. case titles
    for case in inputs.cases.values():
        matches = _most_specific(m.as_dict() for m in taxonomy.match(case.title))
        if not matches:
            continue
        coverage["case_titles_with_mentions"] += 1
        basis, date = _case_basis(case, confirmed=False)
        inst = institution(case)
        for m in matches:
            item = _interest(
                # A title is text an operator or the importer wrote, not an interest linked in
                # the CRM: it is shown, and counted, as not yet recorded.
                m, basis=basis, source="case_title", date=date, recorded_in_crm=False,
                confirmation=None, case=case, detail=case.title,
            )
            if inst is not None:
                inst["interests"].append(item)

    # eligibility, per person and per institution destination
    facts = inputs.eligibility
    for p in persons.values():
        p["eligibility"] = eligibility(p["address"], facts, (p["organization_ids"] or [None])[0])
        p["interests"] = _dedupe_interests(p["interests"])
    for inst in institutions.values():
        inst["interests"] = _dedupe_interests(inst["interests"])
        for p in persons.values():
            if inst["organization_id"] in p["organization_ids"]:
                inst["destinations"][p["key"]] = {
                    "key": p["key"],
                    "address": p["address"],
                    "eligibility": eligibility(p["address"], facts, inst["organization_id"]),
                    "via": "recipient_of_case_quotation",
                }
        for cp in facts.contact_points.values():
            if cp.get("organization_id") == inst["organization_id"]:
                key = destination_key(cp["address"], cp["id"])
                inst["destinations"].setdefault(
                    key,
                    {
                        "key": key,
                        "address": cp["address"],
                        "eligibility": eligibility(cp["address"], facts, inst["organization_id"]),
                        "via": "contact_point_of_institution",
                    },
                )
        inst["destinations"] = list(inst["destinations"].values())

    coverage["brand_linkage"] = {
        b: dict(brand_linkage.get(b, {"crm": 0, "evidence_only": 0})) for b in taxonomy.brands
    }
    coverage["review_queue"] = dict(inputs.review_queue)
    return {
        "persons": sorted(persons.values(), key=lambda p: (-len(p["interests"]), p["address"])),
        "institutions": sorted(
            institutions.values(), key=lambda i: (-len(i["interests"]), i["name"] or "")
        ),
        "coverage": coverage,
    }


def _dedupe_interests(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per (catalogue entry, basis, source record/interest, case), newest first."""
    seen: dict[tuple[Any, ...], dict[str, Any]] = {}
    for it in items:
        s = it["source"]
        key = (
            it["brand_id"], it["model_id"], it["basis"], s["kind"],
            s["opportunity_id"], s["source_record_id"], s["interest_id"],
        )
        seen.setdefault(key, it)
    return sorted(seen.values(), key=lambda i: i["date"] or "", reverse=True)


# --------------------------------------------------------------------------- filtering


@dataclass(frozen=True)
class AudienceFilter:
    family_id: str | None = None
    brand_id: str | None = None
    model_id: str | None = None
    organization_id: str | None = None
    bases: tuple[str, ...] = ()
    #: "crm" — only interests recorded in the CRM; "evidence" — only evidence not yet recorded.
    recorded: str | None = None
    q: str | None = None

    def keeps(self, interest: Mapping[str, Any]) -> bool:
        if self.family_id and interest["family_id"] != self.family_id:
            return False
        if self.brand_id and interest["brand_id"] != self.brand_id:
            return False
        if self.model_id and interest["model_id"] != self.model_id:
            return False
        if self.bases and interest["basis"] not in self.bases:
            return False
        if self.recorded == "crm" and not interest["recorded_in_crm"]:
            return False
        if self.recorded == "evidence" and interest["recorded_in_crm"]:
            return False
        return True


def apply_filter(composed: Mapping[str, Any], flt: AudienceFilter) -> dict[str, Any]:
    """Keep the candidates with at least one matching interest; show only those interests."""
    q = (flt.q or "").strip().lower()

    def narrow(row: dict[str, Any]) -> dict[str, Any] | None:
        kept = [i for i in row["interests"] if flt.keeps(i)]
        if not kept:
            return None
        return {**row, "interests": kept, "other_interest_count": len(row["interests"]) - len(kept)}

    persons = []
    for p in composed["persons"]:
        if flt.organization_id and flt.organization_id not in p["organization_ids"]:
            continue
        if q and q not in (p.get("display_name") or "").lower() and q not in p["address"]:
            continue
        n = narrow(p)
        if n:
            persons.append(n)
    institutions = []
    for inst in composed["institutions"]:
        if flt.organization_id and inst["organization_id"] != flt.organization_id:
            continue
        if q and q not in (inst.get("name") or "").lower():
            continue
        n = narrow(inst)
        if n:
            institutions.append(n)

    # Sending summary: the unique eligible addresses this filter would reach, and why others not.
    unique: dict[str, dict[str, Any]] = {}
    for p in persons:
        unique.setdefault(p["address"], p["eligibility"])
    for inst in institutions:
        for d in inst["destinations"]:
            unique.setdefault(d["address"], d["eligibility"])
    excluded: dict[str, int] = defaultdict(int)
    for elig in unique.values():
        for r in elig["reasons"]:
            excluded[r["code"]] += 1
    return {
        "persons": persons,
        "institutions": institutions,
        "sending": {
            "unique_destinations": len(unique),
            "eligible_unique_destinations": sum(1 for e in unique.values() if e["eligible"]),
            "excluded_by_reason": [
                {"code": c, "label": EXCLUSION_LABEL[c], "count": excluded[c]}
                for c in EXCLUSION_LABEL
                if excluded.get(c)
            ],
        },
    }

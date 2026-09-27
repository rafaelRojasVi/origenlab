"""Audience freeze — a draft campaign's content and audience become one immutable snapshot.

WORKFLOWS.md §W4 step 2 (`freeze_audience`), and nothing after it. A freeze approves nothing,
reserves nothing and sends nothing: there is no send command, no Gmail client and no route to
one anywhere in `apps/api`. What it produces is a record — who would be written to, who would
not and why, on what evidence, with which content and under which policy — that can never be
edited. A changed draft or audience is a new campaign (create-campaign-draft with
`duplicated_from_campaign_id`) and a new freeze; the database refuses to rewrite a frozen one
(`20260927180000_slice5_campaign_audience_freeze.sql`).

One planner, two callers. :func:`plan_freeze` is pure. The preview (a GET on the workspace)
and the command (`freeze-campaign-audience`) both call it on the same inputs; the preview's
``preview_sha256`` is what the operator confirmed, and the command refuses when the audience it
recomputes inside its own transaction digests differently — the operator never freezes an
audience they were not shown.

Relevance and permission are separate, as in :mod:`marketing_audience`:

* **Relevance** — the evidenced interests behind each destination, each with its basis, source
  and date, at the person or the institution level. A canonical line with no evidence is
  «Sin información», never "low".
* **Permission** — every exclusion reason, evaluated completely: invalid destination, address
  or domain block, prior contact, active cooldown, supplier or manufacturer, an unreviewed
  supplier candidate (held, `manual_hold`), a second address of a person already in the
  audience. Malformed destinations cannot be stored at all and are counted instead.

**Ambiguous identities need a person.** A destination with no CRM contact point, linked to more
than one institution, or whose contact point names a different institution from the one it was
reached through is not frozen until an operator decides include or exclude for it, with a note.
The decision is recorded on the row.

**W12 — recontact review** (WORKFLOWS.md §W12), behind its own switch
(``ORIGENLAB_V2_RECONTACT_REVIEW_ENABLED``, default off). Off, a permanent ``prior_contact`` is
an exclusion reason and nothing can lift it (policy ``…v1``). On (policy ``…v2-w12``), a
destination whose *only* reason is ``prior_contact`` is ``recontact_review_required``: the
preview shows its last contact date, campaign or source and destination, and a sales/admin
operator may approve recontact for it, or keep it excluded, with a mandatory note — one row at
a time or as a reviewed bulk selection, always persisted as one decision per recipient. The
command recomputes the audience and revalidates every decision in its own transaction. An
approval lifts ``prior_contact`` and nothing else: never a block or an unsubscribe, a supplier,
a malformed or bounced destination, a second address of the same person or an active cooldown
— a destination carrying any of those is not reviewable and a decision on it is refused.
Without a valid decision ``prior_contact`` stays an exclusion reason. The decision and the W12
policy version are written with the snapshot and sealed by the database
(``20260927200000_slice5_w12_recontact_review.sql``).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Annotated, Any, Iterator, Literal, Mapping
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from origenlab_api.v2.command_core import CommandTransaction
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.equipment_taxonomy import EquipmentTaxonomy, load_taxonomy
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.marketing_audience import (
    BASES,
    AudienceFilter,
    AudienceInputs,
    apply_filter,
    compose,
    eligibility,
)

FREEZE_CAMPAIGN_AUDIENCE = "freeze-campaign-audience"

#: The eligibility rules this module implements. Stored on the campaign and on every row;
#: change it whenever a rule below changes, so a snapshot always says what produced it.
AUDIENCE_POLICY_VERSION = "marketing-audience/2026-09-27.v1"
#: The same rules with W12 on: prior contact is review_required, lifted only by an approval.
AUDIENCE_POLICY_VERSION_W12 = "marketing-audience/2026-09-27.v2-w12"
#: The recontact-review rules, written inside every W12 decision.
RECONTACT_POLICY_VERSION = "recontact-review/2026-09-27.v1"
AUDIENCE_CRITERIA_VERSION = 1


def audience_policy_version(recontact_review: bool) -> str:
    return AUDIENCE_POLICY_VERSION_W12 if recontact_review else AUDIENCE_POLICY_VERSION

#: The six lines OrigenLab sells. The taxonomy must carry each brand on its family
#: (tests/test_v2_audience_freeze.py); a missing interest in a line is «Sin información».
CANONICAL_LINES: tuple[dict[str, str], ...] = (
    {"brand_id": "hielscher", "brand": "Hielscher", "family_id": "sonicacion", "line": "Sonicación"},
    {"brand_id": "ortoalresa", "brand": "Ortoalresa", "family_id": "centrifugacion", "line": "Centrifugación"},
    {"brand_id": "ika", "brand": "IKA", "family_id": "dispersion-homogeneizacion", "line": "Dispersión y homogeneización"},
    {"brand_id": "adam-equipment", "brand": "Adam Equipment", "family_id": "pesaje-humedad", "line": "Pesaje y humedad"},
    {"brand_id": "loeser", "brand": "Löser", "family_id": "osmometria", "line": "Osmometría"},
    {"brand_id": "serva", "brand": "SERVA", "family_id": "electroforesis",
     "line": "Electroforesis, reactivos y consumibles"},
)
SIN_INFORMACION = "Sin información"

#: marketing_audience eligibility codes → the closed vocabulary of WORKFLOWS.md §1.4.
_ELIGIBILITY_REASON = {
    "invalid_address": "invalid_address",
    "blocked_address": "block",
    # An unsubscribe is a marketing block (WORKFLOWS.md §1.6); the note says which kind.
    "unsubscribed": "block",
    "unsubscribe_pending_review": "block",
    "blocked_domain": "block_domain",
    "cooldown": "cooldown",
    "supplier": "policy_supplier",
    "possible_supplier_unreviewed": "manual_hold",
}
REASON_LABEL = {
    "invalid_address": "Destino no válido (rebote o error previo)",
    "block": "Dirección bloqueada",
    "block_domain": "Dominio bloqueado",
    "prior_contact": "Contacto previo registrado",
    "cooldown": "En período de espera",
    "policy_supplier": "Proveedor o fabricante",
    "manual_hold": "Retenido por un operador o sin revisar",
    "already_in_audience": "Otra dirección de la misma persona ya está en la audiencia",
}
NOTE_LABEL = {
    "shared_mailbox": "Buzón compartido",
    "no_contact_point": "Sin punto de contacto en el CRM",
    "multiple_institutions": "Vinculado a más de una institución",
    "institution_mismatch": "El contacto figura en otra institución",
    "identity_reviewed_include": "Identidad revisada: incluir",
    "identity_reviewed_exclude": "Identidad revisada: excluir",
    "possible_supplier_unreviewed": "Posible proveedor sin revisar",
    "operator_excluded": "Excluido por el operador",
    "malformed_address": "Dirección mal formada",
    "recontact_approved": "Recontacto aprobado (W12)",
    "recontact_kept_excluded": "Contacto previo: se mantiene excluido (W12)",
    "recontact_not_reviewed": "Contacto previo sin decisión de recontacto",
    "unsubscribed": "Solicitó la BAJA (supresión permanente; nada la levanta)",
    "unsubscribe_pending_review": "BAJA en revisión: remitente no comprobado, bloqueado hasta revisarla",
}
REVIEW_CODES = ("no_contact_point", "multiple_institutions", "institution_mismatch")

#: Why nothing frozen here can be sent. Shown on the confirmation screen and returned by the
#: command; each one is a missing capability, not a setting.
SEND_BLOCKERS: tuple[dict[str, str], ...] = (
    {
        "code": "unsubscribe_sync_not_automatic",
        "label": "BAJA sin sincronización automática",
        "detail": "Una BAJA se registra como supresión permanente sólo cuando un operador aplica un lote de "
                  "respuestas ya descargadas (W10). Las respuestas de Gmail no se sincronizan automáticamente "
                  "todavía, así que ningún correo que ofrezca «responda BAJA» puede enviarse.",
    },
    {
        "code": "no_send_path",
        "label": "Sin ruta de envío",
        "detail": "Este CRM no tiene comando de envío ni cliente de Gmail; congelar no programa nada.",
    },
    {
        "code": "approval_not_built",
        "label": "Sin aprobación ni ensayo",
        "detail": "La prueba en seco y la aprobación (WORKFLOWS.md §W4, pasos 4–5) no están construidas.",
    },
)

#: `outbound.marketing_contact_refusals` codes — the live send-time contract (WORKFLOWS.md §2
#: clauses 4-6), evaluated against today's contact controls, never the frozen snapshot.
SEND_TIME_REFUSAL_LABEL = {
    "unsubscribe": "Solicitó la BAJA",
    "unsubscribe_pending_review": "BAJA en revisión (bloqueada)",
    "block": "Dirección bloqueada",
    "block_domain": "Dominio bloqueado",
    "cooldown": "En período de espera",
    "prior_contact": "Contacto previo sin aprobación W12",
    "not_snapshotted": "Excluido al congelar",
    "recipient_unknown": "Destinatario desconocido",
}

#: The database's address shape (20260908120200): a destination that fails it cannot be a row.
_DB_ADDRESS = re.compile(r'^[^@\s<>,;"]+@[^@\s<>,;"]+\.[^@\s<>,;"]+$')
#: Stricter than the database: dot-atom local part, LDH domain labels. A destination that the
#: database could store but that fails this is frozen as `invalid_address` (note
#: `malformed_address`), never included.
_WELL_FORMED = re.compile(
    r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$"
)
MAX_REVIEW_DECISIONS = 5000


def _sha256(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- content


class _TextOf(HTMLParser):
    _BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table", "td"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("style", "title", "head"):
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("style", "title", "head") and self._skip:
            self._skip -= 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def text_from_html(html: str) -> str:
    """The plain-text part frozen beside the HTML: its text, one blank line between blocks."""
    parser = _TextOf()
    parser.feed(html)
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in "".join(parser.parts).splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def content_sha256(subject: str, preheader: str | None, body_text: str, body_html: str | None) -> str:
    """`outbound.campaign.content_sha256`: subject \\0 preheader \\0 body_text \\0 body_html."""
    canonical = "\0".join([subject, preheader or "", body_text, body_html or ""])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- request


class FreezeCriteria(BaseModel):
    """Which evidenced interests define the audience. Stored verbatim as audience criteria v1."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    family_id: str | None = None
    brand_id: str | None = None
    model_id: str | None = None
    organization_id: UUID | None = None
    bases: tuple[str, ...] = ()
    recorded: Literal["crm", "evidence"] | None = None
    q: Annotated[str | None, Field(max_length=120)] = None
    #: persons — people with their own evidenced interest; institutions — the destinations of
    #: institutions with an evidenced interest; both — the union, deduplicated by address.
    scope: Literal["persons", "institutions", "both"] = "both"

    @field_validator("family_id", "brand_id", "model_id", "q", mode="before")
    @classmethod
    def _blank_is_absent(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("bases")
    @classmethod
    def _known_bases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = [b for b in value if b not in BASES]
        if unknown:
            raise ValueError(f"unknown basis: {', '.join(unknown)}")
        return tuple(sorted(set(value)))

    def check(self, taxonomy: EquipmentTaxonomy) -> None:
        for value, known, name in (
            (self.family_id, taxonomy.families, "family_id"),
            (self.brand_id, taxonomy.brands, "brand_id"),
            (self.model_id, taxonomy.models, "model_id"),
        ):
            if value is not None and value not in known:
                raise CommandRefused(422, "unknown_catalogue_entry", f"unknown {name}")

    def audience_filter(self) -> AudienceFilter:
        return AudienceFilter(
            family_id=self.family_id, brand_id=self.brand_id, model_id=self.model_id,
            organization_id=str(self.organization_id) if self.organization_id else None,
            bases=self.bases, recorded=self.recorded, q=self.q,
        )

    def canonical(self) -> dict[str, Any]:
        return {"version": AUDIENCE_CRITERIA_VERSION, **self.model_dump(mode="json")}


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: Annotated[str, Field(min_length=3, max_length=80)]
    decision: Literal["include", "exclude"]
    note: Annotated[str, Field(min_length=3, max_length=500)]


class RecontactDecision(BaseModel):
    """W12: one recipient's recontact decision. A bulk selection is sent as one of these per
    recipient, each marked ``bulk`` and each with the note the operator wrote for the batch."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: Annotated[str, Field(min_length=3, max_length=80)]
    decision: Literal["approve", "keep_excluded"]
    note: Annotated[str, Field(min_length=3, max_length=500)]
    mode: Literal["individual", "bulk"] = "individual"


class FreezeAudienceBody(BaseModel):
    """What the confirmation screen sends. Every field is something the operator saw."""

    model_config = ConfigDict(extra="forbid")

    campaign_id: UUID
    expected_version: Annotated[int, Field(ge=1)]
    expected_preview_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    criteria: FreezeCriteria
    review_decisions: Annotated[list[ReviewDecision], Field(max_length=MAX_REVIEW_DECISIONS)] = []
    excluded_keys: Annotated[list[Annotated[str, Field(min_length=3, max_length=80)]],
                             Field(max_length=MAX_REVIEW_DECISIONS)] = []
    recontact_decisions: Annotated[list[RecontactDecision], Field(max_length=MAX_REVIEW_DECISIONS)] = []
    #: The final confirmation. There is no default and no other value.
    confirmed: Literal[True]


# --------------------------------------------------------------------------- planning


@dataclass
class _Candidate:
    key: str
    address: str
    contact_point: Mapping[str, Any] | None
    organization_ids: list[str] = field(default_factory=list)
    via: list[str] = field(default_factory=list)
    evidence: dict[tuple[Any, ...], dict[str, Any]] = field(default_factory=dict)


def _evidence_item(interest: Mapping[str, Any], level: str, organization_id: str | None) -> dict[str, Any]:
    s = interest["source"]
    return {
        "level": level,
        "organization_id": organization_id,
        "brand_id": interest["brand_id"],
        "family_id": interest["family_id"],
        "model_id": interest["model_id"],
        "basis": interest["basis"],
        "source_kind": s["kind"],
        "opportunity_id": s["opportunity_id"],
        "source_record_id": s["source_record_id"],
        "interest_id": s["interest_id"],
        "recorded_in_crm": interest["recorded_in_crm"],
        "observed_at": interest["date"],
    }


def _evidence_key(e: Mapping[str, Any]) -> tuple[Any, ...]:
    return (e["level"], e["organization_id"], e["brand_id"], e["model_id"], e["basis"], e["source_kind"],
            e["opportunity_id"], e["source_record_id"], e["interest_id"])


def _collect(filtered: Mapping[str, Any], inputs: AudienceInputs, scope: str) -> dict[str, _Candidate]:
    cps = inputs.eligibility.contact_points
    out: dict[str, _Candidate] = {}

    def get(address: str, key: str) -> _Candidate:
        return out.setdefault(address, _Candidate(key=key, address=address, contact_point=cps.get(address)))

    if scope in ("persons", "both"):
        for p in filtered["persons"]:
            c = get(p["address"], p["key"])
            if "person_interest" not in c.via:
                c.via.append("person_interest")
            for org in p["organization_ids"]:
                if org not in c.organization_ids:
                    c.organization_ids.append(org)
            for i in p["interests"]:
                e = _evidence_item(i, "person", None)
                c.evidence.setdefault(_evidence_key(e), e)
    if scope in ("institutions", "both"):
        for inst in filtered["institutions"]:
            org = inst["organization_id"]
            for d in inst["destinations"]:
                c = get(d["address"], d["key"])
                if d["via"] not in c.via:
                    c.via.append(d["via"])
                if org not in c.organization_ids:
                    c.organization_ids.append(org)
                for i in inst["interests"]:
                    e = _evidence_item(i, "institution", org)
                    c.evidence.setdefault(_evidence_key(e), e)
    return out


def _lines(evidence: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each canonical line: its latest evidence, or «Sin información»."""
    out = []
    for line in CANONICAL_LINES:
        hits = [e for e in evidence if e["brand_id"] == line["brand_id"]]
        latest = max((e["observed_at"] or "" for e in hits), default="") or None
        out.append({
            "brand_id": line["brand_id"], "line": line["line"], "brand": line["brand"],
            "status": "evidenced" if hits else "sin_informacion",
            "label": f"{len(hits)} evidencia(s)" if hits else SIN_INFORMACION,
            "bases": sorted({e["basis"] for e in hits}),
            "latest_observed_at": latest,
        })
    return out


def _prior_contact(address: str, facts: Any) -> dict[str, Any]:
    """What the operator is shown about a prior contact: last contact, campaign or source, destination.

    A historical fact loaded from V1 often has no date; it is shown as unknown, never guessed.
    """
    d = facts.prior_contact_details.get(address) or {}
    return {
        "destination": address,
        "last_contact_at": d.get("last_contact_at"),
        "campaign_id": d.get("campaign_id"),
        "campaign_name": d.get("campaign_name"),
        "sources": d.get("sources", []),
    }


def plan_freeze(
    taxonomy: EquipmentTaxonomy,
    inputs: AudienceInputs,
    campaign: Mapping[str, Any],
    criteria: FreezeCriteria,
    *,
    decisions: Mapping[str, ReviewDecision] | None = None,
    excluded_keys: frozenset[str] = frozenset(),
    recontact_decisions: Mapping[str, RecontactDecision] | None = None,
    recontact_review: bool = False,
) -> dict[str, Any]:
    """The snapshot a freeze would write, and everything that would stop it.

    Without `decisions` this is the preview: `review_required` lists the destinations still
    awaiting an operator. `preview_sha256` never depends on decisions or exclusions — those are
    what the operator adds on top of what they were shown. `recontact_review` is the W12
    switch; with it off, any recontact decision is a problem, never silently dropped.
    """
    criteria.check(taxonomy)
    decisions = decisions or {}
    recontact_decisions = recontact_decisions or {}
    policy_version = audience_policy_version(recontact_review)
    composed = compose(taxonomy, inputs)
    filtered = apply_filter(composed, criteria.audience_filter())
    candidates = _collect(filtered, inputs, criteria.scope)
    facts = inputs.eligibility
    org_names = {c.organization_id: c.organization_name for c in inputs.cases.values() if c.organization_id}

    malformed = sorted(a for a in candidates if not _DB_ADDRESS.match(a) or a != a.lower())
    rows: list[dict[str, Any]] = []
    for address in sorted(set(candidates) - set(malformed)):
        c = candidates[address]
        reasons: set[str] = set()
        notes: set[str] = set()
        for org in c.organization_ids or [None]:
            elig = eligibility(address, facts, org)
            reasons |= {_ELIGIBILITY_REASON[r["code"]] for r in elig["reasons"]}
            notes |= {n["code"] for n in elig["notes"] if n["code"] != "prior_contact"}
            if any(r["code"] == "possible_supplier_unreviewed" for r in elig["reasons"]):
                notes.add("possible_supplier_unreviewed")
            notes |= {r["code"] for r in elig["reasons"]
                      if r["code"] in ("unsubscribed", "unsubscribe_pending_review")}
        if address in facts.prior_contact_addresses:
            reasons.add("prior_contact")
        if not _WELL_FORMED.match(address):
            reasons.add("invalid_address")
            notes.add("malformed_address")
        if "invalid_address" in reasons:
            reasons = {"invalid_address"}  # the one terminal reason (WORKFLOWS.md §1.4)
        # W12: reviewable only when prior contact is the one thing keeping it out.
        w12_candidate = recontact_review and reasons == {"prior_contact"}
        cp = c.contact_point
        review_codes = []
        if cp is None:
            review_codes.append("no_contact_point")
        if len(c.organization_ids) > 1:
            review_codes.append("multiple_institutions")
        if cp and cp.get("organization_id") and c.organization_ids and cp["organization_id"] not in c.organization_ids:
            review_codes.append("institution_mismatch")
        notes |= {code for code in review_codes if code in NOTE_LABEL}
        evidence = sorted(c.evidence.values(), key=lambda e: (e["observed_at"] or "", _evidence_key(e)), reverse=True)
        rows.append({
            "key": c.key,
            "address": address,
            "contact_point_id": cp["id"] if cp else None,
            "person_id": cp.get("person_id") if cp else None,
            "display_name": cp.get("person_name") if cp else None,
            "organization_id": (cp or {}).get("organization_id") or (c.organization_ids[0] if c.organization_ids else None),
            "organizations": [{"organization_id": o, "name": org_names.get(o)} for o in c.organization_ids],
            "via": sorted(c.via),
            "evidence": evidence,
            "lines": _lines(evidence),
            "relevance": "evidenced" if evidence else "sin_informacion",
            "evidence_observed_at": max((e["observed_at"] or "" for e in evidence), default="") or None,
            "base_reasons": sorted(reasons),
            "notes": sorted(notes),
            # Only a destination that could be included needs a person to decide.
            "review_codes": review_codes if not reasons or w12_candidate else [],
            "recontact_review_required": w12_candidate,
            "prior_contact": (_prior_contact(address, facts) if "prior_contact" in reasons else None),
        })

    # A second address of a person already in the audience (the per-person dedup rule).
    by_person: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["person_id"]:
            by_person[r["person_id"]].append(r)
    for group in by_person.values():
        if len(group) < 2:
            continue
        keep = next((r for r in group if not r["base_reasons"]), group[0])
        for r in group:
            if r is not keep:
                r["base_reasons"] = sorted(set(r["base_reasons"]) | {"already_in_audience"})
                r["review_codes"] = []
                r["recontact_review_required"] = False  # W12 never lifts a duplicate

    preview_sha256 = _sha256({
        "campaign_id": campaign["id"], "campaign_version": campaign["version"],
        "policy_version": policy_version, "criteria": criteria.canonical(),
        "rows": [{k: r[k] for k in ("key", "address", "base_reasons", "notes", "review_codes")}
                 | {"evidence": [_evidence_key(e) for e in r["evidence"]]}
                 # With W12 on, what the operator is shown about a prior contact is part of what
                 # they confirmed: a new send or a new source moves the fingerprint.
                 | ({"recontact": [r["recontact_review_required"], r["prior_contact"]]} if recontact_review else {})
                 for r in rows],
        "malformed": malformed,
    })

    known_keys = {r["key"] for r in rows}
    unknown = sorted((set(decisions) | set(excluded_keys) | set(recontact_decisions)) - known_keys)
    pending = []
    not_reviewable = []
    for r in rows:
        reasons = set(r["base_reasons"])
        notes = set(r["notes"])
        review = None
        recontact = None
        rd = recontact_decisions.get(r["key"])
        if rd is not None and recontact_review:
            if not r["recontact_review_required"]:
                not_reviewable.append(r["key"])
            else:
                recontact = {"decision": rd.decision, "note": rd.note, "mode": rd.mode,
                             "policy_version": RECONTACT_POLICY_VERSION, "prior_contact": r["prior_contact"]}
                if rd.decision == "approve":
                    reasons.discard("prior_contact")
                    notes.add("recontact_approved")
                else:
                    notes.add("recontact_kept_excluded")
        elif r["recontact_review_required"]:
            notes.add("recontact_not_reviewed")
        if r["review_codes"]:
            d = decisions.get(r["key"])
            if d is None:
                # Pending only where the identity decides inclusion: a prior contact nobody
                # approved stays excluded whatever its identity.
                if not reasons:
                    pending.append(r["key"])
            else:
                review = {"codes": r["review_codes"], "decision": d.decision, "note": d.note}
                notes.add(f"identity_reviewed_{d.decision}")
                if d.decision == "exclude":
                    reasons.add("manual_hold")
        if r["key"] in excluded_keys and "invalid_address" not in reasons:
            reasons.add("manual_hold")
            notes.add("operator_excluded")
        r["frozen_reasons"] = sorted(reasons)
        r["frozen_notes"] = sorted(notes)
        r["inclusion"] = "excluded" if reasons else "included"
        r["identity_review"] = review
        r["recontact_review"] = recontact
        r["reasons"] = [{"code": x, "label": REASON_LABEL[x]} for x in r["frozen_reasons"]]
        r["note_labels"] = [{"code": x, "label": NOTE_LABEL[x]} for x in r["frozen_notes"]]

    included = [r for r in rows if r["inclusion"] == "included"]
    by_reason: dict[str, int] = defaultdict(int)
    for r in rows:
        for x in r["frozen_reasons"]:
            by_reason[x] += 1

    has_html = bool(campaign.get("body_html"))
    body_text = text_from_html(campaign["body_html"]) if has_html else ""
    content = {
        "subject": campaign.get("subject"),
        "preheader": campaign.get("preheader"),
        "has_html": has_html,
        "body_text_chars": len(body_text),
        "content_sha256": (content_sha256(campaign["subject"], campaign.get("preheader"), body_text, campaign["body_html"])
                           if campaign.get("subject") and has_html and body_text else None),
        "campaign_version": campaign["version"],
        "promises_baja": bool(re.search(r"\bbaja\b", (campaign.get("body_html") or "").lower())),
    }
    problems: list[dict[str, str]] = []
    if campaign["status"] != "draft":
        problems.append({"code": "campaign_not_draft",
                         "message": f"la campaña está en '{campaign['status']}'; sólo un borrador se congela"})
    if not campaign.get("subject"):
        problems.append({"code": "no_subject", "message": "el borrador no tiene asunto"})
    if not has_html or not body_text:
        problems.append({"code": "no_body", "message": "el borrador no tiene cuerpo HTML con texto"})
    if pending:
        problems.append({"code": "review_pending",
                         "message": f"{len(pending)} destino(s) con identidad ambigua esperan una decisión"})
    if recontact_decisions and not recontact_review:
        problems.append({"code": "recontact_review_disabled",
                         "message": "la revisión de recontacto (W12) no está habilitada en esta API; "
                                    "un contacto previo sigue excluido"})
    if not_reviewable:
        problems.append({"code": "recontact_not_eligible",
                         "message": f"{len(not_reviewable)} decisión(es) de recontacto sobre destinos que no la admiten: "
                                    "W12 sólo levanta un contacto previo, nunca un bloqueo, una baja, un proveedor, "
                                    "un destino inválido, un duplicado o un período de espera"})
    if unknown:
        problems.append({"code": "unknown_keys",
                         "message": f"{len(unknown)} decisión(es) o exclusión(es) no corresponden a esta audiencia"})
    if not included:
        problems.append({"code": "nobody_included", "message": "ningún destino quedaría incluido"})
    if len(included) > int(campaign["max_sends"]):
        problems.append({"code": "exceeds_max_sends",
                         "message": f"{len(included)} incluidos superan el límite de {campaign['max_sends']} envíos"})

    recontact_rows = [r for r in rows if r["recontact_review_required"]]
    return {
        "campaign_id": campaign["id"],
        "policy_version": policy_version,
        "criteria": criteria.canonical(),
        "preview_sha256": preview_sha256,
        "content": content,
        "body_text": body_text,
        "rows": rows,
        "review_required": [r["key"] for r in rows if r["review_codes"]],
        "review_pending": pending,
        "recontact_review": {
            "enabled": recontact_review,
            "policy_version": RECONTACT_POLICY_VERSION if recontact_review else None,
            "required": [r["key"] for r in recontact_rows],
        },
        "malformed_count": len(malformed),
        "counts": {
            "candidates": len(rows) + len(malformed),
            "rows": len(rows),
            "included": len(included),
            "excluded": len(rows) - len(included),
            "malformed_not_stored": len(malformed),
            "review_required": sum(1 for r in rows if r["review_codes"]),
            "recontact_review_required": len(recontact_rows),
            "recontact_approved": sum(1 for r in recontact_rows if (r["recontact_review"] or {}).get("decision") == "approve"),
            "recontact_kept_excluded": sum(1 for r in recontact_rows
                                           if (r["recontact_review"] or {}).get("decision") == "keep_excluded"),
            "recontact_not_reviewed": sum(1 for r in recontact_rows if r["recontact_review"] is None),
            "excluded_by_reason": [{"code": c, "label": REASON_LABEL[c], "count": n} for c, n in sorted(by_reason.items())],
            "included_by_line": [
                {"brand_id": ln["brand_id"], "line": ln["line"], "brand": ln["brand"],
                 "evidenced": sum(1 for r in included if any(e["brand_id"] == ln["brand_id"] for e in r["evidence"])),
                 "sin_informacion": sum(1 for r in included if not any(e["brand_id"] == ln["brand_id"] for e in r["evidence"]))}
                for ln in CANONICAL_LINES
            ],
        },
        "problems": problems,
        "send_blockers": list(SEND_BLOCKERS),
    }


def _snapshot_row(r: Mapping[str, Any]) -> dict[str, Any]:
    """The part of a planned row that is written, in the form `audience_sha256` covers."""
    return {
        "address_norm": r["address"],
        "contact_point_id": r["contact_point_id"],
        "person_id": r["person_id"],
        "organization_id": r["organization_id"],
        "frozen_inclusion": r["inclusion"],
        "frozen_reasons": r["frozen_reasons"],
        "frozen_notes": r["frozen_notes"],
        "relevance": r["relevance"],
        "interest_evidence": r["evidence"],
        "evidence_observed_at": r["evidence_observed_at"],
        "identity_review": r["identity_review"],
        "recontact_review": r["recontact_review"],
    }


# --------------------------------------------------------------------------- the command


class V2AudienceFreezeRepository(CommandTransaction):
    """`freeze-campaign-audience`. Writes `outbound.campaign`, `outbound.campaign_recipient`
    and the audit stream, in one repeatable-read transaction."""

    def __init__(self, *args: Any, recontact_review_enabled: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        #: W12 — the same switch the preview reads; a mismatch moves the policy version and
        #: therefore the fingerprint, so a preview taken under the other setting is refused.
        self._recontact_review = recontact_review_enabled

    @contextmanager
    def _write(self) -> Iterator[Any]:
        # One snapshot for every read the plan makes: the receipt claim is the transaction's
        # first statement, so the isolation level is set before it.
        with self._connect(self._dsn, autocommit=False) as conn:
            with conn.cursor() as cur:
                cur.execute("set transaction isolation level repeatable read")
                cur.execute(f"set local statement_timeout = {int(self._statement_timeout_ms)}")
                try:
                    yield cur
                except BaseException:
                    conn.rollback()
                    raise
                conn.commit()

    def _freeze(self, cur: Any, operator: OperatorIdentity, f: dict[str, Any], receipt_id: str) -> dict[str, Any]:
        from origenlab_api.v2.crm_workspace import read_marketing_audience_inputs

        cur.execute(
            """
            select id::text as id, status, version, name, subject, preheader, body_html,
                   max_sends, recontact_interval_days
              from outbound.campaign where id = %s for update
            """,
            (f["campaign_id"],),
        )
        campaign = self._row(cur)
        if campaign is None:
            raise CommandRefused(404, "campaign_not_found", "no such campaign")
        if campaign["status"] != "draft":
            raise CommandRefused(
                409, "campaign_not_draft",
                f"the campaign is '{campaign['status']}'; a frozen campaign is never re-frozen — "
                "duplicate it as a new draft and freeze that",
            )
        if campaign["version"] != f["expected_version"]:
            raise CommandRefused(
                409, "stale_version",
                f"the draft is at version {campaign['version']}, not {f['expected_version']}; "
                "it changed after the preview — review the audience again",
            )
        criteria = FreezeCriteria(**f["criteria"])
        decisions = {d["key"]: ReviewDecision(**d) for d in f["review_decisions"]}
        if len(decisions) != len(f["review_decisions"]):
            raise CommandRefused(422, "duplicate_decision", "one decision per destination")
        recontact = {d["key"]: RecontactDecision(**d) for d in f.get("recontact_decisions", [])}
        if len(recontact) != len(f.get("recontact_decisions", [])):
            raise CommandRefused(422, "duplicate_decision", "one recontact decision per destination")
        if recontact and not self._recontact_review:
            raise CommandRefused(409, "recontact_review_disabled",
                                 "recontact review (W12) is not enabled on this API; prior contact stays excluded")
        # Recomputed from this transaction's own snapshot: every decision is revalidated
        # against current blocks, cooldowns, suppliers, bounces and duplicates.
        plan = plan_freeze(
            load_taxonomy(), read_marketing_audience_inputs(cur), campaign, criteria,
            decisions=decisions, excluded_keys=frozenset(f["excluded_keys"]),
            recontact_decisions=recontact, recontact_review=self._recontact_review,
        )
        if plan["preview_sha256"] != f["expected_preview_sha256"]:
            raise CommandRefused(
                409, "audience_changed",
                "the audience changed after the preview (new evidence, a new block, a new contact point); "
                "review it again before freezing",
            )
        if plan["problems"]:
            first = plan["problems"][0]
            raise CommandRefused(409, first["code"], "; ".join(p["message"] for p in plan["problems"]))

        snapshot = [_snapshot_row(r) for r in plan["rows"]]
        audience_sha256 = _sha256(snapshot)
        content = plan["content"]
        policy_version = plan["policy_version"]
        criteria_doc = {
            **plan["criteria"],
            "policy_version": policy_version,
            "preview_sha256": plan["preview_sha256"],
            "counts": plan["counts"],
            "review_decisions": len(decisions),
            "operator_excluded": len(f["excluded_keys"]),
            "content_sha256_form": "subject\\0preheader\\0body_text\\0body_html",
            "recontact_review": {**plan["recontact_review"], "required": len(plan["recontact_review"]["required"]),
                                 "decisions": len(recontact)},
        }
        cur.execute(
            """
            update outbound.campaign
               set body_text = %s, content_sha256 = %s, content_frozen_at = now(),
                   audience_criteria = %s::jsonb, audience_criteria_version = %s, audience_frozen_at = now(),
                   audience_policy_version = %s, audience_sha256 = %s,
                   version = version + 1, updated_at = now()
             where id = %s and version = %s and status = 'draft'
            returning version, now()::text as frozen_at
            """,
            (plan["body_text"], content["content_sha256"], json.dumps(criteria_doc, ensure_ascii=False),
             AUDIENCE_CRITERIA_VERSION, policy_version, audience_sha256,
             campaign["id"], campaign["version"]),
        )
        row = self._row(cur)
        assert row is not None  # noqa: S101 - the row is locked and its version checked
        frozen_version = row["version"]
        cur.executemany(
            """
            insert into outbound.campaign_recipient
                (campaign_id, address_norm, contact_point_id, person_id, organization_id,
                 state, exclusion_reasons,
                 frozen_at, frozen_inclusion, frozen_reasons, frozen_notes, relevance,
                 interest_evidence, evidence_observed_at, identity_review, recontact_review,
                 recontact_override_by_operator_id, recontact_override_reason, recontact_override_at,
                 campaign_version, content_sha256, policy_version)
            values (%s, %s, %s, %s, %s, %s, %s, now(), %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s::jsonb,
                    %s, %s, case when %s then now() end, %s, %s, %s)
            """,
            [
                (campaign["id"], s["address_norm"], s["contact_point_id"], s["person_id"], s["organization_id"],
                 "snapshotted" if s["frozen_inclusion"] == "included" else "excluded", s["frozen_reasons"],
                 s["frozen_inclusion"], s["frozen_reasons"], s["frozen_notes"], s["relevance"],
                 json.dumps(s["interest_evidence"], ensure_ascii=False), s["evidence_observed_at"],
                 json.dumps(s["identity_review"] and {**s["identity_review"], "operator_id": operator.operator_id},
                            ensure_ascii=False) if s["identity_review"] else None,
                 json.dumps({**s["recontact_review"], "operator_id": operator.operator_id}, ensure_ascii=False)
                 if s["recontact_review"] else None,
                 *_override_triple(s, operator),
                 frozen_version, content["content_sha256"], policy_version)
                for s in snapshot
            ],
        )
        cur.execute(
            "update outbound.campaign set status = 'audience_frozen', updated_at = now() where id = %s",
            (campaign["id"],),
        )
        counts = plan["counts"]
        recontact_summary = {
            "enabled": self._recontact_review,
            "policy_version": plan["recontact_review"]["policy_version"],
            "review_required": counts["recontact_review_required"],
            "approved": counts["recontact_approved"],
            "kept_excluded": counts["recontact_kept_excluded"],
            "not_reviewed": counts["recontact_not_reviewed"],
            "bulk": sum(1 for d in recontact.values() if d.mode == "bulk"),
        }
        self._append_event(
            cur,
            aggregate_kind="campaign",
            aggregate_id=campaign["id"],
            event_type="campaign.audience_frozen",
            payload={
                "from_version": campaign["version"],
                "to_version": frozen_version,
                "policy_version": policy_version,
                "criteria": plan["criteria"],
                "preview_sha256": plan["preview_sha256"],
                "content_sha256": content["content_sha256"],
                "audience_sha256": audience_sha256,
                "counts": {k: counts[k] for k in ("rows", "included", "excluded", "malformed_not_stored", "review_required")},
                "excluded_by_reason": {x["code"]: x["count"] for x in counts["excluded_by_reason"]},
                "review_decisions": {"include": sum(1 for d in decisions.values() if d.decision == "include"),
                                     "exclude": sum(1 for d in decisions.values() if d.decision == "exclude")},
                "operator_excluded": len(f["excluded_keys"]),
                "recontact_review": recontact_summary,
                "send_blockers": [b["code"] for b in SEND_BLOCKERS],
            },
            operator=operator,
            receipt_id=receipt_id,
        )
        # W12: one audit event per approved recipient, bulk or not (WORKFLOWS.md §W12 step 1).
        cur.execute(
            """
            select id::text as recipient_id, recontact_review
              from outbound.campaign_recipient
             where campaign_id = %s and recontact_override_at is not null
             order by address_norm
            """,
            (campaign["id"],),
        )
        for recipient_id, review in cur.fetchall():
            self._append_event(
                cur,
                aggregate_kind="campaign",
                aggregate_id=campaign["id"],
                event_type="campaign.override_granted",
                payload={
                    "recipient_id": recipient_id,
                    "decision": "approve",
                    "mode": review["mode"],
                    "note": review["note"],
                    "recontact_policy_version": review["policy_version"],
                    "audience_policy_version": policy_version,
                    "lifted": ["prior_contact"],
                    "prior_contact": {k: review["prior_contact"].get(k)
                                      for k in ("last_contact_at", "campaign_id", "campaign_name", "sources")},
                    "campaign_version": frozen_version,
                },
                operator=operator,
                receipt_id=receipt_id,
            )
        cur.execute("select current_database()")
        return {
            "campaign_id": campaign["id"],
            "status": "audience_frozen",
            "version": frozen_version,
            "frozen_at": row["frozen_at"],
            "policy_version": policy_version,
            "content_sha256": content["content_sha256"],
            "audience_sha256": audience_sha256,
            "counts": {k: counts[k] for k in ("rows", "included", "excluded", "malformed_not_stored")},
            "recontact_review": recontact_summary,
            "storage": {"tables": ["outbound.campaign", "outbound.campaign_recipient"], "database": cur.fetchone()[0]},
            "send_blockers": list(SEND_BLOCKERS),
        }

    _HANDLERS = {FREEZE_CAMPAIGN_AUDIENCE: _freeze}


def _override_triple(s: Mapping[str, Any], operator: OperatorIdentity) -> tuple[Any, Any, bool]:
    """The W12 override triple for one snapshot row: set exactly when the decision is approve."""
    review = s["recontact_review"]
    if review and review["decision"] == "approve":
        return operator.operator_id, review["note"], True
    return None, None, False


def freeze_fields(body: FreezeAudienceBody) -> dict[str, Any]:
    fields = body.model_dump(mode="json")
    fields["command"] = FREEZE_CAMPAIGN_AUDIENCE
    return fields

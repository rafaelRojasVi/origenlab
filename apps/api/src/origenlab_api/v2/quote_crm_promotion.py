"""What promoting a recorded quotation decision into the CRM would take — computed, never done.

A document decision in `document_decisions.jsonl` says *this PDF is an OrigenLab customer
quotation, its number is the one it prints, and it belongs to this planned opportunity*. It
carries `applied: false`: nothing in `crm.*` exists for it. This module answers, per decision,
what a later load would have to write, through which command, and what stops it today.

It applies nothing. It opens no file and no connection: the caller hands it the ledger rows,
the identity report, the staged source records and a read-only snapshot of the CRM, and it
returns a plan. `simulate_apply` folds a plan into a copy of that snapshot so a second plan can
prove the steps are idempotent; it mutates nothing it was given.

**What the plan is made of.** Every step is one of the existing V2 commands
(`open_commercial_case`, `add_case_organization`, `advance_case_stage`, `link_case_evidence`,
`create_organization`, `confirm_organization`, `attach_contact_address`,
`confirm_person_from_evidence`) or the one write that has no command yet, the quote itself.
Each step carries a deterministic idempotency key: re-running a load must hand the database the
same key, and `platform.command_receipt` then answers "already done" instead of writing twice.

**What stays a suggestion.** The opportunity, the organization and the contact are always
suggestions (P13 and `quote_apply_candidates`): an exact name match to a machine-proposed
organization is a candidate for an operator, never a link. The requesting institution is a human
act by schema (`opportunity_organization_requesting_is_human`), and no command creates a quote.
So every item requires manual approval; `blocking` conflicts say what must exist first.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from origenlab_api.v2.commands import normalize_email, normalize_organization_name

APPLIED_NEVER = False

# The V2 commands a load would call, spelled as the command vocabulary spells them.
CMD_OPEN_CASE = "open_commercial_case"
CMD_ADD_CASE_ORG = "add_case_organization"
CMD_ADVANCE_STAGE = "advance_case_stage"
CMD_LINK_EVIDENCE = "link_case_evidence"
CMD_CREATE_ORG = "create_organization"
CMD_CONFIRM_ORG = "confirm_organization"
CMD_ATTACH_ADDRESS = "attach_contact_address"
CMD_CONFIRM_PERSON = "confirm_person_from_evidence"
# No command writes crm.quote (case_commands: "no quote command"). Named here so the plan can
# say so rather than leave the step out.
WRITE_QUOTE_NO_COMMAND = "crm.quote (sin comando)"

# The stage path from the opening stage to `quoting`, one `advance_case_stage` per move.
STAGE_PATH_TO_QUOTING = (("lead", "qualifying"), ("qualifying", "qualified"), ("qualified", "quoting"))

STEP_PENDING = "pendiente"
STEP_BLOCKED = "bloqueado"
STEP_DONE = "ya_aplicado"
STEP_OPERATOR = "decision_operador"

ORG_MATCH_EXACT = "exacta"
ORG_MATCH_ACCENT_FOLDED = "misma_clave_sin_tildes"
ORG_MATCH_NONE = "sin_coincidencia"
ORG_NOT_PRINTED = "sin_organizacion_impresa"

FREE_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.es", "outlook.com", "outlook.es",
    "live.com", "live.cl", "yahoo.com", "yahoo.es", "icloud.com", "me.com", "proton.me",
})

_ADDRESS_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


@dataclass(frozen=True)
class Conflict:
    code: str
    blocking: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"codigo": self.code, "bloqueante": self.blocking, "detalle": self.detail}


# Codes and their Spanish reading. `blocking` means no load could perform the write today.
C_SUPERSEDED = "decision_reemplazada"
C_ALREADY_APPLIED = "ledger_applied_true"
C_OPERATOR_UNKNOWN = "operador_sin_platform_operator"
C_SOURCE_NOT_LOADED = "fuente_no_cargada_en_evidence"
C_NO_QUOTE_COMMAND = "sin_comando_de_cotizacion"
C_ORG_NOT_PRINTED = "cliente_sin_institucion_impresa"
C_ORG_AMBIGUOUS = "organizacion_ambigua"
C_ORG_NEW = "organizacion_inexistente_en_crm"
C_ORG_MACHINE = "organizacion_solo_propuesta_por_maquina"
C_NO_ORG_ASSERTION = "sin_asercion_organization_name"
C_NO_ADDRESS_ASSERTION = "sin_asercion_contact_address"
C_RECIPIENT_FREE_MAIL = "destinatario_correo_gratuito"
C_RECIPIENT_NOT_IN_CRM = "destinatario_sin_contact_point"
C_RECIPIENT_SEVERAL = "varios_destinatarios"
C_NO_RECIPIENT = "sin_destinatario"
C_QUOTE_NUMBER_TAKEN = "numero_ya_en_crm_quote"
C_QUOTE_NUMBER_SHARED = "numero_compartido_en_ledger"
C_SEED_PROVISIONAL = "numero_sobre_semilla_provisional"
C_SAME_ORG_OTHER_OPP = "misma_institucion_en_otra_oportunidad"
C_OPP_SHARED = "oportunidad_planificada_compartida"


@dataclass(frozen=True)
class CrmOrganization:
    id: str
    name: str
    legal_name: str | None
    kind: str
    confirmation: str


@dataclass(frozen=True)
class CrmContactPoint:
    id: str
    value_norm: str
    usage: str
    organization_id: str | None
    person_id: str | None


@dataclass
class CrmSnapshot:
    """Only what the plan compares against, read in one read-only transaction."""

    organizations: list[CrmOrganization] = field(default_factory=list)
    contact_points: dict[str, CrmContactPoint] = field(default_factory=dict)  # email value_norm →
    source_records: dict[str, str] = field(default_factory=dict)  # dedupe_key → id
    # (source record dedupe_key, assertion kind) → assertion ids
    assertions: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    quote_numbers: dict[str, str] = field(default_factory=dict)  # quote_number → opportunity id
    receipts: dict[str, str] = field(default_factory=dict)  # idempotency_key → command_name
    operators: list[str] = field(default_factory=list)  # display names

    def as_counts(self) -> dict[str, int]:
        return {
            "crm.organization": len(self.organizations),
            "crm.contact_point(email)": len(self.contact_points),
            "evidence.source_record": len(self.source_records),
            "evidence.assertion(agrupadas)": len(self.assertions),
            "crm.quote": len(self.quote_numbers),
            "platform.command_receipt": len(self.receipts),
            "platform.operator": len(self.operators),
        }


@dataclass(frozen=True)
class Step:
    order: int
    command: str
    idempotency_key: str
    status: str
    what: str
    body: Mapping[str, Any]
    waits_for: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "orden": self.order, "comando": self.command, "clave_idempotencia": self.idempotency_key,
            "estado": self.status, "que_registraria": self.what, "cuerpo_propuesto": dict(self.body),
            "espera": list(self.waits_for),
        }


@dataclass(frozen=True)
class PromotionItem:
    order: int
    decision_id: str
    document_sha256: str
    quote_number: str
    printed_numbers: tuple[str, ...]
    serial: int | None
    opportunity: Mapping[str, Any]
    organization: Mapping[str, Any]
    contact: Mapping[str, Any]
    evidence: Mapping[str, Any]
    conflicts: tuple[Conflict, ...]
    steps: tuple[Step, ...]
    manual_approval: tuple[str, ...]
    applied: bool = APPLIED_NEVER

    @property
    def blocking(self) -> tuple[Conflict, ...]:
        return tuple(c for c in self.conflicts if c.blocking)

    @property
    def promotable_now(self) -> bool:
        return not self.blocking

    def as_dict(self) -> dict[str, Any]:
        return {
            "orden": self.order,
            "decision_id": self.decision_id,
            "documento_sha256": self.document_sha256,
            "numero_cotizacion": self.quote_number,
            "numeros_impresos": list(self.printed_numbers),
            "serie": self.serial,
            "oportunidad_propuesta": dict(self.opportunity),
            "organizacion_sugerida": dict(self.organization),
            "contacto_sugerido": dict(self.contact),
            "evidencia": dict(self.evidence),
            "conflictos": [c.as_dict() for c in self.conflicts],
            "pasos": [s.as_dict() for s in self.steps],
            "requiere_aprobacion_manual": True,
            "aprobaciones_manuales": list(self.manual_approval),
            "promocionable_hoy": self.promotable_now,
            "applied": self.applied,
        }


# ─── helpers ────────────────────────────────────────────────────────────────────────────────


def accent_fold(value: str) -> str:
    """The identity report's key: no accents, lower case, single spaces. Equality only."""
    stripped = "".join(
        ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch)
    )
    return re.sub(r"\s+", " ", stripped).strip().lower()


def recipient_addresses(raw: str | None) -> list[str]:
    """Exact addresses in a staged `recipients` header, normalised as `crm.contact_point` does."""
    return sorted({normalize_email(m) for m in _ADDRESS_RE.findall(raw or "")})


def idempotency_key(*parts: str) -> str:
    """`qpromo:` + the parts; hashed when it would exceed the 200-character receipt column."""
    key = "qpromo:" + ":".join(parts)
    if len(key) <= 200:
        return key
    return "qpromo:h:" + hashlib.sha256(key.encode()).hexdigest()


def effective_rows(ledger_rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """The last decision per document: a later row supersedes an earlier one."""
    out: dict[str, Mapping[str, Any]] = {}
    for row in ledger_rows:
        out[row["document_sha256"]] = row
    return out


def match_organization(printed: str | None, snapshot: CrmSnapshot) -> tuple[str, list[CrmOrganization]]:
    """Exact equality under the command's fold, then under the accent fold. No similarity."""
    if not printed:
        return ORG_NOT_PRINTED, []
    live = snapshot.organizations
    exact_key = normalize_organization_name(printed)
    exact = [o for o in live if exact_key in {
        normalize_organization_name(o.name), normalize_organization_name(o.legal_name or "")}]
    if exact:
        return ORG_MATCH_EXACT, exact
    folded_key = accent_fold(printed)
    folded = [o for o in live if folded_key in {accent_fold(o.name), accent_fold(o.legal_name or "")}]
    if folded:
        return ORG_MATCH_ACCENT_FOLDED, folded
    return ORG_MATCH_NONE, []


def _client(doc: Mapping[str, Any] | None) -> Mapping[str, Any]:
    clients = (doc or {}).get("clients") or []
    return clients[0] if clients else {}


def _value(field_value: Any) -> str | None:
    if isinstance(field_value, Mapping):
        return field_value.get("value")
    return field_value


# ─── the plan ───────────────────────────────────────────────────────────────────────────────


def plan_promotion(
    selected: Sequence[Mapping[str, Any]],
    ledger_rows: Sequence[Mapping[str, Any]],
    identity_docs: Mapping[str, Mapping[str, Any]],
    staged_records: Mapping[int, Mapping[str, Any]],
    snapshot: CrmSnapshot,
    *,
    approved_seed_next_serial: int,
    provisional_seed_next_serial: int | None = None,
) -> list[PromotionItem]:
    """One item per selected ledger row, in the order given. Pure: reads its arguments only."""
    effective = effective_rows(ledger_rows)
    by_quote_number: dict[str, set[str]] = {}
    by_planned_opp: dict[str, set[str]] = {}
    by_org_key: dict[str, set[str]] = {}
    for row in effective.values():
        if row.get("decision") != "confirm_customer_quotation":
            continue
        by_quote_number.setdefault(row.get("quote_number") or "", set()).add(row["document_sha256"])
        opp = _planned_opportunity(row)
        if opp:
            by_planned_opp.setdefault(opp, set()).add(row["document_sha256"])
        client = _client(identity_docs.get(row["document_sha256"]))
        name = client.get("organization") or _value(client.get("addressee"))
        if name and opp:
            by_org_key.setdefault(accent_fold(name), set()).add(opp)

    operator_names = {accent_fold(n) for n in snapshot.operators}
    items: list[PromotionItem] = []
    for order, row in enumerate(selected, start=1):
        items.append(_plan_one(
            order, row, effective, identity_docs, staged_records, snapshot, operator_names,
            by_quote_number, by_planned_opp, by_org_key,
            approved_seed_next_serial, provisional_seed_next_serial,
        ))
    return items


def _planned_opportunity(row: Mapping[str, Any]) -> str | None:
    opp = row.get("opportunity") or {}
    return opp.get("opportunity_id") or opp.get("planned_opportunity_id")


def _plan_one(
    order: int,
    row: Mapping[str, Any],
    effective: Mapping[str, Mapping[str, Any]],
    identity_docs: Mapping[str, Mapping[str, Any]],
    staged_records: Mapping[int, Mapping[str, Any]],
    snapshot: CrmSnapshot,
    operator_names: set[str],
    by_quote_number: Mapping[str, set[str]],
    by_planned_opp: Mapping[str, set[str]],
    by_org_key: Mapping[str, set[str]],
    approved_seed: int,
    provisional_seed: int | None,
) -> PromotionItem:
    sha = row["document_sha256"]
    decision_id = row["decision_id"]
    quote_number = row.get("quote_number") or ""
    doc = identity_docs.get(sha)
    client = _client(doc)
    serial = ((doc or {}).get("quote_number") or {}).get("serial")
    conflicts: list[Conflict] = []

    # ── the ledger row itself ──
    if effective.get(sha) is not row and effective.get(sha, {}).get("decision_id") != decision_id:
        conflicts.append(Conflict(C_SUPERSEDED, True,
            "una decisión posterior sobre el mismo documento reemplaza a esta"))
    if row.get("applied") is not False:
        conflicts.append(Conflict(C_ALREADY_APPLIED, True, "la fila no tiene applied=false"))
    operator = row.get("operator") or ""
    if accent_fold(operator) not in operator_names:
        conflicts.append(Conflict(C_OPERATOR_UNKNOWN, True,
            f"el operador del ledger ('{operator}') no existe en platform.operator; los comandos "
            "exigen un operador de confianza"))

    # ── evidence ──
    occurrences = sorted(row.get("occurrences") or [], key=lambda o: (o.get("sent_at") or "", o["email_id"]))
    canonical_id = row.get("canonical_email_id")
    canonical = next((o for o in occurrences if o["email_id"] == canonical_id), None)
    staged = staged_records.get(canonical_id) or {}
    payload = staged.get("payload") or {}
    dedupe_key = staged.get("dedupe_key") or (
        f"gmail_message:{canonical['gmail_message_id']}" if canonical else "")
    source_record_id = snapshot.source_records.get(dedupe_key)
    if source_record_id is None:
        conflicts.append(Conflict(C_SOURCE_NOT_LOADED, True,
            f"{dedupe_key} no está en evidence.source_record: open_commercial_case exige el "
            "documento de origen cargado"))
    duplicate_keys = [
        f"gmail_message:{o['gmail_message_id']}" for o in occurrences if o["email_id"] != canonical_id
    ]
    evidence = {
        "correo_canonico": canonical_id,
        "correos_duplicados": [o["email_id"] for o in occurrences if o["email_id"] != canonical_id],
        "gmail_message_id": canonical and canonical.get("gmail_message_id"),
        "gmail_thread_id": canonical and canonical.get("gmail_thread_id"),
        "rfc822_message_id": canonical and canonical.get("rfc822_message_id"),
        "enviado": canonical and canonical.get("sent_at"),
        "adjunto_id": canonical and canonical.get("source_attachment_id"),
        "archivo": (row.get("filenames") or [None])[0],
        "linea_numero": (((doc or {}).get("quote_number") or {}).get("evidence") or {}).get("line"),
        "linea_cliente": (row.get("client_evidence_lines") or [None])[0],
        "fecha_documento": _value((doc or {}).get("document_date")),
        "productos": [_value(p) for p in (doc or {}).get("products") or []],
        "source_record_dedupe_key": dedupe_key,
        "source_record_en_v2": source_record_id,
        "duplicados_dedupe_keys": duplicate_keys,
        "decision_id": decision_id,
        "decision_registrada": row.get("recorded_at"),
        "operador_ledger": operator,
    }

    # ── opportunity ──
    opp_row = row.get("opportunity") or {}
    planned = _planned_opportunity(row) or ""
    client_name = client.get("organization") or _value(client.get("addressee")) or "cliente sin nombre"
    printed = (row.get("printed_quote_numbers") or [quote_number])[0]
    shared = sorted(by_planned_opp.get(planned, set()) - {sha})
    if shared:
        conflicts.append(Conflict(C_OPP_SHARED, False,
            f"otros {len(shared)} documento(s) del ledger apuntan a la misma oportunidad planificada"))
    other_opps = sorted(by_org_key.get(accent_fold(client_name), set()) - {planned})
    if other_opps:
        conflicts.append(Conflict(C_SAME_ORG_OTHER_OPP, False,
            f"el mismo cliente impreso tiene {len(other_opps)} otra(s) oportunidad(es) planificada(s): "
            "el operador decide si es el mismo caso"))
    opportunity = {
        "modo_ledger": opp_row.get("mode"),
        "oportunidad_existente": opp_row.get("opportunity_id"),
        "id_planificado": planned,
        "nota_id": "id de planificación del ledger; la base asigna el id real al abrir el caso y "
                   "la correspondencia queda en el recibo de la clave de idempotencia",
        "titulo_sugerido": f"Cotización {printed} — {client_name}",
        "etapa_inicial": "lead",
        "etapa_objetivo": "quoting",
        "comparte_con_documentos": shared,
    }

    # ── organization ──
    printed_org = client.get("organization")
    addressee = _value(client.get("addressee"))
    rut = _value(client.get("rut"))
    match_basis, candidates = match_organization(printed_org or (addressee if rut else None), snapshot)
    if match_basis == ORG_NOT_PRINTED:
        # An addressee with no dash may still be an institution (ICN Chile) or a person: try it,
        # but never promote the guess.
        addressee_basis, addressee_candidates = match_organization(addressee, snapshot)
        conflicts.append(Conflict(C_ORG_NOT_PRINTED, True,
            "la línea At. no separa persona de institución: sin institución solicitante confirmada "
            "el caso no pasa de 'qualifying' y la cotización no puede registrarse"))
        if addressee_candidates:
            candidates, match_basis = addressee_candidates, f"{addressee_basis}_por_destinatario_impreso"
    elif match_basis == ORG_MATCH_NONE:
        conflicts.append(Conflict(C_ORG_NEW, False,
            "ninguna crm.organization coincide exactamente: habría que crearla (create_organization)"))
    if len(candidates) > 1:
        conflicts.append(Conflict(C_ORG_AMBIGUOUS, True,
            f"{len(candidates)} organizaciones coinciden exactamente; el operador elige una"))
    if candidates and all(c.confirmation != "confirmed" for c in candidates):
        conflicts.append(Conflict(C_ORG_MACHINE, False,
            "la organización candidata sólo está propuesta por máquina (confirmation=machine_proposed)"))
    org_assertions = snapshot.assertions.get((dedupe_key, "organization_name"), [])
    needs_org_command = match_basis != ORG_NOT_PRINTED or bool(candidates)
    if needs_org_command and not org_assertions:
        conflicts.append(Conflict(C_NO_ORG_ASSERTION, True,
            "confirm_organization / create_organization deciden una aserción organization_name de "
            "este documento y no existe ninguna"))
    organization = {
        "institucion_impresa": printed_org,
        "destinatario_impreso": addressee,
        "rut_impreso": rut,
        "direccion_impresa": _value(client.get("address")),
        "base_coincidencia": match_basis,
        "candidatos_crm": [
            {"id": c.id, "nombre": c.name, "confirmation": c.confirmation, "kind": c.kind}
            for c in candidates
        ],
        "accion_sugerida": (
            CMD_CONFIRM_ORG if len(candidates) == 1 else
            "elegir candidato" if len(candidates) > 1 else
            CMD_CREATE_ORG if match_basis == ORG_MATCH_NONE else
            "decidir si el cliente es persona o institución"
        ),
        "rol_en_caso": "requesting_institution (sólo humano, por esquema)",
    }

    # ── contact ──
    recipients = recipient_addresses(payload.get("recipients"))
    contact_name = client.get("contact")
    contact_rows = []
    for address in recipients:
        cp = snapshot.contact_points.get(address)
        domain = address.rsplit("@", 1)[-1]
        contact_rows.append({
            "direccion": address,
            "dominio": domain,
            "correo_gratuito": domain in FREE_MAIL_DOMAINS,
            "contact_point_id": cp.id if cp else None,
            "usage": cp.usage if cp else None,
            "vinculado_a_organizacion": bool(cp and cp.organization_id),
            "vinculado_a_persona": bool(cp and cp.person_id),
        })
    if not recipients:
        conflicts.append(Conflict(C_NO_RECIPIENT, False, "el correo canónico no tiene destinatario legible"))
    if len(recipients) > 1:
        conflicts.append(Conflict(C_RECIPIENT_SEVERAL, False,
            f"{len(recipients)} destinatarios: el operador elige quién recibe la cotización"))
    if any(r["correo_gratuito"] for r in contact_rows):
        conflicts.append(Conflict(C_RECIPIENT_FREE_MAIL, False,
            "destinatario en un proveedor de correo gratuito: el dominio no identifica institución"))
    if any(r["contact_point_id"] is None for r in contact_rows):
        conflicts.append(Conflict(C_RECIPIENT_NOT_IN_CRM, False,
            "el destinatario no existe como crm.contact_point"))
    address_assertions = snapshot.assertions.get((dedupe_key, "contact_address"), [])
    if recipients and not address_assertions:
        conflicts.append(Conflict(C_NO_ADDRESS_ASSERTION, True,
            "attach_contact_address / confirm_person_from_evidence deciden una aserción "
            "contact_address de este documento y no existe ninguna"))
    contact = {
        "persona_impresa": contact_name,
        "destinatarios": contact_rows,
        "accion_sugerida": (
            CMD_CONFIRM_PERSON if contact_name else
            CMD_ATTACH_ADDRESS if recipients else "ninguna"
        ),
        "rol_participante": "quote_recipient (no hay comando que cree participantes)",
    }

    # ── quote number ──
    taken_by = snapshot.quote_numbers.get(quote_number)
    if taken_by is not None:
        conflicts.append(Conflict(C_QUOTE_NUMBER_TAKEN, True,
            f"crm.quote ya tiene el número {quote_number} (oportunidad {taken_by})"))
    same_number = sorted(by_quote_number.get(quote_number, set()) - {sha})
    if same_number:
        conflicts.append(Conflict(C_QUOTE_NUMBER_SHARED, True,
            f"el número {quote_number} está en otros {len(same_number)} documento(s) del ledger; "
            "quote_number es único en crm.quote"))
    if serial is not None and serial >= approved_seed:
        seed_note = (f"provisional {provisional_seed}" if provisional_seed else "sin semilla provisional")
        conflicts.append(Conflict(C_SEED_PROVISIONAL, False,
            f"serie {serial} ≥ semilla aprobada en código {approved_seed}; sólo es segura con la "
            f"semilla {seed_note}, aún no aprobada en firme"))
    conflicts.append(Conflict(C_NO_QUOTE_COMMAND, True,
        "ningún comando V2 escribe crm.quote/crm.quote_revision, y el esquema exige que la "
        "oportunidad tenga organización"))

    steps = _steps(row, planned, printed, client_name, source_record_id, dedupe_key, duplicate_keys,
                   organization, contact, recipients, candidates, quote_number, conflicts, snapshot)

    manual = [
        "abrir la oportunidad (crear nueva o elegir existente) y su título",
        "institución solicitante (requesting_institution): acto humano por esquema",
        "vincular o crear la organización en el CRM",
        "contacto / destinatario de la cotización",
    ]
    if any(c.code == C_SEED_PROVISIONAL for c in conflicts):
        manual.append("aprobar en firme la semilla de numeración")
    return PromotionItem(
        order=order, decision_id=decision_id, document_sha256=sha, quote_number=quote_number,
        printed_numbers=tuple(row.get("printed_quote_numbers") or ()), serial=serial,
        opportunity=opportunity, organization=organization, contact=contact, evidence=evidence,
        conflicts=tuple(conflicts), steps=steps, manual_approval=tuple(manual),
    )


def _steps(
    row: Mapping[str, Any],
    planned: str,
    printed: str,
    client_name: str,
    source_record_id: str | None,
    dedupe_key: str,
    duplicate_keys: Sequence[str],
    organization: Mapping[str, Any],
    contact: Mapping[str, Any],
    recipients: Sequence[str],
    candidates: Sequence[CrmOrganization],
    quote_number: str,
    conflicts: Sequence[Conflict],
    snapshot: CrmSnapshot,
) -> tuple[Step, ...]:
    """The ordered commands a load would send. Keys hang off stable ids, never off the clock."""
    codes = {c.code for c in conflicts}
    steps: list[Step] = []

    def add(command: str, key: str, what: str, body: Mapping[str, Any], *,
            blocked_by: Iterable[str] = (), operator_choice: bool = False,
            waits_for: tuple[str, ...] = ()) -> str:
        if key in snapshot.receipts:
            status = STEP_DONE
        elif codes & set(blocked_by):
            status = STEP_BLOCKED
        elif operator_choice:
            status = STEP_OPERATOR
        else:
            status = STEP_PENDING
        steps.append(Step(len(steps) + 1, command, key, status, what, body, waits_for))
        return key

    common = (C_OPERATOR_UNKNOWN, C_SUPERSEDED, C_ALREADY_APPLIED)
    org_key = accent_fold(organization.get("institucion_impresa") or organization.get("destinatario_impreso") or "")

    # 1. the organization, when the document names one
    org_step = None
    if organization["accion_sugerida"] in (CMD_CONFIRM_ORG, CMD_CREATE_ORG, "elegir candidato"):
        org_step = add(
            CMD_CONFIRM_ORG if candidates else CMD_CREATE_ORG,
            idempotency_key("org", dedupe_key, org_key),
            "la institución impresa como organización del CRM",
            {"source_record_dedupe_key": dedupe_key, "assertion_kind": "organization_name",
             "organization_id": candidates[0].id if len(candidates) == 1 else None,
             "name_display": organization.get("institucion_impresa") or organization.get("destinatario_impreso")},
            blocked_by=common + (C_SOURCE_NOT_LOADED, C_NO_ORG_ASSERTION, C_ORG_AMBIGUOUS),
            operator_choice=True,
        )

    # 2. the recipient address, and the printed person when there is one
    for address in recipients:
        add(
            CMD_CONFIRM_PERSON if contact.get("persona_impresa") else CMD_ATTACH_ADDRESS,
            idempotency_key("contact", dedupe_key, address),
            "el destinatario como dirección de contacto" + (" de la persona impresa" if contact.get("persona_impresa") else ""),
            {"source_record_dedupe_key": dedupe_key, "assertion_kind": "contact_address",
             "display_name": contact.get("persona_impresa"), "organization_step": org_step},
            blocked_by=common + (C_SOURCE_NOT_LOADED, C_NO_ADDRESS_ASSERTION),
            operator_choice=True, waits_for=(org_step,) if org_step else (),
        )

    # 3. the case, opened from the canonical message (origin link is written by the command)
    open_key = add(
        CMD_OPEN_CASE, idempotency_key("open", planned),
        "la oportunidad, en 'lead', con el correo canónico como evidencia de origen",
        {"title": f"Cotización {printed} — {client_name}", "origin_source_record_id": source_record_id,
         "note": f"decision_id {row['decision_id']}"},
        blocked_by=common + (C_SOURCE_NOT_LOADED,), operator_choice=True,
    )

    # 4. duplicates of the same PDF as supporting evidence
    for dup in duplicate_keys:
        add(CMD_LINK_EVIDENCE, idempotency_key("link", planned, dup, "mentions"),
            "un reenvío del mismo PDF como evidencia del caso",
            {"source_record_dedupe_key": dup, "relation": "mentions"},
            blocked_by=common + (C_SOURCE_NOT_LOADED,), waits_for=(open_key,))

    # 5. the requesting institution — a human act
    req_key = add(
        CMD_ADD_CASE_ORG, idempotency_key("requesting", planned, org_key),
        "la institución solicitante del caso",
        {"role": "requesting_institution", "organization_step": org_step},
        blocked_by=common + (C_SOURCE_NOT_LOADED, C_ORG_NOT_PRINTED, C_ORG_AMBIGUOUS, C_NO_ORG_ASSERTION),
        operator_choice=True, waits_for=tuple(k for k in (open_key, org_step) if k),
    )

    # 6. the stage path to quoting
    previous = req_key
    for from_stage, to_stage in STAGE_PATH_TO_QUOTING:
        previous = add(
            CMD_ADVANCE_STAGE, idempotency_key("stage", planned, to_stage),
            f"etapa {from_stage} → {to_stage}",
            {"from": from_stage, "to": to_stage},
            blocked_by=common + (C_SOURCE_NOT_LOADED,) + (
                (C_ORG_NOT_PRINTED, C_ORG_AMBIGUOUS, C_NO_ORG_ASSERTION) if to_stage != "qualifying" else ()),
            operator_choice=True, waits_for=(previous,),
        )

    # 7. the quote itself — no command exists
    add(WRITE_QUOTE_NO_COMMAND, idempotency_key("quote", planned, quote_number),
        f"crm.quote {quote_number} (+ revisión 'sent' sin montos: no se extraen precios)",
        {"quote_number": quote_number, "document_sha256": row["document_sha256"]},
        blocked_by=(C_NO_QUOTE_COMMAND, C_QUOTE_NUMBER_TAKEN, C_QUOTE_NUMBER_SHARED) + common,
        waits_for=(previous,))
    return tuple(steps)


# ─── idempotency ────────────────────────────────────────────────────────────────────────────


def canonical_json(items: Sequence[PromotionItem]) -> str:
    return json.dumps([i.as_dict() for i in items], ensure_ascii=False, sort_keys=True, indent=1)


def plan_digest(items: Sequence[PromotionItem]) -> str:
    return hashlib.sha256(canonical_json(items).encode("utf-8")).hexdigest()


def simulate_apply(items: Sequence[PromotionItem], snapshot: CrmSnapshot) -> CrmSnapshot:
    """A copy of the snapshot as if every step had succeeded, blockers lifted. Hypothetical only.

    Used to prove the key scheme: a second plan over the result must find every step already
    receipted and no quote number free to write twice.
    """
    after = copy.deepcopy(snapshot)
    for item in items:
        for step in item.steps:
            after.receipts.setdefault(step.idempotency_key, step.command)
        if item.quote_number:
            after.quote_numbers.setdefault(item.quote_number, str(item.opportunity.get("id_planificado")))
    return after


def idempotency_report(items: Sequence[PromotionItem], replan: Sequence[PromotionItem]) -> dict[str, Any]:
    """Keys unique per logical write, and a replay after a hypothetical apply writes nothing."""
    keys: dict[str, list[tuple[int, str]]] = {}
    for item in items:
        for step in item.steps:
            keys.setdefault(step.idempotency_key, []).append((item.order, step.command))
    # A key reused for a different command is a bug; one reused by two documents of the same
    # planned opportunity is the same logical write (opening that case once) and is intended.
    conflicting = {k: v for k, v in keys.items() if len({c for _, c in v}) > 1}
    shared = {k: v for k, v in keys.items() if len(v) > 1 and k not in conflicting}
    replay_pending = [
        (i.order, s.command) for i in replan for s in i.steps if s.status != STEP_DONE
    ]
    unreceipted = sum(1 for i in items for s in i.steps if s.status != STEP_DONE)
    return {
        "pasos_totales": sum(len(i.steps) for i in items),
        "claves_distintas": len(keys),
        "claves_con_comandos_distintos": {k: [list(x) for x in v] for k, v in conflicting.items()},
        "claves_compartidas_misma_escritura": {k: [list(x) for x in v] for k, v in shared.items()},
        "pasos_sin_recibo_primera_pasada": unreceipted,
        "escrituras_en_repeticion_tras_aplicar": len(replay_pending),
        "repeticion_sin_escrituras": not replay_pending,
        "numeros_que_se_escribirian_dos_veces": sorted(
            i.quote_number for i in replan
            if not any(c.code == C_QUOTE_NUMBER_TAKEN for c in i.conflicts)
        ),
    }

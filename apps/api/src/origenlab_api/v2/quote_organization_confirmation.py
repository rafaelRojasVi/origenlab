"""Organization confirmation for the quotation CRM import — requests out, operator decisions in.

The import plan (`quote_crm_import_plan`) never infers an organization: an opportunity moves
only when an operator confirmation names what to do. This module is the review workflow that
produces those confirmations, in two pure halves:

* :func:`build_requests` turns one dry-run intent (plus a read-only reading of the CRM) into
  one **request** per planned opportunity: what the documents print (institution, addressee,
  RUT), what the CRM already holds under exactly that name, other CRM names that fold to the
  same *candidate key*, the recipients, the quotation numbers, links to the evidence, a
  category and a recommended action. The decision fields are left blank. Nothing here
  decides.
* :func:`load_decisions` reads the operator's filled request sheet back and returns the
  confirmations the planner accepts — or refuses the whole file. It accepts only explicit
  decisions, never fills one in, refuses an opportunity it does not know, a request row that
  was edited, a duplicate or conflicting decision, an action the evidence cannot carry, and an
  operator identity that is not a real, active writer. It records who decided, when, and
  exactly which evidence the decision was made against. It writes nothing anywhere.

The candidate key (:func:`candidate_key`) is **not** a match. It exists so the reviewer sees
"Golden Omega S.A." next to "Golden Omega SA" in the CRM; it is never used to confirm, link or
create anything.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote as urlquote

from origenlab_api.v2.commands import WRITER_ROLES, normalize_organization_name
from origenlab_api.v2.quote_crm_import_plan import (
    S_HELD,
    canonical_json,
    effective_rows,
    recipient_addresses,
    sha256_text,
)

REQUESTS_VERSION = "orgconf-requests/2026-09-25.1"
LOADER_VERSION = "orgconf-loader/2026-09-25.1"

# Categories, in the order the review report groups them.
C_EXACT = "exact_crm_match"
C_CREATE = "organization_must_be_created"
C_PERSON = "person_only_identity"
C_DUPLICATE = "duplicate_organization_candidates"
C_BLOCKED = "blocked_by_documents"
CATEGORIES = (C_EXACT, C_CREATE, C_PERSON, C_DUPLICATE, C_BLOCKED)

# Operator decisions.
CONFIRM_EXISTING = "confirm_existing"
CREATE_FROM_EVIDENCE = "create_from_evidence"
LEAVE_PENDING = "leave_pending"
DECISIONS = (CONFIRM_EXISTING, CREATE_FROM_EVIDENCE, LEAVE_PENDING)
ORGANIZATION_KINDS = ("institution", "unknown")  # the create_organization command's closed list

#: The columns the operator fills. Everything else in a row is the request and must come back
#: byte-identical.
DECISION_COLUMNS = (
    "decision", "organization_id", "organization_version", "organization_kind", "name_display",
    "decision_note", "decided_by_email", "decided_by_operator_id", "decided_by_role", "decided_at",
)
REQUEST_COLUMNS = (
    "planned_opportunity_id", "category", "plan_status", "printed_organization", "printed_addressee",
    "printed_ruts", "exact_crm_matches", "other_crm_candidates", "crm_rut_matches",
    "shared_with_opportunities", "recipient_emails", "quote_numbers", "documents", "evidence_links",
    "recommended_action", "recommendation_detail", "request_sha256",
)

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
#: Reserved or documentation domains: an identity there is a placeholder, never an operator.
_PLACEHOLDER_DOMAIN = re.compile(r"(^|\.)(invalid|test|example|localhost|local)$|(^|\.)example\.(com|org|net)$")
_LEGAL_TOKENS = frozenset({"sa", "spa", "ltda", "limitada", "eirl", "sac", "srl", "inc", "ltd", "cia"})
_LEGAL_PREFIX = re.compile(r"^\s*raz[oó]n\s+social\s*:?\s*", re.IGNORECASE)


def candidate_key(name: str | None) -> str:
    """A review aid, never a match: accents, punctuation, legal forms and a `Razón social:` prefix removed."""
    if not name:
        return ""
    text = _LEGAL_PREFIX.sub("", name)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().replace(".", "")
    tokens = [t for t in re.split(r"[^a-z0-9]+", text) if t and t not in _LEGAL_TOKENS]
    return " ".join(tokens)


def rut_key(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"[^0-9kK]", "", raw).upper()
    return f"{digits[:-1]}-{digits[-1]}" if len(digits) >= 2 else None


def gmail_link(rfc822_message_id: str | None) -> str | None:
    if not rfc822_message_id:
        return None
    return "https://mail.google.com/mail/u/0/#search/" + urlquote(f"rfc822msgid:{rfc822_message_id.strip('<>')}", safe="")


@dataclass(frozen=True)
class CrmOrganization:
    organization_id: str
    name: str
    legal_name: str | None
    confirmation: str
    version: int


@dataclass
class CrmReading:
    """Read once, read-only, from the CRM the confirmations will be applied to."""

    database: str
    organizations: list[CrmOrganization] = field(default_factory=list)
    rut_owners: dict[str, list[str]] = field(default_factory=dict)  # rut value_norm → organization ids
    operators: dict[str, dict[str, str]] = field(default_factory=dict)  # operator id → {email, role, status, display_name}


# ─── requests ──────────────────────────────────────────────────────────────────────────────


def _format_org(o: CrmOrganization) -> str:
    return f"{o.organization_id} {o.name} ({o.confirmation}, v{o.version})"


def build_requests(
    intent: Mapping[str, Any],
    plan_sha256: str,
    ledger_rows: Sequence[Mapping[str, Any]],
    identity_docs: Mapping[str, Mapping[str, Any]],
    staged_by_email: Mapping[int, Mapping[str, Any]],
    crm: CrmReading,
    pdf_paths: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """One request per planned opportunity. Pure; the decision fields stay blank."""
    rows_by_sha = effective_rows(ledger_rows)
    exact_index: dict[str, list[CrmOrganization]] = defaultdict(list)
    loose_index: dict[str, list[CrmOrganization]] = defaultdict(list)
    for org in crm.organizations:
        for n in {normalize_organization_name(org.name), normalize_organization_name(org.legal_name or "")} - {""}:
            exact_index[n].append(org)
        for k in {candidate_key(org.name), candidate_key(org.legal_name)} - {""}:
            loose_index[k].append(org)

    gathered = []
    for o in intent["opportunities"]:
        pid = o["planned_opportunity_id"]
        docs = sorted(o["documents"])
        numbers: list[str] = []
        by_doc_number = {d["document_sha256"]: d["quote_number"] for d in intent["documents"]}
        orgs_printed: list[str] = []
        addressees: list[str] = []
        ruts: list[str] = []
        recipients: list[str] = []
        links: list[str] = []
        for sha in docs:
            row = rows_by_sha[sha]
            number = by_doc_number.get(sha) or row.get("quote_number") or ""
            if number not in numbers:
                numbers.append(number)
            for client in (identity_docs.get(sha) or {}).get("clients") or []:
                if client.get("organization") and client["organization"] not in orgs_printed:
                    orgs_printed.append(client["organization"])
                addressee = client.get("addressee")
                addressee = addressee.get("value") if isinstance(addressee, Mapping) else addressee
                if addressee and addressee not in addressees:
                    addressees.append(addressee)
                rut = client.get("rut")
                rut = rut_key(rut.get("value") if isinstance(rut, Mapping) else rut)
                if rut and rut not in ruts:
                    ruts.append(rut)
            for occ in sorted(row.get("occurrences") or [], key=lambda x: x["sent_at"]):
                staged = staged_by_email.get(int(occ["email_id"])) or {}
                for addr in recipient_addresses((staged.get("payload") or {}).get("recipients")):
                    if addr not in recipients:
                        recipients.append(addr)
                g = gmail_link(occ.get("rfc822_message_id"))
                links.append(f"{number} {occ['sent_at'][:10]} gmail:{g or 'unavailable'}")
            pdf = (pdf_paths or {}).get(sha)
            links.append(f"{number} pdf:{pdf or 'unavailable'} sha256:{sha}")
        printed = o.get("printed_organization") or (orgs_printed[0] if orgs_printed else None)
        exact = _unique(exact_index.get(normalize_organization_name(printed), [])) if printed else []
        loose = [c for c in _unique(loose_index.get(candidate_key(printed), [])) if c not in exact] if printed else []
        rut_owners = sorted({i for r in ruts for i in crm.rut_owners.get(r, [])})
        gathered.append({
            "pid": pid, "status": o["status"], "reasons": o.get("reasons") or [], "printed": printed,
            "all_printed": orgs_printed, "addressees": addressees, "ruts": ruts, "recipients": recipients,
            "numbers": numbers, "documents": docs, "links": links, "exact": exact, "loose": loose,
            "rut_owners": rut_owners,
            "quotes": len(o.get("quotes") or []),
            "revisions": sum(len(q["revisions"]) for q in o.get("quotes") or []),
            "evidence_records": len(o.get("source_dedupe_keys") or []),
            "steps": len(o.get("steps") or []),
        })

    # Opportunities printing the same institution (by candidate key) or the same RUT.
    by_key: dict[str, set[str]] = defaultdict(set)
    for g in gathered:
        if g["printed"]:
            by_key["name:" + candidate_key(g["printed"])].add(g["pid"])
        for r in g["ruts"]:
            by_key["rut:" + r].add(g["pid"])
    for g in gathered:
        shared: set[str] = set()
        for k in (["name:" + candidate_key(g["printed"])] if g["printed"] else []) + ["rut:" + r for r in g["ruts"]]:
            shared |= by_key[k]
        g["shared"] = sorted(shared - {g["pid"]})

    requests = [_request(g) for g in sorted(gathered, key=lambda g: g["pid"])]
    return {
        "requests_version": REQUESTS_VERSION,
        "plan_version": intent["plan_version"],
        "plan_sha256": plan_sha256,
        "crm_database": crm.database,
        "crm_organizations_read": len(crm.organizations),
        "requests": requests,
        "summary": _requests_summary(requests),
    }


def _unique(orgs: Sequence[CrmOrganization]) -> list[CrmOrganization]:
    seen: dict[str, CrmOrganization] = {}
    for o in orgs:
        seen.setdefault(o.organization_id, o)
    return sorted(seen.values(), key=lambda o: o.organization_id)


def _request(g: dict[str, Any]) -> dict[str, Any]:
    category, action, detail = _categorise(g)
    req = {
        "planned_opportunity_id": g["pid"],
        "category": category,
        "plan_status": g["status"],
        "printed_organization": g["printed"] or "",
        "printed_addressee": " | ".join(g["addressees"]),
        "printed_ruts": " ".join(g["ruts"]),
        "exact_crm_matches": " | ".join(_format_org(o) for o in g["exact"]),
        "other_crm_candidates": " | ".join(_format_org(o) for o in g["loose"]),
        "crm_rut_matches": " ".join(g["rut_owners"]),
        "shared_with_opportunities": " ".join(g["shared"]),
        "recipient_emails": " ".join(g["recipients"]),
        "quote_numbers": " ".join(g["numbers"]),
        "documents": " ".join(g["documents"]),
        "evidence_links": " || ".join(g["links"]),
        "recommended_action": action,
        "recommendation_detail": detail,
    }
    req["request_sha256"] = sha256_text(canonical_json(req))
    # Not part of the CSV: what the review report counts, and what a confirmation cites.
    req["_facts"] = {
        "reasons": g["reasons"], "exact_organization_ids": [o.organization_id for o in g["exact"]],
        "exact_organization_versions": {o.organization_id: o.version for o in g["exact"]},
        "candidate_organization_ids": [o.organization_id for o in g["loose"]],
        "all_printed_organizations": g["all_printed"],
        "quotes": g["quotes"], "revisions": g["revisions"], "evidence_records": g["evidence_records"],
        "command_templates": g["steps"],
    }
    return req


def _categorise(g: dict[str, Any]) -> tuple[str, str, str]:
    if g["status"] == S_HELD:
        return (C_BLOCKED, "none_until_documents_resolved",
                "Contiene un documento bloqueado por el owner (G3: la oportunidad se mueve entera). "
                "Resolver el bloqueo primero; el cargador rechaza cualquier confirmación aquí. "
                "Motivos: " + " ".join(g["reasons"]))
    if not g["printed"]:
        return (C_PERSON, LEAVE_PENDING,
                "El documento imprime sólo una persona, sin institución. No hay evidencia de nombre "
                "institucional para crear una organización y no se crea ningún contacto. Sólo cabe "
                "confirm_existing si el operador sabe a qué institución existente pertenece."
                + (f" RUT impreso {' '.join(g['ruts'])}: puede ser de persona natural." if g["ruts"] else ""))
    notes = []
    if len(g["all_printed"]) > 1:
        notes.append("Los documentos imprimen instituciones distintas: " + " | ".join(g["all_printed"]) + ".")
    if _LEGAL_PREFIX.match(g["printed"]):
        notes.append("El nombre impreso arrastra 'Razón social:'; si se crea, indicar name_display limpio.")
    if g["rut_owners"]:
        notes.append("El RUT impreso ya pertenece en el CRM a: " + " ".join(g["rut_owners"]) + ".")
    tail = (" " + " ".join(notes)) if notes else ""
    if len(g["exact"]) > 1:
        return (C_DUPLICATE, "confirm_existing:choose_one",
                f"{len(g['exact'])} organizaciones del CRM llevan exactamente este nombre: elegir una "
                "(y marcar la otra para fusión aparte)." + tail)
    if len(g["exact"]) == 1:
        o = g["exact"][0]
        if g["loose"]:
            return (C_DUPLICATE, f"confirm_existing:{o.organization_id}:v{o.version}?",
                    f"Coincidencia exacta con {o.name}, pero el CRM tiene además {len(g['loose'])} "
                    "nombre(s) que se pliegan igual: revisar si son la misma institución." + tail)
        return (C_EXACT, f"confirm_existing:{o.organization_id}:v{o.version}",
                f"Coincidencia exacta de nombre con {o.name} ({o.confirmation}). Una coincidencia de "
                "nombre es una sugerencia: confirmar que es la misma institución." + tail)
    if g["loose"]:
        return (C_DUPLICATE, "confirm_existing:choose_one_or_create",
                "Sin coincidencia exacta, pero el CRM tiene nombre(s) que se pliegan igual (sin tildes, "
                "puntuación ni forma societaria). Confirmar uno de ellos o crear si es otra institución." + tail)
    if g["shared"]:
        return (C_DUPLICATE, "create_from_evidence:once_for_the_group",
                "Otra(s) oportunidad(es) imprimen la misma institución o RUT: "
                + " ".join(g["shared"]) + ". Crear en UNA sola; las demás quedan leave_pending hasta "
                "una segunda pasada con confirm_existing, cuando la organización exista." + tail)
    return (C_CREATE, CREATE_FROM_EVIDENCE,
            "Ninguna organización del CRM lleva este nombre. Crear desde el nombre impreso "
            "(evidencia organization_name del correo de origen); indicar organization_kind." + tail)


def _requests_summary(requests: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for c in CATEGORIES:
        rs = [r for r in requests if r["category"] == c]
        out[c] = {
            "opportunities": len(rs),
            "quotes": sum(r["_facts"]["quotes"] for r in rs),
            "revisions": sum(r["_facts"]["revisions"] for r in rs),
            "evidence_records": sum(r["_facts"]["evidence_records"] for r in rs),
            "documents": sum(len(r["documents"].split()) for r in rs),
        }
    out["total_opportunities"] = len(requests)
    return out


def request_csv_rows(requests_doc: Mapping[str, Any]) -> list[dict[str, str]]:
    return [{**{c: r[c] for c in REQUEST_COLUMNS}, **dict.fromkeys(DECISION_COLUMNS, "")}
            for r in requests_doc["requests"]]


# ─── loading operator decisions ────────────────────────────────────────────────────────────


@dataclass
class LoadResult:
    confirmations: list[dict[str, Any]]
    pending: list[dict[str, Any]]
    blank: list[str]
    problems: list[dict[str, str]]

    @property
    def accepted(self) -> bool:
        return not self.problems


def _problem(problems: list[dict[str, str]], line: int, pid: str, code: str, detail: str) -> None:
    problems.append({"line": str(line), "planned_opportunity_id": pid, "code": code, "detail": detail})


def _is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value.lower()
    except ValueError:
        return False


def _parse_instant(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def load_decisions(
    sheet_rows: Sequence[Mapping[str, str]],
    requests_doc: Mapping[str, Any],
    requests_sha256: str,
    crm: CrmReading,
    *,
    now: datetime,
) -> LoadResult:
    """Validate a filled request sheet. All-or-nothing: any problem refuses the whole sheet."""
    problems: list[dict[str, str]] = []
    by_pid = {r["planned_opportunity_id"]: r for r in requests_doc["requests"]}
    orgs = {o.organization_id: o for o in crm.organizations}
    seen: dict[str, int] = {}
    decided: list[tuple[int, Mapping[str, str], Mapping[str, Any]]] = []
    blank: list[str] = []

    for line, row in enumerate(sheet_rows, start=2):  # line 1 is the header
        pid = (row.get("planned_opportunity_id") or "").strip()
        req = by_pid.get(pid)
        if req is None:
            _problem(problems, line, pid, "unknown_opportunity", "not an opportunity of this request sheet")
            continue
        if pid in seen:
            _problem(problems, line, pid, "duplicate_opportunity_row",
                     f"the opportunity already appears on line {seen[pid]}; one row per opportunity")
            continue
        seen[pid] = line
        edited = [c for c in REQUEST_COLUMNS if (row.get(c) or "") != req[c]]
        if edited:
            _problem(problems, line, pid, "request_row_edited",
                     "request columns must come back unchanged: " + ", ".join(edited))
            continue
        extra = set(row) - set(REQUEST_COLUMNS) - set(DECISION_COLUMNS)
        if extra:
            _problem(problems, line, pid, "unexpected_columns", ", ".join(sorted(extra)))
            continue
        values = {c: (row.get(c) or "").strip() for c in DECISION_COLUMNS}
        if not any(values.values()):
            blank.append(pid)
            continue
        decided.append((line, values, req))

    # Duplicate or conflicting decisions across rows: one creation per institution.
    creations: dict[str, list[tuple[int, str]]] = defaultdict(list)
    operators: set[tuple[str, str, str]] = set()
    confirmations: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for line, v, req in decided:
        pid = req["planned_opportunity_id"]
        facts = req["_facts"]
        before = len(problems)
        decision = v["decision"]
        if decision not in DECISIONS:
            _problem(problems, line, pid, "decision_not_allowed", f"decision must be one of {', '.join(DECISIONS)}")
            continue
        # Who decided, and when.
        email = v["decided_by_email"].lower()
        if v["decided_by_email"] != email or not _EMAIL.match(email):
            _problem(problems, line, pid, "operator_email_invalid", "decided_by_email must be a lower-case address")
        elif _PLACEHOLDER_DOMAIN.search(email.rsplit("@", 1)[-1]):
            _problem(problems, line, pid, "operator_email_placeholder",
                     f"{email} is a placeholder identity; a confirmation needs a real operator")
        if not _is_uuid(v["decided_by_operator_id"]):
            _problem(problems, line, pid, "operator_id_invalid", "decided_by_operator_id must be a lower-case UUID")
        if v["decided_by_role"] not in WRITER_ROLES:
            _problem(problems, line, pid, "operator_role_not_writer",
                     f"decided_by_role must be one of {', '.join(WRITER_ROLES)}")
        registered = crm.operators.get(v["decided_by_operator_id"])
        if registered is None:
            _problem(problems, line, pid, "operator_not_registered",
                     f"no platform.operator {v['decided_by_operator_id']} in {crm.database}")
        else:
            for f, want in (("email", email), ("role", v["decided_by_role"])):
                if registered[f] != want:
                    _problem(problems, line, pid, f"operator_{f}_mismatch",
                             f"platform.operator says {f} {registered[f]!r}, the sheet says {want!r}")
            if registered["status"] != "active":
                _problem(problems, line, pid, "operator_not_active", f"operator status is {registered['status']}")
        when = _parse_instant(v["decided_at"])
        if when is None:
            _problem(problems, line, pid, "decided_at_invalid", "decided_at must be ISO-8601 with a UTC offset")
        elif when > now:
            _problem(problems, line, pid, "decided_at_in_future", v["decided_at"])
        if not v["decision_note"]:
            _problem(problems, line, pid, "decision_note_required", "say in a sentence why")
        # What was decided.
        if req["category"] == C_BLOCKED and decision != LEAVE_PENDING:
            _problem(problems, line, pid, "opportunity_blocked_by_documents",
                     "a held opportunity cannot be confirmed until its documents are resolved")
        allowed_fields = {CONFIRM_EXISTING: {"organization_id", "organization_version"},
                          CREATE_FROM_EVIDENCE: {"organization_kind", "name_display"},
                          LEAVE_PENDING: set()}[decision]
        stray = [f for f in ("organization_id", "organization_version", "organization_kind", "name_display")
                 if v[f] and f not in allowed_fields]
        if stray:
            _problem(problems, line, pid, "fields_do_not_belong_to_decision",
                     f"{decision} does not take: {', '.join(stray)}")
        if decision == CONFIRM_EXISTING:
            org = orgs.get(v["organization_id"])
            if not _is_uuid(v["organization_id"]) or not v["organization_version"].isdigit():
                _problem(problems, line, pid, "organization_reference_invalid",
                         "confirm_existing needs organization_id (UUID) and organization_version (integer)")
            elif org is None:
                _problem(problems, line, pid, "organization_not_found",
                         f"{v['organization_id']} is not a live organization of {crm.database}")
            elif org.version != int(v["organization_version"]):
                _problem(problems, line, pid, "organization_version_stale",
                         f"organization is at version {org.version}, the sheet says {v['organization_version']}")
        elif decision == CREATE_FROM_EVIDENCE:
            if req["category"] == C_PERSON or not req["printed_organization"]:
                _problem(problems, line, pid, "no_printed_organization",
                         "the documents print no institution; there is no evidence to create one from")
            if v["organization_kind"] not in ORGANIZATION_KINDS:
                _problem(problems, line, pid, "organization_kind_required",
                         f"organization_kind must be stated explicitly: {' or '.join(ORGANIZATION_KINDS)}")
            if facts["exact_organization_ids"]:
                _problem(problems, line, pid, "organization_already_exists",
                         "the CRM already holds this exact name: confirm_existing it instead")
            name = v["name_display"] or req["printed_organization"]
            creations[candidate_key(name)].append((line, pid))
            for r in (req["printed_ruts"].split() if req["printed_ruts"] else []):
                creations["rut:" + r].append((line, pid))
        if len(problems) > before:
            continue
        operators.add((email, v["decided_by_operator_id"], v["decided_by_role"]))
        evidence = {
            "requests_version": REQUESTS_VERSION, "requests_sha256": requests_sha256,
            "plan_sha256": requests_doc["plan_sha256"], "request_sha256": req["request_sha256"],
            "category": req["category"], "recommended_action": req["recommended_action"],
            "printed_organization": req["printed_organization"] or None,
            "printed_ruts": req["printed_ruts"].split(), "quote_numbers": req["quote_numbers"].split(),
            "documents": req["documents"].split(), "exact_crm_matches": facts["exact_organization_ids"],
            "crm_database": crm.database,
        }
        record: dict[str, Any] = {
            "loader_version": LOADER_VERSION, "planned_opportunity_id": pid, "action": decision,
            "confirmed_by": {"email": email, "operator_id": v["decided_by_operator_id"], "role": v["decided_by_role"]},
            "confirmed_at": v["decided_at"], "note": v["decision_note"], "evidence": evidence,
        }
        if decision == CONFIRM_EXISTING:
            record["organization_id"] = v["organization_id"]
            record["organization_version"] = int(v["organization_version"])
        elif decision == CREATE_FROM_EVIDENCE:
            record["kind"] = v["organization_kind"]
            if v["name_display"]:
                record["name_display"] = v["name_display"]
        (pending if decision == LEAVE_PENDING else confirmations).append(record)

    for k, hits in creations.items():
        if len(hits) > 1:
            for line, pid in hits:
                _problem(problems, line, pid, "conflicting_creation",
                         f"{len(hits)} rows create the same institution ({k}); create it once and leave the "
                         "others pending until they can confirm_existing it")
    if len(operators) > 1:
        _problem(problems, 0, "", "several_operators",
                 "one sheet, one operator: the apply runs every confirmation as that operator")
    confirmations.sort(key=lambda r: r["planned_opportunity_id"])
    pending.sort(key=lambda r: r["planned_opportunity_id"])
    return LoadResult(confirmations=confirmations if not problems else [], pending=pending if not problems else [],
                      blank=sorted(blank), problems=problems)

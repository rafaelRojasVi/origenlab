"""The CRM import plan for confirmed historical quotations — computed, never executed here.

Input: the canonical document ledger (`document_decisions.jsonl`), the document identity report,
the quote-evidence staging, the list of documents the owner blocked, and an optional file of
**operator organization confirmations**. Output: an *intent* — the exact commands a load would
run, with deterministic idempotency keys and symbolic references to ids the database will
assign — plus the Gmail evidence manifest the load stages first.

The intent is independent of the target database on purpose. Its SHA-256 is what an apply is
authorised against (`--expect-plan-sha256`); what the database already holds is a separate
*state* reading (`plan_state`), so a second dry run after an apply shows the same intent with
every step already done, and a second apply replays every command from its receipt.

Rules this module enforces, each with the reason code it reports:

* only `confirm_customer_quotation` rows of the effective ledger (last row per document);
* a document the owner blocked stays out (`blocked_document`), and so does every other document
  of its opportunity (`opportunity_has_blocked_document`, gate G3: the CRM moves per whole
  opportunity);
* W116 / Labdelivery-only material never enters (`labdelivery_only`);
* the number recorded is the number printed, exactly (`number_not_printed`) — nothing is minted,
  padded, suffixed or normalised. "Printed" means the identity report read it from the document,
  or it occurs verbatim after `COTIZACIÓN N°` in the document's extracted text (the parser misses
  multi-letter suffixes such as `011453AI-26`). When the ledger wrote a number without the
  leading zero the document prints (`1218-26` for a document printing only `01218-26`), the
  printed form is recorded and the difference is reported (`ledger_number_written_without_leading_zero`);
* two versions the owner linked across *different* printed numbers (AI → AII) stay two quotes,
  each with its own number, reported as `version_across_numbers` — no supersession is invented;
* every occurrence must have its Gmail message in the staging (`evidence_not_staged`);
* versions: an owner-designated canonical supersedes its prior; the canonical must be the later
  document (`canonical_older_than_prior`); a number with several documents and no designation
  imports every document and no supersession (`canonical_undetermined`, a warning);
* a document the owner explicitly left pending — a `leave_pending` ledger row with a document
  `pending` block (`build_document_pending`) — holds its opportunity (`document_left_pending`) and
  is listed in `pending_documents`; a later `confirm_customer_quotation` row for the same document
  supersedes it, append-only. A malformed pending record refuses the whole plan;
* the organization is **never** inferred. An exact name match is a suggestion. An opportunity
  moves only when an operator confirmation names what to do (`needs_organization_confirmation`).
  Contacts are suggestions only; nothing here writes a person or a participant;
* evidence is staged only for opportunities that are `ready` (confirmed organization): the
  manifest carries their Gmail messages and only the observations about their documents. Evidence
  of an opportunity still waiting for confirmation, or held, stays pending. `evidence_scope_violations`
  re-checks this on the finished intent, and an apply refuses any plan it objects to.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from origenlab_api.v2.case_commands import (
    ADD_CASE_ORGANIZATION,
    ADVANCE_CASE_STAGE,
    LINK_CASE_EVIDENCE,
    OPEN_COMMERCIAL_CASE,
    REQUESTING_INSTITUTION,
)
from origenlab_api.v2.commands import (
    CONFIRM_ORGANIZATION,
    CREATE_ORGANIZATION,
    normalize_email,
    normalize_organization_name,
)
from origenlab_api.v2.quote_import_commands import QUOTE_NUMBER_SHAPE, RECORD_HISTORICAL_QUOTATION
from origenlab_api.v2.quote_import_repository import document_reference_value

PLAN_VERSION = "qimport/2026-09-25.1"
KEY_PREFIX = "qimport:v1"
CONFIRM = "confirm_customer_quotation"
LEAVE_PENDING = "leave_pending"
#: Why an owner may leave a whole document pending. A closed list: a new reason is a code change.
PENDING_NO_ORGANIZATION = "owner_hold_no_organization"
PENDING_CODES = frozenset({PENDING_NO_ORGANIZATION})
PENDING_NAMESPACE = uuid.UUID("5d0c4f3e-6a51-4d1b-9a54-2f4f0e0c1a43")
STAGE_PATH = ("qualifying", "qualified", "quoting")

#: Our own mailboxes and domains: never a customer contact suggestion.
OWN_DOMAINS = frozenset({"origenlab.cl", "labdelivery.cl"})
LABDELIVERY_QUESTIONS = frozenset({"W116"})

_ADDRESS = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Reason codes.
R_BLOCKED = "blocked_document"
R_LEFT_PENDING = "document_left_pending"
R_OPP_BLOCKED = "opportunity_has_blocked_document"
R_LABDELIVERY = "labdelivery_only"
R_NOT_PRINTED = "number_not_printed"
R_BAD_SHAPE = "number_shape_rejected_by_crm"
R_NOT_STAGED = "evidence_not_staged"
R_NO_OPP = "no_planned_opportunity"
R_CANONICAL_OLDER = "canonical_older_than_prior"
R_DUPLICATE_CREATION = "confirmation_creates_duplicate_organization"
R_CREATE_WITHOUT_PRINTED_ORG = "confirmation_creates_without_printed_organization"
W_VERSION_ACROSS_NUMBERS = "version_across_numbers"
W_LEADING_ZERO = "ledger_number_written_without_leading_zero"
W_CANONICAL_UNDETERMINED = "canonical_undetermined"
W_NUMBER_OTHER_OPP = "number_on_other_opportunity"
S_NEEDS_ORG = "needs_organization_confirmation"
S_READY = "ready"
S_HELD = "held"

#: Which opportunities' evidence the manifest carries. `ready` is the only scope an apply accepts.
#: `unheld` reproduces the intent the organization-confirmation requests of 2026-09-25 were issued
#: against (evidence of every opportunity not held); it exists only so the staleness check can
#: recompute that hash, never to build a plan to apply.
EVIDENCE_SCOPE_READY = "ready"
EVIDENCE_SCOPE_UNHELD = "unheld"
V_READY_WITHOUT_CONFIRMATION = "ready_without_confirmation"
V_EVIDENCE_OUTSIDE_READY = "evidence_outside_ready_opportunities"
V_OBSERVATION_OUTSIDE_READY = "observation_about_unready_document"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def key(*parts: str) -> str:
    raw = ":".join((KEY_PREFIX, *parts))
    return raw if len(raw) <= 200 else f"{KEY_PREFIX}:h:{sha256_text(raw)}"


def effective_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        out[row["document_sha256"]] = row
    return out


def planned_opportunity(row: Mapping[str, Any]) -> str | None:
    opp = row.get("opportunity") or {}
    return opp.get("opportunity_id") or opp.get("planned_opportunity_id")


# ─── document-level leave_pending ──────────────────────────────────────────────────────────


def is_document_pending(row: Mapping[str, Any]) -> bool:
    """A `leave_pending` row about a whole document. An email-review `leave_pending` (no document
    `pending` block) is not one, and stays out of the plan as before."""
    return row.get("decision") == LEAVE_PENDING and (row.get("pending") or {}).get("scope") == "document"


def document_pending_problems(row: Mapping[str, Any]) -> list[str]:
    """Why this document-level pending record may not stand in the ledger. Empty means it may."""
    out = []
    pend = row.get("pending") or {}
    if row.get("decision") != LEAVE_PENDING or row.get("target") != "quote_document" or pend.get("scope") != "document":
        out.append("not a document-level leave_pending row")
    if pend.get("code") not in PENDING_CODES:
        out.append(f"pending code {pend.get('code')!r} is not one of {', '.join(sorted(PENDING_CODES))}")
    for f in ("detail", "owner_instruction"):
        if not str(pend.get(f) or "").strip():
            out.append(f"pending.{f} is required")
    for f in ("decision_id", "document_sha256", "operator", "quote_number", "recorded_at"):
        if not str(row.get(f) or "").strip():
            out.append(f"{f} is required")
    if not planned_opportunity(row):
        out.append("the opportunity the document belongs to is required")
    if not row.get("occurrences"):
        out.append("the document's occurrences (provenance) are required")
    if "shadow" in row:
        out.append("a shadow row is not a ledger record")
    return out


def build_document_pending(
    template: Mapping[str, Any], *, code: str, detail: str, operator: str, owner_instruction: str, recorded_at: str,
) -> dict[str, Any]:
    """The ledger row that leaves one whole document pending. Writes nothing.

    `template` is the document's `confirm_customer_quotation` row as it would be recorded; the
    pending row keeps its document, number, opportunity and provenance, so the later confirmation
    resolves the same planned opportunity. Deterministic: its id derives from document and code."""
    if template.get("decision") != CONFIRM:
        raise ValueError(f"the template must be a {CONFIRM} row, not {template.get('decision')!r}")
    if code not in PENDING_CODES:
        raise ValueError(f"pending code {code!r} is not one of {', '.join(sorted(PENDING_CODES))}")
    row = json.loads(canonical_json({k: v for k, v in template.items() if k != "shadow"}))
    row.update({
        "decision": LEAVE_PENDING, "target": "quote_document", "operator": operator, "recorded_at": recorded_at,
        "decision_id": str(uuid.uuid5(PENDING_NAMESPACE, f"{row['document_sha256']}:{LEAVE_PENDING}:{code}")),
        "reason": detail, "applied": False,
        "pending": {"scope": "document", "code": code, "detail": detail, "owner_instruction": owner_instruction,
                    "resolved_by": f"a later {CONFIRM} row for this document"},
    })
    problems = document_pending_problems(row)
    if problems:
        raise ValueError("; ".join(problems))
    return row


def _sent_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _client(identity: Mapping[str, Any] | None) -> Mapping[str, Any]:
    clients = (identity or {}).get("clients") or []
    return clients[0] if clients else {}


def _value(v: Any) -> str | None:
    return v.get("value") if isinstance(v, Mapping) else v


_PRINTED_NUMBER = re.compile(r"COTIZACI[OÓ]N\s*N\s*[°º.o]?\s*([0-9][0-9A-Za-z]*-[0-9]{2})(?![0-9A-Za-z])")


def printed_numbers_in_text(text: str) -> set[str]:
    """Numbers that occur verbatim after `COTIZACIÓN N°` in a document's extracted text."""
    return set(_PRINTED_NUMBER.findall(text))


def recipient_addresses(raw: str | None) -> list[str]:
    out = set()
    for match in _ADDRESS.findall(raw or ""):
        addr = normalize_email(match)
        if addr.rsplit("@", 1)[-1] not in OWN_DOMAINS:
            out.add(addr)
    return sorted(out)


# ─── the pieces of a plan ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Revision:
    document_sha256: str
    quote_number: str
    printed_quote_numbers: tuple[str, ...]
    sent_at: str
    origin_dedupe_key: str
    ledger_decision_id: str
    filename: str | None
    supersedes_document_sha256: str | None


@dataclass
class OpportunityPlan:
    planned_id: str
    documents: list[str] = field(default_factory=list)
    revisions: list[Revision] = field(default_factory=list)
    source_dedupe_keys: list[str] = field(default_factory=list)  # origin first
    status: str = S_NEEDS_ORG
    reasons: list[str] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    printed_organization: str | None = None
    printed_addressee: str | None = None
    recipients: list[str] = field(default_factory=list)
    confirmation: Mapping[str, Any] | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)


def _manifest_record(staged: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(staged["payload"])
    payload["intake_class"] = "primary_evidence"
    payload["gmail_labels"] = list(payload.get("gmail_label_ids") or [])
    payload["staging_source_record_sha256"] = sha256_text(canonical_json(staged))
    return {
        "external_id": payload["gmail_message_id"],
        "source_uri": staged.get("source_uri") or f"gmail://msg/{payload['gmail_message_id']}",
        "payload": payload,
        "observations": [],
    }


def build_plan(
    ledger_rows: Sequence[Mapping[str, Any]],
    identity_docs: Mapping[str, Mapping[str, Any]],
    staged_by_email: Mapping[int, Mapping[str, Any]],
    blocked_documents: Mapping[str, Sequence[str]],
    blocked_document_opportunities: Mapping[str, str],
    confirmations: Mapping[str, Mapping[str, Any]],
    printed_in_text: Mapping[str, Iterable[str]] | None = None,
    *,
    acquired_at: str,
    note: str,
    evidence_scope: str = EVIDENCE_SCOPE_READY,
) -> dict[str, Any]:
    """The intent. Pure: reads its arguments only, returns plain JSON-able data."""
    if evidence_scope not in {EVIDENCE_SCOPE_READY, EVIDENCE_SCOPE_UNHELD}:
        raise ValueError(f"unknown evidence scope {evidence_scope!r}")
    effective = effective_rows(ledger_rows)
    held_opps: dict[str, set[str]] = defaultdict(set)
    for sha, opp in blocked_document_opportunities.items():
        held_opps[opp].add(sha)

    documents: list[dict[str, Any]] = []
    opps: dict[str, OpportunityPlan] = {}
    manifest: dict[str, dict[str, Any]] = {}  # dedupe_key → record
    # dedupe_key → every observation proposed for it, with the document it is about, in order.
    candidates: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    number_opps: dict[str, set[str]] = defaultdict(set)

    for r in effective.values():
        if r.get("decision") == LEAVE_PENDING and "pending" in r and document_pending_problems(r):
            raise ValueError(f"leave_pending record for {r.get('document_sha256')}: "
                             + "; ".join(document_pending_problems(r)))
    left_pending = sorted((r for r in effective.values() if is_document_pending(r)),
                          key=lambda r: r["document_sha256"])
    confirmed = [r for r in effective.values() if r.get("decision") == CONFIRM or is_document_pending(r)]
    confirmed.sort(key=lambda r: (planned_opportunity(r) or "", r["document_sha256"]))
    for row in confirmed:
        number_opps[(row.get("quote_number") or "").lstrip("0")].add(planned_opportunity(row) or "")

    for row in confirmed:
        sha = row["document_sha256"]
        opp_id = planned_opportunity(row)
        phase3 = row.get("phase3") or {}
        reasons: list[str] = []
        if sha in blocked_documents:
            reasons.append(R_BLOCKED)
        if is_document_pending(row):
            reasons.append(R_LEFT_PENDING)
        if LABDELIVERY_QUESTIONS & set(phase3.get("questions") or []):
            reasons.append(R_LABDELIVERY)
        number = row.get("quote_number") or ""
        identity_printed = [q["raw"] for q in (identity_docs.get(sha) or {}).get("quote_numbers_found") or []]
        printed = tuple(sorted(set(row.get("printed_quote_numbers") or ()) | set(identity_printed)
                               | set((printed_in_text or {}).get(sha, ()))))
        number_warning = None
        if number not in printed:
            padded = [p for p in printed if p.lstrip("0") == number.lstrip("0")]
            if len(printed) == 1 and len(padded) == 1:
                number_warning = {"code": W_LEADING_ZERO, "document_sha256": sha, "ledger": number,
                                  "printed": padded[0]}
                number = padded[0]
            else:
                reasons.append(R_NOT_PRINTED)
        if not QUOTE_NUMBER_SHAPE.match(number):
            reasons.append(R_BAD_SHAPE)
        if not opp_id:
            reasons.append(R_NO_OPP)
        occurrences = sorted(row.get("occurrences") or [], key=lambda o: _sent_at(o["sent_at"]))
        staged = [staged_by_email.get(int(o["email_id"])) for o in occurrences]
        if not occurrences or any(s is None or s.get("kind") != "gmail_message" for s in staged):
            reasons.append(R_NOT_STAGED)
        documents.append({"document_sha256": sha, "quote_number": number, "planned_opportunity_id": opp_id,
                          "ledger_decision_id": row["decision_id"], "reasons": reasons})
        if not opp_id:
            continue
        plan = opps.setdefault(opp_id, OpportunityPlan(planned_id=opp_id))
        plan.documents.append(sha)
        if number_warning:
            plan.warnings.append(number_warning)
        if reasons:
            plan.reasons.extend(f"{sha[:12]}:{r}" for r in reasons)
            continue
        canonical_staged = staged[0]
        assert canonical_staged is not None  # noqa: S101 - R_NOT_STAGED above
        origin_key = canonical_staged["dedupe_key"]
        for s in staged:
            assert s is not None  # noqa: S101
            if s["dedupe_key"] not in plan.source_dedupe_keys:
                plan.source_dedupe_keys.append(s["dedupe_key"])
            manifest.setdefault(s["dedupe_key"], _manifest_record(s))
            candidates[s["dedupe_key"]].append((sha, {
                "kind": "document_reference", "value": document_reference_value(sha),
                "value_payload": {"quote_number": number, "filename": occurrences[0].get("filename"),
                                  "ledger_decision_id": row["decision_id"]}}))
        client = _client(identity_docs.get(sha))
        org_name = client.get("organization")
        if org_name:
            candidates[origin_key].append((sha, {
                "kind": "organization_name", "value": org_name,
                "value_payload": {"from": "pdf_client_block", "document_sha256": sha}}))
            plan.printed_organization = plan.printed_organization or org_name
        plan.printed_addressee = plan.printed_addressee or _value(client.get("addressee"))
        for addr in recipient_addresses(canonical_staged["payload"].get("recipients")):
            if addr not in plan.recipients:
                plan.recipients.append(addr)
        plan.revisions.append(Revision(
            document_sha256=sha, quote_number=number, printed_quote_numbers=printed,
            sent_at=occurrences[0]["sent_at"], origin_dedupe_key=origin_key,
            ledger_decision_id=row["decision_id"], filename=occurrences[0].get("filename"),
            supersedes_document_sha256=None,
        ))

    # ── versions, holds, confirmations, steps ─────────────────────────────────────────────
    by_sha = {r["document_sha256"]: r for r in confirmed}
    for plan in opps.values():
        if plan.planned_id in held_opps:
            plan.reasons.extend(f"{s[:12]}:{R_OPP_BLOCKED}" for s in sorted(held_opps[plan.planned_id]))
        if not plan.reasons:
            _order_versions(plan, by_sha)
        for number in sorted({r.quote_number for r in plan.revisions}):
            others = sorted(number_opps[number.lstrip("0")] - {plan.planned_id})
            if others:
                plan.warnings.append({"code": W_NUMBER_OTHER_OPP, "quote_number": number, "others": others})
        if plan.reasons:
            plan.status = S_HELD
            plan.steps = []
            continue
        plan.confirmation = confirmations.get(plan.planned_id)
        plan.status = S_READY if plan.confirmation else S_NEEDS_ORG
        plan.steps = _steps(plan, note)

    # Two confirmations may not create the same organization twice: the second must name the
    # first (confirm_existing) once it exists. Refused here, before anything is written.
    creators: dict[str, str] = {}
    for plan in sorted(opps.values(), key=lambda p: p.planned_id):
        conf = plan.confirmation or {}
        if plan.status != S_READY or conf.get("action") != "create_from_evidence":
            continue
        if not plan.printed_organization:
            plan.status = S_NEEDS_ORG
            plan.warnings.append({"code": R_CREATE_WITHOUT_PRINTED_ORG,
                                  "detail": "the document prints no institution, so there is no "
                                            "organization_name evidence to create one from"})
            continue
        name_key = normalize_organization_name(plan.printed_organization)
        if name_key in creators:
            plan.status = S_NEEDS_ORG
            plan.warnings.append({"code": R_DUPLICATE_CREATION, "same_as": creators[name_key],
                                  "detail": "another confirmation already creates this organization; "
                                            "confirm that one instead"})
            continue
        creators[name_key] = plan.planned_id

    # Evidence is staged only for ready opportunities, and a record carries only the observations
    # about their documents: a waiting or held opportunity's evidence stays pending.
    in_scope = [p for p in opps.values()
                if p.status == S_READY or (evidence_scope == EVIDENCE_SCOPE_UNHELD and p.status != S_HELD)]
    wanted = {k for p in in_scope for k in p.source_dedupe_keys}
    scope_docs = {d for p in in_scope for d in p.documents}
    records = []
    for dk in sorted(wanted):
        rec = dict(manifest[dk])
        rec["acquired_at"] = acquired_at
        rec["observations"] = _observations([o for d, o in candidates[dk] if d in scope_docs])
        records.append(rec)

    opp_rows = [_opp_dict(p) for p in sorted(opps.values(), key=lambda p: p.planned_id)]
    intent = {
        "plan_version": PLAN_VERSION,
        "documents": documents,
        "opportunities": opp_rows,
        "evidence_manifest": {
            "manifest_version": 2, "provider": "gmail",
            "note": f"{PLAN_VERSION}: sent Gmail messages carrying owner-confirmed quotations",
            "records": records,
        },
        "blocked_documents": sorted(
            ({"document_sha256": s, "reasons": list(r)} for s, r in blocked_documents.items()),
            key=lambda d: d["document_sha256"]),
    }
    if left_pending:  # absent otherwise, so a ledger without such rows plans byte-identically
        intent["pending_documents"] = [
            {"document_sha256": r["document_sha256"], "quote_number": r["quote_number"],
             "planned_opportunity_id": planned_opportunity(r), "code": r["pending"]["code"],
             "ledger_decision_id": r["decision_id"]} for r in left_pending]
    if evidence_scope == EVIDENCE_SCOPE_READY:
        intent["evidence_scope"] = EVIDENCE_SCOPE_READY
    intent["summary"] = summarize(intent)
    return intent


def _observations(proposed: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One document_reference per document, one organization_name per folded name (first wins)."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for o in proposed:
        k = (o["kind"], normalize_organization_name(o["value"]) if o["kind"] == "organization_name" else o["value"])
        if k not in seen:
            seen.add(k)
            out.append(dict(o))
    return sorted(out, key=lambda o: (o["kind"], o["value"]))


def _observation_document(obs: Mapping[str, Any]) -> str | None:
    if obs["kind"] == "document_reference":
        return str(obs["value"]).removeprefix("sha256:")
    return (obs.get("value_payload") or {}).get("document_sha256")


def evidence_scope_violations(intent: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Why this intent may not be applied: evidence for anything but a confirmed, ready opportunity.

    Checked on the finished intent — independently of how `build_plan` scoped it — so an intent
    built with another scope, edited by hand or produced by a future bug is refused before the
    first write. Empty means every evidence record and observation belongs to an opportunity that
    is `ready` *and* carries an operator confirmation.
    """
    out: list[dict[str, Any]] = []
    if intent.get("evidence_scope") != EVIDENCE_SCOPE_READY:
        out.append({"code": V_EVIDENCE_OUTSIDE_READY, "detail": f"evidence_scope is {intent.get('evidence_scope')!r}"})
    owners: dict[str, list[str]] = defaultdict(list)
    doc_owner: dict[str, str] = {}
    allowed_keys: set[str] = set()
    allowed_docs: set[str] = set()
    status = {o["planned_opportunity_id"]: o["status"] for o in intent["opportunities"]}
    for o in intent["opportunities"]:
        pid = o["planned_opportunity_id"]
        for k in o.get("source_dedupe_keys") or []:
            owners[k].append(pid)
        for d in o.get("documents") or []:
            doc_owner[d] = pid
        confirmed = (o.get("confirmation") or {}).get("action") in {"confirm_existing", "create_from_evidence"}
        if o["status"] == S_READY and not confirmed:
            out.append({"code": V_READY_WITHOUT_CONFIRMATION, "planned_opportunity_id": pid})
        if o["status"] == S_READY and confirmed:
            allowed_keys.update(o.get("source_dedupe_keys") or [])
            allowed_docs.update(o.get("documents") or [])
    for rec in intent["evidence_manifest"]["records"]:
        dk = f"gmail_message:{rec['external_id']}"
        if dk not in allowed_keys:
            out.append({"code": V_EVIDENCE_OUTSIDE_READY, "dedupe_key": dk,
                        "opportunities": [{"planned_opportunity_id": p, "status": status[p]} for p in owners.get(dk, [])]})
        for obs in rec["observations"]:
            doc = _observation_document(obs)
            if doc not in allowed_docs:
                out.append({"code": V_OBSERVATION_OUTSIDE_READY, "dedupe_key": dk, "kind": obs["kind"],
                            "document_sha256": doc, "planned_opportunity_id": doc_owner.get(doc or ""),
                            "status": status.get(doc_owner.get(doc or ""))})
    return out


def _order_versions(plan: OpportunityPlan, by_sha: Mapping[str, Mapping[str, Any]]) -> None:
    """Revisions of one number in sent order; an owner canonical supersedes its prior."""
    by_number: dict[str, list[Revision]] = defaultdict(list)
    for rev in plan.revisions:
        by_number[rev.quote_number].append(rev)
    ordered: list[Revision] = []
    for number in sorted(by_number):
        revs = sorted(by_number[number], key=lambda r: (_sent_at(r.sent_at), r.document_sha256))
        roles = {r.document_sha256: (by_sha[r.document_sha256].get("phase3") or {}) for r in revs}
        canonicals = [r for r in revs if roles[r.document_sha256].get("version_role") == "canonical"]
        for prior in (r for r in revs if roles[r.document_sha256].get("version_role") == "prior"):
            target = roles[prior.document_sha256].get("canonical_sha256")
            if target not in {r.document_sha256 for r in revs}:
                plan.warnings.append({"code": W_VERSION_ACROSS_NUMBERS, "prior": prior.document_sha256,
                                      "canonical": target, "quote_number": number,
                                      "detail": "the owner linked these as versions; they print different "
                                                "numbers, so they stay two quotes and nothing is superseded"})
        if len(revs) > 1 and not canonicals:
            plan.warnings.append({"code": W_CANONICAL_UNDETERMINED, "quote_number": number,
                                  "documents": [r.document_sha256 for r in revs]})
        for canonical in canonicals:
            priors = [r for r in revs if roles[r.document_sha256].get("canonical_sha256") == canonical.document_sha256
                      and r is not canonical]
            if any(_sent_at(p.sent_at) > _sent_at(canonical.sent_at) for p in priors):
                plan.reasons.append(f"{canonical.document_sha256[:12]}:{R_CANONICAL_OLDER}")
                continue
            if priors:
                latest = max(priors, key=lambda p: (_sent_at(p.sent_at), p.document_sha256))
                idx = revs.index(canonical)
                revs[idx] = Revision(**{**canonical.__dict__, "supersedes_document_sha256": latest.document_sha256})
                if len(priors) > 1:
                    plan.warnings.append({"code": W_CANONICAL_UNDETERMINED, "quote_number": number,
                                          "detail": "several priors; only the latest is superseded"})
        ordered.extend(revs)
    plan.revisions = ordered


def _steps(plan: OpportunityPlan, note: str) -> list[dict[str, Any]]:
    """Command templates. `{"$ref": ...}` values are resolved by the executor at run time."""
    pid = plan.planned_id
    origin = plan.source_dedupe_keys[0]
    numbers = ", ".join(sorted({r.quote_number for r in plan.revisions}))
    who = plan.printed_organization or plan.printed_addressee or "sin institución impresa"
    steps: list[dict[str, Any]] = [{
        "name": "open", "command": OPEN_COMMERCIAL_CASE, "key": key(pid, "open"),
        "body": {"title": f"Cotización {numbers} — {who}"[:400],
                 "origin_source_record_id": {"$ref": "source", "dedupe_key": origin},
                 "note": f"{note} Oportunidad planificada {pid}."},
    }]
    for dk in plan.source_dedupe_keys[1:]:
        steps.append({
            "name": f"link:{dk}", "command": LINK_CASE_EVIDENCE, "key": key(pid, "link", dk),
            "body": {"opportunity_id": {"$ref": "opportunity"}, "opportunity_version": {"$ref": "opportunity_version"},
                     "relation": "mentions", "source_record_id": {"$ref": "source", "dedupe_key": dk},
                     "note": "Reenvío o copia del mismo documento de cotización."},
        })
    conf = plan.confirmation or {}
    org_ref: dict[str, Any]
    if conf.get("action") == "create_from_evidence":
        steps.append({
            "name": "organization", "command": CREATE_ORGANIZATION, "key": key(pid, "org:create"),
            "body": {"source_record_id": {"$ref": "source", "dedupe_key": origin},
                     "assertion_id": {"$ref": "assertion", "dedupe_key": origin, "kind": "organization_name",
                                      "value_norm": normalize_organization_name(plan.printed_organization or "")},
                     "kind": conf.get("kind", "institution"),
                     **({"name_display": conf["name_display"]} if conf.get("name_display") else {}),
                     "note": conf.get("note") or "Institución confirmada por el operador."},
        })
        org_ref = {"organization_id": {"$ref": "step", "step": "organization", "field": "organization_id"},
                   "organization_version": {"$ref": "step", "step": "organization", "field": "organization_version"}}
    else:
        org_ref = {"organization_id": conf.get("organization_id") or {"$ref": "confirmation_required"},
                   "organization_version": conf.get("organization_version") or {"$ref": "confirmation_required"}}
    steps.append({
        "name": "requesting_institution", "command": ADD_CASE_ORGANIZATION, "key": key(pid, "org:requesting"),
        "body": {"opportunity_id": {"$ref": "opportunity"}, "opportunity_version": {"$ref": "opportunity_version"},
                 **org_ref, "role": REQUESTING_INSTITUTION,
                 "note": conf.get("note") or "Institución solicitante confirmada por el operador."},
    })
    for stage in STAGE_PATH:
        steps.append({
            "name": f"stage:{stage}", "command": ADVANCE_CASE_STAGE, "key": key(pid, "stage", stage),
            "body": {"opportunity_id": {"$ref": "opportunity"}, "opportunity_version": {"$ref": "opportunity_version"},
                     "stage": stage, "note": "Cotización histórica ya enviada al cliente."},
        })
    for rev in plan.revisions:
        body: dict[str, Any] = {
            "opportunity_id": {"$ref": "opportunity"},
            "quote_number": rev.quote_number,
            "printed_quote_numbers": list(rev.printed_quote_numbers),
            "document_sha256": rev.document_sha256,
            "sent_at": rev.sent_at,
            "origin_source_record_id": {"$ref": "source", "dedupe_key": rev.origin_dedupe_key},
            "ledger_decision_id": rev.ledger_decision_id,
            "note": f"Documento confirmado en document_decisions.jsonl ({rev.ledger_decision_id}).",
        }
        if rev.filename:
            body["filename"] = rev.filename[:400]
        if rev.supersedes_document_sha256:
            body["supersedes_document_sha256"] = rev.supersedes_document_sha256
        steps.append({"name": f"quote:{rev.document_sha256[:12]}", "command": RECORD_HISTORICAL_QUOTATION,
                      "key": key(pid, "quote", rev.document_sha256), "body": body})
    return steps


def _opp_dict(p: OpportunityPlan) -> dict[str, Any]:
    return {
        "planned_opportunity_id": p.planned_id,
        "status": p.status,
        "reasons": sorted(set(p.reasons)),
        "warnings": p.warnings,
        "documents": sorted(p.documents),
        "quotes": [
            {"quote_number": n, "revisions": [
                {"document_sha256": r.document_sha256, "sent_at": r.sent_at,
                 "supersedes_document_sha256": r.supersedes_document_sha256,
                 "origin_dedupe_key": r.origin_dedupe_key, "ledger_decision_id": r.ledger_decision_id}
                for r in p.revisions if r.quote_number == n]}
            for n in sorted({r.quote_number for r in p.revisions})
        ],
        "source_dedupe_keys": p.source_dedupe_keys,
        "printed_organization": p.printed_organization,
        "printed_addressee": p.printed_addressee,
        "recipient_addresses": p.recipients,
        "confirmation": dict(p.confirmation) if p.confirmation else None,
        "steps": p.steps,
    }


def summarize(intent: Mapping[str, Any]) -> dict[str, Any]:
    opps = intent["opportunities"]
    by_status: dict[str, int] = defaultdict(int)
    for o in opps:
        by_status[o["status"]] += 1
    docs = intent["documents"]
    steps = [s for o in opps for s in o["steps"]]
    by_cmd: dict[str, int] = defaultdict(int)
    for s in steps:
        by_cmd[s["command"]] += 1
    ready_steps = [s for o in opps if o["status"] == S_READY for s in o["steps"]]
    pending = {"pending_documents": len(intent["pending_documents"])} if intent.get("pending_documents") else {}
    return {
        **pending,
        "confirmed_documents": len(docs),
        "documents_with_reasons": sum(1 for d in docs if d["reasons"]),
        "owner_blocked_documents": len(intent["blocked_documents"]),
        "opportunities": len(opps),
        "opportunities_by_status": dict(sorted(by_status.items())),
        "quotes": sum(len(o["quotes"]) for o in opps if o["status"] != S_HELD),
        "revisions": sum(len(q["revisions"]) for o in opps if o["status"] != S_HELD for q in o["quotes"]),
        "evidence_records": len(intent["evidence_manifest"]["records"]),
        "evidence_assertions": sum(len(r["observations"]) for r in intent["evidence_manifest"]["records"]),
        "command_templates": len(steps),
        "command_templates_by_command": dict(sorted(by_cmd.items())),
        "commands_ready_to_run": len(ready_steps),
        "idempotency_keys_unique": len({s["key"] for s in steps}) == len(steps),
    }


def plan_sha256(intent: Mapping[str, Any]) -> str:
    return sha256_text(canonical_json(intent))


# ─── what the target already holds ─────────────────────────────────────────────────────────


@dataclass
class TargetState:
    """Read once, read-only, from the target database."""

    source_records: dict[str, str] = field(default_factory=dict)  # dedupe_key → payload sha
    assertions: set[tuple[str, str, str]] = field(default_factory=set)  # (dedupe_key, kind, value_norm)
    receipts: dict[str, str] = field(default_factory=dict)  # key → status (for the load operator)
    organizations: list[tuple[str, str, str | None, str, int]] = field(default_factory=list)
    contact_points: dict[str, str] = field(default_factory=dict)  # value_norm → id
    quote_documents: set[str] = field(default_factory=set)  # pdf_sha256 already a revision
    operator_registered: bool = False


def plan_state(intent: Mapping[str, Any], state: TargetState) -> dict[str, Any]:
    """Per write: would it happen, or is it already done? Plus suggestions (never writes)."""
    evidence_rows = []
    for rec in intent["evidence_manifest"]["records"]:
        dk = f"gmail_message:{rec['external_id']}"
        present = dk in state.source_records
        obs = [{"kind": o["kind"], "value": o["value"],
                "state": "present" if (dk, o["kind"], _fold(o["kind"], o["value"])) in state.assertions else "would_write"}
               for o in rec["observations"]]
        evidence_rows.append({"dedupe_key": dk, "state": "present" if present else "would_write", "assertions": obs})
    opp_rows = []
    for o in intent["opportunities"]:
        steps = [{"name": s["name"], "command": s["command"], "key": s["key"],
                  "state": ("done" if state.receipts.get(s["key"]) == "completed"
                            else "would_write" if o["status"] == S_READY else "waits_for_confirmation")}
                 for s in o["steps"]]
        opp_rows.append({
            "planned_opportunity_id": o["planned_opportunity_id"], "status": o["status"],
            "organization_suggestions": suggest_organizations(o.get("printed_organization"), state),
            "contact_suggestions": [{"address": a, "contact_point_id": state.contact_points.get(a)}
                                    for a in o.get("recipient_addresses") or []],
            "steps": steps,
        })
    writes = [s for o in opp_rows for s in o["steps"] if s["state"] == "would_write"]
    ev_writes = sum(1 for e in evidence_rows if e["state"] == "would_write")
    as_writes = sum(1 for e in evidence_rows for a in e["assertions"] if a["state"] == "would_write")
    return {
        "operator_registration": "present" if state.operator_registered else "would_write",
        "evidence": evidence_rows,
        "opportunities": opp_rows,
        "totals": {
            "operator_would_write": 0 if state.operator_registered else 1,
            "source_records_would_write": ev_writes,
            "assertions_would_write": as_writes,
            "commands_would_write": len(writes),
            "commands_done": sum(1 for o in opp_rows for s in o["steps"] if s["state"] == "done"),
            "commands_waiting_for_confirmation": sum(
                1 for o in opp_rows for s in o["steps"] if s["state"] == "waits_for_confirmation"),
        },
    }


def _fold(kind: str, value: str) -> str:
    return " ".join(value.strip().split()).lower()


def suggest_organizations(printed: str | None, state: TargetState) -> list[dict[str, Any]]:
    """Exact equality under the command's fold. Never similarity. Always a suggestion."""
    if not printed:
        return []
    k = normalize_organization_name(printed)
    return [{"organization_id": i, "name": n, "confirmation": c, "version": v, "match": "exact_name"}
            for i, n, ln, c, v in state.organizations
            if k in {normalize_organization_name(n), normalize_organization_name(ln or "")}]


def idempotency_rows(intent: Mapping[str, Any]) -> Iterable[dict[str, str]]:
    for o in intent["opportunities"]:
        for s in o["steps"]:
            yield {"planned_opportunity_id": o["planned_opportunity_id"], "status": o["status"],
                   "step": s["name"], "command": s["command"], "idempotency_key": s["key"]}

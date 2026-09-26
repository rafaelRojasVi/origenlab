"""Guarded rebase of operator organization confirmations onto a new no-confirmation plan.

An operator confirmation answers one specific plan: the no-confirmation intent its request sheet
was issued from (``evidence.plan_sha256``). That hash covers every input file, so appending one
unrelated document to the ledger stales every confirmation, although nothing the operator looked
at changed. Re-issuing and re-signing them all would ask the operator to decide again what they
already decided; editing them by hand would forge a decision.

A rebase does neither. For each confirmation it proves, per opportunity, that the evidence the
operator decided on is the same under the new plan, and only then moves the plan reference:

* the confirmation is genuine loader output that answers the *old* plan, and its request row is
  the one in the old request sheet (``request_sha256`` recomputed);
* the *new* request sheet — built by the same :func:`quote_organization_confirmation.build_requests`
  over the new plan and a fresh read-only CRM reading — carries that opportunity with the **same
  request_sha256**. The request row holds the printed institution, addressee, RUT, the exact CRM
  matches with their versions, recipients, documents, quote numbers and the recommendation;
* the organization the operator named is unchanged: a ``confirm_existing`` organization is still
  live at the confirmed version, and a ``create_from_evidence`` name still has no exact CRM match;
* the opportunity itself is unchanged in the plan (status, documents, quotes, steps, keys, reasons,
  warnings) and every ledger row of its documents is byte-identical in canonical JSON;
* the review policy, resolution policy and gates fingerprints are the approved ones on both sides.

A confirmation that fails any check is **refused** and left for the operator; the others are
rebased. The rebased record is the original with exactly one value changed —
``evidence.plan_sha256`` → the new basis — and one key added, ``rebase``, which records
``rebased_from_plan_hash`` (the old basis) and what was verified. Operator, timestamp, note,
action, organization and the original request sheet reference are preserved byte for byte.

Pure: no I/O. Writes nothing; the caller decides where the result goes.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from origenlab_api.v2 import quote_organization_confirmation as oc
from origenlab_api.v2.commands import normalize_organization_name
from origenlab_api.v2.quote_crm_import_plan import canonical_json, effective_rows, sha256_text

REBASE_VERSION = "orgconf-rebase/2026-09-26.1"

#: The opportunity fields that must be identical between the old and the new plan.
OPPORTUNITY_FIELDS = ("status", "documents", "quotes", "steps", "reasons", "warnings", "source_dedupe_keys",
                      "printed_organization", "printed_addressee", "recipient_addresses")

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

# Refusal codes, one per failed check.
R_NOT_LOADER_OUTPUT = "not_loader_output"
R_ALREADY_REBASED = "already_rebased"
R_ANSWERS_OTHER_PLAN = "answers_other_plan"
R_OLD_REQUESTS_MISMATCH = "old_requests_sheet_mismatch"
R_OLD_REQUEST_MISSING = "old_request_missing"
R_OLD_REQUEST_TAMPERED = "old_request_hash_mismatch"
R_NEW_REQUEST_MISSING = "opportunity_not_in_new_plan"
R_REQUEST_CHANGED = "request_changed"
R_ORGANIZATION_EVIDENCE_CHANGED = "organization_evidence_changed"
R_ORGANIZATION_GONE = "organization_not_live"
R_ORGANIZATION_VERSION = "organization_version_changed"
R_ORGANIZATION_NOW_EXISTS = "organization_to_create_now_exists"
R_OPPORTUNITY_CHANGED = "opportunity_plan_changed"
R_LEDGER_ROWS_CHANGED = "ledger_rows_changed"
R_FINGERPRINT = "fingerprint_not_approved"
R_DUPLICATE = "duplicate_confirmation"


@dataclass(frozen=True)
class Basis:
    """One side of the rebase: a no-confirmation plan and the request sheet issued over it."""

    plan_sha256: str
    intent: Mapping[str, Any]
    requests_doc: Mapping[str, Any]
    requests_sha256: str
    ledger_rows: Sequence[Mapping[str, Any]]
    #: name → fingerprint, as computed when this side was built (review policy, resolution, gates).
    fingerprints: Mapping[str, str]


@dataclass
class RebaseResult:
    rebased: list[dict[str, Any]] = field(default_factory=list)
    refused: list[dict[str, Any]] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.refused


def request_row_sha256(request: Mapping[str, Any]) -> str:
    """Recompute a request row's hash from its columns (``_facts`` and the hash itself excluded)."""
    return sha256_text(canonical_json({c: request[c] for c in oc.REQUEST_COLUMNS if c != "request_sha256"}))


def _opp_view(intent: Mapping[str, Any], pid: str) -> dict[str, Any] | None:
    for o in intent["opportunities"]:
        if o["planned_opportunity_id"] == pid:
            return {k: o.get(k) for k in OPPORTUNITY_FIELDS}
    return None


def _ledger_view(rows: Sequence[Mapping[str, Any]], documents: Sequence[str]) -> str:
    by_sha = effective_rows(rows)
    return sha256_text(canonical_json([by_sha.get(s) for s in sorted(documents)]))


def _refuse(result: RebaseResult, conf: Mapping[str, Any], code: str, detail: str) -> None:
    result.refused.append({"planned_opportunity_id": conf.get("planned_opportunity_id"), "code": code,
                           "detail": detail, "action": conf.get("action")})


def rebase_confirmations(
    confirmations: Sequence[Mapping[str, Any]],
    old: Basis,
    new: Basis,
    crm: oc.CrmReading,
    *,
    approved_fingerprints: Mapping[str, str],
    shadow_plan_sha256: str | None,
    rebased_at: str,
) -> RebaseResult:
    """Rebase each confirmation that passes every check; refuse, with a reason, each one that does not."""
    result = RebaseResult()
    # Fingerprints are global: if the policy or the gates moved, no decision carries over.
    bad = [f"{side}:{name}" for side, basis in (("old", old), ("new", new))
           for name, want in approved_fingerprints.items() if basis.fingerprints.get(name) != want]
    if bad:
        for c in confirmations:
            _refuse(result, c, R_FINGERPRINT, "not the approved fingerprint: " + ", ".join(bad))
        return result

    old_requests = {r["planned_opportunity_id"]: r for r in old.requests_doc["requests"]}
    new_requests = {r["planned_opportunity_id"]: r for r in new.requests_doc["requests"]}
    orgs = {o.organization_id: o for o in crm.organizations}
    exact_names = {normalize_organization_name(n) for o in crm.organizations for n in (o.name, o.legal_name or "")} - {""}
    seen: set[str] = set()

    for conf in confirmations:
        pid = conf.get("planned_opportunity_id") or ""
        ev = conf.get("evidence") or {}
        if pid in seen:
            _refuse(result, conf, R_DUPLICATE, "a second confirmation for this opportunity")
            continue
        seen.add(pid)
        if conf.get("simulation") or conf.get("loader_version") != oc.LOADER_VERSION \
                or ev.get("requests_version") != oc.REQUESTS_VERSION:
            _refuse(result, conf, R_NOT_LOADER_OUTPUT, f"only {oc.LOADER_VERSION} owner decisions are rebased")
            continue
        if "rebase" in conf:
            _refuse(result, conf, R_ALREADY_REBASED, "rebase the original loader output, not a rebased copy")
            continue
        if ev.get("plan_sha256") != old.plan_sha256:
            _refuse(result, conf, R_ANSWERS_OTHER_PLAN,
                    f"answers {ev.get('plan_sha256')}, the old basis is {old.plan_sha256}")
            continue
        if ev.get("requests_sha256") != old.requests_sha256:
            _refuse(result, conf, R_OLD_REQUESTS_MISMATCH,
                    f"issued from requests {ev.get('requests_sha256')}, the old sheet is {old.requests_sha256}")
            continue
        o_req = old_requests.get(pid)
        if o_req is None:
            _refuse(result, conf, R_OLD_REQUEST_MISSING, "the old request sheet has no row for this opportunity")
            continue
        if not (ev.get("request_sha256") == o_req["request_sha256"] == request_row_sha256(o_req)):
            _refuse(result, conf, R_OLD_REQUEST_TAMPERED,
                    "the confirmation's request_sha256, the old row's hash and its recomputed hash disagree")
            continue
        n_req = new_requests.get(pid)
        if n_req is None:
            _refuse(result, conf, R_NEW_REQUEST_MISSING, "the new plan has no request for this opportunity")
            continue
        if request_row_sha256(n_req) != n_req["request_sha256"]:
            _refuse(result, conf, R_REQUEST_CHANGED, "the new request row does not hash to its request_sha256")
            continue
        if n_req["request_sha256"] != o_req["request_sha256"]:
            changed = [c for c in oc.REQUEST_COLUMNS if c != "request_sha256" and o_req[c] != n_req[c]]
            _refuse(result, conf, R_REQUEST_CHANGED, "request columns changed: " + ", ".join(changed))
            continue
        # The organization evidence the confirmation itself restates (redundant with the request
        # hash by construction; checked separately so a refusal names it).
        restated = {
            "printed_organization": (n_req["printed_organization"] or None, ev.get("printed_organization")),
            "printed_ruts": (n_req["printed_ruts"].split(), ev.get("printed_ruts")),
            "documents": (n_req["documents"].split(), ev.get("documents")),
            "quote_numbers": (n_req["quote_numbers"].split(), ev.get("quote_numbers")),
            "exact_crm_matches": (n_req["_facts"]["exact_organization_ids"], ev.get("exact_crm_matches")),
            "category": (n_req["category"], ev.get("category")),
            "recommended_action": (n_req["recommended_action"], ev.get("recommended_action")),
        }
        drift = [k for k, (now, then) in restated.items() if now != then]
        if drift:
            _refuse(result, conf, R_ORGANIZATION_EVIDENCE_CHANGED, "differs from the confirmation: " + ", ".join(drift))
            continue
        if conf.get("action") == oc.CONFIRM_EXISTING:
            org = orgs.get(conf.get("organization_id") or "")
            if org is None:
                _refuse(result, conf, R_ORGANIZATION_GONE,
                        f"{conf.get('organization_id')} is not a live organization of {crm.database}")
                continue
            if org.version != conf.get("organization_version"):
                _refuse(result, conf, R_ORGANIZATION_VERSION,
                        f"organization is at version {org.version}, confirmed at {conf.get('organization_version')}")
                continue
        elif conf.get("action") == oc.CREATE_FROM_EVIDENCE:
            name = normalize_organization_name(conf.get("name_display") or n_req["printed_organization"])
            if name in exact_names:
                _refuse(result, conf, R_ORGANIZATION_NOW_EXISTS,
                        "the CRM now holds this exact name; the operator must confirm_existing it instead")
                continue
        else:
            _refuse(result, conf, R_NOT_LOADER_OUTPUT, f"action {conf.get('action')!r} is not rebased")
            continue
        o_opp, n_opp = _opp_view(old.intent, pid), _opp_view(new.intent, pid)
        if o_opp is None or n_opp is None or o_opp != n_opp:
            changed = sorted(k for k in OPPORTUNITY_FIELDS if (o_opp or {}).get(k) != (n_opp or {}).get(k))
            _refuse(result, conf, R_OPPORTUNITY_CHANGED, "plan fields changed: " + ", ".join(changed))
            continue
        documents = n_req["documents"].split()
        ledger_old, ledger_new = _ledger_view(old.ledger_rows, documents), _ledger_view(new.ledger_rows, documents)
        if ledger_old != ledger_new:
            _refuse(result, conf, R_LEDGER_ROWS_CHANGED, "a ledger row of this opportunity's documents changed")
            continue

        rebased = copy.deepcopy(dict(conf))
        rebased["evidence"]["plan_sha256"] = new.plan_sha256
        rebased["rebase"] = {
            "rebase_version": REBASE_VERSION,
            "rebased_from_plan_hash": old.plan_sha256,
            "rebased_to_plan_hash": new.plan_sha256,
            "shadow_plan_sha256": shadow_plan_sha256,
            "old_requests_sha256": old.requests_sha256,
            "new_requests_sha256": new.requests_sha256,
            "request_sha256": n_req["request_sha256"],
            "ledger_rows_sha256": ledger_new,
            "fingerprints": dict(sorted(approved_fingerprints.items())),
            "crm_database": crm.database,
            "rebased_at": rebased_at,
            "verified": ["loader_output", "answers_old_plan", "old_request_row", "request_sha256_unchanged",
                         "organization_evidence_unchanged", "organization_live_and_version" if
                         conf["action"] == oc.CONFIRM_EXISTING else "organization_name_still_absent",
                         "opportunity_plan_unchanged", "ledger_rows_unchanged", "fingerprints_approved"],
        }
        result.rebased.append(rebased)
    result.rebased.sort(key=lambda r: r["planned_opportunity_id"])
    result.refused.sort(key=lambda r: (r["planned_opportunity_id"] or "", r["code"]))
    return result


def rebase_problem(conf: Mapping[str, Any]) -> str | None:
    """Structural check the importer applies to a line carrying ``rebase``; None when well formed."""
    rb = conf.get("rebase")
    if rb is None:
        return None
    if not isinstance(rb, Mapping) or rb.get("rebase_version") != REBASE_VERSION:
        return f"rebase is not {REBASE_VERSION}"
    for k in ("rebased_from_plan_hash", "rebased_to_plan_hash", "request_sha256"):
        if not _HEX64.match(str(rb.get(k) or "")):
            return f"rebase.{k} must be a sha256"
    ev = conf.get("evidence") or {}
    if rb["rebased_to_plan_hash"] != ev.get("plan_sha256"):
        return "rebase.rebased_to_plan_hash differs from evidence.plan_sha256"
    if rb["rebased_from_plan_hash"] == rb["rebased_to_plan_hash"]:
        return "a rebase must move the plan reference"
    if rb["request_sha256"] != ev.get("request_sha256"):
        return "rebase.request_sha256 differs from evidence.request_sha256"
    return None

# COMMERCIAL_PROCUREMENT_CONTACT_RESOLUTION_PR5E

**Status:** draft planning slice (read-only)  
**Planner version:** `procurement_contact_resolution_planner_v1`  
**Resolver vocabulary:** PR5A `CONTACT_RESOLUTION_STATUSES` / `CONTACT_RESOLVER_VERSION`

## Sequence (authoritative)

```text
PR5D product relevance — merged
↓
PR5E organization/contact resolution — this document
↓
PR5F lead persistence
↓
PR5G agent-assisted adjudication/review
```

Stale design text that still lists PR5E as “scheduling” or PR5D as “persistence” is superseded by this roadmap (see also `COMMERCIAL_PROCUREMENT_LIVE_RELEVANCE_PR5.md` §11 and the PR5D audit).

## Pipeline

```text
PR5C coalesced tender + provenance
         +
PR5D TenderRelevanceDecision
         ↓
OrganizationResolution (exactly one per tender)
         ↓
Internal contact search (only if unique linked account)
         ↓
ContactResolutionSummary (exactly one per tender)
         +
ContactCandidate (zero or more)
```

Contact resolution is a separate dimension from lead eligibility. PR5D currently has proposed/reviewed/gold `200 / 0 / 0`. PR5E never treats unreviewed relevance predictions as validated leads and never persists leads.

## Field-sufficiency audit

| Field | Role in PR5E |
|-------|----------------|
| `buyer_display_selected` | Normalized via `safe_org_normalized` for exact name/alias routes |
| `buyer_source_id_selected` | **Provenance only.** Used as an institutional-domain *candidate* when it looks like a domain; **never** as a PR2 `account_id` |
| `pr4_procurement_ids` | Carry forward linked PR4 resolutions only when all constituents agree on one PR2 account that still exists in the frozen identity input |
| `commercial_identity_contact.role` | Sole role-suitability authority (token audit below). Email local-parts and display names never establish suitability |
| `commercial_identity_evidence` (contact subjects) | Explicit verification support for `existing_verified_contact` |
| Suppression / outreach sidecars | Read-only via existing `marketing_export_context` + `candidate_export_gate` |

If buyer fields cannot support a lossless live link and no consistent PR4 linked account exists, organization status is `deferred_insufficient_buyer_fields` / unlinked / ambiguous / refused — contact search is not run (`contact_resolution_deferred`).

## Source-of-truth decisions

- **Accounts / contacts / evidence / conflicts:** PR2 `commercial_identity_*` (frozen fingerprint in `commercial_identity_build_meta`).
- **Account link routes:** PR4 `classify_account_link_route` / `build_account_resolution` (no fuzzy / LLM / web).
- **Tender coalescence + lifecycle:** PR5C.
- **Relevance class echo:** PR5D `TenderRelevanceDecision` (diagnostic only for actionability).
- **Safety:** existing export gate policy — not a second suppression implementation.

## Status state machine

| Status | Meaning |
|--------|---------|
| `contact_resolution_deferred` | Prerequisite failed; search not run; zero candidates; empty `search_stages_completed`; next action `resolve_account` |
| `no_contact_found` | Allowed internal sources searched; empty result |
| `contact_research_required` | Search exhausted; no selectable contact without total block |
| `ambiguous_contact` | Multiple materially competing selectable contacts |
| `contact_blocked` | Candidates exist; all selectable paths blocked by safety |
| `existing_contact_needs_role_review` | Contact exists; suitability/verification incomplete |
| `role_known_email_missing` | Suitable role known; usable email missing |
| `existing_verified_contact` | Usable email + suitable role + explicit verification + clear safety + account membership |

Historical / closed / negative / ambiguous tenders may still receive a contact-dimension resolution for audit, but must not become actionable leads.

## Ranking policy

Declarative `contact_resolution_policy_v1` (execution + fingerprint):

1. Exact buyer email belonging to the resolved account  
2. Verified suitable  
3. Suitable role, unverified  
4. Role review  
5. Role known / email missing  
6. Blocked  

Stable tie-breaker: `contact_id`.

Role tokens (smallest viable taxonomy from fixture/test audit): procurement (`compras`, `adquisiciones`, …), laboratory (`laboratorio`, …), unsuitable (`estudiante`, …). Unknown → abstain (`unknown`).

## Reconciliation equations

```text
PR5D tender decisions
=
organization resolutions
=
contact-resolution summaries
```

Also enforced:

- Exactly one organization and one contact summary per tender  
- Every candidate belongs to the summary’s resolved account  
- No contact search when the account is not uniquely linked  
- Deferred ⇒ no candidates and no completed search stages  
- Existing-contact statuses have supporting candidates  
- Selected contacts are selectable, not suppressed/blocked, account-compatible  
- No duplicate `(contact_resolution_id, contact_id)`  
- Order-independent semantic digest  

## Privacy / artifacts

CLI:

```bash
uv run python scripts/commercial/build_commercial_procurement_contact_resolution_plan.py \
  --sqlite-path ... \
  --acquisition-snapshot-json ... \
  --as-of-utc 2026-08-01T19:00:30Z \
  --out-dir reports/out/active/current/commercial_procurement_contact_resolution_pr5e_<UTC>
```

Forbidden: `--apply --persist --network --ticket --gmail --postgres --outreach --send --schedule --label`.

Shareable `summary.json` / walkthrough are redacted (no raw names, emails, domains, or identifying buyer wording). Operational detail stays under gitignored `reports/out`. Publication is atomic; prior complete bundles are preserved on failure.

## Known limitations

- `existing_verified_contact` is rare until identity evidence routinely carries high-confidence contact verification.  
- Title-only / sparse buyer provenance yields many deferred or unlinked organization outcomes.  
- No Contact Master parallel truth — PR2 contacts only.  
- No external research, Gmail, or outreach mutation.

## Explicit exclusions

**Not in PR5E:** PR5F persistence, PR5G adjudication, human labeling, scheduling, API/dashboard exposure, outreach send, network research, fuzzy matching, embeddings, LLMs.

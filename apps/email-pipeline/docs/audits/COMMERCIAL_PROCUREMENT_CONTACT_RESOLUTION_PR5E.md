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
| `pr4_procurement_ids` | Carry forward only when **every** constituent PR4 resolution exists, is `linked` to the **same** PR2 account still present in the frozen identity input, and no foreign `candidate_account_ids` remain |
| `commercial_identity_contact.role` | Sole role-suitability authority (token audit below). Email local-parts and display names never establish suitability |
| `commercial_identity_evidence` (contact subjects) | Explicit verification: audited production type is `contact_identity` + `exact_email` + high confidence from `contact_master` |
| Suppression / outreach / Sent / suppliers | Full canonical `build_marketing_export_gate_context` → `GateContext` (not a subset) |

If buyer fields cannot support a lossless live link and no unanimous PR4 linked account exists, organization status is `deferred_insufficient_buyer_fields` / unlinked / ambiguous / refused — contact search is not run (`contact_resolution_deferred`).

## Source-of-truth decisions

- **Accounts / contacts / evidence / conflicts:** PR2 `commercial_identity_*` (frozen fingerprint in `commercial_identity_build_meta`). Fail closed if the identity fingerprint is missing/malformed.
- **Account link routes:** PR4 `classify_account_link_route` / `build_account_resolution` (no fuzzy / LLM / web). Constituent `candidate_account_ids_json` is preserved and can force ambiguity.
- **Tender coalescence + lifecycle:** PR5C built **once** and injected into PR5D; PR5E asserts `pr5d.pr5c_semantic_digest == candidate_plan.semantic_digest`.
- **Relevance class echo:** PR5D `TenderRelevanceDecision` (diagnostic only for actionability).
- **Safety:** complete outbound gate truth via `marketing_export_context` / `outbound_core` defaults — Sent recipients, internal blocked domains, email/domain suppression, blocking outreach states (`contacted`/`replied`/`snoozed`), supplier + noise filters. Missing required safety tables ⇒ `safety_unknown` / non-selectable (never represented as empty blocker sets).
- **Usable email:** `normalize_export_email` / `emails_in` must succeed. Non-empty invalid strings are **not** usable.

## Status state machine

| Status | Meaning |
|--------|---------|
| `contact_resolution_deferred` | Prerequisite failed; search not run; zero candidates; empty `search_stages_completed`; next action `resolve_account` |
| `no_contact_found` | Allowed internal sources searched; empty result (or exhausted non-actionable tender without research) |
| `contact_research_required` | Internal search exhausted **and** tender is otherwise approved for human/external research |
| `ambiguous_contact` | Multiple materially competing selectable contacts (same material tier); contact ID must not silently break the tie |
| `contact_blocked` | Candidates exist; all selectable paths blocked / safety-unknown |
| `existing_contact_needs_role_review` | Contact exists; suitability/verification incomplete |
| `role_known_email_missing` | Suitable role known; usable email missing |
| `existing_verified_contact` | Usable email + suitable role + declarative verification evidence + clear safety + resolved identity + account membership |

Historical / closed / negative / ambiguous / awarded / conflicting / date-missing / status-unknown tenders may still receive a contact-dimension resolution for audit, but must not receive gated lead or research/outreach next actions (`use_existing_contact`, `research_contact_if_active` → `none`). Unknown lifecycle/relevance/currentness fail closed. Unreviewed PR5D predictions (absence of reviewed/gold + independent review) fail closed — confidence/class alone is insufficient. Currentness truth comes from frozen PR5C, not lifecycle inference.

## Ranking / verification / actionability policy

Declarative `contact_resolution_policy_v2` drives **both** execution and `rules_fingerprint`:

1. Exact buyer email belonging to the resolved account
2. Verified suitable
3. Suitable role, unverified
4. Role review
5. Role known / email missing
6. Blocked / identity-incompatible

Stable tie-breaker (`contact_id`) applies only **after** material ambiguity is evaluated.

Verification predicate (shared):

- `evidence_type=contact_identity`
- `matching_reason_code=exact_email`
- `confidence=high`
- `source_table=contact_master` and `source_plane=contact_master`
- contact `identity_status=resolved` with high contact confidence
- all pointer fields present and resolving to the frozen PR2 evidence row for that contact

High confidence alone is insufficient. Mere presence in `commercial_identity_contact` is insufficient. Ambiguous / needs_review / unlinked / internal_actor identities are never selectable.

Role tokens (smallest viable taxonomy from fixture/test audit): procurement (`compras`, `adquisiciones`, …), laboratory (`laboratorio`, …), unsuitable (`estudiante`, …). Unknown → abstain (`unknown`). Production roles are mostly null → suitable-role outcomes remain rare without inventing roles.

## Reconciliation equations

Binding over an **independent source projection** (not parallel unrelated ID lists):

```text
Frozen PR5C tender + PR5D decision + PR2/PR4/Safety + policy
→ expected OrganizationResolution / ContactResolutionSummary / candidates / evidence / conflicts
→ compare emitted rows for exact equality
```

Also enforced:

- Organization/summary `relevance_decision_id` equals the actual decision for that tender
- Summary organization ID/account matches its organization
- Candidate contact-ID set equals `frozen_index.contact_ids_for_account(account_id)`
- Every candidate field recomputed from frozen + `evaluate_contact_safety` (never from emitted safety flags)
- Candidate evidence IDs exactly equal frozen contact evidence IDs; plan evidence IDs exactly equal the union of candidate evidence IDs
- Stable IDs from shared pure projectors (`stable_ids.py`); candidate IDs never encode a provisional parent CRS
- Conflict set exactly equals `select_final_status` / projected conflicts
- PR4 procurement IDs begin from the frozen tender constituents (not from emitted org)
- `selected_candidate_id` resolves exactly once; selected contact agrees
- Selected candidate satisfies role/identity/verification/safety for its status
- Considered/suitable/blocked counts equal actual candidates
- Ranks are deterministic, unique, and contiguous; material rank swaps fail
- Ambiguous status has competing candidates **and** a matching conflict row
- Order-independent semantic digest; PII-safe fingerprints (hashed tokens, not raw emails/names/domains/buyer wording)
## SQLite load contract

- URI `mode=ro` + `PRAGMA query_only=ON`
- One `BEGIN DEFERRED` read transaction for the complete PR2/PR4/safety source load, with `enable_require_active_read_transaction`
- Rollback/disable on exit; mtime must be unchanged

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

- `existing_verified_contact` requires suitable **role** text plus accepted verification evidence; production roles are mostly null, so zero verified rows is expected until roles are populated — do not weaken the rule.
- Unanimous PR4 constituent linking is stricter than “any surviving linked account,” so linked organization counts may drop mechanically.
- Title-only / sparse buyer provenance yields many deferred or unlinked organization outcomes.
- No Contact Master parallel truth — PR2 contacts only.
- No external research, Gmail, or outreach mutation.

## Explicit exclusions

**Not in PR5E:** PR5F persistence, PR5G adjudication, human labeling, scheduling, API/dashboard exposure, outreach send, network research, fuzzy matching, embeddings, LLMs.

# Data authority map

Status: canonical
Owner: project-maintainers
Last reviewed: 2026-09-03, verified against `main` @ `774cc36cf4dee2b90ff043e4307544573787b229` (Alembic head `20260902_0046`)
Part of: [`docs/refoundation/REFOUNDATION_PLAN.md`](../refoundation/REFOUNDATION_PLAN.md)

One row per business concept: canonical authority, secondary/read model,
evidence source, writer(s), reader(s), rebuildability, durability, and
desired future authority (marked *speculative* where no decision has been
made). This is the wide, concept-by-concept companion to
[`docs/architecture/CURRENT_SYSTEM_TRUTH.md`](../architecture/CURRENT_SYSTEM_TRUTH.md)'s
durable/rebuildable table — if the two ever disagree, `CURRENT_SYSTEM_TRUTH.md`
wins. This file builds on and updates the domain matrix in
[`docs/architecture/COMMERCIAL_OPERATING_SYSTEM_AUDIT.md`](../architecture/COMMERCIAL_OPERATING_SYSTEM_AUDIT.md)
(historical, point-in-time, 2026-08-29) — several of that audit's findings
(CRM-4A writer, customer-quote existence) are now superseded by shipped work;
this file reflects current state and should be kept current going forward,
where that audit should not be (git history is its record).

## Identity concepts

| Concept | Canonical authority | Secondary / read model | Evidence source | Writer(s) | Reader(s) | Rebuildable | Durable | Desired future authority |
|---|---|---|---|---|---|---|---|---|
| Organization (post-promotion / customer) | `commercial.organization` (+`organization_source` provenance) | — | Gmail archive, PR3 evidence | `promote_sales_opportunity` (org-first, conservative — resolves via `(source_kind, source_id)` in `*_source`, never raw string match; creates only when evidence is sufficient; never fabricates) | `/operations/*`, intake-resolution service, dashboard Clientes/Pipeline | No | **Yes** | Stable — CRM-4A writer now exists (was schema-only as of the 2026-08-29 audit; resolved by PR #522) |
| Contact / person (post-promotion) | `commercial.contact` (+`contact_source`) | — | Gmail archive, PR3 evidence | same as organization, via `_resolve_or_create_contact` (only linked once an organization is resolved) | same as organization | No | **Yes** | Stable |
| Contact / person (pre-promotion, machine identity) | `commercial_identity_contact` (SQLite; mirrored 1:1 shape only, not 1:1 table, in the PR3 Postgres side as denormalized fields — see note) | — | `commercial_identity_evidence` | `commercial_identity/builder.py`, deterministic resolver | `/contacts/{email}`, promotion, intake resolution | **Yes** | No | Stable as evidence — 27K+ rows (2026-08-29 audit); closest thing to "known contact" today short of promotion |
| Email / contact point | Not a first-class table anywhere today — represented as a plain string column (`email`, `email_norm`, `contact_display_email`, `primary_email`) on each of: `contact_master`, `commercial_identity_contact`, `commercial.contact`, `outbound_campaign_recipient`, `lead_master`, `lead_intel.prospect` | — | `emails` archive | each owning table's own writer | each owning table's own readers | mixed | mixed (durable only on `commercial.contact.primary_email`) | **Speculative / open question** — see [Refoundation Plan](../refoundation/REFOUNDATION_PLAN.md) decision register on whether a first-class contact-point concept is justified yet |
| Supplier identity | `supplier_master` (SQLite, canonical key `domain_norm`) | `commercial.organization` (target direction per `docs/business/BUSINESS_RULES_QUOTES_AND_SUPPLIERS.md`: "Canonical identity target: `commercial.organization` with a supplier role") | `supplier_evidence`, warm-case supplier threads | `supplier_import_batch` workbook imports | dashboard Suppliers page (via warm-case domain grouping, **not** `supplier_master` directly — see gap below) | **Yes** | No | **Direction stated, not implemented** — no bridge exists yet from `supplier_master`/`SuppliersPage` to `commercial.organization`. The dashboard's Suppliers view is additionally gated by a frozen ~90-domain Python literal (`SUPPLIER_VENDOR_DOMAINS`), not by `supplier_master` or `commercial.organization` at all — see decision register |
| Prospect / lead | `lead_master` (SQLite, mutable) | `lead_intel.prospect` (Postgres mirror), `lead_research_prospect` (SQLite, DeepSearch batches) | `external_leads_raw` | lead-fetch CLIs | `/mirror/leads/*`, dashboard Prospectos, intake-resolution service (via `api.v_lead_intel_prospect_evidence`, migration 0046) | **Yes** | No | Stable — pre-CRM discovery evidence, promotes via the same `/sales-opportunities/promote` path once a machine opportunity row exists for it |
| Customer (post-sale identity) | Same row as Organization/Contact above — OrigenLab has no separate "customer" table; a customer is an organization/contact that has an associated `sales_opportunity`/`customer_quote` | — | — | — | — | No | **Yes** | Stable — this collapse is intentional, not a gap |

## Commercial / CRM concepts

| Concept | Canonical authority | Secondary / read model | Evidence source | Writer(s) | Reader(s) | Rebuildable | Durable | Desired future authority |
|---|---|---|---|---|---|---|---|---|
| Sales opportunity (post-promotion) | `commercial.sales_opportunity` (+`_event`, append-only) | — | PR3 `commercial.opportunity*` (machine), manual intake | `CommercialOperationsService.promote_sales_opportunity` / `create_manual_sales_opportunity` / `transition_sales_opportunity_stage` | dashboard Pipeline/Cotizaciones | No | **Yes** | Stable, architecturally strong — no FK to PR3 by design (rebuilds can't cascade-delete human state) |
| Machine-proposed opportunity (pre-promotion) | `commercial_opportunity` (SQLite, PR3, full-replace each build) | `commercial.opportunity` (Postgres mirror, 1:1) | ChileCompra/Gmail evidence via PR3 builder | PR3 builder | `/opportunities/commercial`, Negocios intake cockpit, promotion | **Yes** | No | Stable as "opportunity/evidence mart" — as of the 2026-08-29 audit, 9,576 of 9,577 rows were `commercial_history` identity-touch reconstructions, not live candidates; treat row-count claims from this table with that caveat |
| Pre-promotion triage state | `commercial.opportunity_operator_state` | — | — | confirm/reject via `/operations/*` | Negocios intake cockpit | No | **Yes**, but transitional | A distinct lifecycle from `sales_opportunity`, not a duplicate to merge away — this is the "candidate intake" stage the target model calls for |
| Task | `commercial.task` | — | — | `/operations/*` create/complete/cancel | Hoy queue, opportunity workspace | No | **Yes** | Stable — pure human-authored concept, no machine source to backfill from |
| Activity | `commercial.activity` | — | — | `/operations/*` create | Hoy queue, opportunity workspace | No | **Yes** | Stable |
| Historical deal (executed, pre-CRM-quote era) | SQLite deal ledger design (`commercial_deal*`, `docs/commercial/COMMERCIAL_DEAL_LEDGER_SCHEMA_V1.md` — **historical design doc**, schema partially implemented) | `commercial.deal` (Postgres mirror, one real row: CEAF/SERVA) | Gmail archive | opt-in mirror sync | Negocios historical ledger | No (mirror is a full-replace-per-run projection of a small, largely-manual ledger) | Evidence, not durable CRM truth | Keep as historical evidence, surfaced from the Organization/Opportunity workspace — not a competing "deal" concept alongside `sales_opportunity`/`customer_quote` |
| Customer quote (aggregate) | `commercial.customer_quote` | — | — | `create_quote`, `adopt_drive_folder` | Cotizaciones board, Pipeline drawer | No | **Yes** | Stable, architecturally strong |
| Quote revision / lifecycle | `commercial.customer_quote_revision.status` (single axis: `draft → pending_approval/adjustments_requested → approved → sent → closed_won/closed_null`) | — | — | `submit_for_review`, `request_adjustments`, `approve`, `confirm_send`, `close_quote` | Cotizaciones board ("Revisión" lane collapses the first three states) | No | **Yes** | Stable — closure is deliberately decoupled from `sales_opportunity`'s own stage; moving the opportunity to `won` is a separate, operator-visible Pipeline action |
| Quote document (the actual working file) | Google Drive (folder + copied template sheet) | `commercial.customer_quote_drive_workspace` (references + provisioning state only — `folder_id`, `sheet_file_id`, `provisioning_status`, lease-fenced `version`) | — | Drive provisioning (`_provision`), lease-fenced against double-create | Cotizaciones/Pipeline Drive-link UI | No | Drive is authoritative for content; the CRM row is durable **metadata about** the document, not the document | Stable — explicit design choice per `docs/business/BUSINESS_RULES_QUOTES_AND_SUPPLIERS.md` (Sheets is the editing authority in V1, no line/price ingestion) |
| Quote number vs. document number | `commercial.customer_quote.quote_number` (human-facing CRM number, `commercial.customer_quote_number_series` allocator) and `.document_number` (drives the Drive folder/sheet naming stem) | — | — | number-series allocator (generated quotes only); operator-confirmed strings (adopted quotes) | — | No | **Yes**, both mandatory+unique | Deliberately independent, never derived from one another — `quote_origin` (`generated`/`adopted`) is a sum-type CHECK: adopted quotes have neither serial nor issue_year, by design (forcing/guessing one would fabricate provenance) |
| Drive-intake evidence resolution | Not a stored concept — a read-only ranking service (`customer_quote_intake_resolution_service`) over three evidence tiers (Drive folder-name parse < durable CRM < `lead_intel.prospect` via `api.v_lead_intel_prospect_evidence`) | — | — | — (never writes) | "Incorporar al CRM" flow | n/a | n/a | Closed confidence vocabulary (`confirmed_durable_match`/`possible_match`/`ambiguous_match`/`unresolved`), structurally fail-closed — never returns one candidate when more than one exists. **This is the smaller, lighter-weight sibling of `promote_sales_opportunity`'s heavier snapshot-and-create pattern** — both are real, current evidence→signal precedents; see the Refoundation Plan's decision register on which shape a future campaign-reply signal should resemble |
| Command idempotency | `commercial.command_idempotency` (PK `(operator_key, idempotency_key)`) | — | — | every `/operations/*` write route | internal only — no `api.*` view | No | **Yes** | Stable |

## Marketing / outbound concepts

| Concept | Canonical authority | Secondary / read model | Evidence source | Writer(s) | Reader(s) | Rebuildable | Durable | Desired future authority |
|---|---|---|---|---|---|---|---|---|
| Campaign | `outbound_campaign` (SQLite) | — | — | `outbound_campaign_cli.py init` | same CLI | No | Yes, but **operational, not CRM** — no dashboard surface at all | **Open question** — see decision register |
| Campaign recipient (lifecycle) | `outbound_campaign_recipient` (SQLite, `state`: candidate→selected/reserved→sent/blocked/bounced/replied/inactive) | — | — | `outbound_campaign_cli.py select`/`send` | same CLI | No | Yes, operationally durable, but not in Postgres/CRM | **Open question** whether/when this belongs in durable Postgres — it is already durable-*shaped* (a real lifecycle, not a projection), just not in the same platform as the rest of durable truth |
| Send attempt | `outbound_send_attempt` (SQLite, append-only, two-phase `in_flight→accepted/failed`) | — | Gmail Sent-folder scan, `contact_email_suppression` | `begin_live_attempt`/`finish_live_attempt` | reconciliation | No | Yes, append-only ledger | Stable — this is OrigenLab's existing hand-rolled outbox pattern for one provider (Gmail); see the Refoundation Plan on whether/how it should generalize |
| Reply / bounce evidence | Not a dedicated table — reply inferred from `outbound_campaign_recipient.state='replied'` + `outreach_contact_state`; bounce evidence in `contact_email_suppression` (`suppression_reason_code`) | — | `emails` archive (Sent + inbound scan) | reconciliation job | campaign CLI, `/mirror/outbound` (suppression/state only) | No | Yes (suppression/outreach-state), evidentiary (reply inference) | **Open question** — a campaign reply today never touches CRM state at all; see Refoundation Plan §"Campaign↔CRM connection" |
| Suppression / hold | `outbound.contact_email_suppression`, `outbound.contact_domain_suppression` (Postgres, migration `0004`) — **populated only by the `EXPERIMENTAL_PARKED` break-glass script**, not daily runtime; `contact_email_suppression` (SQLite) is the actual operationally-current copy | `/mirror/outbound` reads the Postgres tables live and correctly, but nothing in the documented daily workflow refreshes them | manual/bounce classification | SQLite: pipeline scripts. Postgres: `scripts/migrate/sqlite_outbound_sidecars_to_postgres.py` (parked, not scheduled) | SQLite: outbound CLIs. Postgres: `/mirror/outbound` route (code path real; data freshness not guaranteed) | No | Yes (SQLite copy) | **Do not assume the Postgres copy is current** — see decision register; this is a precise three-layer situation, not "outbound is SQLite-only" nor "outbound is already migrated" |
| Outbound export/audit mirror | `outbound.outbound_batch`/`outbound_batch_recipient` (Postgres, migration `0005`) | — | CLI export runs | opt-in `--write-postgres-audit` flag (default off) | none identified beyond the mirror tables themselves | No | Yes, but write-optional | A genuinely separate mechanism from the suppression tables above — "does not orchestrate or record sends" (`OUTBOUND_SOURCE_OF_TRUTH.md`) |
| Manual contact status (hard block) | `manual_contact_status` (SQLite, PK `email_norm`) | — | — | operator CLI | send-eligibility gate | No | Yes | Stable |

## Procurement / tender concepts

| Concept | Canonical authority | Secondary / read model | Evidence source | Writer(s) | Reader(s) | Rebuildable | Durable | Desired future authority |
|---|---|---|---|---|---|---|---|---|
| Procurement tender signal | `commercial_procurement_signal` (SQLite) | — | ChileCompra acquisition | acquisition pipeline | candidate planner | **Yes** | No | Stable |
| Tender evidence (annexes) | `commercial_procurement_evidence` (SQLite) | file-backed W1/T1 read models on API disk (no Postgres table) | anexo extraction | anexo acquisition | planner, annex preview/import route | **Yes** | No | Stable — largest single contributor to SQLite footprint |
| Tender ↔ opportunity link | Denormalized `codigo_licitacion` label on the PR3/`sales_opportunity` object — **not a first-class FK, no dedicated tender-opportunity table** | — | — | — | — | — | Partially durable (the label persists once promoted) | **Known gap, not fixed**: if a promoted opportunity's source tender later changes (new close date, addendum), nothing re-notifies or re-links the durable `sales_opportunity` — documented in `COMMERCIAL_OPERATING_SYSTEM_AUDIT.md`, not resolved since |

## Catalog concepts

| Concept | Canonical authority | Secondary / read model | Evidence source | Writer(s) | Reader(s) | Rebuildable | Durable | Desired future authority |
|---|---|---|---|---|---|---|---|---|
| Catalog product | `catalog.product` (SQLite v1 → Postgres mirror, migrations `0019`-`0020`) | Website `apps/web/src/data/products.ts` (separate, editorial, public-facing) | manufacturer datasheets, supplier quotes, operator confirmation | catalog build scripts | `/mirror/catalog/products`, dashboard Catálogo | **Yes** | No | Stable — thin (9 products, 2 supplier offers as of the 2026-08-29 audit) but real; website and operator catalogue are intentionally two separate "canonical" layers (editorial vs. operator-indexed), not a conflict |
| Supplier offer / price snapshot | `catalog.supplier_offer`, `catalog.price_snapshot` (append-only) | — | Gmail supplier-quote threads | catalog build scripts | dashboard Catálogo | **Yes** | No | Stable — append-only by design, never UPDATE amount in place |

## Notes on this map's own maintenance

- This file should be updated whenever a migration changes what's durable vs.
  rebuildable for a concept listed here — it is meant to stay current, unlike
  the historical point-in-time audits it builds on.
- Rows marked "Open question" or "Speculative" are intentionally not resolved
  here — resolving them is exactly the kind of decision the
  [Refoundation Plan](../refoundation/REFOUNDATION_PLAN.md)'s decision
  register exists to track, not something to pre-decide while just mapping
  current state.

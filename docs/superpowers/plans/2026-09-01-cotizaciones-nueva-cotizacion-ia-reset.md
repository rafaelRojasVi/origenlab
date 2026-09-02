# Cotizaciones + Nueva Cotización + Primary IA Reset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Cotizaciones the default landing surface of OrigenLab's operator dashboard — a real global work queue over durable customer quotes, a quote detail/Drive workspace, and a "Nueva Cotización" flow that creates a durable quote against either an existing or a newly-created manual sales opportunity — without touching the durable backend beyond one small additive field exposure.

**Architecture:** Phase 1 (backend-foundation, commits `94dcd68..48a1506`) already ships every durable command and read this phase needs: `GET /operations/customer-quotes` (global list), `POST /operations/sales-opportunities/manual`, and the existing per-opportunity quote commands (`POST/GET .../quotes`, `POST .../drive-workspace`). This phase is dashboard-only except for Task 0. It reworks the flat nav IA (3 files), replaces the `CotizacionesPage` placeholder with a real list→detail workspace, and adds a create dialog that reuses the exact same `createCustomerQuote`/`createManualSalesOpportunity` client calls the Ventas drawer already uses — one quote-creation implementation, two entry points.

**Tech Stack:** FastAPI + Pydantic (apps/api), React + TypeScript + Vite + Tailwind + Vitest/@testing-library/react (apps/dashboard). No new dependencies (no Radix, no new HTTP libs — the dialog is hand-rolled like `SalesOpportunityWorkspaceDrawer`).

**Spec:** User's Phase 2 brief (Cotizaciones + Nueva Cotización + primary IA reset), read into this session directly — no separate spec file. Phase 1 audit trail: `docs/superpowers/plans/2026-09-01-crm-backend-foundation.md` (see its "Remaining phases" section, lines 2663-2672).

## Global Constraints

- Never allocate quote serial **1191** (production's next serial) in any local test, fixture, or default. Existing unit-test convention uses prefix `CN`, seed `1183` (see `apps/api/tests/test_drive_factory_and_quote_settings.py:340-342`) — reuse that for schema-level unit tests. For any test that actually exercises `POST .../quotes` against a real disposable Postgres, use an obviously-fake config: `ORIGENLAB_QUOTE_DOCUMENT_PREFIX=ZZ`, `ORIGENLAB_QUOTE_SERIAL_PAD_WIDTH=5`, `ORIGENLAB_QUOTE_SEED_NEXT_SERIAL=90000`.
- No production Postgres, no production Google Drive, no production quote creation, no push/deploy/PR — this phase stays local until the user reviews it.
- Opening the Nueva Cotización dialog, selecting an existing opportunity, creating a manual opportunity, cancelling, and validation failure must **never** call `createCustomerQuote`. Only an explicit, final, successful submit may.
- Do not invent quote lifecycle states (`approved`, `sent`, `adjustments`, `V2`/`V3`, `superseded`) — the durable schema has exactly one `QuoteStatus` (`"draft"`) and three `QuoteProvisioningStatus` values (`pending`/`ready`/`failed`). All UI copy is built from those, not new ones.
- No trusted-operator header or API token in browser code — identity is injected by the Cloudflare Worker proxy, exactly as every existing client file already assumes (`credentials: "include"`, no `X-OriginLab-Operator-Email` on the client).
- Every new durable-write client call carries an `Idempotency-Key`, generated with `crypto.randomUUID()`, retained across a failed HTTP attempt so a manual retry replays instead of double-submitting (the existing `QuoteWorkspaceSection.handleCreate` pattern — see Task 1).
- `apps/dashboard-proxy`'s allowlist already covers every route this phase calls (`GET/POST /operations/customer-quotes*`, `POST /operations/sales-opportunities/manual`, `GET /operations/sales-opportunities`, `POST /operations/sales-opportunities/{id}/quotes`) — confirmed by reading `apps/dashboard-proxy/src/allowlist.ts`. **No proxy changes in this phase.**

---

## Amendments (approved 2026-09-01, supersede the base plan below)

1. **Exact-opportunity Ventas deep linking.** "Ver en Ventas" must open the
   specific durable opportunity's drawer, not just navigate to Ventas.
   Route convention: `#/ventas?opportunity=sales_<id>`. The internal
   section id for Ventas stays `pipeline` (renaming it is out of scope and
   touches the nav/hash-route/sidebar test surface for no functional
   gain) — `ventas` is added as a **parse-time alias** in
   `dashboardHashRoute.ts` that resolves to section `pipeline`; deep links
   are always *built* as `#/ventas?opportunity=...`. New module
   `apps/dashboard/src/lib/ventasDeepLink.ts`:
   `buildVentasDeepLinkHash(opportunityId)`,
   `parseVentasDeepLinkOpportunityId(hash)` (validates
   `/^sales_[0-9a-f]{32}$/`, same shape the backend generates —
   `commercial_operations_service.py:430,520`), and
   `useVentasDeepLinkOpportunityId()` (hashchange-subscribed hook). No
   backend route needed — purely a browser-side parse of an id that
   `VentasPage` already has in its loaded board. `VentasPage` gains an
   optional `deepLinkOpportunityId` prop; on board load, if the id matches
   a loaded item, opens that item's drawer exactly once (tracked via a
   ref so closing it doesn't reopen it). If the id is syntactically
   invalid or doesn't match a loaded item (wrong stage, stale, garbage),
   nothing happens — no crash, no drawer, no error banner. `QuoteDetailDrawer`'s
   "Ver en Ventas" button now calls `onOpenVentas(item.quote.sales_opportunity_id)`
   and `DashboardApp` sets `window.location.hash =
   buildVentasDeepLinkHash(opportunityId)` directly (bypassing the plain
   `navigate()` used elsewhere, since that would overwrite the query
   string) — both hash-subscribed hooks pick up the change independently.

2. **Successful Nueva Cotización opens the real created quote, never a
   placeholder.** `createCustomerQuote`'s response is already the real
   durable quote (never synthetic) — the base plan's Task 5 Step 9 risk
   was the *identity/opportunity fields* wrapped around it for the
   drawer, which it proposed faking. Fixed by moving assembly into the
   dialog, which already holds real source data for both paths: existing-
   opportunity flow uses the exact `SalesOpportunityListItem` the operator
   selected from the picker (already carries real
   `organization_display_name`/`contact_display_name`/`contact_primary_email`/
   `stage`/`owner_key`/`next_task_*`); manual flow uses the operator's own
   just-submitted form values for identity (these are what was durably
   persisted — see amendment 3) plus the real `stage`/`owner_key` from
   `createManualSalesOpportunity`'s response (`next_task_*` is genuinely
   null for a brand-new opportunity — accurate, not a placeholder).
   `NuevaCotizacionDialog`'s `onCreated` signature changes to
   `(item: CustomerQuoteGlobalItem) => void` (a fully real, assembled
   item) instead of `(quote, opportunityId)`. `CotizacionesPage`'s handler
   becomes: close dialog, `setOpenItem(item)` (drawer opens immediately,
   already real), background `void queue.refetch()` (non-blocking). No
   synthetic one-frame item is ever constructed. `QuoteDetailDrawer`'s
   existing on-open `fetchCustomerQuote` refresh (Task 4 Step 3) still
   runs and covers "fetch by returned quote_id if necessary" for the
   Drive-workspace fields specifically. Manual-create-succeeds /
   quote-create-fails retry behavior needs no design change — the base
   plan's `createdOpportunityId` guard (Task 5 Step 7) already retries
   only the quote step; this amendment adds explicit test coverage for it
   (see amendment 6).

3. **Manual identity semantics.** Verified in
   `apps/api/src/origenlab_api/services/commercial_operations_service.py:447-527`:
   `organization_display_name`/`contact_display_name`/`contact_email` on
   `POST /operations/sales-opportunities/manual` are stored as free-text
   columns on the `sales_opportunity` row itself — not
   `commercial.organization`/`commercial.contact` rows, and not
   browser-only state (already noted in the base plan's audit section,
   line 38). No canonical CRM organization/contact/customer record is
   created by this flow. `NuevaCotizacionDialog`'s manual tab keeps the
   existing field labels ("Organización"/"Contacto", matching
   `SalesOpportunityWorkspaceDrawer.tsx:222-226`'s established
   vocabulary) and adds one inline hint under those fields making the
   scope explicit: these are durable opportunity context fields, not a
   customer/contact-master create. No copy anywhere in this phase claims
   otherwise.

4. **Task 0 approved as specified, no migration required** — plus one
   addition found while implementing this amendment set:
   `CustomerQuoteBundle.sales_opportunity_title` (`customer_quotes.py:177`,
   already populated from `so.title` in every bundle/global-entry query,
   same "computed but dropped at the API boundary" shape as
   `document_number`) is exposed alongside it, both as additive fields on
   `CustomerQuoteResponse` — still no migration, no repository change.
   `quote_number` remains the primary commercial identifier;
   `document_number` and `sales_opportunity_title` are secondary context.

5. **Cotizaciones as an operational work list.** Already matches the base
   plan's Task 3 design (no KPI cards, header + filter bar + table +
   drawer) — confirmed, no structural change. `CustomerQuoteQueueTable`'s
   column set is extended with the now-available `sales_opportunity_title`
   (amendment 4) so the row hierarchy is: quote number (+ document
   number), customer/institution, opportunity/title, commercial stage,
   quote status + Drive state, recency — matching the approved hierarchy
   exactly. `QuoteDetailDrawer`'s "Oportunidad" section also renders the
   title.

6. **Additional test coverage**, layered onto the base plan's tasks
   (not a new task — each lands in the task that owns the relevant
   module): exact-opportunity Ventas deep linking and invalid-deep-link
   safety (`ventasDeepLink.test.ts`, `VentasPage.test.tsx` — new, added
   to Task 4); quote → Ventas exact navigation
   (`QuoteDetailDrawer.test.tsx`, Task 4); successful create opens the
   real durable quote with no synthetic placeholder
   (`NuevaCotizacionDialog.test.tsx`/`CotizacionesPage.test.tsx`, Task 5);
   manual-success + quote-failure retries only quote creation
   (`NuevaCotizacionDialog.test.tsx`, Task 5); duplicate submit disabled
   while in flight (`NuevaCotizacionDialog.test.tsx`, Task 5);
   idempotency-key stability across a retry (`NuevaCotizacionDialog.test.tsx`,
   Task 5); opening/cancelling still allocates nothing (already in the
   base plan, Task 5); Drive `pending` renders as provisioning language,
   not failure (`customerQuoteQueueFilters.test.ts` already covers the
   label map — `QuoteDetailDrawer.test.tsx` adds a direct assertion).

## Correction (2026-09-01, post-implementation review)

**Amendment 3 above, and two pre-existing base-plan lines (audit summary
line ~150, "Concrete blockers" item 2 near the plan's end), are factually
wrong and superseded by this note.** They claimed `POST
/operations/sales-opportunities/manual` stores `organization_display_name`/
`contact_display_name`/`contact_email` as free-text columns on
`sales_opportunity` only, creating no canonical CRM record. That claim was
based on reading only the service layer
(`commercial_operations_service.py`), which normalizes/validates the
fields, not the repository layer that actually persists them.

The repository layer
(`apps/api/src/origenlab_api/repositories/postgres/commercial_operations.py`,
`_resolve_manual_organization`/`_resolve_manual_contact`, called from
`create_manual_sales_opportunity`) **does create real, durable
`commercial.organization` and `commercial.contact` rows** from the
free-text input (`INSERT INTO commercial.organization (...) VALUES (...)`
when no `organization_id` is given; same for `commercial.contact` when an
organization was resolved), and links the new `sales_opportunity` row to
them via `organization_id`/`primary_crm_contact_id`. There is still no
durable **search** endpoint for existing organizations/contacts by name
(that part of amendment 3 / the audit's "not built in this phase" note
stands) — but manual intake is canonical CRM **create**, not free-text
opportunity context.

Fixed in `NuevaCotizacionDialog.tsx`: after `createManualSalesOpportunity`
succeeds, the dialog re-fetches the durable record via
`fetchSalesOpportunities({sourceOpportunityId: [id]})` (the list endpoint
filtered to that one id — the only read that returns
`organization_display_name`/`contact_display_name`/`contact_primary_email`,
since the singular `GET /operations/sales-opportunities/{id}` schema
doesn't include them) and uses **only that server-returned record** to
assemble the drawer item — never the submitted form values, which may
diverge from server normalization. This re-fetch result is cached
(`resolvedOpportunity` state) alongside the opportunity id
(`createdOpportunityId`) so a retry after a quote-creation failure repeats
neither the create nor the re-fetch. The manual-tab hint copy was
corrected to say organization/contact ARE created in the CRM. Test:
`NuevaCotizacionDialog.test.tsx` — "renders the server-returned durable
opportunity, not the submitted form values, when they diverge".

---

## Audit summary (read before starting any task)

This section is evidence, not narrative — it's what later tasks cite instead of re-deriving.

**Durable API surface already shipped (Phase 1), file:line:**
- `GET /operations/customer-quotes` — `apps/api/src/origenlab_api/routes/operations.py:817-848`. Query params: `stage: list[str]`, `drive_status: list[str]` (values are exactly `pending`/`ready`/`failed`, matched against `w.provisioning_status` — `apps/api/src/origenlab_api/repositories/postgres/customer_quotes_read.py:301`), `limit` (≤200), `offset`. Server-orders by `q.created_at DESC` (`customer_quotes_read.py:251,326`) — recency is already the default order, no client sort needed.
- `POST /operations/sales-opportunities/manual` — `operations.py:237-275`. Body: `SalesOpportunityManualCreateCommand` (`apps/api/src/origenlab_api/schemas/commercial_operations.py:149-177`) — `title` (required), `owner_key?` (defaults to the requesting operator if omitted — `apps/api/src/origenlab_api/services/commercial_operations_service.py:456-459`), and **either** `organization_id` **or** `organization_display_name` (not both), **either** `contact_id` **or** (`contact_display_name`/`contact_email`) (not both), and contact fields require an organization to be present (`commercial_operations_service.py:479-502`). This command does not touch `customer_quote_number_series` — it cannot allocate a quote serial.
- `POST /operations/sales-opportunities/{id}/quotes` — the only place a quote serial is ever allocated (`apps/api/src/origenlab_api/repositories/postgres/customer_quotes.py:420-571`). Body is `CustomerQuoteCreateCommand` (`{}`, `extra="forbid"`) — every quote field is server-controlled.
- `POST /operations/customer-quotes/{id}/drive-workspace` — retry command, body `{expected_version}`.
- `GET /operations/sales-opportunities` — durable list, filters `stage`/`owner_key`/`source_opportunity_id`, no text search (`operations.py:316-352`).

**Blocker found and resolved in this plan (Task 0):** `commercial.customer_quote` already stores a separate `document_number` column (Drive-facing business number, distinct from the customer-facing `quote_number` — see migration `20260831_0041_customer_quote_business_numbering_v1.py` and `customer_quotes.py:124-131`), and the repository already selects it (`customer_quotes.py:210-213`). But `CustomerQuoteResponse` (`apps/api/src/origenlab_api/schemas/customer_quotes.py:73-136`) never maps it — the field is durably stored and read into `CustomerQuoteBundle` but dropped before the API boundary. The spec explicitly asks for "document number where useful" in the queue. This is additive-only (no migration, no repository change, the data already exists) — Task 0 exposes it.

**No durable organization/contact search or creation exists.** Grepped every repository under `apps/api/src/origenlab_api/repositories/postgres/` — there is no `list_organizations`, `search_contacts`, or equivalent. `GET /contacts/{email}` (`apps/api/src/origenlab_api/routes/contacts.py`) is a rebuildable-mirror lookup by exact email, not durable search. Per the spec's instruction 5 ("if some identity operation remains unavailable, call it out instead of inventing one"): the manual-intake form in Task 5 uses only `organization_display_name`/`contact_display_name`/`contact_email` (free text, but durably stored as columns on `sales_opportunity` itself, not browser-only state or a mirror fallback) — exactly the same shape `SalesOpportunityWorkspaceDrawer` already renders for existing manual-sourced opportunities (`organization_display_name ?? account_display_domain`, `SalesOpportunityWorkspaceDrawer.tsx:222-230`). `organization_id`/`contact_id` linking stays unavailable until a durable org/contact search endpoint exists — **not built in this phase**.

**`CommercialOpportunitiesCockpit` (Negocios/`DealsPage.tsx`) is not reused for "existing opportunity" selection.** It operates on the PR3 *mirror* (promotion candidates, `confirmation_status`/`machine_review_status`), a different object model from the durable `sales_opportunity` rows `GET /operations/sales-opportunities` returns. Flow A's opportunity picker (Task 5) queries the durable list directly — the same one `useSalesOpportunityBoard` already uses.

**Current IA (3 files hold it all):**
- `apps/dashboard/src/lib/dashboardNav.ts` — full 12-id registry (`DASHBOARD_NAV_ITEMS`) + 9-id visible subset (`DASHBOARD_TOP_NAV_IDS`, currently `today, pipeline, cotizaciones, contacts, tenders, suppliers, payments-logistics, catalogo, system`) + `DEFAULT_DASHBOARD_SECTION = "today"`. `inbox`, `deals`, `prospectos` are *already* hidden (registered but excluded from `DASHBOARD_TOP_NAV_IDS`), reachable only by hash deep link.
- `apps/dashboard/src/lib/dashboardHashRoute.ts` — generic; drives everything off `DEFAULT_DASHBOARD_SECTION`. **No changes needed.**
- `apps/dashboard/src/pages/DashboardApp.tsx` — the section→page `switch`. **No changes needed** (`cotizaciones` already routes to `CotizacionesPage`).
- `apps/dashboard/src/components/layout/DashboardSidebar.tsx` — renders `DASHBOARD_TOP_NAV_ITEMS` in order; `navHref` hardcodes `section === "today" ? "#/" : ...` — **must change** to key off `DEFAULT_DASHBOARD_SECTION`.

**Reusable components (this phase must not duplicate):**
- `QuoteWorkspaceSection.tsx` (`apps/dashboard/src/components/pipeline/`) already implements quote creation + Drive-state rendering + retry, embedded in `SalesOpportunityWorkspaceDrawer`. It holds four things worth extracting once, not copy-pasting: `DriveLink`, `QuoteWorkspaceStatus` (+ `FAILURE_CATEGORY_MESSAGES`/`failureCategoryMessage`), `newIdempotencyKey`, and `createErrorMessage`/`NUMBERING_NOT_CONFIGURED_MESSAGE`.
- `useSalesOpportunityBoard.ts` is the fetch-all-then-filter hook shape (`BOARD_FETCH_LIMIT = 200`, loading/error/refetch) — `useCustomerQuotesGlobal` (Task 3) mirrors it exactly.
- `V2PageHeader` (title/subtitle/`actions` slot) and `V2EmptyState` are the existing page primitives — reused as-is, not rebuilt.
- `salesOpportunityStageLabel`/`SALES_OPPORTUNITY_STAGE_LABELS` (`apps/dashboard/src/lib/salesOpportunityFormat.ts`) and `formatCommercialOpportunityDate`/`formatSalesOpportunityAge` (`commercialOpportunityFormat.ts`/`salesOpportunityFormat.ts`) are reused for stage labels and dates — no new label maps.
- Three call sites already hand-roll `newIdempotencyKey` (`QuoteWorkspaceSection.tsx`, `SalesOpportunityWorkPanel.tsx`, `CommercialOpportunityOperationsPanel.tsx`). This phase extracts one shared version and uses it in `QuoteWorkspaceSection` (Task 1) and the new dialog (Task 5); the other two pre-existing call sites are left alone — not this phase's scope.
- Platt reference (`/home/rafael/dev/freelance/platt-commercial-platform/apps/web/components/QuotationCreateDialog.tsx`): minimal required fields, no financials, controlled local form state, **the create command only fires on explicit submit** ("Opening or cancelling this dialog allocates nothing" — verbatim from its own code comment). No Radix in OrigenLab's dashboard — Task 5's dialog is hand-rolled (fixed overlay + centered panel + focus trap + Escape), matching `SalesOpportunityWorkspaceDrawer`'s existing conventions, not a new dependency.

**Existing tests that hardcode the old IA and must be rewritten, not just extended** (found by reading them, not inferred): `apps/dashboard/src/lib/dashboardNav.test.ts` (asserts the old 9-item order and old hidden set), `apps/dashboard/src/pages/DashboardApp.test.tsx` (asserts `Inicio` is active-by-default, old nav order, `Proveedores`/`Pagos y logística` reachable via nav click — they become hidden, so those two tests switch to hash deep-link like the existing `inbox`/`deals`/`prospectos` hidden tests already do; the `Cotizaciones` placeholder test is replaced outright in Task 3).

---

## Task 0: Backend — expose `document_number` on customer-quote responses

**Files:**
- Modify: `apps/api/src/origenlab_api/schemas/customer_quotes.py:73-136` (`CustomerQuoteResponse`)
- Modify: `apps/api/tests/test_customer_quote_routes.py` (assertions around line 189, 500)
- Modify: `apps/dashboard/src/api/customerQuoteTypes.ts:36-48` (`CustomerQuote`)
- Modify: `apps/dashboard/src/api/customerQuoteParse.ts:176-217` (`parseCustomerQuote`)
- Modify: `apps/dashboard/src/api/customerQuoteParse.test.ts` (fixtures at lines ~35, ~178, ~214)
- Modify: `apps/dashboard/src/components/pipeline/QuoteWorkspaceSection.test.tsx` (fixture at line ~26)

**Interfaces:**
- Produces: `CustomerQuoteResponse.document_number: str` (API), `CustomerQuote.document_number: string` (dashboard) — every later task that renders a quote row/detail reads `quote.document_number` alongside `quote.quote_number`.

- [ ] **Step 1: Add the failing API-side assertion**

In `apps/api/tests/test_customer_quote_routes.py`, the fixture at line ~74-77 already builds a bundle with `document_number="CN01183"`. Add, right after the existing `assert body["quote_number"] == "01183-26"` at line 189:

```python
    assert body["document_number"] == "CN01183"
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd apps/api && ./scripts/sync_test_env.sh && .venv/bin/pytest tests/test_customer_quote_routes.py -k document_number -x` (or the enclosing test name if there's no standalone `-k` match — run the whole file: `.venv/bin/pytest tests/test_customer_quote_routes.py -x`)
Expected: `KeyError: 'document_number'` or `AssertionError` (body has no `document_number` key).

- [ ] **Step 3: Expose the field**

In `apps/api/src/origenlab_api/schemas/customer_quotes.py`, add to `CustomerQuoteResponse` (after `quote_number: str` on line 76):

```python
class CustomerQuoteResponse(BaseModel):
    quote_id: str
    sales_opportunity_id: str
    quote_number: str
    document_number: str
    status: QuoteStatus
    ...
```

And in `from_bundle` (line 110-120), add the field to the constructor call:

```python
        return cls(
            quote_id=bundle.quote.quote_id,
            sales_opportunity_id=bundle.quote.sales_opportunity_id,
            quote_number=bundle.quote.quote_number,
            document_number=bundle.quote.document_number,
            status=bundle.quote.status,  # type: ignore[arg-type]
            ...
```

`CustomerQuoteGlobalItem.from_entry` (`customer_quotes.py:168-179`) needs no change — it already builds `quote=CustomerQuoteResponse.from_bundle(entry.bundle)`, so the field propagates automatically to the global list.

- [ ] **Step 4: Run the API test, confirm green**

Run: `.venv/bin/pytest tests/test_customer_quote_routes.py -x`
Expected: PASS. Also run the full quote suite to catch any other fixture that builds a `CustomerQuoteResponse` by hand: `.venv/bin/pytest tests/test_customer_quote_service.py tests/test_customer_quote_repository.py -x`.

- [ ] **Step 5: Add the dashboard-side failing test**

In `apps/dashboard/src/api/customerQuoteParse.test.ts`, the fixture around line 35 already has `quote_number: "CN011729"` (no real `document_number` in that fixture yet — add one, e.g. `document_number: "CN00011729"`), then extend the assertion at line 52:

```ts
    expect(quote.quote_number).toBe("CN011729");
    expect(quote.document_number).toBe("CN00011729");
```

Do the same for the fixtures feeding the assertions at lines 178 and 214 (list and global-list parse tests) — add `document_number` to each raw fixture object and assert it survives parsing.

- [ ] **Step 6: Run it, confirm it fails**

Run: `cd apps/dashboard && npm test -- customerQuoteParse.test.ts`
Expected: FAIL (`quote.document_number` is `undefined`, or a TS compile error if `document_number` isn't yet on the fixture's target type).

- [ ] **Step 7: Add the field to the dashboard type and parser**

`apps/dashboard/src/api/customerQuoteTypes.ts`:

```ts
export interface CustomerQuote {
  quote_id: string;
  sales_opportunity_id: string;
  quote_number: string;
  document_number: string;
  status: CustomerQuoteStatus;
  ...
}
```

`apps/dashboard/src/api/customerQuoteParse.ts`, inside `parseCustomerQuote` (after the `quote_number` line):

```ts
  return {
    quote_id: quoteId,
    sales_opportunity_id: salesOpportunityId,
    quote_number: stringValue(data.quote_number, "quote_number"),
    document_number: stringValue(data.document_number, "document_number"),
    status: status as CustomerQuoteStatus,
    ...
```

- [ ] **Step 8: Run parse tests, confirm green; then fix any other fixture the type change breaks**

Run: `npm test -- customerQuoteParse.test.ts`. Then run the full dashboard suite once (`npm test`) — `QuoteWorkspaceSection.test.tsx`'s fixture at line ~26 (which builds a raw `CustomerQuote`-shaped mock) will now fail TypeScript/parse checks without a `document_number`; add `document_number: "CN011729"` next to its existing `quote_number: "CN011729"`.
Expected: PASS across the board.

- [ ] **Step 9: Commit**

```bash
git add apps/api/src/origenlab_api/schemas/customer_quotes.py \
        apps/api/tests/test_customer_quote_routes.py \
        apps/dashboard/src/api/customerQuoteTypes.ts \
        apps/dashboard/src/api/customerQuoteParse.ts \
        apps/dashboard/src/api/customerQuoteParse.test.ts \
        apps/dashboard/src/components/pipeline/QuoteWorkspaceSection.test.tsx
git commit -m "feat(crm): expose durable document_number on customer-quote responses"
```

---

## Task 1: Extract shared quote-workspace UI primitives (no behavior change)

De-risks every later task: pull the parts of `QuoteWorkspaceSection.tsx` that Task 4 (detail drawer) and Task 5 (create dialog) both need into a shared module, with zero behavior change to the existing Ventas quote flow. `QuoteWorkspaceSection.test.tsx` must pass unmodified at the end of this task (besides the `document_number` fixture touch from Task 0).

**Files:**
- Create: `apps/dashboard/src/lib/idempotencyKey.ts`
- Create: `apps/dashboard/src/components/quotes/driveWorkspaceUi.tsx`
- Modify: `apps/dashboard/src/components/pipeline/QuoteWorkspaceSection.tsx`
- Test: `apps/dashboard/src/lib/idempotencyKey.test.ts`
- Test: `apps/dashboard/src/components/pipeline/QuoteWorkspaceSection.test.tsx` (must still pass, unchanged assertions)

**Interfaces:**
- Produces: `newIdempotencyKey(kind: string): string` from `lib/idempotencyKey.ts`.
- Produces: `DriveLink`, `QuoteWorkspaceStatus`, `failureCategoryMessage`, `createErrorMessage`, `NUMBERING_NOT_CONFIGURED_MESSAGE` from `components/quotes/driveWorkspaceUi.tsx` — Task 4's `QuoteDetailDrawer` and (for `createErrorMessage`/`NUMBERING_NOT_CONFIGURED_MESSAGE`) Task 5's `NuevaCotizacionDialog` both import these directly.

- [ ] **Step 1: Write the failing test for the shared idempotency key util**

```ts
// apps/dashboard/src/lib/idempotencyKey.test.ts
import { describe, expect, it } from "vitest";
import { newIdempotencyKey } from "./idempotencyKey";

describe("newIdempotencyKey", () => {
  it("prefixes the key with the given kind", () => {
    expect(newIdempotencyKey("quote")).toMatch(/^quote:[0-9a-f-]+$/);
  });

  it("generates a different key on each call", () => {
    expect(newIdempotencyKey("quote")).not.toBe(newIdempotencyKey("quote"));
  });
});
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `npm test -- idempotencyKey.test.ts`
Expected: FAIL — module `./idempotencyKey` doesn't exist.

- [ ] **Step 3: Implement it (moved, generalized from `QuoteWorkspaceSection.tsx:38-51`)**

```ts
// apps/dashboard/src/lib/idempotencyKey.ts
export function newIdempotencyKey(kind: string): string {
  const cryptoApi = globalThis.crypto;
  if (!cryptoApi) {
    throw new Error("No se pudo generar una clave segura para la operación.");
  }
  if (typeof cryptoApi.randomUUID === "function") {
    return `${kind}:${cryptoApi.randomUUID()}`;
  }
  const bytes = new Uint8Array(16);
  cryptoApi.getRandomValues(bytes);
  return `${kind}:${Array.from(bytes, (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("")}`;
}
```

- [ ] **Step 4: Run it, confirm it passes**

Run: `npm test -- idempotencyKey.test.ts` — Expected: PASS.

- [ ] **Step 5: Move `DriveLink`/`QuoteWorkspaceStatus`/failure-message helpers into `driveWorkspaceUi.tsx`**

Cut (not copy) `DriveLink`, `QuoteWorkspaceStatus`, `FAILURE_CATEGORY_MESSAGES`, `failureCategoryMessage`, `NUMBERING_NOT_CONFIGURED_MESSAGE`, `CREATE_ERROR_MESSAGE`, `createErrorMessage` out of `QuoteWorkspaceSection.tsx` (lines 10-178, 53-62) verbatim into the new file, exporting each:

```ts
// apps/dashboard/src/components/quotes/driveWorkspaceUi.tsx
import { OperatorApiError } from "../../api/operatorClient";
import type { CustomerQuote } from "../../api/customerQuoteTypes";

export const NUMBERING_NOT_CONFIGURED_MESSAGE =
  "La numeración de cotizaciones aún no está activada. Avisa al administrador del sistema.";

const CREATE_ERROR_MESSAGE = "No pudimos crear la cotización. Reintenta.";

export const FAILURE_CATEGORY_MESSAGES: Record<string, string> = { /* ...moved verbatim... */ };

export function failureCategoryMessage(category: string | null): string { /* ...moved verbatim... */ }

export function createErrorMessage(reason: unknown): string { /* ...moved verbatim... */ }

export function DriveLink({ href, label }: { href: string; label: string }) { /* ...moved verbatim... */ }

export function QuoteWorkspaceStatus({ quote, retryPending, retryError, onRetry }: {
  quote: CustomerQuote;
  retryPending: boolean;
  retryError: string | null;
  onRetry: () => void;
}) { /* ...moved verbatim... */ }
```

- [ ] **Step 6: Update `QuoteWorkspaceSection.tsx` to import from the shared modules**

Replace the local `newIdempotencyKey` definition and its four call sites with `import { newIdempotencyKey } from "../../lib/idempotencyKey"` + `newIdempotencyKey("quote")`. Replace the moved-out declarations with:

```ts
import {
  createErrorMessage,
  DriveLink,
  QuoteWorkspaceStatus,
} from "../quotes/driveWorkspaceUi";
```

(`DriveLink` becomes unused directly in this file if only `QuoteWorkspaceStatus` renders it internally — keep the import only if `QuoteWorkspaceSection.tsx` itself references `DriveLink`; check current usage before deciding whether to re-export it.)

- [ ] **Step 7: Run the full existing quote-workspace test, confirm no behavior change**

Run: `npm test -- QuoteWorkspaceSection.test.tsx`
Expected: PASS, same assertions as before this task (the file's test content is untouched except Task 0's `document_number` fixture line).

- [ ] **Step 8: Run the full dashboard suite once**

Run: `npm run validate` (or `npm test` if `validate` also runs lint/typecheck and is slower — check `package.json`'s `validate` script first)
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add apps/dashboard/src/lib/idempotencyKey.ts \
        apps/dashboard/src/lib/idempotencyKey.test.ts \
        apps/dashboard/src/components/quotes/driveWorkspaceUi.tsx \
        apps/dashboard/src/components/pipeline/QuoteWorkspaceSection.tsx
git commit -m "refactor(dashboard): extract shared quote idempotency-key + Drive-status primitives"
```

---

## Task 2: Primary IA reset — Cotizaciones-first nav, default route, visual emphasis

**Files:**
- Modify: `apps/dashboard/src/lib/dashboardNav.ts`
- Modify: `apps/dashboard/src/components/layout/DashboardSidebar.tsx`
- Modify: `apps/dashboard/src/lib/dashboardNav.test.ts` (rewritten)
- Modify: `apps/dashboard/src/pages/DashboardApp.test.tsx` (IA-order test, active-nav test, Proveedores/Pagos-y-logística tests switched to hash deep link)
- Create: `apps/dashboard/src/components/layout/DashboardSidebar.test.tsx`
- No changes: `apps/dashboard/src/lib/dashboardHashRoute.ts`, `apps/dashboard/src/pages/DashboardApp.tsx` (both already generic/complete)

**Interfaces:**
- Produces: `DEFAULT_DASHBOARD_SECTION = "cotizaciones"`, `DASHBOARD_TOP_NAV_IDS = ["cotizaciones", "tenders", "pipeline", "contacts", "prospectos", "inbox", "catalogo", "system"]`, `DASHBOARD_EMPHASIZED_NAV_IDS: ReadonlySet<DashboardSection>` (new export, `{"cotizaciones", "tenders", "pipeline"}`) — consumed by `DashboardSidebar`.
- Consumes (unchanged): `useDashboardSection`/`parseDashboardSectionFromHash`/`dashboardSectionToHash` from `dashboardHashRoute.ts`; `DashboardSectionView`'s switch in `DashboardApp.tsx`.

- [ ] **Step 1: Write the failing nav-registry test (full rewrite of `dashboardNav.test.ts`)**

```ts
// apps/dashboard/src/lib/dashboardNav.test.ts
import { describe, expect, it } from "vitest";
import {
  DASHBOARD_NAV_ITEMS,
  DASHBOARD_TOP_NAV_ITEMS,
  DASHBOARD_TOP_NAV_IDS,
  DASHBOARD_EMPHASIZED_NAV_IDS,
  DEFAULT_DASHBOARD_SECTION,
  dashboardSectionLabel,
} from "./dashboardNav";

describe("dashboardNav", () => {
  it("exposes exactly the 8-item Cotizaciones-first primary IA, in order", () => {
    expect(DASHBOARD_TOP_NAV_ITEMS.map((item) => item.label)).toEqual([
      "Cotizaciones",
      "Licitaciones",
      "Ventas",
      "Clientes",
      "Prospectos",
      "Correos",
      "Catálogo",
      "Sistema",
    ]);
  });

  it("defaults to Cotizaciones as the landing section", () => {
    expect(DEFAULT_DASHBOARD_SECTION).toBe("cotizaciones");
  });

  it("does not surface retired primary-nav concepts as top-level nav items", () => {
    const topIds = new Set(DASHBOARD_TOP_NAV_IDS as readonly string[]);
    expect(topIds.has("today")).toBe(false);
    expect(topIds.has("inbox")).toBe(false); // renamed+repositioned as "Correos", not removed
    expect(topIds.has("deals")).toBe(false);
    expect(topIds.has("suppliers")).toBe(false);
    expect(topIds.has("payments-logistics")).toBe(false);
  });

  it("keeps hidden sections resolvable by id for deep links", () => {
    expect(dashboardSectionLabel("today")).toBe("Inicio");
    expect(dashboardSectionLabel("deals")).toBe("Negocios");
    expect(dashboardSectionLabel("suppliers")).toBe("Proveedores");
    expect(dashboardSectionLabel("payments-logistics")).toBe("Pagos y logística");
  });

  it("relabels the former Bandeja de revisión as Correos and promotes it to primary nav", () => {
    const inbox = DASHBOARD_NAV_ITEMS.find((item) => item.id === "inbox")!;
    expect(inbox.label).toBe("Correos");
    expect((DASHBOARD_TOP_NAV_IDS as readonly string[]).includes("inbox")).toBe(true);
  });

  it("emphasizes exactly Cotizaciones, Licitaciones and Ventas", () => {
    expect([...DASHBOARD_EMPHASIZED_NAV_IDS].sort()).toEqual(
      ["cotizaciones", "pipeline", "tenders"].sort(),
    );
  });

  it("has no duplicate ids across the full registry", () => {
    const ids = DASHBOARD_NAV_ITEMS.map((item) => item.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("still resolves the full 12-id registry (nothing deleted, only reordered/relabeled)", () => {
    expect(DASHBOARD_NAV_ITEMS).toHaveLength(12);
  });
});
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `npm test -- dashboardNav.test.ts`
Expected: FAIL (old order, old default, no `DASHBOARD_EMPHASIZED_NAV_IDS` export).

- [ ] **Step 3: Rewrite `dashboardNav.ts`**

```ts
/** Secciones principales del panel operador (Dashboard V2 — IA Cotizaciones-first). */

export type DashboardSection =
  | "today"
  | "inbox"
  | "pipeline"
  | "deals"
  | "prospectos"
  | "cotizaciones"
  | "catalogo"
  | "suppliers"
  | "tenders"
  | "payments-logistics"
  | "contacts"
  | "system";

export type DashboardNavIconName =
  | "home"
  | "inbox"
  | "pipeline"
  | "deals"
  | "prospectos"
  | "quotes"
  | "contacts"
  | "tenders"
  | "payments"
  | "suppliers"
  | "catalog"
  | "system";

export interface DashboardNavItem {
  id: DashboardSection;
  label: string;
  shortLabel: string;
  description: string;
  iconName: DashboardNavIconName;
}

/**
 * Full section registry (12 ids), used for id -> label lookups so deep-linked
 * hidden sections (today/inbox is no longer hidden; deals/suppliers/payments-logistics
 * now are) still get a correct page title. Sidebar rendering uses
 * `DASHBOARD_TOP_NAV_ITEMS` below, not this list.
 */
export const DASHBOARD_NAV_ITEMS: DashboardNavItem[] = [
  {
    id: "today",
    label: "Inicio",
    shortLabel: "Inicio",
    description: "Resumen del día y conteos",
    iconName: "home",
  },
  {
    id: "cotizaciones",
    label: "Cotizaciones",
    shortLabel: "Cotiz.",
    description: "Cola global de cotizaciones y su carpeta en Drive (CRM durable)",
    iconName: "quotes",
  },
  {
    id: "tenders",
    label: "Licitaciones",
    shortLabel: "Licit.",
    description: "Cola de equipos y señales de compras públicas",
    iconName: "tenders",
  },
  {
    id: "pipeline",
    label: "Ventas",
    shortLabel: "Ventas",
    description: "Oportunidades de venta en gestión activa (CRM durable)",
    iconName: "pipeline",
  },
  {
    id: "contacts",
    label: "Clientes",
    shortLabel: "Clientes",
    description: "Instituciones compradoras, contactos e historial",
    iconName: "contacts",
  },
  {
    id: "prospectos",
    label: "Prospectos",
    shortLabel: "Prospectos",
    description: "Nuevas oportunidades de clientes (investigación DeepSearch)",
    iconName: "prospectos",
  },
  {
    id: "inbox",
    label: "Correos",
    shortLabel: "Correos",
    description: "Correspondencia entrante con filtros por rol",
    iconName: "inbox",
  },
  {
    id: "catalogo",
    label: "Catálogo",
    shortLabel: "Catálogo",
    description: "Productos, reactivos, equipos y repuestos cotizables",
    iconName: "catalog",
  },
  {
    id: "system",
    label: "Sistema",
    shortLabel: "Sistema",
    description: "Estado del servicio y política de lectura",
    iconName: "system",
  },
  {
    id: "deals",
    label: "Negocios",
    shortLabel: "Negocios",
    description: "Espejo de negocios comerciales",
    iconName: "deals",
  },
  {
    id: "suppliers",
    label: "Proveedores",
    shortLabel: "Prov.",
    description: "Cotizaciones y seguimientos de proveedores",
    iconName: "suppliers",
  },
  {
    id: "payments-logistics",
    label: "Pagos y logística",
    shortLabel: "Pagos",
    description: "Banco, transferencias, DHL e importación",
    iconName: "payments",
  },
];

/** The flat, ordered top-level nav — exactly the 8 primary sections, Cotizaciones-first. */
export const DASHBOARD_TOP_NAV_IDS: readonly DashboardSection[] = [
  "cotizaciones",
  "tenders",
  "pipeline",
  "contacts",
  "prospectos",
  "inbox",
  "catalogo",
  "system",
];

export const DASHBOARD_TOP_NAV_ITEMS: DashboardNavItem[] = DASHBOARD_TOP_NAV_IDS.map(
  (id) => DASHBOARD_NAV_ITEMS.find((item) => item.id === id)!,
);

/** Visually emphasized primary-work items, per the Phase 2 IA reset. */
export const DASHBOARD_EMPHASIZED_NAV_IDS: ReadonlySet<DashboardSection> = new Set([
  "cotizaciones",
  "tenders",
  "pipeline",
]);

export const DEFAULT_DASHBOARD_SECTION: DashboardSection = "cotizaciones";

export function dashboardSectionLabel(section: DashboardSection): string {
  return DASHBOARD_NAV_ITEMS.find((item) => item.id === section)?.label ?? section;
}
```

- [ ] **Step 4: Run the nav test, confirm green**

Run: `npm test -- dashboardNav.test.ts` — Expected: PASS.

- [ ] **Step 5: Write the failing sidebar-emphasis test**

```tsx
// apps/dashboard/src/components/layout/DashboardSidebar.test.tsx
import "@testing-library/jest-dom";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DashboardSidebar } from "./DashboardSidebar";

describe("DashboardSidebar", () => {
  it("renders the 8 primary items in Cotizaciones-first order", () => {
    render(
      <DashboardSidebar active="cotizaciones" collapsed={false} onNavigate={vi.fn()} onToggleCollapsed={vi.fn()} />,
    );
    const nav = screen.getByRole("navigation", { name: "Navegación del panel" });
    expect(within(nav).getAllByRole("link").map((el) => el.textContent)).toEqual([
      "Cotizaciones",
      "Licitaciones",
      "Ventas",
      "Clientes",
      "Prospectos",
      "Correos",
      "Catálogo",
      "Sistema",
    ]);
  });

  it("marks Cotizaciones, Licitaciones and Ventas as visually emphasized", () => {
    render(
      <DashboardSidebar active="cotizaciones" collapsed={false} onNavigate={vi.fn()} onToggleCollapsed={vi.fn()} />,
    );
    const nav = screen.getByRole("navigation", { name: "Navegación del panel" });
    for (const label of ["Cotizaciones", "Licitaciones", "Ventas"]) {
      expect(within(nav).getByRole("link", { name: label }).getAttribute("data-emphasized")).toBe("true");
    }
    for (const label of ["Clientes", "Prospectos", "Correos", "Catálogo", "Sistema"]) {
      expect(within(nav).getByRole("link", { name: label }).getAttribute("data-emphasized")).toBe("false");
    }
  });

  it("bare-hash href resolves to the default section, not literally 'today'", () => {
    render(
      <DashboardSidebar active="cotizaciones" collapsed={false} onNavigate={vi.fn()} onToggleCollapsed={vi.fn()} />,
    );
    const nav = screen.getByRole("navigation", { name: "Navegación del panel" });
    expect(within(nav).getByRole("link", { name: "Cotizaciones" }).getAttribute("href")).toBe("#/");
  });
});
```

- [ ] **Step 6: Run it, confirm it fails**

Run: `npm test -- DashboardSidebar.test.tsx`
Expected: FAIL (`data-emphasized` doesn't exist yet; `navHref` still hardcodes `"today"` so Cotizaciones' href would be `#/cotizaciones`, not `#/`).

- [ ] **Step 7: Update `DashboardSidebar.tsx`**

```tsx
import { OrigenLabStaticLogo } from "../brand/OrigenLabStaticLogo";
import {
  DASHBOARD_TOP_NAV_ITEMS,
  DASHBOARD_EMPHASIZED_NAV_IDS,
  DEFAULT_DASHBOARD_SECTION,
  type DashboardNavItem,
  type DashboardSection,
} from "../../lib/dashboardNav";
import { NavIcon } from "./NavIcon";

function navHref(section: DashboardSection): string {
  return section === DEFAULT_DASHBOARD_SECTION ? "#/" : `#/${section}`;
}

// ...SidebarCollapseToggle unchanged...

function NavLink({
  item,
  isActive,
  collapsed,
  onNavigate,
}: {
  item: DashboardNavItem;
  isActive: boolean;
  collapsed: boolean;
  onNavigate: (section: DashboardSection) => void;
}) {
  const emphasized = DASHBOARD_EMPHASIZED_NAV_IDS.has(item.id);
  return (
    <a
      href={navHref(item.id)}
      onClick={(e) => {
        e.preventDefault();
        onNavigate(item.id);
      }}
      aria-current={isActive ? "page" : undefined}
      aria-label={item.label}
      title={collapsed ? item.label : item.description}
      data-emphasized={emphasized ? "true" : "false"}
      className={`group flex items-center gap-3 rounded-lg text-sm transition-colors motion-reduce:transition-none ${
        collapsed ? "justify-center px-2 py-2.5" : "px-3 py-2"
      } ${emphasized ? "font-semibold" : "font-medium"} ${
        isActive
          ? "bg-brand-600 text-white shadow-sm ring-1 ring-brand-700/50"
          : emphasized
            ? "border-l-2 border-brand-400 text-slate-200 hover:bg-slate-800 hover:text-white"
            : "border-l-2 border-transparent text-slate-300 hover:bg-slate-800 hover:text-white"
      }`}
    >
      <NavIcon
        name={item.iconName}
        className={`h-5 w-5 shrink-0 ${isActive ? "text-white" : "text-slate-400 group-hover:text-white"}`}
      />
      {!collapsed ? <span className="truncate">{item.label}</span> : null}
    </a>
  );
}

// ...DashboardSidebar body unchanged (still maps DASHBOARD_TOP_NAV_ITEMS)...
```

- [ ] **Step 8: Run the sidebar test, confirm green**

Run: `npm test -- DashboardSidebar.test.tsx` — Expected: PASS.

- [ ] **Step 9: Update `DashboardApp.test.tsx`'s IA-order and active-nav tests**

Replace the `"sidebar renders exactly the flat V2 IA..."` test's expected array with the new 8-item order, and its removed-labels loop with `["Inicio", "Negocios", "Proveedores", "Pagos y logística"]` (now hidden — `"Bandeja de revisión"` is gone as a label entirely, replaced by `"Correos"`, so drop it from that loop and instead assert `"Correos"` **is** present). Replace the `"marks active nav item with aria-current"` test's `"Inicio"` assertion with `"Cotizaciones"` (now the default-active item) — but note `mockAllOk()` doesn't currently mock `fetchCustomerQuotesGlobal`; Task 3 must extend `mockAllOk()` before this test can pass with real data, so mark this specific assertion `it.skip` in this task with a comment `// unskipped in Task 3 once fetchCustomerQuotesGlobal is mocked` if landing Task 2 standalone, or land Task 2 and Task 3 as one reviewed unit if that skip feels dishonest — prefer the latter given how small Task 3's mock addition is.

Convert `"Suppliers page excludes client opportunities"` and `"Payments & logistics excludes supplier and client rows"` to hash deep-link setup instead of `navigateTo(...)` clicks (mirroring the existing `"Negocios is still reachable by deep link..."` pattern already in the file):

```ts
  it("Suppliers page excludes client opportunities", async () => {
    window.location.hash = "#/suppliers";
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));
    await waitFor(() => {
      expect(screen.getByTestId("suppliers-workspace")).toBeTruthy();
      expect(screen.getByTestId("supplier-detail-title").textContent).toBe("IKA");
    });
    fireEvent.click(screen.getByRole("button", { name: /IKA, 1 caso en espejo/i }));
    await waitFor(() => {
      screen.getByText("beatriz.bonon@ika.net.br");
    });
    expect(screen.queryByText("buyer@acme.cl")).toBeNull();

    const nav = screen.getByRole("navigation", { name: "Navegación del panel" });
    expect(within(nav).queryByRole("link", { name: "Proveedores" })).toBeNull();
  });
```

(same shape for Payments & logistics, hash `"#/payments-logistics"`, asserting `"Pagos y logística"` is absent from `nav`). Remove the now-stale `"Bandeja de revisión is still reachable by deep link..."` test's assertion that `"Bandeja de revisión"` is absent from nav — replace with an equivalent test asserting `"Correos"` **is present** in nav (since it's promoted, not hidden) and that navigating to it via nav click (not hash) still renders the same triage content (`buyer@acme.cl`) — i.e. convert this from a hidden-section test to a normal `navigateTo("Correos")` test.

- [ ] **Step 10: Run `DashboardApp.test.tsx`, confirm it's green except the one deliberately-deferred assertion (if deferred)**

Run: `npm test -- DashboardApp.test.tsx`

- [ ] **Step 11: Commit**

```bash
git add apps/dashboard/src/lib/dashboardNav.ts \
        apps/dashboard/src/lib/dashboardNav.test.ts \
        apps/dashboard/src/components/layout/DashboardSidebar.tsx \
        apps/dashboard/src/components/layout/DashboardSidebar.test.tsx \
        apps/dashboard/src/pages/DashboardApp.test.tsx
git commit -m "feat(dashboard): reset primary IA to Cotizaciones-first, retire Inicio as default"
```

---

## Task 3: Cotizaciones global work queue (read-only)

**Files:**
- Create: `apps/dashboard/src/components/quotes/useCustomerQuotesGlobal.ts`
- Create: `apps/dashboard/src/components/quotes/CustomerQuoteQueueTable.tsx`
- Create: `apps/dashboard/src/components/quotes/customerQuoteQueueFilters.ts`
- Modify: `apps/dashboard/src/pages/CotizacionesPage.tsx` (full replacement)
- Modify: `apps/dashboard/src/pages/CotizacionesPage.test.tsx` (full replacement)
- Modify: `apps/dashboard/src/pages/DashboardApp.test.tsx` (extend `mockAllOk()` with `fetchCustomerQuotesGlobal`; unskip/complete the active-nav assertion from Task 2 Step 9; replace the old `"Cotizaciones page renders and its 'Ir a Ventas' action..."` test)
- Test: `apps/dashboard/src/components/quotes/useCustomerQuotesGlobal.test.ts`
- Test: `apps/dashboard/src/components/quotes/customerQuoteQueueFilters.test.ts`

**Interfaces:**
- Consumes: `fetchCustomerQuotesGlobal(params?: {stage?, driveStatus?, limit?, offset?})` (`apps/dashboard/src/api/customerQuoteClient.ts:158-179`), `CustomerQuoteGlobalItem`/`CustomerQuoteGlobalListResponse` (`customerQuoteTypes.ts`), `salesOpportunityStageLabel` (`lib/salesOpportunityFormat.ts`), `formatCommercialOpportunityDate` (`lib/commercialOpportunityFormat.ts`).
- Produces: `useCustomerQuotesGlobal()` → `{items, loading, error, refetch, stageToggles, toggleStage, driveStatusToggles, toggleDriveStatus}` — Task 4's detail drawer and Task 5's dialog-success handler both call `.refetch()`. `filterQuoteQueueItems(items, {searchText, recency}): CustomerQuoteGlobalItem[]` — pure function, independently testable.
- Produces: `quoteQueueStateLabel(item: CustomerQuoteGlobalItem): {status: "Borrador"; drive: "Drive listo" | "Aprovisionando" | "Error de Drive"}`.

- [ ] **Step 1: Write the failing filter-logic test (pure function, no network)**

```ts
// apps/dashboard/src/components/quotes/customerQuoteQueueFilters.test.ts
import { describe, expect, it } from "vitest";
import { filterQuoteQueueItems, quoteQueueStateLabel } from "./customerQuoteQueueFilters";
import type { CustomerQuoteGlobalItem } from "../../api/customerQuoteTypes";

function item(overrides: Partial<CustomerQuoteGlobalItem["quote"]> = {}, extra: Partial<CustomerQuoteGlobalItem> = {}): CustomerQuoteGlobalItem {
  return {
    quote: {
      quote_id: "quote_" + "a".repeat(32),
      sales_opportunity_id: "sales_" + "b".repeat(32),
      quote_number: "01183-26",
      document_number: "CN01183",
      status: "draft",
      version: 1,
      latest_revision_number: 1,
      created_by: "op@origenlab.cl",
      updated_by: "op@origenlab.cl",
      created_at: "2026-08-30T10:00:00Z",
      updated_at: "2026-08-30T10:00:00Z",
      drive_workspace: {
        provider: "google_drive",
        provisioning_status: "ready",
        folder_id: "f1",
        folder_web_url: "https://drive.google.com/drive/folders/f1",
        sheet_file_id: "s1",
        sheet_web_url: "https://docs.google.com/spreadsheets/d/s1",
        failure_category: null,
        attempt_count: 1,
        version: 1,
        retryable: false,
        lease_expires_at: null,
        requested_at: "2026-08-30T10:00:00Z",
        completed_at: "2026-08-30T10:00:05Z",
      },
      ...overrides,
    },
    sales_opportunity_stage: "quoting",
    sales_opportunity_owner_key: "op@origenlab.cl",
    organization_display_name: "CEAF",
    contact_display_name: "Tatiana Rojas",
    contact_primary_email: "tatiana@ceaf.cl",
    next_task_title: null,
    next_task_due_at: null,
    ...extra,
  };
}

describe("filterQuoteQueueItems", () => {
  it("matches on quote_number, document_number, organization and contact (case-insensitive)", () => {
    const rows = [item({ quote_number: "01183-26" }, { organization_display_name: "CEAF" })];
    expect(filterQuoteQueueItems(rows, { searchText: "ceaf", recency: "all" })).toHaveLength(1);
    expect(filterQuoteQueueItems(rows, { searchText: "01183", recency: "all" })).toHaveLength(1);
    expect(filterQuoteQueueItems(rows, { searchText: "nope", recency: "all" })).toHaveLength(0);
  });

  it("filters by recency against quote.updated_at", () => {
    const now = new Date("2026-09-01T00:00:00Z");
    const recent = item({ updated_at: "2026-08-31T00:00:00Z" });
    const old = item({ updated_at: "2026-06-01T00:00:00Z" });
    const result = filterQuoteQueueItems([recent, old], { searchText: "", recency: "7d" }, now);
    expect(result).toHaveLength(1);
  });
});

describe("quoteQueueStateLabel", () => {
  it.each([
    ["ready", "Drive listo"],
    ["pending", "Aprovisionando"],
    ["failed", "Error de Drive"],
  ] as const)("maps drive provisioning_status %s to %s", (status, label) => {
    const row = item({ drive_workspace: { ...item().quote.drive_workspace, provisioning_status: status } });
    expect(quoteQueueStateLabel(row).drive).toBe(label);
    expect(quoteQueueStateLabel(row).status).toBe("Borrador");
  });
});
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `npm test -- customerQuoteQueueFilters.test.ts` — Expected: FAIL (module doesn't exist).

- [ ] **Step 3: Implement `customerQuoteQueueFilters.ts`**

```ts
import type { CustomerQuoteGlobalItem, QuoteProvisioningStatus } from "../../api/customerQuoteTypes";

export type QueueRecencyFilter = "all" | "7d" | "30d";

const RECENCY_DAYS: Record<Exclude<QueueRecencyFilter, "all">, number> = {
  "7d": 7,
  "30d": 30,
};

export function filterQuoteQueueItems(
  items: readonly CustomerQuoteGlobalItem[],
  filters: { searchText: string; recency: QueueRecencyFilter },
  now: Date = new Date(),
): CustomerQuoteGlobalItem[] {
  const search = filters.searchText.trim().toLowerCase();

  return items.filter((row) => {
    if (search) {
      const haystack = [
        row.quote.quote_number,
        row.quote.document_number,
        row.organization_display_name,
        row.contact_display_name,
        row.contact_primary_email,
      ]
        .filter((value): value is string => Boolean(value))
        .join(" ")
        .toLowerCase();
      if (!haystack.includes(search)) return false;
    }

    if (filters.recency !== "all") {
      const days = RECENCY_DAYS[filters.recency];
      const updated = new Date(row.quote.updated_at);
      const ageMs = now.getTime() - updated.getTime();
      if (ageMs > days * 24 * 60 * 60 * 1000) return false;
    }

    return true;
  });
}

const DRIVE_STATE_LABELS: Record<QuoteProvisioningStatus, "Drive listo" | "Aprovisionando" | "Error de Drive"> = {
  ready: "Drive listo",
  pending: "Aprovisionando",
  failed: "Error de Drive",
};

export function quoteQueueStateLabel(
  item: CustomerQuoteGlobalItem,
): { status: "Borrador"; drive: "Drive listo" | "Aprovisionando" | "Error de Drive" } {
  return {
    status: "Borrador",
    drive: DRIVE_STATE_LABELS[item.quote.drive_workspace.provisioning_status],
  };
}
```

- [ ] **Step 4: Run it, confirm green**

Run: `npm test -- customerQuoteQueueFilters.test.ts` — Expected: PASS.

- [ ] **Step 5: Write the failing hook test**

```ts
// apps/dashboard/src/components/quotes/useCustomerQuotesGlobal.test.ts
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useCustomerQuotesGlobal } from "./useCustomerQuotesGlobal";
import * as client from "../../api/customerQuoteClient";

vi.mock("../../api/customerQuoteClient");

describe("useCustomerQuotesGlobal", () => {
  it("fetches on mount with limit 200 and no filters", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });

    const { result } = renderHook(() => useCustomerQuotesGlobal());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(client.fetchCustomerQuotesGlobal).toHaveBeenCalledWith({
      stage: undefined,
      driveStatus: undefined,
      limit: 200,
      offset: 0,
    });
  });

  it("surfaces a load error without throwing", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockRejectedValue(new Error("boom"));

    const { result } = renderHook(() => useCustomerQuotesGlobal());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBeTruthy();
    expect(result.current.items).toEqual([]);
  });

  it("re-fetches with the drive-status filter when toggled", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });

    const { result } = renderHook(() => useCustomerQuotesGlobal());
    await waitFor(() => expect(result.current.loading).toBe(false));

    result.current.toggleDriveStatus("failed");
    await waitFor(() =>
      expect(client.fetchCustomerQuotesGlobal).toHaveBeenLastCalledWith(
        expect.objectContaining({ driveStatus: ["failed"] }),
      ),
    );
  });
});
```

- [ ] **Step 6: Run it, confirm it fails**

Run: `npm test -- useCustomerQuotesGlobal.test.ts` — Expected: FAIL (module doesn't exist).

- [ ] **Step 7: Implement the hook, mirroring `useSalesOpportunityBoard.ts`'s shape**

```ts
// apps/dashboard/src/components/quotes/useCustomerQuotesGlobal.ts
import { useCallback, useEffect, useState } from "react";
import { fetchCustomerQuotesGlobal } from "../../api/customerQuoteClient";
import type { CustomerQuoteGlobalItem, QuoteProvisioningStatus } from "../../api/customerQuoteTypes";
import type { SalesOpportunityStage } from "../../api/commercialOperationsTypes";

const QUEUE_FETCH_LIMIT = 200;

export function useCustomerQuotesGlobal() {
  const [items, setItems] = useState<CustomerQuoteGlobalItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [stageToggles, setStageToggles] = useState<SalesOpportunityStage[]>([]);
  const [driveStatusToggles, setDriveStatusToggles] = useState<QuoteProvisioningStatus[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const result = await fetchCustomerQuotesGlobal({
        stage: stageToggles.length ? stageToggles : undefined,
        driveStatus: driveStatusToggles.length ? driveStatusToggles : undefined,
        limit: QUEUE_FETCH_LIMIT,
        offset: 0,
      });
      setItems(result.items);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "No pudimos cargar las cotizaciones.");
    } finally {
      setLoading(false);
    }
  }, [stageToggles, driveStatusToggles]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggleStage = useCallback((stage: SalesOpportunityStage) => {
    setStageToggles((current) =>
      current.includes(stage) ? current.filter((value) => value !== stage) : [...current, stage],
    );
  }, []);

  const toggleDriveStatus = useCallback((status: QuoteProvisioningStatus) => {
    setDriveStatusToggles((current) =>
      current.includes(status) ? current.filter((value) => value !== status) : [...current, status],
    );
  }, []);

  return { items, loading, error, refetch: load, stageToggles, toggleStage, driveStatusToggles, toggleDriveStatus };
}
```

- [ ] **Step 8: Run it, confirm green**

Run: `npm test -- useCustomerQuotesGlobal.test.ts` — Expected: PASS.

- [ ] **Step 9: Build `CustomerQuoteQueueTable.tsx`**

A row-per-quote table (desktop) reusing the row shape already proven in `QuoteWorkspaceSection`'s `<li>` (quote number + state badges), extended with the queue-specific columns. Props:

```ts
export function CustomerQuoteQueueTable({
  items,
  onOpenQuote,
}: {
  items: readonly CustomerQuoteGlobalItem[];
  onOpenQuote: (item: CustomerQuoteGlobalItem) => void;
}): JSX.Element
```

Each row renders: `quote.quote_number` (primary) + `quote.document_number` (secondary, muted), `organization_display_name ?? "—"`, `contact_display_name ?? contact_primary_email ?? "—"`, `salesOpportunityStageLabel(sales_opportunity_stage)`, the two-part state badge from `quoteQueueStateLabel(item)` ("Borrador" + drive state, drive state colored amber for `Error de Drive`, slate for `Aprovisionando`, emerald for `Drive listo` — reuse the color vocabulary already established in `QuoteWorkspaceStatus`), `formatCommercialOpportunityDate(quote.updated_at)`, and `next_task_title` (muted, `"—"` if null). Row is a `<button>` (not a link — no per-quote deep link exists yet) calling `onOpenQuote(item)`. No KPI cards above the table — just the header + filter bar + table, per the spec's "avoid KPI-card clutter" instruction.

- [ ] **Step 10: Wire `CotizacionesPage.tsx`**

```tsx
// apps/dashboard/src/pages/CotizacionesPage.tsx
import { useMemo, useState } from "react";
import { V2PageHeader } from "../components/v2/V2PageHeader";
import { V2EmptyState } from "../components/v2/V2EmptyState";
import { useCustomerQuotesGlobal } from "../components/quotes/useCustomerQuotesGlobal";
import { CustomerQuoteQueueTable } from "../components/quotes/CustomerQuoteQueueTable";
import { filterQuoteQueueItems, type QueueRecencyFilter } from "../components/quotes/customerQuoteQueueFilters";
import type { CustomerQuoteGlobalItem } from "../api/customerQuoteTypes";

export function CotizacionesPage({ onOpenVentas }: { onOpenVentas: () => void }) {
  const queue = useCustomerQuotesGlobal();
  const [searchText, setSearchText] = useState("");
  const [recency, setRecency] = useState<QueueRecencyFilter>("all");
  const [openItem, setOpenItem] = useState<CustomerQuoteGlobalItem | null>(null);

  const visibleItems = useMemo(
    () => filterQuoteQueueItems(queue.items, { searchText, recency }),
    [queue.items, searchText, recency],
  );

  return (
    <div className="space-y-4">
      <V2PageHeader
        title="Cotizaciones"
        subtitle="Cola global de cotizaciones durables y su carpeta en Drive."
        actions={
          <button
            type="button"
            onClick={queue.refetch}
            disabled={queue.loading}
            className="rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            {queue.loading ? "Actualizando…" : "Actualizar"}
          </button>
        }
      />

      {/* filter bar: search input, stage + drive-status toggle chips, recency select — Task 5 adds the "Nueva Cotización" primary CTA here */}

      {queue.error ? (
        <p role="alert" className="text-sm text-amber-900">{queue.error}</p>
      ) : null}

      {!queue.loading && !queue.error && visibleItems.length === 0 ? (
        <V2EmptyState
          title={queue.items.length === 0 ? "Aún no hay cotizaciones" : "Sin resultados para estos filtros"}
          description={
            queue.items.length === 0
              ? "Crea la primera cotización desde una oportunidad en Ventas, o usa Nueva Cotización aquí."
              : "Ajusta la búsqueda o los filtros."
          }
          action={
            queue.items.length === 0 ? (
              <button type="button" onClick={onOpenVentas} className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700">
                Ir a Ventas
              </button>
            ) : undefined
          }
        />
      ) : (
        <CustomerQuoteQueueTable items={visibleItems} onOpenQuote={setOpenItem} />
      )}

      {/* Task 4 wires QuoteDetailDrawer here, keyed on openItem */}
    </div>
  );
}
```

- [ ] **Step 11: Replace `CotizacionesPage.test.tsx`**

```tsx
import "@testing-library/jest-dom";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CotizacionesPage } from "./CotizacionesPage";
import * as client from "../api/customerQuoteClient";
import { globalQuoteItemFixture } from "../test/fixtures/customerQuoteFixtures"; // new small fixture helper, Step 12

vi.mock("../api/customerQuoteClient");

describe("CotizacionesPage", () => {
  it("renders the durable global queue, not a placeholder", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [globalQuoteItemFixture()],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);

    await waitFor(() => screen.getByText("01183-26"));
    screen.getByText("CEAF");
    expect(screen.queryByText(/próximamente/)).toBeNull();
  });

  it("shows an honest empty state when there are no quotes yet", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);

    await waitFor(() => screen.getByText("Aún no hay cotizaciones"));
  });

  it("filters the visible rows by search text against quote/document number and customer", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [globalQuoteItemFixture()],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText("01183-26"));

    const search = screen.getByRole("searchbox", { name: /buscar/i });
    fireEvent.change(search, { target: { value: "no-match" } });
    await waitFor(() => screen.getByText("Sin resultados para estos filtros"));
  });
});
```

- [ ] **Step 12: Add the shared fixture helper**

```ts
// apps/dashboard/src/test/fixtures/customerQuoteFixtures.ts
import type { CustomerQuoteGlobalItem } from "../../api/customerQuoteTypes";

export function globalQuoteItemFixture(
  overrides: Partial<CustomerQuoteGlobalItem> = {},
): CustomerQuoteGlobalItem {
  return {
    quote: {
      quote_id: "quote_" + "a".repeat(32),
      sales_opportunity_id: "sales_" + "b".repeat(32),
      quote_number: "01183-26",
      document_number: "CN01183",
      status: "draft",
      version: 1,
      latest_revision_number: 1,
      created_by: "op@origenlab.cl",
      updated_by: "op@origenlab.cl",
      created_at: "2026-08-30T10:00:00Z",
      updated_at: "2026-08-30T10:00:00Z",
      drive_workspace: {
        provider: "google_drive",
        provisioning_status: "ready",
        folder_id: "f1",
        folder_web_url: "https://drive.google.com/drive/folders/f1",
        sheet_file_id: "s1",
        sheet_web_url: "https://docs.google.com/spreadsheets/d/s1",
        failure_category: null,
        attempt_count: 1,
        version: 1,
        retryable: false,
        lease_expires_at: null,
        requested_at: "2026-08-30T10:00:00Z",
        completed_at: "2026-08-30T10:00:05Z",
      },
    },
    sales_opportunity_stage: "quoting",
    sales_opportunity_owner_key: "op@origenlab.cl",
    organization_display_name: "CEAF",
    contact_display_name: "Tatiana Rojas",
    contact_primary_email: "tatiana@ceaf.cl",
    next_task_title: null,
    next_task_due_at: null,
    ...overrides,
  };
}
```

(Used by Task 4 and Task 5's tests too — build once here.)

- [ ] **Step 13: Run `CotizacionesPage.test.tsx`, confirm green**

Run: `npm test -- CotizacionesPage.test.tsx`

- [ ] **Step 14: Extend `DashboardApp.test.tsx`'s `mockAllOk()` and finish the deferred Task 2 assertions**

Add to the `../api/customerQuoteClient` mock block and `mockAllOk()`:

```ts
vi.mock("../api/customerQuoteClient", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/customerQuoteClient")>();
  return { ...actual, fetchCustomerQuotesGlobal: vi.fn() };
});
// ...
import { fetchCustomerQuotesGlobal } from "../api/customerQuoteClient";
// ...inside mockAllOk():
  vi.mocked(fetchCustomerQuotesGlobal).mockResolvedValue({
    meta: { count: 0, total_count: 0, limit: 200, offset: 0 },
    items: [],
  });
```

Now un-skip/complete the `"marks active nav item with aria-current"` test from Task 2 (asserts `"Cotizaciones"` has `aria-current="page"` on initial render at `#/`), and replace the old `"Cotizaciones page renders and its 'Ir a Ventas' action navigates to Ventas"` test with:

```ts
  it("root hash resolves to Cotizaciones and renders the durable queue empty state", async () => {
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));

    expect(screen.getByRole("heading", { level: 1, name: "Cotizaciones" })).toBeTruthy();
    await waitFor(() => screen.getByText("Aún no hay cotizaciones"));
  });
```

- [ ] **Step 15: Run full dashboard suite, confirm green**

Run: `npm run validate`

- [ ] **Step 16: Commit**

```bash
git add apps/dashboard/src/components/quotes/useCustomerQuotesGlobal.ts \
        apps/dashboard/src/components/quotes/useCustomerQuotesGlobal.test.ts \
        apps/dashboard/src/components/quotes/customerQuoteQueueFilters.ts \
        apps/dashboard/src/components/quotes/customerQuoteQueueFilters.test.ts \
        apps/dashboard/src/components/quotes/CustomerQuoteQueueTable.tsx \
        apps/dashboard/src/pages/CotizacionesPage.tsx \
        apps/dashboard/src/pages/CotizacionesPage.test.tsx \
        apps/dashboard/src/pages/DashboardApp.test.tsx \
        apps/dashboard/src/test/fixtures/customerQuoteFixtures.ts
git commit -m "feat(dashboard): replace Cotizaciones placeholder with the durable global work queue"
```

---

## Task 4: Quote detail / Drive workspace drawer

**Files:**
- Create: `apps/dashboard/src/components/quotes/QuoteDetailDrawer.tsx`
- Create: `apps/dashboard/src/components/quotes/QuoteDetailDrawer.test.tsx`
- Modify: `apps/dashboard/src/pages/CotizacionesPage.tsx` (wire the drawer)
- Modify: `apps/dashboard/src/pages/CotizacionesPage.test.tsx` (open/close/retry coverage)

**Interfaces:**
- Consumes: `fetchCustomerQuote(quoteId)`, `retryCustomerQuoteDriveWorkspace(quoteId, {expected_version})` (`api/customerQuoteClient.ts`), `QuoteWorkspaceStatus`/`DriveLink` from Task 1's `driveWorkspaceUi.tsx`, `salesOpportunityStageLabel`, `formatCommercialOpportunityDate`.
- Props: `{item: CustomerQuoteGlobalItem | null; open: boolean; onClose: () => void; onOpenVentas: () => void}`.

- [ ] **Step 1: Write the failing drawer test**

```tsx
// apps/dashboard/src/components/quotes/QuoteDetailDrawer.test.tsx
import "@testing-library/jest-dom";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { QuoteDetailDrawer } from "./QuoteDetailDrawer";
import * as client from "../../api/customerQuoteClient";
import { globalQuoteItemFixture } from "../../test/fixtures/customerQuoteFixtures";

vi.mock("../../api/customerQuoteClient");

describe("QuoteDetailDrawer", () => {
  it("refreshes the quote on open and shows identity, opportunity and Drive links", async () => {
    const fixture = globalQuoteItemFixture();
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: fixture.quote });

    render(<QuoteDetailDrawer item={fixture} open onClose={vi.fn()} onOpenVentas={vi.fn()} />);

    await waitFor(() => expect(client.fetchCustomerQuote).toHaveBeenCalledWith(fixture.quote.quote_id));
    screen.getByText("CEAF");
    screen.getByText("01183-26");
    screen.getByText("CN01183");
    expect(screen.getByRole("link", { name: /Abrir carpeta/ })).toHaveAttribute(
      "href",
      "https://drive.google.com/drive/folders/f1",
    );
  });

  it("shows the failure category and a retry action for a failed workspace, reusing the retry command", async () => {
    const failed = globalQuoteItemFixture({
      quote: {
        ...globalQuoteItemFixture().quote,
        drive_workspace: {
          ...globalQuoteItemFixture().quote.drive_workspace,
          provisioning_status: "failed",
          failure_category: "drive_unavailable",
          retryable: true,
        },
      },
    });
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: failed.quote });
    vi.mocked(client.retryCustomerQuoteDriveWorkspace).mockResolvedValue({
      ...failed.quote,
      drive_workspace: { ...failed.quote.drive_workspace, provisioning_status: "ready" },
    });

    render(<QuoteDetailDrawer item={failed} open onClose={vi.fn()} onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText(/Google Drive no está disponible/));

    fireEvent.click(screen.getByRole("button", { name: /Reintentar/ }));
    await waitFor(() =>
      expect(client.retryCustomerQuoteDriveWorkspace).toHaveBeenCalledWith(failed.quote.quote_id, {
        expected_version: failed.quote.drive_workspace.version,
      }),
    );
  });

  it("never renders a raw Drive id as a link — only server-validated https URLs", async () => {
    const noLinks = globalQuoteItemFixture({
      quote: {
        ...globalQuoteItemFixture().quote,
        drive_workspace: {
          ...globalQuoteItemFixture().quote.drive_workspace,
          folder_web_url: null,
          sheet_web_url: null,
        },
      },
    });
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: noLinks.quote });

    render(<QuoteDetailDrawer item={noLinks} open onClose={vi.fn()} onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText("01183-26"));
    expect(screen.queryByRole("link", { name: /Abrir carpeta/ })).toBeNull();
  });

  it("'Ver en Ventas' calls onOpenVentas", async () => {
    const fixture = globalQuoteItemFixture();
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: fixture.quote });
    const onOpenVentas = vi.fn();

    render(<QuoteDetailDrawer item={fixture} open onClose={vi.fn()} onOpenVentas={onOpenVentas} />);
    await waitFor(() => screen.getByText("01183-26"));

    fireEvent.click(screen.getByRole("button", { name: "Ver en Ventas" }));
    expect(onOpenVentas).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `npm test -- QuoteDetailDrawer.test.tsx` — Expected: FAIL (module doesn't exist).

- [ ] **Step 3: Implement `QuoteDetailDrawer.tsx`**

Structure follows `SalesOpportunityWorkspaceDrawer.tsx`'s proven shape (fixed overlay + right-side panel on desktop, full-screen on mobile, Escape-to-close, focus management) but simpler — no stage-change menu, no task panel. On `open` + `item` change: `fetchCustomerQuote(item.quote.quote_id)` to refresh `drive_workspace`/`version` (mirrors `SalesOpportunityWorkspaceDrawer`'s `fetchSalesOpportunity` refresh-on-open), merging only if the response's `version >= current.version` (reuse the same `mergeIfNewer`-style guard, adapted to `CustomerQuote`). Sections, top to bottom:

1. Header: `quote.quote_number` (large) + `document_number` (muted, smaller) + close button.
2. "Identidad" — `organization_display_name`, `contact_display_name`, `contact_primary_email` from the `CustomerQuoteGlobalItem` passed in as `item` (no extra fetch — already loaded by the queue).
3. "Oportunidad" — `salesOpportunityStageLabel(item.sales_opportunity_stage)`, `sales_opportunity_owner_key`, `next_task_title`/`next_task_due_at` if present (same "Próxima acción" banner styling as `SalesOpportunityWorkspaceDrawer.tsx:248-262`), and a **"Ver en Ventas"** button calling `onOpenVentas()` — a deliberate scope trim: no opportunity-id deep link exists in the hash router yet, so this navigates to the Ventas section generally (same `onOpenVentas`/`navigate("pipeline")` callback the placeholder already used), not directly into that opportunity's own drawer. Documented here, not treated as a blocker — building opportunity-id deep linking is out of this phase's scope.
4. "Cotización" — revision (`latest_revision_number`), status badge ("Borrador"), created/updated dates via `formatCommercialOpportunityDate`.
5. Drive workspace — `<QuoteWorkspaceStatus quote={core} retryPending={...} retryError={...} onRetry={...} />` from Task 1's shared module, wired to `retryCustomerQuoteDriveWorkspace` exactly like `QuoteWorkspaceSection.handleRetry` (same 409-conflict re-fetch-and-merge behavior).

- [ ] **Step 4: Run the drawer test, confirm green**

Run: `npm test -- QuoteDetailDrawer.test.tsx`

- [ ] **Step 5: Wire into `CotizacionesPage.tsx`**

Add `<QuoteDetailDrawer item={openItem} open={openItem !== null} onClose={() => setOpenItem(null)} onOpenVentas={onOpenVentas} />` where the "Task 4 wires..." comment currently sits (Task 3, Step 10).

- [ ] **Step 6: Extend `CotizacionesPage.test.tsx`**

```ts
  it("opens the detail drawer on row click and shows the quote number", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [globalQuoteItemFixture()],
    });
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: globalQuoteItemFixture().quote });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText("01183-26"));

    fireEvent.click(screen.getByRole("button", { name: /01183-26/ }));
    await waitFor(() => screen.getByRole("dialog"));
  });
```

- [ ] **Step 7: Run it, confirm green; run full suite**

Run: `npm test -- CotizacionesPage.test.tsx && npm run validate`

- [ ] **Step 8: Commit**

```bash
git add apps/dashboard/src/components/quotes/QuoteDetailDrawer.tsx \
        apps/dashboard/src/components/quotes/QuoteDetailDrawer.test.tsx \
        apps/dashboard/src/pages/CotizacionesPage.tsx \
        apps/dashboard/src/pages/CotizacionesPage.test.tsx
git commit -m "feat(dashboard): add the Cotizaciones quote detail / Drive workspace drawer"
```

---

## Task 5: Nueva Cotización — existing-opportunity and manual-intake dialog

**Files:**
- Create: `apps/dashboard/src/components/quotes/useExistingOpportunityPicker.ts`
- Create: `apps/dashboard/src/components/quotes/useExistingOpportunityPicker.test.ts`
- Create: `apps/dashboard/src/components/quotes/NuevaCotizacionDialog.tsx`
- Create: `apps/dashboard/src/components/quotes/NuevaCotizacionDialog.test.tsx`
- Modify: `apps/dashboard/src/pages/CotizacionesPage.tsx` (add the CTA + dialog + post-create refetch/open)
- Modify: `apps/dashboard/src/pages/CotizacionesPage.test.tsx` (numbering-invariant coverage)
- Modify: `apps/dashboard/src/components/layout/DashboardShell.tsx` (add a `cotizaciones` branch to the header's write-disclosure line, matching the existing `pipeline`/`deals` pattern at lines 90-96)

**Interfaces:**
- Consumes: `fetchSalesOpportunities`, `createManualSalesOpportunity`, from `commercialOperationsClient.ts`; `createCustomerQuote` from `customerQuoteClient.ts`; `newIdempotencyKey` from Task 1's `lib/idempotencyKey.ts`.
- Produces: `useExistingOpportunityPicker()` → `{items, loading, error, searchText, setSearchText, visibleItems}`. `NuevaCotizacionDialog` props: `{open: boolean; onClose: () => void; onCreated: (quote: CustomerQuote, opportunityId: string) => void}`.

- [ ] **Step 1: Write the failing opportunity-picker hook test**

```ts
// apps/dashboard/src/components/quotes/useExistingOpportunityPicker.test.ts
import { renderHook, waitFor, act } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useExistingOpportunityPicker } from "./useExistingOpportunityPicker";
import * as client from "../../api/commercialOperationsClient";

vi.mock("../../api/commercialOperationsClient");

function opportunity(overrides: Record<string, unknown> = {}) {
  return {
    sales_opportunity_id: "sales_" + "c".repeat(32),
    source_kind: "manual" as const,
    source_opportunity_id: "sales_" + "c".repeat(32),
    account_id: null,
    primary_contact_id: null,
    organization_id: null,
    primary_crm_contact_id: null,
    title: "Reactor CEAF",
    stage: "quoting" as const,
    owner_key: "op@origenlab.cl",
    version: 1,
    created_by: "op@origenlab.cl",
    updated_by: "op@origenlab.cl",
    created_at: "2026-08-30T10:00:00Z",
    updated_at: "2026-08-30T10:00:00Z",
    stage_updated_at: "2026-08-30T10:00:00Z",
    contact_display_email: null,
    account_display_domain: null,
    organization_display_name: "CEAF",
    contact_display_name: "Tatiana Rojas",
    contact_primary_email: "tatiana@ceaf.cl",
    open_task_count: 0,
    next_task_id: null,
    next_task_title: null,
    next_task_due_at: null,
    ...overrides,
  };
}

describe("useExistingOpportunityPicker", () => {
  it("fetches up to 200 durable opportunities once on mount, no stage filter", async () => {
    vi.mocked(client.fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });

    renderHook(() => useExistingOpportunityPicker());

    await waitFor(() =>
      expect(client.fetchSalesOpportunities).toHaveBeenCalledWith({ limit: 200, offset: 0 }),
    );
  });

  it("filters visibleItems client-side by title/organization/contact", async () => {
    vi.mocked(client.fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [opportunity()],
    });

    const { result } = renderHook(() => useExistingOpportunityPicker());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.visibleItems).toHaveLength(1);
    act(() => result.current.setSearchText("no-match"));
    expect(result.current.visibleItems).toHaveLength(0);
    act(() => result.current.setSearchText("ceaf"));
    expect(result.current.visibleItems).toHaveLength(1);
  });
});
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `npm test -- useExistingOpportunityPicker.test.ts`

- [ ] **Step 3: Implement it**

```ts
// apps/dashboard/src/components/quotes/useExistingOpportunityPicker.ts
import { useEffect, useMemo, useState } from "react";
import { fetchSalesOpportunities } from "../../api/commercialOperationsClient";
import type { SalesOpportunityListItem } from "../../api/commercialOperationsTypes";

const PICKER_FETCH_LIMIT = 200;

export function useExistingOpportunityPicker() {
  const [items, setItems] = useState<SalesOpportunityListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchText, setSearchText] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    void fetchSalesOpportunities({ limit: PICKER_FETCH_LIMIT, offset: 0 })
      .then((result) => {
        if (cancelled) return;
        setItems(result.items);
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : "No pudimos cargar las oportunidades.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const visibleItems = useMemo(() => {
    const search = searchText.trim().toLowerCase();
    if (!search) return items;
    return items.filter((item) =>
      [item.title, item.organization_display_name, item.contact_display_name, item.contact_primary_email]
        .filter((value): value is string => Boolean(value))
        .join(" ")
        .toLowerCase()
        .includes(search),
    );
  }, [items, searchText]);

  return { items, loading, error, searchText, setSearchText, visibleItems };
}
```

- [ ] **Step 4: Run it, confirm green**

Run: `npm test -- useExistingOpportunityPicker.test.ts`

- [ ] **Step 5: Write the failing numbering-invariant tests for `NuevaCotizacionDialog`** (the most important tests in this phase)

```tsx
// apps/dashboard/src/components/quotes/NuevaCotizacionDialog.test.tsx
import "@testing-library/jest-dom";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { NuevaCotizacionDialog } from "./NuevaCotizacionDialog";
import * as opsClient from "../../api/commercialOperationsClient";
import * as quoteClient from "../../api/customerQuoteClient";

vi.mock("../../api/commercialOperationsClient");
vi.mock("../../api/customerQuoteClient");

function mockNoExistingOpportunities() {
  vi.mocked(opsClient.fetchSalesOpportunities).mockResolvedValue({
    meta: { data_source: "postgres", read_only: true, count: 0, total_count: 0, limit: 200, offset: 0 },
    items: [],
  });
}

describe("NuevaCotizacionDialog — numbering invariants", () => {
  beforeEach(() => {
    mockNoExistingOpportunities();
  });

  it("opening the dialog allocates nothing", async () => {
    render(<NuevaCotizacionDialog open onClose={vi.fn()} onCreated={vi.fn()} />);
    await waitFor(() => expect(opsClient.fetchSalesOpportunities).toHaveBeenCalled());
    expect(opsClient.createManualSalesOpportunity).not.toHaveBeenCalled();
    expect(quoteClient.createCustomerQuote).not.toHaveBeenCalled();
  });

  it("selecting an existing opportunity allocates nothing", async () => {
    vi.mocked(opsClient.fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [{
        sales_opportunity_id: "sales_" + "c".repeat(32),
        source_kind: "manual", source_opportunity_id: "sales_" + "c".repeat(32),
        account_id: null, primary_contact_id: null, organization_id: null, primary_crm_contact_id: null,
        title: "Reactor CEAF", stage: "quoting", owner_key: "op@origenlab.cl", version: 1,
        created_by: "op@origenlab.cl", updated_by: "op@origenlab.cl",
        created_at: "2026-08-30T10:00:00Z", updated_at: "2026-08-30T10:00:00Z", stage_updated_at: "2026-08-30T10:00:00Z",
        contact_display_email: null, account_display_domain: null,
        organization_display_name: "CEAF", contact_display_name: "Tatiana Rojas", contact_primary_email: "tatiana@ceaf.cl",
        open_task_count: 0, next_task_id: null, next_task_title: null, next_task_due_at: null,
      }],
    });

    render(<NuevaCotizacionDialog open onClose={vi.fn()} onCreated={vi.fn()} />);
    await waitFor(() => screen.getByText("Reactor CEAF"));

    fireEvent.click(screen.getByRole("button", { name: /Reactor CEAF/ }));

    expect(quoteClient.createCustomerQuote).not.toHaveBeenCalled();
  });

  it("filling the manual-opportunity form without submitting allocates nothing", async () => {
    render(<NuevaCotizacionDialog open onClose={vi.fn()} onCreated={vi.fn()} />);
    await waitFor(() => expect(opsClient.fetchSalesOpportunities).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("tab", { name: "Oportunidad nueva" }));
    fireEvent.change(screen.getByLabelText("Título"), { target: { value: "Balanza analítica" } });
    fireEvent.change(screen.getByLabelText("Organización"), { target: { value: "IKA" } });

    expect(opsClient.createManualSalesOpportunity).not.toHaveBeenCalled();
    expect(quoteClient.createCustomerQuote).not.toHaveBeenCalled();
  });

  it("cancelling allocates nothing", async () => {
    const onClose = vi.fn();
    render(<NuevaCotizacionDialog open onClose={onClose} onCreated={vi.fn()} />);
    await waitFor(() => expect(opsClient.fetchSalesOpportunities).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("tab", { name: "Oportunidad nueva" }));
    fireEvent.change(screen.getByLabelText("Título"), { target: { value: "Balanza analítica" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));

    expect(onClose).toHaveBeenCalled();
    expect(opsClient.createManualSalesOpportunity).not.toHaveBeenCalled();
    expect(quoteClient.createCustomerQuote).not.toHaveBeenCalled();
  });

  it("a validation failure (empty title) never calls createCustomerQuote", async () => {
    render(<NuevaCotizacionDialog open onClose={vi.fn()} onCreated={vi.fn()} />);
    await waitFor(() => expect(opsClient.fetchSalesOpportunities).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("tab", { name: "Oportunidad nueva" }));
    expect(screen.getByRole("button", { name: /Crear/ })).toBeDisabled();
    expect(quoteClient.createCustomerQuote).not.toHaveBeenCalled();
  });

  it("a successful manual-opportunity submit calls createManualSalesOpportunity then createCustomerQuote exactly once each, with idempotency keys", async () => {
    const opportunityId = "sales_" + "d".repeat(32);
    vi.mocked(opsClient.createManualSalesOpportunity).mockResolvedValue({
      sales_opportunity_id: opportunityId,
      source_kind: "manual", source_opportunity_id: opportunityId,
      account_id: null, primary_contact_id: null, organization_id: null, primary_crm_contact_id: null,
      title: "Balanza analítica", stage: "new", owner_key: "op@origenlab.cl", version: 1,
      created_by: "op@origenlab.cl", updated_by: "op@origenlab.cl",
      created_at: "2026-09-01T10:00:00Z", updated_at: "2026-09-01T10:00:00Z",
    });
    vi.mocked(quoteClient.createCustomerQuote).mockResolvedValue({
      quote_id: "quote_" + "e".repeat(32), sales_opportunity_id: opportunityId,
      quote_number: "01184-26", document_number: "CN01184", status: "draft", version: 1, latest_revision_number: 1,
      created_by: "op@origenlab.cl", updated_by: "op@origenlab.cl",
      created_at: "2026-09-01T10:00:05Z", updated_at: "2026-09-01T10:00:05Z",
      drive_workspace: {
        provider: "google_drive", provisioning_status: "pending", folder_id: null, folder_web_url: null,
        sheet_file_id: null, sheet_web_url: null, failure_category: null, attempt_count: 1, version: 1,
        retryable: false, lease_expires_at: null, requested_at: "2026-09-01T10:00:05Z", completed_at: null,
      },
    });
    const onCreated = vi.fn();

    render(<NuevaCotizacionDialog open onClose={vi.fn()} onCreated={onCreated} />);
    await waitFor(() => expect(opsClient.fetchSalesOpportunities).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("tab", { name: "Oportunidad nueva" }));
    fireEvent.change(screen.getByLabelText("Título"), { target: { value: "Balanza analítica" } });
    fireEvent.change(screen.getByLabelText("Organización"), { target: { value: "IKA" } });
    fireEvent.click(screen.getByRole("button", { name: /Crear/ }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));

    expect(opsClient.createManualSalesOpportunity).toHaveBeenCalledTimes(1);
    expect(opsClient.createManualSalesOpportunity).toHaveBeenCalledWith(
      { title: "Balanza analítica", organization_display_name: "IKA" },
      expect.stringMatching(/^opportunity:/),
    );
    expect(quoteClient.createCustomerQuote).toHaveBeenCalledTimes(1);
    expect(quoteClient.createCustomerQuote).toHaveBeenCalledWith(opportunityId, expect.stringMatching(/^quote:/));
  });

  it("a successful existing-opportunity submit calls createCustomerQuote exactly once and never calls createManualSalesOpportunity", async () => {
    const opportunityId = "sales_" + "c".repeat(32);
    vi.mocked(opsClient.fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [{
        sales_opportunity_id: opportunityId, source_kind: "manual", source_opportunity_id: opportunityId,
        account_id: null, primary_contact_id: null, organization_id: null, primary_crm_contact_id: null,
        title: "Reactor CEAF", stage: "quoting", owner_key: "op@origenlab.cl", version: 1,
        created_by: "op@origenlab.cl", updated_by: "op@origenlab.cl",
        created_at: "2026-08-30T10:00:00Z", updated_at: "2026-08-30T10:00:00Z", stage_updated_at: "2026-08-30T10:00:00Z",
        contact_display_email: null, account_display_domain: null,
        organization_display_name: "CEAF", contact_display_name: "Tatiana Rojas", contact_primary_email: "tatiana@ceaf.cl",
        open_task_count: 0, next_task_id: null, next_task_title: null, next_task_due_at: null,
      }],
    });
    vi.mocked(quoteClient.createCustomerQuote).mockResolvedValue({
      quote_id: "quote_" + "e".repeat(32), sales_opportunity_id: opportunityId,
      quote_number: "01185-26", document_number: "CN01185", status: "draft", version: 1, latest_revision_number: 1,
      created_by: "op@origenlab.cl", updated_by: "op@origenlab.cl",
      created_at: "2026-09-01T10:00:05Z", updated_at: "2026-09-01T10:00:05Z",
      drive_workspace: {
        provider: "google_drive", provisioning_status: "pending", folder_id: null, folder_web_url: null,
        sheet_file_id: null, sheet_web_url: null, failure_category: null, attempt_count: 1, version: 1,
        retryable: false, lease_expires_at: null, requested_at: "2026-09-01T10:00:05Z", completed_at: null,
      },
    });
    const onCreated = vi.fn();

    render(<NuevaCotizacionDialog open onClose={vi.fn()} onCreated={onCreated} />);
    await waitFor(() => screen.getByText("Reactor CEAF"));

    fireEvent.click(screen.getByRole("button", { name: /Reactor CEAF/ }));
    fireEvent.click(screen.getByRole("button", { name: /Crear cotización/ }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
    expect(opsClient.createManualSalesOpportunity).not.toHaveBeenCalled();
    expect(quoteClient.createCustomerQuote).toHaveBeenCalledWith(opportunityId, expect.stringMatching(/^quote:/));
  });
});
```

- [ ] **Step 6: Run it, confirm it fails**

Run: `npm test -- NuevaCotizacionDialog.test.tsx` — Expected: FAIL (module doesn't exist).

- [ ] **Step 7: Implement `NuevaCotizacionDialog.tsx`**

Hand-rolled modal (fixed overlay + centered panel, no Radix), two tabs (`role="tab"`, simple local `activeTab` state — no tab library needed for two panes): "Oportunidad existente" (default) and "Oportunidad nueva". State:

```ts
type ExistingSelection = { kind: "existing"; opportunityId: string; label: string };
type ManualForm = { title: string; organizationName: string; contactName: string; contactEmail: string };

const [tab, setTab] = useState<"existing" | "manual">("existing");
const [selected, setSelected] = useState<ExistingSelection | null>(null);
const [manual, setManual] = useState<ManualForm>({ title: "", organizationName: "", contactName: "", contactEmail: "" });
const [createdOpportunityId, setCreatedOpportunityId] = useState<string | null>(null); // crash-safety: set once manual-create succeeds, so a retry never re-runs it
const [opportunityKey] = useState(() => newIdempotencyKey("opportunity")); // stable for the dialog's lifetime — regenerated only when the dialog is reopened (key the state on `open` via a reset effect)
const [quoteKey] = useState(() => newIdempotencyKey("quote"));
const [submitting, setSubmitting] = useState(false);
const [submitError, setSubmitError] = useState<string | null>(null);
```

Validation (`canSubmit`): existing tab → `selected !== null`; manual tab → `manual.title.trim() !== "" && manual.organizationName.trim() !== ""` (organization required before contact fields are enabled, mirroring the backend's `has_organization` gate at `commercial_operations_service.py:491-497` — contact fields stay disabled in the form until organization is non-empty, so the client can never even construct a request the server would reject for that reason).

Submit handler (the invariant-critical logic — every branch either makes exactly the calls the test above asserts, or makes none):

```ts
async function submit() {
  if (!canSubmit || submitting) return;
  setSubmitting(true);
  setSubmitError(null);

  try {
    let opportunityId = tab === "existing" ? selected!.opportunityId : createdOpportunityId;

    if (tab === "manual" && !opportunityId) {
      const created = await createManualSalesOpportunity(
        {
          title: manual.title.trim(),
          organization_display_name: manual.organizationName.trim(),
          ...(manual.contactName.trim() ? { contact_display_name: manual.contactName.trim() } : {}),
          ...(manual.contactEmail.trim() ? { contact_email: manual.contactEmail.trim() } : {}),
        },
        opportunityKey,
      );
      opportunityId = created.sales_opportunity_id;
      setCreatedOpportunityId(opportunityId); // durably created even if the quote step below now fails — never re-create on retry
    }

    const quote = await createCustomerQuote(opportunityId!, quoteKey);
    onCreated(quote, opportunityId!);
  } catch (reason: unknown) {
    setSubmitError(
      reason instanceof OperatorApiError && reason.status === 503 && reason.message.includes("quote_numbering_not_configured")
        ? NUMBERING_NOT_CONFIGURED_MESSAGE
        : "No pudimos crear la cotización. Reintenta.",
    );
  } finally {
    setSubmitting(false);
  }
}
```

`handleClose` resets **all** of the above state (including regenerating both idempotency keys) — a dialog reopened after a cancel must start with fresh keys, since nothing was allocated. Cancel button and backdrop/Escape all call the same `handleClose` → `onClose()`, with no network calls.

- [ ] **Step 8: Run it, confirm green**

Run: `npm test -- NuevaCotizacionDialog.test.tsx`

- [ ] **Step 9: Wire the CTA into `CotizacionesPage.tsx`**

```tsx
const [dialogOpen, setDialogOpen] = useState(false);
// ...
<V2PageHeader
  title="Cotizaciones"
  subtitle="Cola global de cotizaciones durables y su carpeta en Drive."
  actions={
    <>
      <button type="button" onClick={() => setDialogOpen(true)} className="rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700">
        Nueva Cotización
      </button>
      <button type="button" onClick={queue.refetch} disabled={queue.loading} className="rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50">
        {queue.loading ? "Actualizando…" : "Actualizar"}
      </button>
    </>
  }
/>
// ...
<NuevaCotizacionDialog
  open={dialogOpen}
  onClose={() => setDialogOpen(false)}
  onCreated={(quote) => {
    setDialogOpen(false);
    void queue.refetch();
    setOpenItem({ quote, sales_opportunity_stage: "new", sales_opportunity_owner_key: "", organization_display_name: null, contact_display_name: null, contact_primary_email: null, next_task_title: null, next_task_due_at: null });
  }}
/>
```

(the synthetic `openItem` passed to the just-created quote is a display-only placeholder until `QuoteDetailDrawer`'s own `fetchCustomerQuote` refresh completes — its `sales_opportunity_stage`/owner fields are cosmetically wrong for one render frame; if that's judged not acceptable during review, the simpler alternative is: don't auto-open the detail drawer on create, just refetch the queue and let the operator click the new row — note this as an open design call for the reviewer, not a blocker).

- [ ] **Step 10: Extend `CotizacionesPage.test.tsx` with the end-to-end creation flow and the header write-disclosure line**

```ts
  it("creating a quote from Nueva Cotización refreshes the queue", async () => {
    // ...mount CotizacionesPage, open dialog, complete existing-opportunity flow,
    // assert fetchCustomerQuotesGlobal was called again after onCreated fires.
  });
```

- [ ] **Step 11: Add the `cotizaciones` branch to `DashboardShell.tsx`'s header disclosure line**

```tsx
              {section === "tenders"
                ? "No envía correos ni modifica datos comerciales"
                : section === "pipeline"
                  ? "No envía correos · los cambios de etapa quedan registrados en el CRM"
                  : section === "cotizaciones"
                    ? "No envía correos · crear una cotización queda registrado en el CRM"
                    : section === "deals"
                      ? "No envía correos · promover a Ventas es la única escritura"
                      : "No envía correos ni modifica datos"}
```

- [ ] **Step 12: Run the full dashboard suite**

Run: `npm run validate`

- [ ] **Step 13: Commit**

```bash
git add apps/dashboard/src/components/quotes/useExistingOpportunityPicker.ts \
        apps/dashboard/src/components/quotes/useExistingOpportunityPicker.test.ts \
        apps/dashboard/src/components/quotes/NuevaCotizacionDialog.tsx \
        apps/dashboard/src/components/quotes/NuevaCotizacionDialog.test.tsx \
        apps/dashboard/src/pages/CotizacionesPage.tsx \
        apps/dashboard/src/pages/CotizacionesPage.test.tsx \
        apps/dashboard/src/components/layout/DashboardShell.tsx
git commit -m "feat(dashboard): add Nueva Cotización — existing-opportunity and manual-intake flows"
```

---

## Task 6: Final cross-cutting checks and full-suite validation

**Files:**
- Test: `apps/dashboard/src/pages/DashboardApp.test.tsx` (one more full-flow smoke test)
- No source changes expected — this task is verification. If it finds a real gap, the fix lands here with its own test, scoped to that gap only.

- [ ] **Step 1: Add one DashboardApp-level smoke test tying the whole phase together**

```ts
  it("end to end: landing on Cotizaciones, opening a quote, and Nueva Cotización's dialog never allocates on open", async () => {
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));

    expect(screen.getByRole("heading", { level: 1, name: "Cotizaciones" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Nueva Cotización" }));
    await waitFor(() => screen.getByRole("dialog"));
    expect(opsClient... /* createManualSalesOpportunity not called */);

    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
```

(Requires extending `DashboardApp.test.tsx`'s existing `vi.mock("../api/commercialOperationsClient", ...)` block to also stub `createManualSalesOpportunity` and the `../api/customerQuoteClient` mock to stub `createCustomerQuote`/`fetchCustomerQuote`/`retryCustomerQuoteDriveWorkspace` — reuse the same mock pattern already established for `fetchSalesOpportunities`.)

- [ ] **Step 2: Grep-based constraint checks (run once, no test file needed unless a violation is found)**

```bash
# No trusted-operator header/token introduced in browser code:
grep -rn "X-OriginLab-Operator" apps/dashboard/src | grep -v ".test."
# Expected: no matches.

# No invented quote lifecycle states in new files:
grep -rn "approved\|\"sent\"\|adjustments\|superseded\|V2\b\|V3\b" apps/dashboard/src/components/quotes apps/dashboard/src/pages/CotizacionesPage.tsx
# Expected: no matches (or only in comments explicitly citing this constraint).
```

- [ ] **Step 3: Run every affected suite**

```bash
cd apps/api && ./scripts/sync_test_env.sh && ./scripts/validate.sh
cd apps/dashboard && npm run validate
cd apps/dashboard-proxy && npm run validate   # confirms the untouched allowlist still passes its own tests
```

Expected: all green. `apps/api`'s Postgres-integration tests remain skipped unless `ORIGENLAB_TEST_POSTGRES_URL` is set to a disposable local Postgres — if the user wants Task 0's `document_number` change (and the pre-existing quote-creation path) proven against a real Postgres before sign-off, that's a separate, explicit follow-up using the `ZZ`/`90000` numbering config from Global Constraints, not silently folded into this task.

- [ ] **Step 4: Commit** (only if Step 1-2 required source changes; otherwise this task produces only the one new smoke test)

```bash
git add apps/dashboard/src/pages/DashboardApp.test.tsx
git commit -m "test(dashboard): add end-to-end Cotizaciones + Nueva Cotización smoke coverage"
```

---

## Test matrix (spec → test, cross-reference)

| Spec requirement | Covered by |
|---|---|
| `/` resolves to Cotizaciones | Task 2 Step 1 (`dashboardNav.test.ts`), Task 3 Step 14 |
| New IA/order is correct | Task 2 Steps 1, 5 |
| Retired sections absent from primary nav | Task 2 Steps 1, 9 |
| Global quote list uses the durable endpoint | Task 3 Step 5 (`useCustomerQuotesGlobal.test.ts`) |
| Filters operate on real returned fields | Task 3 Step 1 (`customerQuoteQueueFilters.test.ts`) |
| Quote workspace renders Drive states correctly | Task 4 Step 1 |
| Only server-returned safe Drive URLs become links | Task 4 Step 1 ("never renders a raw Drive id...") — relies on `safeDriveUrl` already proven in `customerQuoteParse.test.ts` |
| Retry uses the existing retry command | Task 4 Step 1 |
| Nueva Cotización supports existing-opportunity selection | Task 5 Step 5 |
| Manual opportunity flow uses the Phase 1 durable command | Task 5 Step 5 |
| Opening/cancelling the dialog allocates no quote | Task 5 Step 5 |
| Manual opportunity creation allocates no quote | Task 5 Step 5 |
| Validation failure allocates no quote | Task 5 Step 5 |
| Successful local quote creation calls `createCustomerQuote` exactly once with idempotency | Task 5 Step 5 |
| No trusted-operator header/token in browser code | Task 6 Step 2 |
| No invented quote lifecycle states | Task 6 Step 2 |

## Concrete blockers found (both resolved within this plan's scope)

1. `document_number` durably stored, never exposed via `CustomerQuoteResponse` — resolved by Task 0 (additive schema field, no migration).
2. No durable organization/contact search or creation exists — **not resolved**, by design: Task 5's manual-intake form uses only the free-text `organization_display_name`/`contact_display_name`/`contact_email` fields the Phase 1 command already accepts. Building durable org/contact search is out of this phase's scope per the spec's own backend-scope constraint; flagged here so it isn't silently assumed to exist later.

## Design decisions the reviewer should confirm

- "Ver en Ventas" in the quote detail drawer navigates to the Ventas section generally, not to that specific opportunity's own drawer (no opportunity-id deep link exists in the hash router yet). (Task 4, Step 3.)
- Manual-opportunity creation and quote creation are one user-facing action (one "Crear" button, two sequential durable calls) rather than two separate dialog steps — chosen because it's the only way to guarantee "creating a manual opportunity must not allocate a quote number" *and* "only the final successful customer-quote creation command may consume a serial" hold simultaneously without a second confirmation click. (Task 5, Step 7.)
- Whether a successful create should auto-open the new quote's detail drawer with a cosmetically-incomplete placeholder for one render frame, or just refetch and let the operator click the row. (Task 5, Step 9 — flagged inline as an open call, not decided in this plan.)

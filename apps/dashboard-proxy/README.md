# Dashboard API proxy (Cloudflare Worker)

Same-origin, method+path allowlisted proxy for production dashboard builds. The browser calls `https://dashboard.origenlab.cl/api/*`; the Worker strips `/api`, checks an allowlist, and forwards to `apps/api` with upstream auth headers from Worker secrets. GET is allowlisted for the named V2 reads and `/health`; POST for the V2 marketing and CRM-authoring commands and the auth routes — this Worker is the trust boundary for both, not a pure read-only pass-through. From 2026-10-03 (built, not deployed: the Worker in Cloudflare still carries the previous allowlist and still rebuilds the operator header from Access until `docs/OPERATIONS.md` §1.2 step 9 runs; the Access application on the dashboard hostname is not yet removed, step 10; `docs/STATUS.md` §2.7.41) it carries no identity of its own: the browser's identity is the dashboard session cookie, resolved by the API, and Cloudflare Access is to be retired from the dashboard hostname at Phase B (`docs/MIGRATION.md` decision 5). Only `api.origenlab.cl` keeps an Access application, which this Worker reaches with a service token.

**No browser token.** `ORIGENLAB_API_AUTH_TOKEN` is a Worker secret only — never `VITE_*`.

## Upstream auth (Worker → API)

The Worker adds **two independent layers** when forwarding to a production upstream like `https://api.origenlab.cl`:

| Layer | Worker secret / header | Required when |
|-------|------------------------|---------------|
| **Cloudflare Access (edge)** | `CF-Access-Client-Id` + `CF-Access-Client-Secret` from `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` | Upstream hostname is behind Cloudflare Access (production `api.origenlab.cl` today) |
| **API origin token** | `X-OriginLab-API-Key` from `ORIGENLAB_API_AUTH_TOKEN` | Always (production `ORIGENLAB_ENV=production`) |

Plain `curl https://api.origenlab.cl/health` returns **302** to Access login — the Worker must present a **Cloudflare Access service token** to reach the origin. The API token alone is not enough for that upstream.

For **unprotected** upstreams (local dev, internal URL, FastAPI Cloud without Access), `CF_ACCESS_*` secrets are **not** required — only `ORIGENLAB_API_AUTH_TOKEN` when the API enforces origin auth.

## Routes (allowlist)

**GET** (dashboard reads):

| Upstream path | Purpose |
|---------------|---------|
| `/health` | Public health |
| `/v2/*` (named paths only — see `src/allowlist.ts`) | V2 durable reads |
| `/auth/google/login`, `/auth/google/callback`, `/auth/session` | Dashboard Google Workspace sign-in (see *Sign-in exceptions* below) |
| `/auth/profiles` | The signed-in shared Workspace account's operator profiles (id, name, role label) |
| `/v2/workspace/mail-quote-numbers` | Quote numbers already seen in the captured Gmail (number, first sighting, message count; the 500 newest), for the Resumen's quote-number box. No address, subject or institution; GET only, operator session |
| `/v2/workspace/mail-rules/preview` | Email → cases dry run: each planned action with its rule (R1–R8), reasons, case, institution and quote number, and the actions already applied. **Admin only** upstream (403 for sales and viewer); writes nothing |
| `/v2/catalog/*` (7 exact paths — see *Catalog 1a* below) | Catalog reads |

**Refused on purpose** (GET: 403 `path_not_allowed`; POST: 405 `method_not_allowed`; never forwarded): every V1 surface — `/operator/*`, `/cases/warm`, `/opportunities/*`, `/operations/*`, `/contacts/*` and `/mirror/*`. Upstream they are gated only by the shared API key — no operator identity, no role, no redaction — and the current dashboard calls none of them, so V2 `/v2/*` is the only browser surface for CRM, contacts and evidence. The V1 paths and the V1 POST commands (including the tender annex upload) were removed from the allowlist in the source on 2026-10-03 (built, not deployed; see the note at the top). `/v2/cockpit/*` and every `/v2/workspace/*` path other than the named reads below are not listed either. The W10 unsubscribe tooling that carries message bodies (`POST /v2/unsubscribe/preview`, `POST /v2/commands/apply-unsubscribe-replies`) is API-only and never listed. See `docs/OPERATIONS.md`.

**POST** (trusted operator identity resolved upstream from the session cookie, `Idempotency-Key`, optimistic concurrency; each path is exact, no wildcard route):

| Upstream path | Purpose |
|---------------|---------|
| `/auth/logout` | Clears the dashboard session cookie and records the logout in `platform.auth_event`; writes no commercial state. Requires an allowed `Origin` and no cross-site `Sec-Fetch-Site` |
| `/auth/profile/select`, `/auth/profile/clear` | Choose an operator profile with its PIN (verified by the API, never here), or return to the profile screen. Allowed `Origin`, no cross-site `Sec-Fetch-Site`, `Content-Type: application/json`, body ≤ 1 KiB. The API's local-only `/auth/dev/*` is never reachable |
| `/v2/commands/{create-campaign-draft,save-campaign-draft,freeze-campaign-audience,set-campaign-planning,resolve-unsubscribe-review,dismiss-unsubscribe-review,block-campaign,unblock-campaign}` | CRM Marketing — see *Marketing commands* below. Nothing here approves, schedules or sends; a block only refuses |
| `/v2/commands/apply-mail-rules`, `/v2/commands/undo-mail-rule-action` | Email → cases: apply the previewed automatic actions — the browser sends `(evidence_id, rule_id)` pairs, at most ten per request, and upstream re-plans and refuses any pair it no longer yields, or undo one applied action with a note. **Admin only** upstream; mounted only behind `ORIGENLAB_V2_COMMANDS_ENABLED`. Same Origin / JSON / `Idempotency-Key` guard as the marketing commands, body ≤ 128 KiB. The other case commands the rules call stay unlisted |
| `/v2/commands/advance-case-stage`, `/v2/commands/record-case-won`, `/v2/commands/resolve-current-revision`, `/v2/commands/record-case-quotation` | The case drawer's «Cambiar etapa», «Marcar ganada», «Elegir revisión vigente» and «Registrar cotización» / «Nueva revisión» (`CASE_COMMAND_POST_PATHS`). Upstream: active `sales`/`admin` operator, `Idempotency-Key`, the case version shown, a note; mounted only behind `ORIGENLAB_V2_COMMANDS_ENABLED`. Same Origin / JSON / key guard as the marketing commands, body ≤ 16 KiB. The other five case commands (`open-commercial-case`, `link-case-evidence`, `add-case-organization`, `set-case-organization-role`, `record-case-interest`) stay refused |
| `/v2/commands/<name>` for the 29 CRM-authoring commands (person, organization — `confirm-organization-record` included —, contact point, classification, product line, supplier-candidate resolution, notes) | CRM authoring; exact paths in `CRM_AUTHORING_COMMAND_POST_PATHS` (`src/allowlist.ts`). Evidence-bound and case commands are not in this list. Mounted upstream only behind `ORIGENLAB_V2_CRM_AUTHORING_ENABLED` (default off) |
| `/v2/commands/{create-product,update-product,confirm-product-content,record-supplier-cost,set-supplier-terms,set-cost-parameter,record-fx-rate,update-product-image,review-document-line}` | Catalog commands: 9 exact paths, JSON, body ≤ 64 KiB — see *Catalog 1a* below |
| `/v2/commands/add-product-image` | Catalog image upload: the one multipart POST, body ≤ 8 MiB + 64 KiB — see *Catalog 1a* below |

All other POST requests, and all `PUT`, `PATCH`, and `DELETE` requests, return **405**.

## CRM card reads

Five exact GET paths (`src/allowlist.ts`), nothing under or beside them:

| Method | Upstream path | Upstream behaviour |
|---|---|---|
| GET | `/v2/workspace/providers` | the six catalogue brands as the supplier directory, then the machine-detected candidates (hints only, never promoted) |
| GET | `/v2/workspace/equipment-interests` | observed equipment interests per line, institution and destination; CRM people apart from address-only evidence |
| GET | `/v2/workspace/person-suggestions` | people the quote emails name and the CRM does not hold yet (name, address, institution, quotes, last date); addresses masked for `viewer`; creates nothing |
| GET | `/v2/workspace/opportunities/<uuid>/notes` | the notes on one case, for the case drawer's «Registrar seguimiento» (same shape as person/organization notes); writing one is `add-note` |
| GET | `/v2/workspace/opportunities/<uuid>/mail-documents` | the Gmail messages linked to one case and the documents each carries (file name, SHA-256, capture's quote-number tokens, whether already a quote revision), for «Registrar cotización»; recording one is `record-case-quotation` |

Same guarantees as every V2 read: the session cookie is the only cookie forwarded, the
browser-sent operator header is dropped and nothing replaces it (identity is the session cookie), addresses are
masked upstream for `viewer`, and masked destinations carry only an opaque keyed `address_ref`
(an HMAC scoped to the API, never a plain hash of the address). Any other method is **405**;
a neighbouring path is **403** `path_not_allowed`.

## Marketing commands

Exact paths only (`src/allowlist.ts`; UUIDs lower-case):

| Method | Upstream path | Upstream behaviour |
|---|---|---|
| GET | `/v2/workspace/marketing` | campaigns (with planning, origin, real send batches), contact-control counts, `authoring.{drafts_enabled,freeze_enabled,planning_enabled}` |
| GET | `/v2/workspace/marketing/taxonomy` | the six brands, six families, models and verified images |
| GET | `/v2/workspace/marketing/audience` | people and institutions with evidenced interest; eligibility separate |
| GET | `/v2/workspace/marketing/campaigns/<uuid>` | one campaign's content and freeze facts |
| GET | `/v2/workspace/marketing/campaigns/<uuid>/freeze-preview` | the snapshot a freeze would write, every reason, what stops it; writes nothing |
| GET | `/v2/workspace/marketing/campaigns/<uuid>/recipients` | a frozen campaign's recipient snapshot |
| GET | `/v2/workspace/marketing/campaigns/<uuid>/archive` | the frozen (sent) content, only when its fingerprint recomputes; real send batches; «not archived» otherwise |
| GET | `/v2/workspace/marketing/campaigns/<uuid>/history/recipients` | one page of the recorded recipients under the same predicate as the card total clicked (`total`, `reason`, `identity`, `q`, `page`, `page_size` ≤ 100); a `viewer` searches names only and reads masked addresses |
| GET | `/v2/workspace/marketing/campaigns/<uuid>/history/replies` | replies and «BAJA» stored with lineage, «BAJA» held for review, counts by address; «Respuestas no sincronizadas desde Gmail» when none is stored; Gmail never called |
| GET | `/v2/workspace/marketing/campaigns/<uuid>/history/audit` | the campaign row, its import provenance, its domain events, whether the database enforces archived immutability |
| GET | `/v2/workspace/marketing/suppressions` | W10 «BAJA» suppressions and the «BAJA» replies held for review (addresses masked for `viewer`, never a message body), frozen recipients refused by them today, and «Gmail replies are not synchronized automatically» |
| GET | `/v2/workspace/marketing/campaign-blocks` | campaign safety blocks: every active one (the September wave-2 incident hold included), the latest lifted, each target's `block_version`, `may_decide`; reason and operator withheld from `viewer` upstream |
| POST | `/v2/commands/create-campaign-draft` | new `draft` row; event `campaign.draft_created` |
| POST | `/v2/commands/save-campaign-draft` | compare-and-set on `expected_version`; event `campaign.draft_content_saved` per change |
| POST | `/v2/commands/freeze-campaign-audience` | `confirmed: true`, `expected_version`, `expected_preview_sha256`; write-once snapshot in `outbound.campaign` + `outbound.campaign_recipient`; event `campaign.audience_frozen`; refused `audience_changed` if the audience moved since the preview |
| POST | `/v2/commands/set-campaign-planning` | `expected_planning_version`; set, change or clear an unsent campaign's internal planned day; events `campaign.planning_set` / `campaign.planning_cleared`; body limit 4 KB. Schedules nothing |
| POST | `/v2/commands/resolve-unsubscribe-review` | confirm one «BAJA» held for review (or one an admin dismissed) as the permanent unsubscribe; a note; body limit 8 KB |
| POST | `/v2/commands/dismiss-unsubscribe-review` | **admin only**: dismiss one *pending* hold as a false positive; `expected_review_sha256` and an explanation; never a confirmed unsubscribe; body limit 8 KB |
| POST | `/v2/commands/block-campaign` | **admin only** upstream; `scope` (`campaign` / `all_campaigns`), `expected_block_version`, mandatory `reason`; one `outbound.campaign_block` row; event `campaign_block.placed`; body limit 16 KB. Refuses freeze, approval, dry run, reservation and dispatch while active; sends, enqueues and rewrites nothing; never expires |
| POST | `/v2/commands/unblock-campaign` | **admin only** upstream; `block_id`, `expected_version`, mandatory `reason`; event `campaign_block.lifted`; body limit 16 KB. Lifting starts nothing |

**Roles.** Reads: any active operator; contact addresses are masked upstream for `viewer`
(`contact_redaction.py`). Commands: an active `sales` or `admin` operator only (`admin` alone to dismiss a «BAJA» review and for the two block commands), resolved
upstream from the verified identity (the dashboard session cookie; the browser-sent operator
header is always dropped and nothing replaces it). Nothing in a body names the actor.

**Session and CSRF.** The session cookie is `__Host-origenlab_session` (HttpOnly, Secure,
SameSite=Lax); only the two sign-in cookies ever reach upstream. For the eight commands the
Worker also refuses, before forwarding: a missing or unlisted `Origin` (403
`origin_not_allowed`), `Sec-Fetch-Site: cross-site` (403 `cross_site_request`), any
`Content-Type` other than `application/json` (415 — a cross-site form cannot send one without
a preflight, which is answered only for listed origins), a body over 3.5 MB (4 KB for `set-campaign-planning`, 16 KB for the two block commands) declared or actual
(413). CORS advertises `POST` on every listed POST path and `Idempotency-Key` on the marketing and catalog command paths.

**Idempotency.** `Idempotency-Key` is required and must match `^[A-Za-z0-9._:-]{8,128}$` (400
`idempotency_key_required`); it is forwarded byte-for-byte. Upstream the key is one
`platform.command_receipt` per operator: a replay returns the first answer with
`replayed: true`, the same key with a different body is refused.

**Audit.** Every command writes exactly one `crm.domain_event` per change in the same
transaction as the change (`campaign.draft_created`, `campaign.draft_content_saved`,
`campaign.audience_frozen`, with operator, receipt, versions and fingerprints). The freeze
event records criteria, policy version, preview/content/audience fingerprints, counts, the
review decisions and the send blockers.

**Upstream switches.** The commands exist only where the API runs with
`ORIGENLAB_V2_CAMPAIGN_DRAFTS_ENABLED` / `ORIGENLAB_V2_AUDIENCE_FREEZE_ENABLED`; elsewhere they
are 404 upstream. **Sending is blocked**: no send, approve, activate or recontact-override
command exists, and BAJA/unsubscribe processing is unsupported until an inbound-reply
processor and a durable suppression ledger are proven.

## Catalog 1a

Seven exact GET paths (`src/allowlist.ts`; UUIDs lower-case, query strings pass through unchanged):

| Method | Upstream path | Upstream behaviour |
|---|---|---|
| GET | `/v2/catalog/products` | the product list |
| GET | `/v2/catalog/products/<uuid>` | one product |
| GET | `/v2/catalog/price-history` | price history (`model_key`, `limit`) |
| GET | `/v2/catalog/suppliers/<uuid>/terms` | one supplier's terms |
| GET | `/v2/catalog/fx` | FX rates |
| GET | `/v2/catalog/parameters` | cost parameters |
| GET | `/v2/catalog/images/<uuid>/url` | JSON `{url, expires_in}`: a short-lived signed URL. The Worker never streams image bytes |

Ten exact POST paths, all under `/v2/commands/`, each behind `catalogCommandRefusal` (`src/index.ts`):
an allowed `Origin` (403 `origin_not_allowed`), no cross-site `Sec-Fetch-Site` (403
`cross_site_request`), the right `Content-Type` (415), a well-formed `Idempotency-Key` (400) and a
body within the limit, declared or actual (413). CORS advertises `POST` and `Idempotency-Key` on
these paths. The upload's multipart type needs no preflight, so its CSRF protection rests on the
required `Idempotency-Key` (a custom header, which forces one) plus the Origin and Sec-Fetch-Site checks.

- Nine JSON commands (`create-product`, `update-product`, `confirm-product-content`,
  `record-supplier-cost`, `set-supplier-terms`, `set-cost-parameter`, `record-fx-rate`,
  `update-product-image`, `review-document-line`): `application/json`, body ≤ 64 KiB
  (`CATALOG_COMMAND_MAX_BYTES`).
- One multipart command, `add-product-image`: `multipart/form-data; boundary=...` (a type without
  a boundary is 415), body ≤ 8 MiB + 64 KiB (`CATALOG_UPLOAD_MAX_BYTES`). The API caps the file at
  8 MiB itself. JSON to this path and multipart to a JSON command are both 415.

## Response hardening

The Worker is deliberately stricter than a generic pass-through proxy:

- Upstream **3xx** responses are not forwarded to the browser. They become **502** JSON: `{ "error": { "code": "upstream_redirect_blocked" } }`.
- Upstream `Location`, `Set-Cookie`, `Set-Cookie2`, and upstream CORS headers are stripped before the dashboard sees the response.
- Allowed dashboard origins receive credentialed CORS headers and `Access-Control-Expose-Headers: X-Request-ID`.
- Responses include `X-OriginLab-Proxy: dashboard-proxy`; forwarded upstream responses also include `X-OriginLab-Upstream-Status`.

This keeps Cloudflare Access redirects/cookies (from the API hostname's Access application) and upstream CORS policy from leaking through `/api/*`.

### Sign-in exceptions

Google Workspace sign-in ([`apps/api/docs/PRODUCTION_AUTH.md`](../api/docs/PRODUCTION_AUTH.md#google-workspace-login-dashboard-v2-boundary))
needs cookies and redirects, so `src/auth.ts` makes three exceptions and no others:

- **Cookies upstream:** on `/auth/*` and `/v2/*` only, and only `__Host-origenlab_session` and
  `__Host-origenlab_signin`. Every other browser cookie — Access's `CF_Authorization`
  included — is dropped. No other path receives a `Cookie` header.
- **`Set-Cookie` back:** from `/auth/*` only, and only those two names.
- **Redirects:** `GET /auth/google/login` may redirect to
  `https://accounts.google.com/o/oauth2/v2/auth` only; `GET /auth/google/callback` may redirect
  to this dashboard's own root only, optionally with `?login_error=<code>`. Every other
  upstream 3xx is still a 502.

The operator header is deleted on every path and never rebuilt: the Worker no longer reads the
Cloudflare Access identity, so identity reaches the API only as the `__Host-` session cookie
(source changed 2026-10-03; built, not deployed — `docs/STATUS.md` §2.7.41).

## Environment

| Name | Where | Required |
|------|-------|----------|
| `ORIGENLAB_API_UPSTREAM` | `wrangler.toml` `[vars]` | yes — production: `https://api.origenlab.cl` |
| `ORIGENLAB_API_AUTH_TOKEN` | Worker secret | **always** (same value as Render API) |
| `CF_ACCESS_CLIENT_ID` | Worker secret | **yes** when upstream is behind Cloudflare Access (production default) |
| `CF_ACCESS_CLIENT_SECRET` | Worker secret | **yes** when upstream is behind Cloudflare Access (production default) |

Both CF Access secrets must be set together. The Worker never sends a lone `CF-Access-Client-Id` without the matching secret.

### Deploy (production — `api.origenlab.cl` upstream)

```bash
cd apps/dashboard-proxy
npm ci
npx wrangler secret put ORIGENLAB_API_AUTH_TOKEN
npx wrangler secret put CF_ACCESS_CLIENT_ID
npx wrangler secret put CF_ACCESS_CLIENT_SECRET
npx wrangler deploy
```

Route in Cloudflare: `dashboard.origenlab.cl/api*` → this Worker (see `wrangler.toml` comment).

## Failure modes (browser → `/api/*`)

| Symptom | Likely cause |
|---------|----------------|
| **302 / 403** (HTML Access page) | Worker missing/wrong `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` for Access-protected upstream |
| **502** JSON (`upstream_redirect_blocked`) | Upstream returned a redirect (often Cloudflare Access login); fix Worker Access service-token secrets |
| **401** JSON (`unauthorized`) | Worker missing/wrong `ORIGENLAB_API_AUTH_TOKEN` (request passed Access but failed API origin auth) |
| **403** `path_not_allowed` | Route not on the allowlist (every V1 path is refused) |
| **405** | Mutating HTTP method |

Secrets are never logged or returned in Worker responses.

## Dashboard build

```bash
VITE_ORIGENLAB_API_BASE_URL=https://dashboard.origenlab.cl/api npm run build
```

Browser fetches stay same-origin (`credentials: include` for the dashboard session cookie). The Worker adds upstream auth — not the browser.

## Tests

```bash
npm ci
npm run validate
```

## Related

- [`../dashboard/docs/PRODUCTION_API_AUTH.md`](../dashboard/docs/PRODUCTION_API_AUTH.md)
- [`../../docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md`](../../docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md)

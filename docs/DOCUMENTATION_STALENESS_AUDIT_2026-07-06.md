# Documentation staleness audit — 2026-07-06

**Type:** Docs-only refresh after dashboard Worker proxy / CORS / auth hardening.  
**Branch:** `docs/production-read-path-staleness-audit`  
**Safety:** No runtime code, no env files, no secrets, no Gmail/SQLite/Postgres/outreach/send/mirror-sync mutations.

## Production read path (reference)

```text
dashboard.origenlab.cl
  → Cloudflare Access (operator SSO)
  → Cloudflare Worker dashboard.origenlab.cl/api*
  → api.origenlab.cl (Access service token + ORIGENLAB_API_AUTH_TOKEN)
  → FastAPI apps/api
  → Postgres mirror
```

## Documents checked

| Document | Result |
|----------|--------|
| `docs/SECURITY_AUDIT_RENDER_DASHBOARD.md` | **Stale** — 2026-05-18 “needs changes” / “no authentication” framing |
| `docs/dashboard/PRODUCTION_DASHBOARD_SMOKE_CHECKLIST.md` | **Stale** — origin API smoke only; missing Worker proxy smoke |
| `docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md` | **Partial** — Access live; missing 2026-07 Worker hardening summary |
| `apps/dashboard/docs/PRODUCTION_API_AUTH.md` | **Current** (post #342/#351) — no change |
| `apps/api/docs/PRODUCTION_AUTH.md` | **Current** (post #351) — no change |
| `apps/dashboard-proxy/README.md` | **Current** (post #347–#349) — no change |
| `apps/dashboard/README.md` | **Stale** — “mirror not used by Today” |
| `apps/email-pipeline/docs/PHASE1_CLOUD_READ_PATH.md` | **Current** — `VITE_ORIGENLAB_API_BASE_URL=https://dashboard.origenlab.cl/api` |
| `render.yaml` | **Current** — dashboard build uses `dashboard.origenlab.cl/api` |

## Stale items found

| Phrase / claim | Where | Action |
|----------------|-------|--------|
| “Needs changes before production” | `SECURITY_AUDIT_RENDER_DASHBOARD.md` | Marked historical (2026-05-18) |
| “API has no authentication” | `SECURITY_AUDIT_RENDER_DASHBOARD.md` risk R1 | Replaced in current status; historical table retained |
| “planned Cloudflare Access” | `SECURITY_AUDIT_RENDER_DASHBOARD.md` scope | Removed from current scope; Access live |
| “/mirror/* not used by Today” | `SECURITY_AUDIT_RENDER_DASHBOARD.md`, `apps/dashboard/README.md` | Updated — dashboard uses mirror for Catálogo/Negocio/audit |
| “Production static dashboard calls API directly” | Not found verbatim (already fixed in #351) | N/A |
| `VITE_ORIGENLAB_API_BASE_URL=https://api.origenlab.cl` as dashboard prod base | Not found in tracked docs | N/A |
| “server-side proxy follow-up” / auth gap | Not found (PRODUCTION_API_AUTH.md current) | N/A |
| Missing Worker proxy smoke | `PRODUCTION_DASHBOARD_SMOKE_CHECKLIST.md` | Added section B with 8 routes |
| Missing Worker strip behavior in Access doc | `CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md` | Added 2026-07 production update |

## Docs updated (this PR)

- `docs/SECURITY_AUDIT_RENDER_DASHBOARD.md` — 2026-07 current status + historical sections
- `docs/dashboard/PRODUCTION_DASHBOARD_SMOKE_CHECKLIST.md` — origin vs Worker proxy smoke
- `docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md` — 2026-07 Worker hardening + secret rotation note
- `apps/dashboard/README.md` — mirror route usage
- `docs/DOCUMENTATION_STALENESS_AUDIT_2026-07-06.md` — this record

## Production smoke summary (2026-07-06)

**Method:** GET via `https://dashboard.origenlab.cl/api` with CF Access service-token headers and `Origin: https://dashboard.origenlab.cl`.

**Result:** **8/8 PASS**

| Route | HTTP | JSON | CORS | `x-originlab-proxy` | `x-originlab-upstream-status` |
|-------|------|------|------|----------------------|-------------------------------|
| `/health` | 200 | yes | `https://dashboard.origenlab.cl` | `dashboard-proxy` | 200 |
| `/operator/status?max_staleness_days=14` | 200 | yes | yes | yes | 200 |
| `/operator/automation-status?cooldown-seconds=900` | 200 | yes | yes | yes | 200 |
| `/mirror/catalog/products?limit=100` | 200 | yes | yes | yes | 200 |
| `/mirror/leads/summary` | 200 | yes | yes | yes | 200 |
| `/mirror/leads/prospects?limit=20&include_blocked=false` | 200 | yes | yes | yes | 200 |
| `/mirror/audits/gmail-interactions` | 200 | yes | yes | yes | 200 |
| `/mirror/commercial/deals?limit=20` | 200 | yes | yes | yes | 200 |

No secrets or cookie values recorded in this audit.

## Validation commands

```bash
git diff --check

cd apps/api && uv run pytest tests/test_api_response_contract.py tests/test_response_model_coverage.py tests/test_http_security.py tests/test_no_write_policy.py -q

cd apps/dashboard-proxy && npm ci && npm run validate && npm run typecheck
```

**Optional post-merge:** rerun the 8-route Worker proxy curl smoke per [`docs/dashboard/PRODUCTION_DASHBOARD_SMOKE_CHECKLIST.md`](dashboard/PRODUCTION_DASHBOARD_SMOKE_CHECKLIST.md) section B.

## Merged PRs referenced

#342 dashboard read-only API proxy · #345 CORS headers · #347 redirect blocking · #348 Set-Cookie stripping · #349 upstream CORS stripping tests · #346 backend-unavailable tests · #351 docs alignment · #350 legacy dedupe safety guard

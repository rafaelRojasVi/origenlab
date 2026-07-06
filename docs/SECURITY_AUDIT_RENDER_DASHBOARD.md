# Security audit — Render API + dashboard (read-only operator plane)

**Last updated:** 2026-07-06 (docs refresh after dashboard Worker proxy hardening)
**Scope:** `apps/api` (FastAPI on Render `origenlab-api`), `apps/dashboard` (Vite/React static on `origenlab-dashboard`), Cloudflare Worker (`apps/dashboard-proxy`), Render Postgres mirror, Cloudflare Access on custom domains.

---

## Current status (2026-07)

Production read path (verified 2026-07-06):

```text
Browser → dashboard.origenlab.cl
       → Cloudflare Access (operator SSO)
       → Cloudflare Worker dashboard.origenlab.cl/api*
       → api.origenlab.cl (Access + ORIGENLAB_API_AUTH_TOKEN)
       → FastAPI apps/api
       → Postgres mirror
```

| Area | Status (2026-07) |
|------|------------------|
| Mutation-free API surface | **Pass** — GET/HEAD/OPTIONS only; enforced by `test_no_write_policy.py` |
| Production origin auth | **Pass** — `ORIGENLAB_API_AUTH_TOKEN` required when `ORIGENLAB_ENV=production` |
| Cloudflare Access (custom domains) | **Live** — `dashboard.origenlab.cl` and `api.origenlab.cl` |
| Dashboard Worker proxy | **Live** — same-origin `/api/*`; injects API token + CF Access service token upstream |
| Worker response hardening | **Pass** — blocks upstream 3xx; strips upstream `Location`, `Set-Cookie`/`Set-Cookie2`, upstream CORS; credentialed CORS for dashboard origin |
| API host allowlist | **Pass** — `ORIGENLAB_API_ALLOWED_HOSTS=api.origenlab.cl` rejects wrong `Host` on origin |
| Raw Render URL bypass | **Partially mitigated** — API raw host blocked by allowlist; **verify manually** whether `origenlab-dashboard.onrender.com` is still reachable without Access |
| Dashboard `/mirror/*` usage | **In use** — Catálogo, Negocio, leads, audit, commercial views call `GET /mirror/*` via Worker |
| Browser API token exposure | **Pass** — no `VITE_*` auth; Worker secrets only |
| OpenAPI in production | **Pass** — disabled via `ORIGENLAB_API_DISABLE_DOCS` |

**Production Worker proxy smoke (2026-07-06):** 8/8 GET routes through `https://dashboard.origenlab.cl/api` returned HTTP **200**, `content-type: application/json`, `access-control-allow-origin: https://dashboard.origenlab.cl`, `x-originlab-proxy: dashboard-proxy`, `x-originlab-upstream-status: 200`. See [`docs/dashboard/PRODUCTION_DASHBOARD_SMOKE_CHECKLIST.md`](dashboard/PRODUCTION_DASHBOARD_SMOKE_CHECKLIST.md).

**Safety confirmation (this doc pass):** Docs-only refresh. No sends, Gmail mutation, outreach writes, mart rebuild, or destructive SQL.

### Remaining follow-ups (verify manually)

| Item | Notes |
|------|--------|
| Raw `origenlab-dashboard.onrender.com` | Confirm blocked or unpublished; API raw host mitigated by allowlist |
| Postgres API role | Prefer read-only DB user for API service — **verify manually** on Render |
| `/emails/recent` | Still mounted; not used by active dashboard tabs — optional disable |
| Rate limiting | Not implemented at API edge |
| Equipment `source_path` | May expose filesystem paths — redaction backlog |
| Optional Access JWT at origin | `ORIGENLAB_REQUIRE_ACCESS_JWT` not implemented |

### Related runbooks

- [`CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md`](CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md) — Access + Worker deploy record
- [`apps/api/docs/PRODUCTION_AUTH.md`](../apps/api/docs/PRODUCTION_AUTH.md) — bearer token auth
- [`apps/dashboard/docs/PRODUCTION_API_AUTH.md`](../apps/dashboard/docs/PRODUCTION_API_AUTH.md) — browser vs Worker auth
- [`apps/dashboard-proxy/README.md`](../apps/dashboard-proxy/README.md) — Worker allowlist and hardening

---

## Historical audit (2026-05-18) — superseded framing

> **Note:** The sections below record the **2026-05-18** audit snapshot. Verdict language such as **“Needs changes before production”** and **“API has no authentication”** applied **before** Cloudflare Access, production bearer auth, host allowlist, and the dashboard Worker proxy landed. Keep for traceability; do **not** treat as current production status.

**Historical verdict (2026-05-18):** Read-only API design was sound, but authentication and raw `*.onrender.com` exposure were open until Access and API guards shipped.

**Historical update note (2026-07, pre–full doc refresh):** Production bearer token auth (`ORIGENLAB_API_AUTH_TOKEN`) and host allowlist were implemented in `apps/api`.

---

## Executive summary (2026-05-18 snapshot)

| Area | Status (2026-05-18) |
|------|---------------------|
| Mutation-free API surface | **Pass** |
| Production CORS / docs / Postgres backend | **Pass** (when `render.yaml` env applied) |
| Authentication | **Open** — no origin bearer token yet |
| Raw Render URL bypass | **High** |
| Sensitive data minimization | **Mostly pass** |
| HTTP security headers | **Improved** |
| Secrets in repo | **Pass** (git) / **Action** — local `.env` rotation |

---

## 1. API authentication and exposure (2026-05-18 snapshot)

### Routes at time of audit

| Method | Path | Used by dashboard (2026-07) | Data sensitivity |
|--------|------|-----------------------------|------------------|
| GET | `/health` | Yes | Low |
| GET | `/operator/status` | Yes | Medium |
| GET | `/operator/automation-status` | Yes | Medium |
| GET | `/cases/warm` | Yes | **High** |
| GET | `/opportunities/equipment` | Yes | **High** |
| GET | `/contacts/{email}` | Yes | **High** |
| GET | `/mirror/catalog/*` | Yes (Catálogo) | Medium–High |
| GET | `/mirror/leads/*` | Yes (Negocio) | Medium–High |
| GET | `/mirror/audits/*` | Yes (audit views) | Medium |
| GET | `/mirror/commercial/*` | Yes (Negocio) | **High** |
| GET | `/emails/recent` | **No** (active clients) | Medium |
| GET | `/mirror/outbound/*` | **No** (active clients) | **High** |

**Read-only confirmation:** `apps/api/tests/test_no_write_policy.py`. Repositories issue `SELECT` only.

### Historical recommendations — route exposure

| Phase | Action | 2026-07 status |
|-------|--------|----------------|
| Access on custom domains | Protect `api.origenlab.cl` + `dashboard.origenlab.cl` | **Done** |
| Origin bearer token | `ORIGENLAB_API_AUTH_TOKEN` | **Done** |
| Worker proxy | Same-origin `/api/*` for browser | **Done** (#342–#349) |
| Omit `/mirror/*` in production | Suggested when Today did not use mirror | **Not done** — dashboard now uses `/mirror/*`; allowlist controls exposure |
| Disable `/emails/recent` | Optional | **Open** |

---

## 2. Raw Render URL bypass (2026-05-18 snapshot)

**Historical risk:** Cloudflare Access on custom hostnames did **not** block `https://origenlab.onrender.com`.

**2026-07 mitigation:** `ORIGENLAB_API_ALLOWED_HOSTS=api.origenlab.cl` returns **403** for wrong `Host` on the API service. **Verify manually** that the default Render hostname is not still serving JSON health without Access.

| Phase | Mitigation | 2026-07 status |
|-------|------------|----------------|
| Access on both custom domains | Cloudflare Zero Trust apps | **Done** |
| API host allowlist | `AllowedHostMiddleware` | **Done** |
| Do not publish raw URLs | Operator discipline | Ongoing |
| Private service / Tunnel | Optional | Not done |

---

## 3. CORS (2026-05-18 snapshot + 2026-07 note)

**API:** `CORSMiddleware` with explicit origins; production rejects `*`.

**Dashboard browser path (2026-07):** Browser calls same-origin `dashboard.origenlab.cl/api/*`. The **Worker** applies credentialed CORS (`Access-Control-Allow-Origin: https://dashboard.origenlab.cl`, never `*`) and strips upstream CORS headers. Direct API CORS still matters for CLI/origin smokes hitting `api.origenlab.cl`.

**Render:** `ORIGENLAB_API_CORS_ORIGINS=https://dashboard.origenlab.cl`.

---

## 4. OpenAPI / docs

Disabled in production when `ORIGENLAB_ENV=production` or `ORIGENLAB_API_DISABLE_DOCS=true`.

---

## 5. Sensitive data minimization

Unchanged from 2026-05-18 findings — preview schemas; mirror sync excludes `archive.emails` bodies. `/contacts/{email}` returns outreach notes and sent subjects by design.

---

## 6. HTTP security headers

API `OperatorSecurityHeadersMiddleware`; dashboard static headers in `render.yaml`; dashboard `noindex`.

---

## 7. Secrets and environment

| Finding | Severity |
|---------|----------|
| `.env` gitignored | OK |
| Local `.env` may hold live credentials (not committed) | Rotate if exposed |
| Worker / Render secrets | Store in platform only — **rotated after accidental exposure (2026-07)** |

---

## 8. Dependency / build posture

See 2026-05-18 table; recommend ongoing `pip-audit` / Dependabot in CI.

---

## 9. Dashboard security (2026-05-18 + 2026-07)

| Check | Result |
|-------|--------|
| `VITE_ORIGENLAB_API_BASE_URL` | Production: `https://dashboard.origenlab.cl/api` (Worker path) |
| No browser API token | **Pass** — Worker injects upstream |
| GET-only clients | **Pass** |
| No send/mailto abuse | **Pass** — safety tests |

---

## 10. Tests

```bash
cd apps/api && uv run pytest tests/test_http_security.py tests/test_no_write_policy.py tests/test_emails_recent.py tests/test_contacts_detail.py -q
cd apps/dashboard && npm test -- --run src/test/dashboard0Safety.test.ts src/api/operatorClient.test.ts
cd apps/dashboard-proxy && npm run validate
```

---

## Historical risk table (2026-05-18)

| ID | Severity (2026-05) | Finding | 2026-07 status |
|----|-------------------|---------|----------------|
| R1 | **Critical** | API had **no authentication** | **Mitigated** — bearer token + Access + Worker |
| R2 | **High** | Raw `*.onrender.com` bypass | **Partially mitigated** — API host allowlist; verify dashboard raw URL |
| R3 | **High** | `/mirror/*` exposed but unused by Today | **Accepted** — dashboard now uses mirror routes; Worker allowlist |
| R4 | **High** | Local `.env` live credentials | **Ongoing** — rotate if exposed |
| R5 | **Medium** | Postgres user may have write privileges | **Open** — verify manually |
| R6 | **Medium** | `/contacts/{email}` outreach detail | By design (operator drilldown) |
| R7 | **Medium** | Equipment `source_path` leak | **Open** |
| R8 | **Medium** | `/emails/recent` unused but mounted | **Open** |
| R9 | **Low** | `/health` backend hints | Accepted |
| R10 | **Low** | No rate limiting | **Open** |
| R11 | **Low** | No dashboard CSP | **Open** |

---

## Historical patch plan (2026-05-18)

Phases 1–2 items largely completed (Access, bearer auth, host allowlist, Worker proxy, CORS hardening). Phase 3 optional items remain in **Remaining follow-ups** above.

---

## Files inspected (original 2026-05-18 audit)

`apps/api/src/origenlab_api/main.py`, `http_security.py`, `settings.py`, `backends/factory.py`, `routes/*`, `mirror/**`, `repositories/**`, `schemas/**`, `Dockerfile`, `tests/test_*`, `render.yaml`, `apps/dashboard/src/api/*`, `apps/dashboard-proxy/src/*`, `docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md`, `.gitignore`.

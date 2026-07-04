# Security audit — Render API + dashboard (read-only operator plane)

**Date:** 2026-05-18  
**Scope:** `apps/api` (FastAPI on Render `origenlab-api`), `apps/dashboard` (Vite/React static on `origenlab-dashboard`), Render Postgres mirror, planned Cloudflare Access.  
**Verdict (2026-05-18):** **Needs changes before production** — read-only API design is sound, but **no authentication** and **raw `*.onrender.com` bypass** leave commercial data public until Access (and preferably API JWT/host guards) are live.

**Update (2026-07):** Production **bearer token auth** (`ORIGENLAB_API_AUTH_TOKEN`) and host allowlist are implemented in `apps/api`. See [`apps/api/docs/PRODUCTION_AUTH.md`](../apps/api/docs/PRODUCTION_AUTH.md). Cloudflare Access remains recommended for custom domains; CORS is not authentication.

---

## Executive summary

| Area | Status |
|------|--------|
| Mutation-free API surface | **Pass** — GET/HEAD/OPTIONS only; no ingest/send/sync imports in `apps/api` |
| Production CORS / docs / Postgres backend | **Pass** (when `render.yaml` env applied) |
| Authentication | **Partial (2026-07)** — production bearer token on private routes; Cloudflare Access still recommended at edge |
| Raw Render URL bypass | **High** — custom domain protection does not cover `origenlab.onrender.com` |
| Sensitive data minimization | **Mostly pass** — preview schemas; mirror sync excludes `archive.emails` bodies |
| HTTP security headers | **Improved** — API middleware + dashboard static headers (this audit) |
| Secrets in repo | **Pass** (git) / **Action** — local `.env` contains live credentials (rotate) |

**Safety confirmation (audit actions):** No sends, Gmail mutation, outreach writes, mart rebuild, or destructive SQL were performed. Changes in this pass are headers, tests, docs, and `render.yaml` header routes only.

---

## 1. API authentication and exposure

### Public routes (all unauthenticated today)

Enumerated from `create_app()` in `apps/api/src/origenlab_api/main.py` and mirror router.

| Method | Path | Used by Dashboard Today? | Data sensitivity |
|--------|------|--------------------------|------------------|
| GET | `/health` | Yes | Low — liveness, backend mode |
| GET | `/operator/status` | Yes | Medium — verdict, warnings, redacted sqlite hint |
| GET | `/cases/warm` | Yes | **High** — contacts, subjects, snippets, categories |
| GET | `/opportunities/equipment` | Yes | **High** — buyers, emails, tender codes |
| GET | `/contacts/{email}` | Yes | **High** — profile, outreach state, notes, sent subjects |
| GET | `/emails/recent` | **No** (v1 `operatorClient.ts`) | Medium — sender/subject previews, `source_file` |
| GET | `/mirror/dashboard/summary` | No (legacy parked) | Medium–High — mart counts |
| GET | `/mirror/classification/*` | No | Medium — classified mail metadata |
| GET | `/mirror/commercial/purchase-events` | No | **High** — purchase events, buyer emails |
| GET | `/mirror/contacts` | No | **High** — paginated contact list |
| GET | `/mirror/organizations` | No | Medium |
| GET | `/mirror/outbound/*` | No | **High** — suppressions, outreach state |
| GET | `/mirror/meta/dashboard-sync` | No | Low — sync watermarks |
| GET | `/mirror/health/dependencies` | No | Low |

**Read-only confirmation:** `apps/api/tests/test_no_write_policy.py` — `test_app_exposes_get_only_routes`, `test_origenlab_api_source_has_no_mutation_script_imports`. Repositories under `apps/api/src/origenlab_api/repositories/**` issue `SELECT` only.

**No mutation paths:** No routes call Gmail send, SQLite ingest, mirror sync, Alembic, or outreach upsert from the API process.

### Recommendations — route exposure

| Phase | Action |
|-------|--------|
| **1 (before Access)** | Document public surface; ensure production env matches `render.yaml` |
| **2 (with Access)** | Protect **both** `api.origenlab.cl` and `dashboard.origenlab.cl` |
| **2–3** | Add `ORIGENLAB_API_MOUNT_MIRROR=false` (new flag) in production to **omit `/mirror/*`** from the app — Today does not use them (`apps/dashboard/src/api/operatorClient.ts`, `dashboard0Safety.test.ts`) |
| **3** | Optionally disable `/emails/recent` in production if no consumer (not used by Today) |

---

## 2. Raw Render URL bypass

**Risk:** Cloudflare Access on custom hostnames does **not** block `https://origenlab.onrender.com` or `https://origenlab-dashboard.onrender.com`.

| Phase | Mitigation |
|-------|------------|
| **1** | Do not publish raw URLs; rotate Postgres password if ever shared; enable Access on custom domains ASAP |
| **2** | Cloudflare Access apps for `api.origenlab.cl` + `dashboard.origenlab.cl` (see `docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md`) |
| **2** | API middleware: validate `CF-Access-Jwt-Assertion` (or `Cf-Access-Jwt-Assertion`) when `ORIGENLAB_REQUIRE_ACCESS_JWT=true` — reject missing/invalid JWT with 401 |
| **2** | Optional `ORIGENLAB_ALLOWED_HOSTS=api.origenlab.cl` — return 421/403 for other `Host` (does not stop direct Render URL unless Render disables default hostname) |
| **3** | Render private service / Cloudflare Tunnel; Postgres role with **SELECT-only** on mirror schemas |
| **3** | **Do not** use `VITE_*` shared secrets as API auth (public in static bundle) |

---

## 3. CORS

**Implementation:** `apps/api/src/origenlab_api/http_security.py` — `CORSMiddleware` with explicit origins, `allow_credentials=False`, methods `GET`, `HEAD`, `OPTIONS` only.

**Settings:** `ORIGENLAB_API_CORS_ORIGINS` → field `api_cors_origins` via prefix `ORIGENLAB_` in `apps/api/src/origenlab_api/settings.py` (lines 19–45).

**Production gates:** `apps/api/src/origenlab_api/backends/factory.py` — requires Postgres + non-empty CORS; wildcard rejected in `validate_http_security_settings`.

**Render:** `render.yaml` sets `ORIGENLAB_API_CORS_ORIGINS=https://dashboard.origenlab.cl`.

**Doc fix:** `docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md` incorrectly said `ORIGENLAB_CORS_ORIGINS` — corrected to `ORIGENLAB_API_CORS_ORIGINS`.

**Gap:** Temporary Render dashboard URL (`https://origenlab-dashboard.onrender.com`) is **not** in CORS allowlist — correct for production; add only during cutover if needed.

**Tests:** `apps/api/tests/test_http_security.py` — wildcard rejection, preflight origin, credentials absent, security headers.

---

## 4. OpenAPI / docs

**Implementation:** `openapi_docs_enabled()` in `http_security.py` (lines 37–42); `create_app()` passes `docs_url=None` when disabled (`main.py` lines 26–28).

**Production:** Disabled when `ORIGENLAB_ENV=production` or `ORIGENLAB_API_DISABLE_DOCS=true`. `render.yaml` sets both.

**Tests:** `test_production_mode_disables_openapi_docs`, `test_production_hides_docs_routes`.

---

## 5. Sensitive data minimization

### `/cases/warm`

- Schema: `apps/api/src/origenlab_api/schemas/cases.py` — `subject`, `snippet`, no body fields.
- Postgres view sets `gmail_url` to `NULL` (Alembic `20260524_0017`, `20260519_0014`).
- Dashboard parser nulls `gmail_url`: `apps/dashboard/src/api/commercialParse.ts`.

### `/emails/recent`

- Schema: `schemas/emails.py` — previews only.
- Postgres: `api.v_recent_email` (`repositories/postgres/email.py`) — no `full_body_clean`.
- Test: `test_emails_recent_no_body_fields` in `tests/test_emails_recent.py`.

### `/contacts/{email}`

- Returns outreach `notes`, `sent_history.latest_subject` — justified for operator drilldown; invalid email → 422 (`routes/contacts.py`, `outreach_contact_state.normalize_contact_email_for_outreach`).
- Tests: `tests/test_contacts_detail.py`.

### Postgres mirror vs archive bodies

- **Cloud sync** (`dashboard_postgres_sync.py` `REQUIRED_MIRROR_TABLES`) syncs **outbound** + **mart** tables only — **not** `archive.emails`.
- Alembic defines `archive.emails.full_body_clean` for optional full archive loads; production dashboard mirror path does not depend on API reading bodies from Postgres.
- API Postgres email path uses `api.v_recent_email`, not `archive.emails` body columns.

### Equipment `source_path`

- `EquipmentOpportunitiesMeta.source_path` may expose server paths from mirror (`repositories/postgres/equipment.py`) — **Medium**; recommend basename-only redaction in production (Phase 2).

---

## 6. HTTP security headers

| Layer | Status |
|-------|--------|
| API | **Added** `OperatorSecurityHeadersMiddleware` in `http_security.py` — `nosniff`, `Referrer-Policy`, `DENY` framing, `Cache-Control: no-store, private` |
| Dashboard static | **Added** `render.yaml` header routes + `<meta name="robots" content="noindex">` in `apps/dashboard/index.html` |
| Dashboard CSP | **Deferred** — strict CSP can break Vite; revisit after Access |

---

## 7. Secrets and environment

| Finding | Severity |
|---------|----------|
| `.env` gitignored (`.gitignore` lines 23–29) | OK |
| Local `apps/email-pipeline/.env` contains **live** Postgres URL, OpenAI key, Gmail paths (not committed) | **Critical action** — rotate Render Postgres + API keys if disk/chat exposed |
| `render.yaml` / `.env.example` use placeholders only | OK |
| `REFRESH_RENDER_DASHBOARD_ONCE.md` uses `YOUR_PASSWORD` placeholder | OK |

**Never commit:** `ORIGENLAB_POSTGRES_URL`, `ORIGENLAB_CLOUD_POSTGRES_URL`, Gmail OAuth JSON, OpenAI keys.

---

## 8. Dependency / build posture

| Item | Finding |
|------|---------|
| Python | 3.12 (`apps/api/Dockerfile`) |
| API image | `uv sync --frozen --no-dev` — **no dev deps in production image** |
| API runtime deps | fastapi, uvicorn, pydantic, email-pipeline (editable in monorepo build) |
| Dashboard `npm audit --omit=dev` | 0 vulnerabilities (2026-05-18) |
| pip-audit | Not in dev env; recommend CI `uv run pip-audit` or Dependabot |

---

## 9. Dashboard security

| Check | Result |
|-------|--------|
| `VITE_ORIGENLAB_API_BASE_URL` only | **Pass** — public API URL; production throws if unset (`operatorClient.ts`) |
| No send/mailto with subject/body in warm UI | **Pass** — `dashboard0Safety.test.ts` |
| No `dangerouslySetInnerHTML` in commercial/Today | **Pass** — grep clean |
| `MailtoEmailLink` | `mailto:email` only — no pre-filled send |
| Fetch methods | GET only in `operatorClient.ts` |

---

## 10. Tests (existing + added)

| Test file | Coverage |
|-----------|----------|
| `test_no_write_policy.py` | GET-only routes, no mutation imports |
| `test_http_security.py` | CORS, docs off, headers, no credentials |
| `test_emails_recent.py` | No body fields |
| `test_contacts_detail.py` | Invalid email 422 |
| `dashboard0Safety.test.ts` | v1 routes only, no mailto abuse, no legacy panels |

**Commands:**

```bash
cd apps/api && uv run pytest tests/test_http_security.py tests/test_no_write_policy.py tests/test_emails_recent.py tests/test_contacts_detail.py -q
cd apps/dashboard && npm test -- --run src/test/dashboard0Safety.test.ts src/api/operatorClient.test.ts
```

---

## Risk table

| ID | Severity | Finding | Location |
|----|----------|---------|----------|
| R1 | **Critical** | API has **no authentication**; full commercial mirror readable by anyone who can reach the host | All routes; mitigated by Access (planned) |
| R2 | **High** | Raw `*.onrender.com` URLs bypass Cloudflare Access on custom domains | Deployment architecture |
| R3 | **High** | `/mirror/*` exposes suppressions, outreach state, purchase events — **not used by Today** but mounted in production | `mirror/__init__.py`, `main.py` |
| R4 | **High** | Local `.env` holds live Postgres password / API keys (rotation required if exposed) | `apps/email-pipeline/.env` (gitignored) |
| R5 | **Medium** | Postgres DB user may have write privileges; API relies on discipline not DB read-only role | `repositories/postgres/common.py` |
| R6 | **Medium** | `/contacts/{email}` exposes outreach notes and sent subjects | `schemas/contacts.py`, `postgres/contact.py` |
| R7 | **Medium** | `EquipmentOpportunitiesMeta.source_path` may leak filesystem paths | `schemas/opportunities.py`, postgres equipment repo |
| R8 | **Medium** | `/emails/recent` exposes `source_file` (folder hints) — unused by Today but public | `schemas/emails.py` |
| R9 | **Low** | `/health` reveals `backend`/`postgres_configured` | `schemas/health.py` |
| R10 | **Low** | No rate limiting on public API | — |
| R11 | **Low** | Dashboard lacks CSP (XSS reliance on React escaping) | React default escaping — OK if no `dangerouslySetInnerHTML` |

---

## Patch plan

### Phase 1 — before Cloudflare Access (done / immediate)

- [x] API security headers middleware + tests
- [x] Dashboard `noindex` + static headers in `render.yaml`
- [x] Fix `ORIGENLAB_API_CORS_ORIGINS` doc typo
- [ ] Confirm Render env: `ORIGENLAB_ENV=production`, `ORIGENLAB_API_BACKEND=postgres`, CORS, docs disabled
- [ ] Rotate Postgres password if ever pasted in chat/logs
- [ ] Stop sharing raw `onrender.com` URLs

### Phase 2 — after custom domains + Access

- [ ] Enable Cloudflare Access (both hostnames) per `CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md`
- [ ] Add optional `ORIGENLAB_REQUIRE_ACCESS_JWT` middleware (validate Cloudflare JWT)
- [ ] Add `ORIGENLAB_API_MOUNT_MIRROR=false` to hide `/mirror/*` in production
- [ ] Redact equipment `source_path` to basename in API responses
- [ ] Create Postgres **read-only** DB user for API service

### Phase 3 — optional hardening

- [ ] Rate limiting (Cloudflare or API middleware)
- [ ] Private Render service / Tunnel
- [ ] Dashboard CSP (tested in staging)
- [ ] CI: `pip-audit`, `npm audit`
- [ ] Disable `/emails/recent` if permanently unused

---

## Deploy requirements (header/doc changes only)

1. **Redeploy `origenlab-api`** — security headers middleware.
2. **Redeploy `origenlab-dashboard`** — `index.html` robots meta + Render header routes from `render.yaml`.
3. No Postgres migration or mirror re-sync required.

---

## Files inspected (audit)

`apps/api/src/origenlab_api/main.py`, `http_security.py`, `settings.py`, `backends/factory.py`, `routes/*`, `mirror/**`, `repositories/**`, `schemas/**`, `Dockerfile`, `tests/test_*`, `render.yaml`, `apps/dashboard/src/api/operatorClient.ts`, `commercialParse.ts`, `warmCaseViewPreset.ts`, `src/test/dashboard0Safety.test.ts`, `index.html`, `apps/email-pipeline/src/.../dashboard_postgres_sync.py`, `docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md`, `.gitignore`.

## Files changed (this pass)

`apps/api/src/origenlab_api/http_security.py`, `apps/api/tests/test_http_security.py`, `render.yaml`, `apps/dashboard/index.html`, `docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md`, `docs/SECURITY_AUDIT_RENDER_DASHBOARD.md` (this file).

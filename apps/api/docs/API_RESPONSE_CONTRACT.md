# API JSON response contract

**Audience:** Dashboard, operator tooling, and any HTTP client of `apps/api` (port **8001**).

**Scope:** Operator API (`origenlab_api`). Most routes remain read-only. Durable commercial-operations commands under `/operations/*` and the explicitly sanctioned procurement document-import commands form the narrow mutation boundary. This document defines the contract clients should rely on, documents **stable shapes already shipped**, and lists **remaining gaps**.

**Non-goals (this doc):** Route refactors, error-handler rewrites, or breaking response renames. Those require a dedicated migration PR with client + test updates.

---

## Principles

| Principle        | Rule                                                                                     |
| ---------------- | ---------------------------------------------------------------------------------------- |
| Predictable      | Success and error bodies use documented top-level keys.                                  |
| Consistent       | List endpoints share `meta` + `items`; errors share the unified `error` envelope.        |
| Machine-readable | Stable string `code` values for errors; typed OpenAPI `response_model` on public routes. |
| Human-debuggable | Short `message` for operators; optional safe `details` for UI hints.                     |
| Safe             | No stack traces, secrets, raw env vars, DSNs, Gmail tokens, or full email bodies.        |
| Stable           | Prefer **additive** changes. Renames/removals require tests + this doc in the same PR.   |

---

## Success responses

### General rules

1. Every **successful** response MUST be a **JSON object** (never a bare array or string).
2. Public routes SHOULD declare a Pydantic `response_model` (see OpenAPI `/openapi.json` in dev).
3. Target: do **not** expose stack traces, `postgres://…` URLs, raw `ORIGENLAB_*` secrets, Gmail OAuth tokens, full MIME bodies, or unredacted filesystem paths in operator-facing fields. Legacy exceptions are documented under **Current gaps**.

### List endpoints (`meta` + `items`)

Collection routes return:

```json
{
  "meta": {
    "count": 0,
    "data_source": "sqlite",
    "read_only": true,
    "note": ""
  },
  "items": []
}
```

| `meta` field  | Required         | Notes                                                   |
| ------------- | ---------------- | ------------------------------------------------------- |
| `count`       | **Yes** (target) | Number of items returned (may be `0`).                  |
| `data_source` | Optional         | e.g. `sqlite`, `postgres_mirror`, `active_current_csv`. |
| `read_only`   | Optional         | Should be `true` for operator read routes.              |
| `note`        | Optional         | Human context when `reduced_mode` or empty data.        |

Routes may add **documented** meta fields (e.g. `reduced_mode`, `campaign_mode`, `enrichment_available`). Clients should ignore unknown meta keys.

**Examples (shipped):**

| Route                          | Response model                                      |
| ------------------------------ | --------------------------------------------------- |
| `GET /cases/warm`              | `WarmCasesResponse` — `meta` + `items`              |
| `GET /opportunities/equipment` | `EquipmentOpportunitiesResponse` — `meta` + `items` |
| `GET /opportunities/commercial` | `CommercialOpportunitiesResponse` — `meta` + `items` |

**`GET /cases/warm` (production Postgres read model):**

| Rule                           | Requirement                                                                                                                                        |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `meta.data_source`             | `postgres_mirror` when `ORIGENLAB_API_BACKEND=postgres`                                                                                            |
| `meta.read_only`               | `true`                                                                                                                                             |
| `meta.enrichment_available`    | `true`                                                                                                                                             |
| `meta.count`                   | Integer equal to `len(items)`                                                                                                                      |
| `meta.canonical_categories`    | Array of canonical category strings for CLI/display grouping                                                                                       |
| `meta.legacy_category_aliases` | Object mapping accepted legacy filter aliases to canonical category names                                                                          |
| Item identity                  | Each item has non-empty string `case_id`                                                                                                           |
| Item typing                    | `last_email_id` is int; `category` and `status` are non-empty strings                                                                              |
| Internal fields                | Must **not** appear in JSON: `body_snippet`, `source_file`, `recipients_preview`, `sender_preview` (schema `exclude=True`; audit enforces absence) |
| Path leaks                     | Raw `/home/` or `/mnt/` paths forbidden anywhere in the response body                                                                              |

SQLite dev fallback may return `meta.data_source: "sqlite"` locally; production contract is locked via `PostgresWarmCaseRepository` → `api.v_warm_case` and `scripts/remote_response_audit.py` (`require_warm_cases_contract`). Legacy category filters remain accepted, but clients should prefer `meta.canonical_categories` and normalize via `meta.legacy_category_aliases`.

**`GET /emails/recent` (production Postgres read model):**

| Rule                   | Requirement                                                                                                                                                       |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `meta.data_source`     | `postgres_mirror` when `ORIGENLAB_API_BACKEND=postgres`                                                                                                           |
| `total_returned`       | Integer equal to `len(items)`                                                                                                                                     |
| `enrichment_available` | Boolean                                                                                                                                                           |
| `reduced_mode`         | Boolean                                                                                                                                                           |
| `days_window`          | Integer (echo of `days` query param)                                                                                                                              |
| Item identity          | Each item has int `email_id`                                                                                                                                      |
| `source_file`          | Optional public field on `EmailRecentRow`: `null` or a safe logical mailbox/source string (e.g. `gmail:…`, `imap:…`); must **not** be an absolute filesystem path |
| Internal fields        | Must **not** appear in JSON items: `body`, `raw_body`, `headers`, `recipients_raw`                                                                                |
| Item values            | Public fields are JSON-safe scalars (no nested objects in item payloads)                                                                                          |
| Path leaks             | Raw `/home/` or `/mnt/` paths forbidden anywhere in the response body (including unsafe `source_file` values)                                                     |

Production reads `api.v_recent_email` through `PostgresEmailRecentRepository` when Postgres is configured. Remote contract: `scripts/remote_response_audit.py` (`require_recent_emails_contract`).

`EquipmentOpportunityItem` includes **`opportunity_key`** (stable cross-source correlation id: `equipment:<source_slug>:<codigo>`). Additive; clients may ignore until needed.

| Route                       | Response model                                                                              |
| --------------------------- | ------------------------------------------------------------------------------------------- |
| `GET /emails/recent`        | `EmailsRecentResponse` — `meta` + `items` (+ extra top-level counters documented in schema) |
| `GET /mirror/*` list routes | Pipeline mirror schemas — `meta` + `items` (or paginated variants)                          |

### Single-resource endpoints (`meta` + resource key)

Prefer:

```json
{
  "meta": { "read_only": true, "data_source": "sqlite" },
  "item": { }
}
```

**Stable exceptions (do not rename without migration):**

| Route                                        | Shape                          | Resource key              |
| -------------------------------------------- | ------------------------------ | ------------------------- |
| `GET /contacts/{email}`                      | `ContactDetailResponse`        | `contact` (not `item`)    |
| `GET /mirror/catalog/products/{product_key}` | `CatalogProductDetailResponse` | product nested per schema |
| `GET /mirror/commercial/deals/{deal_key}`    | `CommercialDealDetailResponse` | `deal`                    |

New single-resource routes SHOULD use `meta` + `item` unless mirroring an existing nested name.

### Health and status endpoints

Small **stable objects** are allowed when documented and covered by tests.

| Route                             | Shape                                                 | Notes                                                                           |
| --------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------- |
| `GET /health`                     | `{ ok, service, mode, backend, postgres_configured }` | Liveness; no `meta` wrapper.                                                    |
| `GET /operator/status`            | `OperatorStatusResponse`                              | Flat operator verdict + warnings.                                               |
| `GET /operator/automation-status` | `OperatorAutomationStatusResponse`                    | Automation health snapshot; includes additive `ndr_pending_review` (see below). |
| `GET /mirror/health/dependencies` | `HealthDependenciesResponse`                          | Postgres/SQLite reachability (redacted URL fields only).                        |

Keys on these endpoints must not change casually; update `tests/test_health.py` and this table together.

**`GET /operator/automation-status` — `ndr_pending_review` (additive):**

| Rule          | Requirement                                                                                                |
| ------------- | ---------------------------------------------------------------------------------------------------------- |
| Field         | Top-level `ndr_pending_review` object (default `{}` when absent in source snapshot).                       |
| Purpose       | Pending NDR review counts, paths, and status for operator visibility.                                      |
| Sources       | Populated from filesystem `active/current` or Postgres automation snapshot when present.                   |
| Path safety   | Nested path-like fields are **basename-redacted** like other automation sections (`path_info` companions). |
| Compatibility | Additive; existing clients may ignore until needed.                                                        |

---

## Error responses

### Contract (all routes)

Errors use a unified envelope:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Short human-readable summary",
    "details": {},
    "request_id": null
  }
}
```

Implemented via centralized handlers in `origenlab_api.errors` (registered from `create_app()`). Host allowlist rejections in `http_security` use the same shape.

| Field        | Type           | Purpose                                                               |
| ------------ | -------------- | --------------------------------------------------------------------- |
| `code`       | string         | Stable machine code for client branching.                             |
| `message`    | string         | Operator-facing summary (no secrets).                                 |
| `details`    | object         | Safe structured context (field names, allowed values).                |
| `request_id` | string \| null | Correlation id; matches the `X-Request-ID` response header on errors. |

### Request correlation (`X-Request-ID`)

Every API response includes an **`X-Request-ID`** header.

| Behavior         | Rule                                                                                                          |
| ---------------- | ------------------------------------------------------------------------------------------------------------- |
| Incoming header  | If `X-Request-ID` is present and **safe** (letters, digits, `_`, `-`, `.`, `:`, max 128 chars), it is reused. |
| Missing / unsafe | A new `uuid4().hex` value is generated. Unsafe values (URLs, secrets, spaces, etc.) are **never echoed**.     |
| Storage          | Resolved id is stored on `request.state.request_id`.                                                          |
| Errors           | `error.request_id` matches the response `X-Request-ID` header.                                                |
| Success          | Header is set; success JSON bodies do not duplicate the id (use the header).                                  |

Implemented in `origenlab_api.request_id.RequestIdMiddleware` (outermost middleware). Host allowlist 403 responses also resolve the id before returning.

### Browser clients (CORS)

When `ORIGENLAB_API_CORS_ORIGINS` is configured, credentialed dashboard/browser origins receive:

| Header                          | Value          |
| ------------------------------- | -------------- |
| `Access-Control-Expose-Headers` | `X-Request-ID`, `Server-Timing`, `X-Process-Time-Ms` |

Browser JavaScript on allowed origins can read the request/correlation id and read-only duration headers from response headers (including error responses). CORS permits **`GET`**, **`HEAD`**, **`OPTIONS`**, and **`POST`**. POST remains restricted to explicitly sanctioned command routes by API/proxy method+path allowlists and authentication.

**CORS is not authentication.** Allowed origins may send credentialed browser requests; private routes still require production bearer auth (below).

Implemented in `origenlab_api.http_security.configure_http_security`.

### Production bearer auth (`ORIGENLAB_ENV=production`)

When `ORIGENLAB_API_AUTH_TOKEN` is set (required in production):

| Access                        | Routes                              |
| ----------------------------- | ----------------------------------- |
| **No token**                  | `GET /health`, `OPTIONS` (any path) |
| **Bearer / API key required** | All other routes                    |

Clients send `Authorization: Bearer <token>` or `X-OriginLab-API-Key: <token>`. Failures: **401**, `error.code`: `unauthorized`, `WWW-Authenticate: Bearer`, `X-Request-ID`.

Runbook: [`PRODUCTION_AUTH.md`](PRODUCTION_AUTH.md).

### Standard `code` values

| `code`                  | HTTP | When                                                                                                                                                            |
| ----------------------- | ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `invalid_query_param`   | 422  | Unknown enum/filter (e.g. invalid warm `category`).                                                                                                             |
| `validation_error`      | 422  | FastAPI/Pydantic query or path validation.                                                                                                                      |
| `not_found`             | 404  | Resource missing or unknown path.                                                                                                                               |
| `forbidden`             | 403  | Host allowlist or policy rejection.                                                                                                                             |
| `unauthorized`          | 401  | Missing or invalid bearer/API-key token (production).                                                                                                           |
| `backend_unavailable`   | 503  | Postgres read-model connection or driver failure (mirror unreachable). Response is sanitized: no DSNs, passwords, SQL, or tracebacks in `message` or `details`. |
| `mirror_not_configured` | 503  | Mirror route without `ORIGENLAB_POSTGRES_URL`.                                                                                                                  |
| `internal_error`        | 500  | Unexpected failure (no traceback in body).                                                                                                                      |

**Examples (shipped):**

- `GET /cases/warm?category=not_a_real_category` → **422**, `error.code`: `invalid_query_param`.
- `GET /cases/warm?limit=0` → **422**, `error.code`: `validation_error`, `details.validation_errors`.
- `GET /contacts/not-an-email` → **422**, `error.code`: `validation_error`.
- `GET /mirror/...` 404 → **404**, `error.code`: `not_found`.
- Unknown path → **404**, `error.code`: `not_found`.
- Host allowlist reject → **403**, `error.code`: `forbidden`.

### Safety rules for errors

Never include in `message` or `details`:

- Stack traces or `traceback` text
- Raw `postgres://` / `postgresql://` connection strings
- Env var **names with values** (e.g. full `ORIGENLAB_POSTGRES_URL=…`)
- Passwords, API tickets, Gmail refresh tokens
- Full email bodies or private Gmail thread content
- Raw SQL queries

Redacted placeholders (`<redacted>`, `<unset>`) are OK when explicitly documented.

---

## OpenAPI and stability

- **Dev:** `/openapi.json`, `/docs`, `/redoc` when docs are enabled (disabled in production by default).
- Every public route MUST have a `response_model` OR a documented exception in this file.
- If a response shape changes, update **tests** (`tests/test_api_response_contract.py`, `tests/test_response_model_coverage.py` + route-specific tests) and this doc in the **same PR**.
- Prefer **additive** fields. Deprecate before removing.

---

## Route inventory (GET, public)

Operator plane (SQLite / active CSV):

| Method | Path                          |
| ------ | ----------------------------- |
| GET    | `/health`                     |
| GET    | `/operator/status`            |
| GET    | `/operator/automation-status` |
| GET    | `/emails/recent`              |
| GET    | `/cases/warm`                 |
| GET    | `/opportunities/equipment`    |
| GET    | `/opportunities/commercial`   |
| GET    | `/opportunities/commercial/{opportunity_id}` |
| GET    | `/operations/work-queue` |
| GET    | `/operations/sales-opportunities/{sales_opportunity_id}` |
| GET    | `/operations/opportunities/{opportunity_id}/state` |
| GET    | `/operations/opportunities/{opportunity_id}/activities` |
| GET    | `/operations/opportunities/{opportunity_id}/tasks` |
| GET    | `/operator/procurement/status` |
| GET    | `/operator/procurement/institutions` |
| GET    | `/operator/procurement/institutions/{institution_id}` |
| GET    | `/operator/procurement/queues/{queue_name}` |
| GET    | `/operator/procurement/tenders/{tender_code}` |
| GET    | `/operator/procurement/tenders/{tender_code}/attachment-navigation` |
| GET    | `/contacts/{email}`           |

Postgres mirror (`/mirror/*`, read-only reporting):

| Method | Path                                            |
| ------ | ----------------------------------------------- |
| GET    | `/mirror/health/dependencies`                   |
| GET    | `/mirror/meta/dashboard-sync`                   |
| GET    | `/mirror/audits/gmail-interactions`             |
| GET    | `/mirror/dashboard/summary`                     |
| GET    | `/mirror/classification/summary`                |
| GET    | `/mirror/classification/recent`                 |
| GET    | `/mirror/classification/actions`                |
| GET    | `/mirror/commercial/purchase-events`            |
| GET    | `/mirror/commercial/purchase-events/{event_id}` |
| GET    | `/mirror/commercial/deals`                      |
| GET    | `/mirror/commercial/deals/{deal_key}`           |
| GET    | `/mirror/catalog/products`                      |
| GET    | `/mirror/catalog/products/{product_key}`        |
| GET    | `/mirror/leads/prospects`                       |
| GET    | `/mirror/leads/prospects/export.csv`            |
| GET    | `/mirror/leads/prospects/{prospect_key}`        |
| GET    | `/mirror/leads/summary`                         |
| GET    | `/mirror/contacts`                              |
| GET    | `/mirror/organizations`                         |
| GET    | `/mirror/outbound/suppressions/emails`          |
| GET    | `/mirror/outbound/contact-state`                |
| GET    | `/mirror/outbound/readiness`                    |

---

## Current gaps

These are **known** remaining deviations or follow-ups. Clients should not depend on undocumented behavior.

### 1. Legacy resource key names

- `GET /contacts/{email}` uses `contact`, not `item` — **stable; do not rename casually**.

### 2. Health / operator status omit `meta`

Flat objects are intentional for liveness and operator verdict routes (see table above).

### 3. `EmailsRecentResponse` extra top-level fields

Besides `meta` + `items`, includes `total_returned`, `days_window`, `scope_note`, etc. Documented in `schemas/emails.py`; clients should treat unknown keys as optional.

### 4. Operator-facing filesystem paths are basename-only

`GET /operator/status`, `GET /operator/automation-status`, and `GET /opportunities/equipment` expose filesystem locations using **basename-only** legacy string fields for dashboard compatibility, plus structured `*_info` / `path_info` companions.

| Endpoint                          | Legacy field (basename only)                   | Companion metadata                                  |
| --------------------------------- | ---------------------------------------------- | --------------------------------------------------- |
| `GET /operator/status`            | `sqlite_path`                                  | `sqlite_path_info` → `{ redacted, basename, kind }` |
| `GET /operator/automation-status` | `active_current_dir`, nested queue/audit paths | `active_current_dir_info`, section `path_info`      |
| `GET /opportunities/equipment`    | `meta.source_path`                             | `meta.source_path_info`                             |

Raw absolute paths (`/home/…`, `/mnt/…`, parent directories) must **not** appear in JSON responses. `scripts/audit_response_contract.py` fails on `/home/` and `/mnt/` anywhere in audited payloads.

---

## Resolved (changelog)

| Date    | Change                                                                                                                                                                                                                                                             |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 2026-07 | `GET /operator/automation-status`: additive `ndr_pending_review` (pending NDR review counts/paths/status); nested paths basename-redacted like other automation sections.                                                                                          |
| 2026-07 | Production bearer auth: `ORIGENLAB_API_AUTH_TOKEN` required when `ORIGENLAB_ENV=production`; public routes are `GET /health` and `OPTIONS` only.                                                                                                                   |
| 2026-07 | CORS: `Access-Control-Expose-Headers` includes `X-Request-ID`, `Server-Timing`, and `X-Process-Time-Ms` for configured dashboard origins; methods remain `GET` / `HEAD` / `OPTIONS`.                                                                              |
| 2026-07 | Postgres read-model failures map to **503** `backend_unavailable` without leaking DSNs, passwords, SQL, or tracebacks.                                                                                                                                             |
| 2026-07 | `GET /cases/warm`: additive `meta.canonical_categories` and `meta.legacy_category_aliases` for CLI clients; legacy category filters remain accepted and normalized.                                                                                                |
| 2026-07 | `GET /opportunities/equipment`: empty Postgres read-model note now points to `auto-refresh-chilecompra-equipment --once --apply`; CSV reload is documented as legacy/backfill only.                                                                                |
| 2026-06 | Operator responses redact filesystem paths: legacy fields are basename-only; `sqlite_path_info`, `source_path_info`, `active_current_dir_info`, and nested `path_info` carry `{ redacted, basename, kind }`. Contract audit fails on `/home/` and `/mnt/` in JSON. |
| 2026-06 | `GET /operator/automation-status`: additive `active_current_dir_info`, `path_redaction_applied`, and nested `path_info` redacted path companions; legacy absolute path fields retained for dashboard compatibility.                                                |
| 2026-06 | `X-Request-ID` middleware: header on all responses; `error.request_id` populated on errors.                                                                                                                                                                        |
| 2026-06 | Unified `error` envelope via `origenlab_api.errors` handlers; replaced FastAPI `detail` responses for HTTP 4xx/5xx, validation errors, and host allowlist 403.                                                                                                     |

---

## Client checklist

- [ ] Parse success bodies as objects; never assume a top-level array.
- [ ] For lists, read `meta.count` and `items`.
- [ ] Use `meta.canonical_categories` and `meta.legacy_category_aliases` when displaying or filtering warm cases.
- [ ] Branch on HTTP status first; then `error.code`; use `error.request_id` / `X-Request-ID` for support correlation (browser clients on allowed CORS origins can read the header via `Access-Control-Expose-Headers`).
- [ ] On `GET /operator/automation-status`, treat `ndr_pending_review` as optional additive context.
- [ ] Treat unknown JSON keys as optional (forward-compatible).
- [ ] Do not log full error bodies in production if they might contain user input; our API should not echo secrets.

---

## Tests

Contract smoke tests live in:

```bash
cd apps/api
uv run pytest tests/test_api_response_contract.py -q
```

Full suite:

```bash
uv run pytest -q
```

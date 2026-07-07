# API-3 Phase 6B — Post-removal stabilization

**Status:** Complete (2026-05). Legacy email-pipeline FastAPI **not reintroduced**.

---

## Current architecture (operator truth)

| Component                                              | Role                                                                                                                                                         |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **`apps/api`** (:8001)                                 | **Only** FastAPI app — operator Dashboard routes + Postgres **`GET /mirror/*`**                                                                              |
| **`apps/email-pipeline`**                              | Ingest, SQLite OLTP, Streamlit, sync CLI — **no** FastAPI package                                                                                            |
| **`apps/dashboard` Today**                             | Read-only operator UI — **`/health`**, `/operator/*`, `/cases/warm`, `/opportunities/equipment`, `/contacts/{email}`, `/emails/recent` — **not** `/mirror/*` |
| **`origenlab_email_pipeline/postgres_dashboard_api/`** | Shared SQL used by mirror routers in `apps/api`                                                                                                              |

---

## What changed in 6B

1. **Monorepo context** — `docs/PROJECT_CONTEXT.md` and root `README.md` list `apps/api` and `apps/dashboard`.
2. **API-3 history** — phases 1–5B moved to [`archive/api3/`](./archive/api3/README.md).
3. **Grep allowlist** — tightened to archive + dev guardrails + unrelated port-8000 tooling docs.
4. **Policy tests** — `test_mirror_phase6b_stabilization.py` locks removal + operator doc hygiene.

---

## Operator quick reference

```bash
# Terminal 1 — API (from repo root)
cd apps/api && uv sync --group dev
export ORIGENLAB_SQLITE_PATH="$HOME/data/origenlab-email/sqlite/emails.sqlite"
uv run uvicorn origenlab_api.main:app --host 127.0.0.1 --port 8001 --reload

# Terminal 2 — Dashboard
cd apps/dashboard && npm run dev -- --host 127.0.0.1

# Mirror reporting smoke (optional)
cd apps/dashboard && npm run smoke:mirror
```

Mirror curls (after Postgres sync): see email-pipeline `RUNBOOK.md` — all paths under `/mirror/*` on **:8001**.

---

## Enforcement

```bash
apps/api/scripts/api3_phase6_grep_gate.sh
```

---

## Related

- [API-3_PHASE6_LEGACY_REMOVAL_COMPLETE.md](./API-3_PHASE6_LEGACY_REMOVAL_COMPLETE.md)

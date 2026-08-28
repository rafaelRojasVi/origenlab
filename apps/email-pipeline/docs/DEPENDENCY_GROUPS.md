# Dependency groups

Status: canonical install guide  
Owner: email-pipeline-maintainers  
Last reviewed: 2026-06-07

## Purpose

Explain which `uv sync` dependency groups are needed for each workflow.

Default **`uv sync`** is intentionally small and supports **daily SQLite / document / operator tooling**. Optional groups install heavier or external-service-specific dependencies (OpenAI, Torch, pandas/xlrd, Postgres drivers, Google OAuth). **Keep optional groups out of default installs** unless the workflow you are running actually needs them.

**Phase 8F context:** OpenAI moved to **`lab`** (8F-1); HDBSCAN moved to **`ml`** (8F-2). **`streamlit`** was removed from the repo (2026-06-04); **`data-tools`** holds pandas/xlrd for tests and spreadsheet helpers.

---

## Quick commands

| Workflow | Command |
|----------|---------|
| Daily operator / SQLite / document tooling | `uv sync` |
| Gmail ingest / Workspace OAuth | `uv sync --group gmail` or `uv sync --group workspace` |
| Tatiana / research / OpenAI-backed lab tools | `uv sync --group lab` |
| ML / embeddings / HDBSCAN / FAISS / Torch | `uv sync --group ml` |
| Pandas / xlrd (read tests, draft helpers, legacy .xls) | `uv sync --group data-tools` |
| Postgres mirror / Alembic / verifiers | `uv sync --group postgres` |
| Legacy FastAPI slice in this package (historical) | `uv sync --group api --group postgres` |
| **Running the test suite** | **`./scripts/test.sh`** (see below — do not use a bare `uv run pytest`) |
| Full CI-style local test install | `uv sync --group dev --group data-tools --group postgres --group lab --frozen` |
| Full local kitchen-sink install (only when needed) | `uv sync --group dev --group data-tools --group postgres --group lab --group gmail --group ml` |

---

## Running tests: use `./scripts/test.sh`

`uv sync` **prunes** the virtualenv to exactly the groups you pass it. Syncing a
narrower set — `uv sync --group dev --group gmail`, for example — uninstalls
pandas (`data-tools`) and openai (`lab`). A later bare `uv run pytest -q` then
*under-collects*: several test modules fail at import time and the reported pass
count is smaller but still looks authoritative. This has repeatedly produced
misleading suite-count comparisons across sessions.

Always bootstrap and run through the canonical wrapper, from `apps/email-pipeline/`:

```bash
./scripts/test.sh                     # full suite
./scripts/test.sh tests/test_foo.py   # focused run
```

It calls `scripts/sync_test_env.sh` — the single source of truth for the test
group list — and then `pytest`. `scripts/validate.sh` bootstraps through the same
script, so running it can no longer prune the suite's dependencies.

`tests/test_test_environment_contract.py` enforces this: if the venv is missing a
collection-critical dependency, the suite fails with an explicit message instead
of quietly shrinking.

---

## Default install boundary

Packages in **`[project.dependencies]`** (no extra `--group` flags):

| Package | Role |
|---------|------|
| `orjson` | Fast JSON |
| `pydantic` / `pydantic-settings` | Settings and validation |
| `python-dotenv` | `.env` loading |
| `tqdm` | Progress bars |
| `pymupdf` | PDF attachment/text extraction |
| `python-docx` | Word documents |
| `openpyxl` | Excel spreadsheets |

**Not included in default sync:**

- **OpenAI** → `lab`
- **HDBSCAN** → `ml`
- **Torch** / sentence-transformers / FAISS → `ml`
- **pandas** / **xlrd** (read tests, Tatiana helpers, legacy .xls) → `data-tools`
- **Postgres** driver / Alembic → `postgres`
- **Google OAuth** (Gmail IMAP) → `gmail` / `workspace`

Daily operator commands such as `uv run origenlab status`, `refresh-safety`, and **plan-only** `uv run origenlab refresh-dashboard` work after default sync (subprocess scripts may still need their own groups at runtime — e.g. Gmail ingest needs `gmail`).

---

## Groups

### `lab`

| | |
|---|---|
| **Purpose** | Tatiana copilot, research automation, and other OpenAI-backed lab tooling |
| **Main packages** | `openai` |
| **Example** | `uv sync --group lab` · `uv run python scripts/tatiana/run_tatiana_pilot_batch.py --help` |
| **Daily operator?** | **No** — not required for outbound lanes or `origenlab` daily subcommands |

See also: [`TATIANA_LAB_BOUNDARY.md`](TATIANA_LAB_BOUNDARY.md).

### `ml`

| | |
|---|---|
| **Purpose** | Embeddings, clustering, exploratory ML scripts under `scripts/ml/` |
| **Main packages** | `torch`, `torchvision`, `torchaudio`, `sentence-transformers`, `faiss-cpu`, `scikit-learn`, `numpy`, **`hdbscan`**, `pandas` (pinned subset) |
| **Example** | `uv sync --group ml` · `uv run python scripts/ml/explore_email_clusters.py --help` |
| **Daily operator?** | **No** |

**Note:** `ml` uses the explicit PyTorch **CUDA** index (`pytorch-cu129`) in `pyproject.toml`. Avoid raw `pip install -U torch` without that index — see [`README.md`](../README.md#ml-environment-setup-wsl-project-local-venv-only).

**Security (Dependabot / CVE):** The `ml` group is optional lab-only (`uv sync --group ml`); not installed by CI or production API. Pinned trio (2026-07): `torch==2.12.1`, `torchvision==0.27.1`, `torchaudio==2.11.0` on `pytorch-cu129` remediates **CVE-2025-2999** (≥2.9.1) and **CVE-2025-3001** (≥2.10.0). **CVE-2025-3000** (`torch.jit.script`) had no `first_patched_version` in GitHub’s advisory (range `<= 2.12.0`); 2.12.1+cu129 is the current cu129 pin. ML scripts do not use `torch.jit.script`. If alert #31 remains open, dismiss as risk-accepted for optional ML or re-check when PyTorch publishes an explicit fix.

### `gmail`

| | |
|---|---|
| **Purpose** | Google Workspace Gmail IMAP ingest and OAuth helpers |
| **Main packages** | `google-auth`, `google-auth-oauthlib` |
| **Example** | `uv sync --group gmail` · `uv run origenlab gmail-ingest-help` |
| **Daily operator?** | **Only if** you run Gmail ingest (`gmail-ingest` or `scripts/ingest/05_workspace_gmail_imap_to_sqlite.py`) on this machine |

### `workspace`

| | |
|---|---|
| **Purpose** | **Back-compat alias** for `gmail` — same packages |
| **Example** | `uv sync --group workspace` |
| **Daily operator?** | Same as `gmail` |

### `postgres`

| | |
|---|---|
| **Purpose** | Postgres mirror lane: Alembic migrations, psycopg drivers, verify scripts |
| **Main packages** | `alembic`, `sqlalchemy`, `psycopg[binary]` |
| **Example** | `uv sync --group postgres` · `uv run alembic -c alembic.ini history` |
| **Daily operator?** | **No** — SQLite remains operational truth; mirror is **parked** |

See: [`EXPERIMENTAL_PARKED.md`](EXPERIMENTAL_PARKED.md).

### `api`

| | |
|---|---|
| **Purpose** | Legacy FastAPI/uvicorn stack declared inside email-pipeline (historical read API slice) |
| **Main packages** | `fastapi`, `uvicorn[standard]` |
| **Example** | `uv sync --group postgres --group api` (bootstrap notes in RUNBOOK) |
| **Daily operator?** | **No** — active operator HTTP API is **`apps/api`** on port **8001** |

### `data-tools`

| | |
|---|---|
| **Purpose** | **pandas** + **xlrd** for read-module tests, Tatiana `draft_review_helpers`, legacy `.xls` ingest |
| **Main packages** | `pandas`, `xlrd` |
| **Example** | `uv sync --group data-tools` · `uv run pytest tests/test_tatiana_draft_review_helpers.py -q` |
| **Daily operator?** | **No** — only needed when running those tests or spreadsheet helpers |

**Removed (2026-06-04):** the old **`ui`** group included **Streamlit**; no Python module in this package imports `streamlit` anymore.

### `dev`

| | |
|---|---|
| **Purpose** | Local development and **pytest** |
| **Main packages** | `pytest` + **`{ include-group = "postgres" }`** |
| **Example** | `uv sync --group dev` |
| **Daily operator?** | **No** (test tooling) |

**Note:** Because `dev` **includes** `postgres`, `uv sync --group dev` already pulls Alembic/psycopg. CI still passes **`--group postgres`** and **`--group data-tools`** explicitly for clarity alongside `dev`.

---

## Cross-package installs (monorepo)

These apps have **their own** `pyproject.toml` / `uv.lock` — do not assume `apps/email-pipeline` groups cover them:

| App | Install | Role |
|-----|---------|------|
| **`apps/api`** | `cd apps/api && uv sync` | **Active operator mirror API** on port **8001** (`GET /mirror/*`) |
| **`apps/dashboard`** | `cd apps/dashboard && npm install` | Operator React UI (read-only Today view) |

The email-pipeline **`api`** group is a **legacy FastAPI/uvicorn** slice kept for Postgres bootstrap notes in this package. Day-to-day dashboard work uses **`apps/api`**, not email-pipeline `api`.

---

## Safety

Installing a dependency group **does not** approve running mutating commands in that lane.

| Lane | Still requires explicit operator intent |
|------|----------------------------------------|
| Gmail ingest | OAuth + network; writes SQLite |
| Postgres mirror / migrate | `--apply`, `--replace`, Alembic upgrade |
| Send / purge | Break-glass scripts |
| Broad NDR apply | Documented apply paths only |

**Daily plan-only check (safe after default sync):**

```bash
uv run origenlab refresh-dashboard
```

This prints the workflow plan only — no Gmail ingest, mart rebuild, or Postgres writes unless you pass **`--apply`** separately and deliberately.

---

## Troubleshooting

| Error | Fix |
|-------|-----|
| `ModuleNotFoundError: google` | `uv sync --group gmail` |
| `ModuleNotFoundError: openai` | `uv sync --group lab` |
| `ModuleNotFoundError: hdbscan` | `uv sync --group ml` |
| `ModuleNotFoundError: pandas` | `uv sync --group data-tools` (or `--group ml` for ML scripts) |
| `ModuleNotFoundError: xlrd` | `uv sync --group data-tools` |
| `psycopg` / `alembic` import fails | `uv sync --group postgres` (or `--group dev`, which includes postgres) |

After adding groups, re-run your command with `uv run …` so the project venv is used.

---

## Related docs

- [`README.md`](../README.md) — clone setup, ML CUDA notes
- [`REPRODUCIBILITY.md`](REPRODUCIBILITY.md) — what git can and cannot reproduce
- [`TATIANA_LAB_BOUNDARY.md`](TATIANA_LAB_BOUNDARY.md) — lab vs daily outbound
- [`OPERATOR_COMMAND_SURFACE.md`](OPERATOR_COMMAND_SURFACE.md) — `origenlab` subcommands
- [`EXPERIMENTAL_PARKED.md`](EXPERIMENTAL_PARKED.md) — Postgres / API / Tatiana parked index

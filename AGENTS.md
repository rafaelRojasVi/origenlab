# OrigenLab monorepo — agent instructions

## Scope

This file applies to the **whole monorepo**. For **email-pipeline** work, read **[`apps/email-pipeline/AGENTS.md`](apps/email-pipeline/AGENTS.md)** first — it has stricter safety rules and the operator reading list.

For **public website** work, read **[`apps/web/AGENTS.md`](apps/web/AGENTS.md)**.

For **operator API** or **dashboard** work, read **[`apps/api/AGENTS.md`](apps/api/AGENTS.md)** and **[`apps/dashboard/AGENTS.md`](apps/dashboard/AGENTS.md)** (pointers only — full freeze rules in dashboard handoff).

## Factual entry

1. [`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md) — what each app is
2. App-specific `docs/APP_CONTEXT.md` and `docs/RUNBOOK.md`

## Operator stack

| Topic | Canonical doc |
|-------|----------------|
| **Canonical system truth** | [`docs/architecture/CURRENT_SYSTEM_TRUTH.md`](docs/architecture/CURRENT_SYSTEM_TRUTH.md) |
| Target commercial architecture | [`docs/architecture/TARGET_COMMERCIAL_ARCHITECTURE.md`](docs/architecture/TARGET_COMMERCIAL_ARCHITECTURE.md) |
| Monorepo architecture | [`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md) |
| Active HTTP API | [`apps/api/README.md`](apps/api/README.md) — **:8001**, operator routes + `GET /mirror/*` + `POST /operations/*` CRM commands |
| Outbound / send safety (SQLite) | [`apps/email-pipeline/docs/OUTBOUND_SOURCE_OF_TRUTH.md`](apps/email-pipeline/docs/OUTBOUND_SOURCE_OF_TRUTH.md) |

**Agents must assume:**

- **Active API** is **`apps/api` on port 8001** only. Legacy email-pipeline FastAPI on **:8000** was **removed** (API-3 Phase 6) — see [`apps/api/docs/API-3_PHASE6_LEGACY_REMOVAL_COMPLETE.md`](apps/api/docs/API-3_PHASE6_LEGACY_REMOVAL_COMPLETE.md).
- **Machine systems propose; the durable CRM records human commercial truth.** The only human write path is `POST /operations/*` (trusted operator identity, Idempotency-Key, optimistic concurrency) plus the explicit tender annex import; the dashboard has **no** send/archive/Gmail actions.
- **SQLite** remains machine operational truth for ingest, outbound safety, DNR/suppression, and send decisions.
- **Postgres** holds rebuildable machine mirrors **and** the durable human CRM (`commercial.sales_opportunity`, tasks, activities, organizations, contacts). Never treat mirror API responses as send approval; never write durable CRM state outside `/operations/*`.
- The historical v1-freeze handoff ([`apps/dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md`](apps/dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md)) describes the retired read-only era — reference only.

## Hard rules (all apps)

- Do not invent business facts (brands, specs, certifications, client names).
- Do not delete files unless the user explicitly requests it.
- Do not run destructive git operations without explicit approval.
- Prefer read-only investigation before mutating production data.

## Git and pull-request workflow

This policy intentionally preserves the colored branch-and-merge graph in Cursor and GitHub (visible topology via merge commits, not a linear squash history).

- For every **non-trivial** change, start from an up-to-date `main` and create a descriptive feature branch. Do **not** commit non-trivial work directly to `main`.
- Keep commits **logical and intentionally named** so the branch history remains understandable.
- Push the feature branch and open a GitHub pull request.
- Merge PRs with GitHub’s **“Create a merge commit”** method so branch ancestry stays visible.
- Do **not** use “Squash and merge,” “Rebase and merge,” or a fast-forward feature-branch merge unless Rafael **explicitly** requests that strategy.
- Do **not** rewrite published branch history or force-push unless Rafael **explicitly** approves it.
- After a PR is merged:

  ```bash
  git switch main
  git pull --ff-only origin main
  git branch -d <feature-branch>
  ```

- Delete the **remote** feature branch only after the merge and post-merge verification succeed. Deleting a merged branch is safe because the merge commit preserves its ancestry and visible graph.
- Explicit user instructions override this default workflow.

Cursor agents: also see [`.cursor/rules/origenlab-git-workflow.mdc`](.cursor/rules/origenlab-git-workflow.mdc) (pointer; this section is canonical for all agents).

## Email-pipeline reminder

Outbound and archive work lives under **`apps/email-pipeline/`**. Agents must **not** send email, mutate Gmail, or run Postgres migrations without explicit user approval. Read **`apps/email-pipeline/docs/EXPERIMENTAL_PARKED.md`** before Postgres/API/Tatiana/ML work; do not use **LEGACY_DO_NOT_USE** scripts for current operator tasks. Start with **`operator_status.py`** and **`reports/out/active/current/manifest.json`** when checking operational state.

# OrigenLab monorepo — agent instructions

## Scope

This file applies to the **whole monorepo** and is read by Cursor and other
AGENTS.md-aware tooling. **Claude Code sessions start at
[`CLAUDE.md`](CLAUDE.md)** — it covers architecture, responsibility
boundaries, and the durable-vs-rebuildable rule; this file does not repeat
that and stays focused on workflow rules below.

**Before anything else, know which architecture you are in.** V1 is running;
a **V2 architecture was accepted 2026-09-05** in which one Supabase project
([`supabase/`](supabase/)) replaces the V1 Postgres durable core. For V2 work,
[`docs/README.md`](docs/README.md) is canonical and the `docs/architecture/**`,
`docs/refoundation/**`, `docs/data/**` and `docs/workflows/**` trees are V1
reference with no authority over V2. **What is actually built and deployed is
in [`docs/STATUS.md`](docs/STATUS.md)** — read it first, and update it in the
same PR whenever you change what is built.

For **email-pipeline** work, read **[`apps/email-pipeline/AGENTS.md`](apps/email-pipeline/AGENTS.md)** first — it has stricter safety rules and the operator reading list.

For **public website** work, read **[`apps/web/AGENTS.md`](apps/web/AGENTS.md)**.

For **operator API** or **dashboard** work, read **[`apps/api/AGENTS.md`](apps/api/AGENTS.md)** and **[`apps/dashboard/AGENTS.md`](apps/dashboard/AGENTS.md)** (pointers only — full freeze rules in dashboard handoff).

For **`supabase/` (V2 schema) work**, read **[`docs/README.md`](docs/README.md)**, then **[`docs/DOMAIN.md`](docs/DOMAIN.md)** and **[`docs/OPERATIONS.md`](docs/OPERATIONS.md) §4** — migrations there follow the Supabase CLI, not Alembic, and every new table needs its grants, its named RLS policies and matching pgTAP assertions **in the same change**.

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

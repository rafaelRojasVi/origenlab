# Release process

Status: canonical  
Owner: project-maintainers  
Last reviewed: 2026-06-17

GitHub Releases in this repository are **deployment and changelog snapshots**, not package distribution. Nothing here is published to PyPI, npm, or a container registry as part of the release workflow today.

## What a release is

A release marks a **known-good commit** (or short series of commits) that operators or maintainers deployed or validated together. Use it to:

- record what changed since the last tag
- link to validation notes (smoke, remote audits, dashboard freeze checks)
- give future-you a rollback reference point

A release is **not** a substitute for runbooks, secrets, or operational data — those stay outside Git.

## Tag naming

Prefer **date-stamped operator tags** when the release tracks a production deploy:

```text
ops-YYYY-MM-DD
ops-YYYY-MM-DD.N   # same-day follow-up (.2, .3, …)
```

Use **semver-style app tags** only when a single app has a deliberate version bump worth citing in isolation:

```text
api-v0.3.0
dashboard-v0.3.0
```

Avoid vague tags (`latest`, `prod`, `fix`) and never reuse a tag name for a different commit.

## Release notes — include

- **Scope:** which apps changed (`api`, `dashboard`, `email-pipeline`, `web`)
- **Operator-visible changes:** new read-only routes, dashboard sections, automation status fields
- **Breaking or behavior changes:** API response shape, env var renames, cron wrapper changes
- **Validation performed:** `make check-all` before; after the deploy, `make smoke` (or run the `hosted-smoke` workflow) — every Worker route answers as it should and `/health` reports the released commit. To also read every route as yourself, sign in to the dashboard, copy the `__Host-origenlab_session` cookie value from the browser and run `OL_SMOKE_SESSION=<value> make smoke` (GET only; the session and the responses are never printed or stored)
- **Deploy notes:** Render service restarted, mirror sync verified, known follow-ups

Keep notes factual. Do not invent business claims, client names, or certification language not supported by canonical docs.

## Release notes — never attach

Do **not** upload release assets containing:

- `.env`, OAuth tokens, API keys, or Cloudflare Access secrets
- SQLite databases, Postgres dumps, or mail exports (`*.pst`, `*.mbox`, `*.jsonl`)
- `reports/out`, `reports/in`, or client deliverables
- Full mailbox snippets, customer PII, or unredacted operator paths (`/home/`, `/mnt/`)
- Ad-hoc CSV/JSON exports from production unless they are already safe, redacted, and explicitly approved for public sharing (default: **do not**)

If operational artifacts are needed for an incident, share them through private channels — not GitHub Releases.

## Suggested workflow

1. Merge to `main` with CI green for touched apps.
2. Run targeted validation (see root [`README.md`](../README.md) — **Validation and audits**).
3. Deploy from the validated commit (operator action; not automated by this doc).
4. Create an annotated tag on that commit; push the tag.
5. Open a GitHub Release from the tag; paste release notes (no sensitive attachments).
6. Update [`apps/web/docs/deployment-status.md`](../apps/web/docs/deployment-status.md) or app runbooks when external hosting state changed (label **externally verified** with date).

## Related docs

- Public-repo safety: [`SECURITY.md`](../SECURITY.md) · [`SECURITY_PUBLIC_REPO.md`](./SECURITY_PUBLIC_REPO.md)
- Documentation placement: [`docs/README.md`](./README.md#m-docmap-entry)
- Operator API remote checks: [`apps/api/README.md`](../apps/api/README.md)

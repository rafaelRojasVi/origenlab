# OrigenLab V2: production release and database migrations

STATUS: DRAFT, NOT YET ACTIVE. No production credentials or infrastructure settings are changed by this PR.

## Why

Render currently auto-deploys the worker, Gmail cron, API and dashboard independently on every main commit. This allowed application code to precede migration 50, breaking email triage. The permanent change is one release controller responsible for database compatibility and code deployment order.

## Automated path after one-time activation

1. Every main commit runs the existing Supabase database CI, even when only application files change. Failed CI does not release production.
2. Successful main/push CI triggers the release workflow. It checks the exact main SHA and refuses all releases if any of the four Render services still has independent auto-deploy enabled.
3. PostgreSQL 17 takes a fresh dump of seven OrigenLab application schemas plus the Procrastinate worker queue schema. The runner verifies the custom-format TOC, records the ledger and checksum, and encrypts the archive using an offline age public key. Only ciphertext is uploaded as a GitHub Actions artifact with 30-day retention. No artifact means no migration.
4. The single, protected migrator login applies each pending reviewed migration as owner and records its ledger entry IN THE SAME DATABASE TRANSACTION. It uses an advisory lock and verifies that the existing hosted ledger is an exact prefix of the reviewed main migration chain.
5. Render deploys the pinned SHA sequentially: worker, Gmail-sync cron, API, dashboard. Each must reach live with the expected SHA before the next begins. If one fails, downstream deploys stop. A separate read-only postflight confirms all four service SHAs, a recent Procrastinate worker heartbeat, a periodic sweep, no triage/NRD/requester evidence gaps older than 15 minutes (among parseable email in the last 14 days), and a Gmail cron run completed after the active cron deployment; failure marks the release incomplete.
6. A scheduled workflow takes the same encrypted application-schema backup every day at 06:15 UTC. Manual plan, backup-only and guarded release are also available from GitHub Actions.

Changes must remain backward-compatible during transition because old application instances can still be active while the schema is migrated.

## One-time activation required, NOT per deployment

1. Protect the main branch using the active [Protect main ruleset](https://github.com/rafaelRojasVi/origenlab/rules/17174991). **Audit found zero required approvals and zero mandatory checks as of 2026-10-08.** Require at least **one independent approving review**, dismissal of stale approvals, and a strict required check for `gitleaks` (the universal PR secret scan). The API, worker, dashboard and full Supabase test suites remain path-filtered on PRs; they are mandatory on **every main-branch production release** via the separate live CI gate. Do not require their path-filtered PR checks universally or unrelated PRs will be permanently blocked. Remove all bypass actors. The new `check_branch_protection.py` refuses to provision GitHub production secrets or release code until all those checks pass. Also require reviewed PRs and passing API, worker, dashboard and Supabase CI checks. Restrict GitHub Environment origenlab-release to main and trusted maintainers. If environment approval reviewers are configured, automatic releases will pause for approval. Unattended releases require appropriate trust in branch protection and workflow reviews.
2. Review and execute the following ONCE from an authorized Supabase postgres SQL Editor session. It gives the migrator limited access to its version ledger; runtime roles receive no new privileges.

    begin;
    grant usage on schema supabase_migrations to origenlab_migrator;
    grant select, insert on table supabase_migrations.schema_migrations to origenlab_migrator;
    commit;

   Do not grant this access to origenlab_api, origenlab_worker, PUBLIC, anon, authenticated or service_role. Do not grant ownership or BYPASSRLS. Remove any reliance on a postgres password in GitHub.
3. Generate an age identity offline on a trusted PC, outside the repository:

    umask 077
    mkdir -p ~/.config/origenlab-v2
    age-keygen -o ~/.config/origenlab-v2/production-backup-age-identity.txt

   Keep the private identity in a secure offline password vault with a second recovery copy, NEVER in GitHub or chat. Store only its printed public age1 recipient in the GitHub origenlab-release environment VARIABLE OL_PROD_BACKUP_AGE_RECIPIENT.
4. One-time setup can be performed on your trusted laptop with `gh` authenticated and `age-keygen` installed: run `bash scripts/deploy/configure_release_secrets.sh` from a reviewed checkout. It uses the verified local PEM, privately prompts for the rotated migrator password and Render API key, generates an offline age identity (never commits it), and stores release environment secrets via GitHub CLI standard input. It also offers an explicitly confirmed, scoped operation to switch all four Render services to Auto-Deploy OFF without restarting them. If declined, you can turn off each service manually in Render Settings. Safeguard and separately copy its private identity before trusting backup recoverability.

   Alternatively configure GitHub origenlab-release ENVIRONMENT SECRETS manually: OL_PROD_POOLER_HOST (copy from Supabase Connect, session pooler port 5432), OL_PROD_PROJECT_REF, OL_PROD_CA_PEM (official root certificate PEM), OL_PROD_MIGRATOR_PASSWORD (rotate if previously exposed), RENDER_API_KEY (protected Render API key). Never store the admin postgres password there. Restrict environment secret access to reviewed main code.
5. Either accept the setup helper's explicit Render auto-deploy change or, in Render Settings, turn Auto-Deploy OFF (not suspend, not stop) on all four: origenlab-mail-triage, origenlab-gmail-sync, origenlab API, origenlab-dashboard. This is the critical sequencing guarantee. The new workflow refuses deployment if even one remains on commit/checksPass autodeploy. Render's API does **not** accept an explicit `commitId` when deploying cron jobs; the controller checks GitHub main immediately before the cron trigger and verifies the returned commit, refusing a mismatch. If main advances at the exact trigger moment the cron update might still start at the newer commit; avoid merging new changes mid-release and treat this residual Render limitation as an operational hazard.
6. After CI passes, merge the reviewed PR. In GitHub Actions, run production-db-migrations mode plan (must find the schema/ledger current at 50), then backup_only. Download and decrypt the age archive offline. Rehearse a restore into a disposable PostgreSQL 17 environment and validate representative data. Only then trust the automatic production-release path.

No computer is needed for later merges, unless production repair is required.

## Backups and recovery honesty

This encrypted artifact is an independent application-schema logical backup, NOT a complete Supabase backup. It includes the Procrastinate job queue state (after restore, stale worker leases still require operational review) but excludes Supabase Storage objects and platform-managed schemas. A readable TOC is not a tested restore. Daily 30-day GitHub artifact retention is not permanent retention. Keep a separate long-term offsite encrypted archive, Storage backup and real recovery drill.

The existing operations policy requires Supabase Pro daily platform backups and a 24-hour RPO; verify actual plan and backup status. This PR does not provision or charge a Supabase plan. Missing platform backups must be recorded as an outstanding risk.

Migrations roll forward, never rewind. If the ledger differs from the reviewed main prefix, the job refuses to auto-fix it. A failed SQL file rolls back its ledger insert. A failed Render deployment prevents subsequent services from deploying; review worker queue, cron, API, UI and send controls separately before declaring full business recovery.

## Entry points

Workflow: .github/workflows/production-db-migrations.yml
Database target: supabase/scripts/hosted_env.py
Backup: supabase/scripts/hosted_backup.py
Atomic migrator: supabase/scripts/hosted_migrations.py
Render controller: scripts/deploy/render_release.py
Failure tests: supabase/scripts/tests/test_hosted_release.py
One-time secrets setup and optional Render auto-deploy switch: scripts/deploy/configure_release_secrets.sh and scripts/deploy/set_render_autodeploy_off.py
Branch-protection gate: scripts/deploy/check_branch_protection.py
Postrelease queue and cron verification: scripts/deploy/verify_workers.py
Original policy: docs/OPERATIONS.md sections 3-4

Important: the `origenlab-release` GitHub Environment is separate from the existing `production` Environment used by web-deploy. Never weaken existing website deployment approvals. The setup helper creates/reconciles the release Environment with protected-branches-only policy; require main branch protection before unattended runs. This dedicated release Environment must NOT require per-release reviewers, or automatic releases will wait for approval.

## Release-engineering audit limits

The offline contract tests check CI selection, main branch protection, migration file ordering and guards, Render deployment payloads and fail-closed behaviors. A successful Render `live` deployment proves build/deploy status only, not successful Gmail capture, job queue processing, API `/health` or CRM login. The new postflight verifies queue heartbeat/sweep/evidence-age and cron success, but not end-to-end mail ingestion or API authorization; after the first activation, verify user-facing routes against real production, and add authenticated end-to-end smoke alerts before declaring the system fully self-healing. Data migrations must stay backward-compatible with prior worker/API versions. Deployment retry is safe only after rechecking the pinned SHA and schema ledger. A partial rollout can leave earlier services at new code and later services at old code; never auto-rollback the schema.

# OrigenLab V2: production release and database migrations

STATUS: DRAFT, NOT YET ACTIVE. No production credentials or infrastructure settings are changed by this PR.

## Why

Render currently auto-deploys the worker, Gmail cron, API and dashboard independently on every main commit. This allowed application code to precede migration 50, breaking email triage. The permanent change is one release controller responsible for database compatibility and code deployment order.

## Automated path after one-time activation

1. Every main commit runs the existing Supabase database CI, even when only application files change. Failed CI does not release production.
2. Successful main/push CI triggers the release workflow. It checks the exact main SHA and refuses all releases if any of the four Render services still has independent auto-deploy enabled.
3. PostgreSQL 17 takes a fresh dump of seven OrigenLab application schemas. The runner verifies the custom-format TOC, records the ledger and checksum, and encrypts the archive using an offline age public key. Only ciphertext is uploaded as a GitHub Actions artifact with 30-day retention. No artifact means no migration.
4. The single, protected migrator login applies each pending reviewed migration as owner and records its ledger entry IN THE SAME DATABASE TRANSACTION. It uses an advisory lock and verifies that the existing hosted ledger is an exact prefix of the reviewed main migration chain.
5. Render deploys the pinned SHA sequentially: worker, Gmail-sync cron, API, dashboard. Each must reach live with the expected SHA before the next begins. If one fails, downstream deploys stop.
6. A scheduled workflow takes the same encrypted application-schema backup every day at 06:15 UTC. Manual plan, backup-only and guarded release are also available from GitHub Actions.

Changes must remain backward-compatible during transition because old application instances can still be active while the schema is migrated.

## One-time activation required, NOT per deployment

1. Protect the main branch: require reviewed PRs and passing API, worker, dashboard and Supabase CI checks. Restrict GitHub Environment production to main and trusted maintainers. If environment approval reviewers are configured, automatic releases will pause for approval. Unattended releases require appropriate trust in branch protection and workflow reviews.
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

   Keep the private identity in a secure offline password vault with a second recovery copy, NEVER in GitHub or chat. Store only its printed public age1 recipient in the GitHub production environment VARIABLE OL_PROD_BACKUP_AGE_RECIPIENT.
4. One-time setup can be performed on your trusted laptop with `gh` authenticated and `age-keygen` installed: run `bash scripts/deploy/configure_release_secrets.sh` from a reviewed checkout. It uses the verified local PEM, privately prompts for the rotated migrator password and Render API key, generates an offline age identity (never commits it), and stores production environment secrets via GitHub CLI standard input. Safeguard and separately copy its private identity before trusting backup recoverability.

   Alternatively configure GitHub production ENVIRONMENT SECRETS manually: OL_PROD_POOLER_HOST (copy from Supabase Connect, session pooler port 5432), OL_PROD_PROJECT_REF, OL_PROD_CA_PEM (official root certificate PEM), OL_PROD_MIGRATOR_PASSWORD (rotate if previously exposed), RENDER_API_KEY (protected Render API key). Never store the admin postgres password there. Restrict environment secret access to reviewed main code.
5. In Render Settings, turn Auto-Deploy OFF (not suspend, not stop) on all four: origenlab-mail-triage, origenlab-gmail-sync, origenlab API, origenlab-dashboard. This is the critical sequencing guarantee. The new workflow refuses deployment if even one remains on commit/checksPass autodeploy.
6. After CI passes, merge the reviewed PR. In GitHub Actions, run production-db-migrations mode plan (must find the schema/ledger current at 50), then backup_only. Download and decrypt the age archive offline. Rehearse a restore into a disposable PostgreSQL 17 environment and validate representative data. Only then trust the automatic production-release path.

No computer is needed for later merges, unless production repair is required.

## Backups and recovery honesty

This encrypted artifact is an independent application-schema logical backup, NOT a complete Supabase backup. It excludes Supabase Storage objects and platform-managed schemas. A readable TOC is not a tested restore. Daily 30-day GitHub artifact retention is not permanent retention. Keep a separate long-term offsite encrypted archive, Storage backup and real recovery drill.

The existing operations policy requires Supabase Pro daily platform backups and a 24-hour RPO; verify actual plan and backup status. This PR does not provision or charge a Supabase plan. Missing platform backups must be recorded as an outstanding risk.

Migrations roll forward, never rewind. If the ledger differs from the reviewed main prefix, the job refuses to auto-fix it. A failed SQL file rolls back its ledger insert. A failed Render deployment prevents subsequent services from deploying; review worker queue, cron, API, UI and send controls separately before declaring full business recovery.

## Entry points

Workflow: .github/workflows/production-db-migrations.yml
Database target: supabase/scripts/hosted_env.py
Backup: supabase/scripts/hosted_backup.py
Atomic migrator: supabase/scripts/hosted_migrations.py
Render controller: scripts/deploy/render_release.py
Failure tests: supabase/scripts/tests/test_hosted_release.py
One-time secrets setup: scripts/deploy/configure_release_secrets.sh
Original policy: docs/OPERATIONS.md sections 3-4

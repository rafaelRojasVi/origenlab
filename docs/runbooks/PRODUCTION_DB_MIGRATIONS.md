# Hosted production migrations — controlled GitHub Actions

**Workflow:** `.github/workflows/production-db-migrations.yml`. It is **manual only** (`workflow_dispatch`), accepts **only `main`**, and uses the protected GitHub environment `production`. It does not deploy code, restart Render, toggle auto-mail rules, send mail, or change any runtime role grants.

## One-time environment configuration

The repo administrator must first configure `production` with required reviewers and these **environment-scoped secrets** (never repository variables, chat messages, workflow inputs or tracked files):

| Secret | Purpose |
|---|---|
| `OL_PROD_POOLER_HOST` | Exact Supabase session-pooler hostname from the project's Connect dialog (`aws-N-sa-east-1.pooler.supabase.com`, **port 5432**) |
| `OL_PROD_PROJECT_REF` | The production Supabase project reference; both logins are tenant-bound to it |
| `OL_PROD_CA_PEM` | Official Supabase production root CA PEM, preserved byte-for-byte from the verified certificate |
| `OL_PROD_MIGRATOR_PASSWORD` | Password for `origenlab_migrator.<project-ref>`; no other role can assume `origenlab_owner` |
| `OL_PROD_LEDGER_PASSWORD` | Password for `postgres.<project-ref>`, restricted to the protected production environment; **only** for Supabase-owned migration-ledger maintenance |

Rotate passwords that previously passed through chats; keep current credentials in the password manager. The runner does not echo passwords or print connection URLs. **Only enable after the environment has explicit review approval and its branch protection is verified.** Anyone able to alter a workflow on `main` plus approve production can potentially use these powerful secrets.

## Run

1. Obtain and **restore-check a fresh production backup**, stored outside Actions and outside the repository. Record a change approval. Confirm no other operator is applying migrations.
2. In GitHub Actions, select **production-db-migrations → Run workflow**, branch **main**, `apply=false`, `backup_attested=false`. Approval unlocks a read-only plan that checks both login identities, TLS `verify-full`, and that the hosted ledger is exactly a prefix of the committed migration chain.
3. Review the pending file names and SQL on `main`; confirm compatibility before authorizing deployment. For applying, repeat the workflow on **main** with `apply=true`, `backup_attested=true` after production environment approval.
4. The action runs each file as `origenlab_migrator` using `psql --single-transaction -v ON_ERROR_STOP=1`, then records the version and name as the separate `postgres` ledger login. Each version is verified before moving to the next. DDL failure rolls back that file; a ledger-write failure **stops immediately** for operator reconciliation.
5. Run another `apply=false` plan; it must report zero pending. Run the existing hosted security checks, validate application health, and **only then** deploy/redeploy the worker, API and dashboard in that order.

**Limitations:** Because Supabase owns `supabase_migrations.schema_migrations`, the restricted migrator cannot commit its DDL and the ledger entry in one database transaction. A failure between those steps requires manual inspection and repair: **do not blindly retry**. This workflow intentionally never falls back to a privileged application role or weaker TLS. Some future migrations may require temporary administrator grants, which this action must refuse instead of automatically widening privileges.

**Important deployment gap:** Render currently auto-deploys the worker/API from `main`, independently of this action. A GitHub migration action alone cannot guarantee migrations-before-code. **Disable automatic Render deploy for schema-dependent services and adopt a staged release gate before treating this as the complete production release mechanism.** Do not merge schema-dependent application code and assume this workflow ran. No Render service settings are changed by this PR.

For existing ownership, migration and verification policy, see [OPERATIONS.md](../OPERATIONS.md) §§3–4.

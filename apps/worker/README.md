# apps/worker — the V2 worker

Python 3.12, uv. Two jobs today. **Gmail capture** (Phase 4a): new mail in contacto@origenlab.cl →
`comms.message` (+ participants, attachment metadata, the `.eml` in the private `mail` bucket) and
`pending` `gmail_message` evidence in the hosted V2 database, every 10 minutes as a Render Cron Job.
It writes as `origenlab_worker` only and reads Gmail with `gmail.readonly` only. Direct table writes
outside the worker's own evidence/comms lanes remain limited to `INSERT` on
`outbound.campaign_reply`, the designed 4c reply-proposal lane. The worker may additionally EXECUTE
exactly one closed-list privileged function, `outbound.add_contact_control`; that function accepts
the worker only for a proven single-recipient Batch-A no-such-user bounce and writes the global
`invalid_address` block itself. On every connect the worker refuses any role membership, a port other
than 5432 (6543 by name), any other `crm`/`outbound` write (column-level grants, `TRIGGER`, view and
materialized-view writes included), or any other executable `SECURITY DEFINER` function.

    origenlab-worker gmail-sync [--init] [--dry-run]

History mode also reads `labelAdded`: a draft that is sent later, and a message moved out of spam, are
captured. Label removals and other label changes are not tracked.

Exit 0 done or nothing to do · 1 failed · 2 Gmail authorization · 3 configuration. Setup, pause,
rotation and recovery: [`docs/OPERATIONS.md`](../../docs/OPERATIONS.md) §8. State:
[`docs/STATUS.md`](../../docs/STATUS.md) §2.7.49.

**Drive filing**, right after each capture in the same cron:

    origenlab-worker drive-file [--dry-run] [--limit N]
    origenlab-worker drive-ledger-import <archive_links.jsonl>... [--dry-run]   # owner, once

Every quote revision recorded from a captured email gets its PDF put in its case's folder under
`Cotizaciones/Casos` in contacto@'s Drive (the case archive's rules, `origenlab_api.v2.quote_case_archive`;
the Drive port is `drive_client.py`, plain HTTPS, exactly the `drive` scope) and an
`evidence.source_record` of kind `drive_file` saying where — read by the API for the case card. No
`crm.*` row, no rename, move or delete in Drive. Paused until `ORIGENLAB_WORKER_DRIVE_FILING_ENABLED=true`;
setup in [`docs/OPERATIONS.md`](../../docs/OPERATIONS.md) §8.10.

**Mail triage**, a separate Render Background Worker (`origenlab-mail-triage`):

    origenlab-worker triage-worker                                   # Procrastinate, queue `triage`
    origenlab-worker triage-once [--since-days N] [--limit N] [--dry-run]   # one pass, no queue

Every minute a periodic sweep defers one job per captured message without a reading. Each job
reads the `.eml` from Storage, settles machine mail with cheap rules (`triage_rules.py`), matches
catalog products (`catalog_match.py`), asks Claude only when a person wrote something commercial
(`triage_model.py`), and records `message_triage` / `product_mention` `evidence.assertion`
proposals. A bounce also gets a versioned `delivery_failure` assertion; only an explicit single-recipient
Batch-A no-such-user result may invoke the global hard-bounce block. The queue lives in the `procrastinate` schema of the V2 database
(`supabase/migrations/20261006180000`); Procrastinate is pinned to the vendored version. Setup,
pause and log codes: [`docs/OPERATIONS.md`](../../docs/OPERATIONS.md) §8.11.

V1 reuse: `src/origenlab_worker/v1_reuse.py` is the only import of `origenlab_email_pipeline`;
before slice 8 deletes V1, those functions move here and only that file changes. The build pulls
email-pipeline's OCR stack (about 650 MB) that the worker never imports at run time. The case
archive comes from `apps/api` (a uv path dependency, so the cron also installs the API's packages).

## Tests

    ./scripts/validate.sh                                  # database tests skip
    S=$(mktemp -d)
    ../api/scripts/disposable_test_cluster.sh up "$S/db.env"
    set -a; . "$S/db.env"; set +a
    uv run --frozen pytest tests -q -rs                    # no ORIGENLAB_V2_*TEST_DSN skip
    ../api/scripts/disposable_test_cluster.sh down "$OL_TEST_CLUSTER"; rm -rf "$S"

Owner scripts (`scripts/`): `gmail_readonly_authorize.py` (consent, once), `payload_parity.py`
(before go-live), `shadow_reconcile.py` (shadow week).

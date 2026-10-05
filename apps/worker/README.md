# apps/worker — the V2 worker

Python 3.12, uv. One job today, **Gmail capture** (Phase 4a): new mail in contacto@origenlab.cl →
`comms.message` (+ participants, attachment metadata, the `.eml` in the private `mail` bucket) and
`pending` `gmail_message` evidence in the hosted V2 database, every 10 minutes as a Render Cron Job.
It writes as `origenlab_worker` only and reads Gmail with `gmail.readonly` only. It writes nothing in
`crm.*` or `outbound.*` except `INSERT` on `outbound.campaign_reply`, the designed 4c reply-proposal
lane (`supabase/migrations/20260908120100_slice0_outbound_campaign_reply.sql`), which 4a never writes.
On every connect the worker refuses a session with any role membership, a port other than 5432
(6543 by name), any other `crm`/`outbound` write (column-level grants, `TRIGGER`, view and
materialized-view writes included), or an executable `SECURITY DEFINER` function (extension-owned and
trigger functions and schemas without `USAGE` are not counted; nothing is skipped by schema name).

    origenlab-worker gmail-sync [--init] [--dry-run]

Exit 0 done or nothing to do · 1 failed · 2 Gmail authorization · 3 configuration. Setup, pause,
rotation and recovery: [`docs/OPERATIONS.md`](../../docs/OPERATIONS.md) §8. State:
[`docs/STATUS.md`](../../docs/STATUS.md) §2.7.49.

V1 reuse: `src/origenlab_worker/v1_reuse.py` is the only import of `origenlab_email_pipeline`;
before slice 8 deletes V1, those functions move here and only that file changes. The build pulls
email-pipeline's OCR stack (about 650 MB) that the worker never imports at run time.

## Tests

    ./scripts/validate.sh                                  # database tests skip
    S=$(mktemp -d)
    ../api/scripts/disposable_test_cluster.sh up "$S/db.env"
    set -a; . "$S/db.env"; set +a
    uv run --frozen pytest tests -q -rs                    # no ORIGENLAB_V2_*TEST_DSN skip
    ../api/scripts/disposable_test_cluster.sh down "$OL_TEST_CLUSTER"; rm -rf "$S"

Owner scripts (`scripts/`): `gmail_readonly_authorize.py` (consent, once), `payload_parity.py`
(before go-live), `shadow_reconcile.py` (shadow week).

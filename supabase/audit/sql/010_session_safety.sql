-- s01 — session safety facts (docs/OPERATIONS.md §4.2).
--
-- The audit's own read-only boundary, read back from the server rather than assumed. The engine
-- opens exactly one `begin read only` transaction and sets `default_transaction_read_only=on`
-- through the libpq connection options; this file proves both actually took effect, and records
-- the identity pair the rest of the run executes under.
--
-- Read-only: one SELECT, no mutation, no session change. See supabase/audit/olaudit/sqlbank.py.
select jsonb_build_object(
  'check', 's01',
  'data', jsonb_build_object(
    'transaction_read_only',               current_setting('transaction_read_only'),
    'default_transaction_read_only',       current_setting('default_transaction_read_only'),
    'session_user',                        session_user::text,
    'current_user',                        current_user::text,
    'current_database',                    current_database()::text,
    'is_superuser',                        current_setting('is_superuser'),
    'statement_timeout',                   current_setting('statement_timeout'),
    'idle_in_transaction_session_timeout', current_setting('idle_in_transaction_session_timeout'),
    'server_version_num',                  current_setting('server_version_num'),
    'server_version',                      current_setting('server_version')
  )
)::text;

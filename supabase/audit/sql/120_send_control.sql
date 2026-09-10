-- a11 — WORKFLOWS.md §1.6 / OPERATIONS.md §5: outbound.send_control is one row and both flags are
-- false. Slice 0's gate says "both send flags false" (MIGRATION.md §5).
--
-- Read-only: one SELECT, no mutation, no session change.
select jsonb_build_object(
  'check', 'a11',
  'data', jsonb_build_object(
    'row_count', (select count(*) from outbound.send_control),
    'rows', coalesce((select jsonb_agg(jsonb_build_object(
                                'id',                    sc.id,
                                'marketing_enabled',     sc.marketing_enabled,
                                'transactional_enabled', sc.transactional_enabled)
                              order by sc.id)
                        from outbound.send_control sc), '[]'::jsonb),
    'any_flag_true', coalesce((select bool_or(sc.marketing_enabled or sc.transactional_enabled)
                                 from outbound.send_control sc), false)
  )
)::text;

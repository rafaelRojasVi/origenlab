-- Slice 0 / M14 — revoke the database-level CREATE privilege from origenlab_owner.
--
-- M01 granted `CREATE ON DATABASE` to origenlab_owner for one purpose: the owner creates the seven
-- application schemas. That is the only statement in Slice 0 that needs it. Creating a table,
-- index, function, type, trigger or policy inside an existing schema needs CREATE on the *schema*,
-- which origenlab_owner holds by owning the schema — not CREATE on the database. Left in place the
-- privilege would be a permanent, undocumented licence for the owner to add a schema outside the
-- reviewed seven, so it is withdrawn here, immediately after the schemas exist.
--
-- Executed by the connection role (locally `postgres`, the database owner). origenlab_owner cannot
-- revoke a privilege granted to it by the database owner, so this migration deliberately does not
-- `set role origenlab_owner`.
--
-- How a future, deliberately approved schema addition regains it: that migration re-grants the
-- privilege in its own first statement, creates the schema under `set role origenlab_owner`, and
-- revokes it again in its last statement — the same open/close shape as M01 plus this file, inside
-- one reviewed change. It is never re-granted permanently, and the pgTAP assertion in
-- supabase/tests/030_grant_boundary.sql fails if a migration leaves it granted.
--
-- docs/ARCHITECTURE.md §6.4; docs/OPERATIONS.md §4.1; docs/MIGRATION.md §5.2 check 3.

do $$
begin
  execute format('revoke create on database %I from origenlab_owner', current_database());
end
$$;

-- Fail closed: the privilege must be gone, and CONNECT must remain.
do $$
begin
  if has_database_privilege('origenlab_owner', current_database(), 'CREATE') then
    raise exception 'origenlab_owner still holds CREATE on database %', current_database();
  end if;
  if not has_database_privilege('origenlab_owner', current_database(), 'CONNECT') then
    raise exception 'origenlab_owner lost CONNECT on database %', current_database();
  end if;
end
$$;

-- Slice 7 — «people first»: a person confirms a learned name (DOMAIN.md §7, #69).
--
-- The contact page's «Confirmar» (`POST /v2/commands/confirm-person-fact`) turns a machine-proposed
-- name into a person's: the API sets crm.person_name.confirmation = 'confirmed'. The column-level
-- UPDATE of 20261029100000 covered only the soft-remove (removed_at, removed_by_operator_id); this
-- adds the one column the confirm writes. No table, policy, function or foreign key changes — the
-- existing origenlab_api update policy already covers the row.

set role origenlab_owner;

grant update (confirmation) on crm.person_name to origenlab_api;

reset role;

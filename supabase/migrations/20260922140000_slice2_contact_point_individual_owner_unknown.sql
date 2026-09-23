-- Slice 2 — a named mailbox whose owner nobody has recorded is not a shared mailbox.
--
-- docs/DOMAIN.md §2.5 (contact point — the channel, and only the channel).
--
-- Additive only: one new `usage` value with its own shape rule. No existing row changes, no
-- existing value loses its meaning, no grant widens and no RLS policy relaxes.
--
-- ── Why the vocabulary was short by one ────────────────────────────────────────────────────
--
-- §2.5 gave four shapes. Three of them need something the evidence review queue does not
-- have: `personal` and `work` need a `crm.person`, and no command creates one; `unattributed`
-- needs BOTH person and organization to be null, so it cannot hold the one fact an operator
-- attributing a sender does know — which institution operates the address.
--
-- That left exactly one usable value for "attach this address to this institution":
-- `shared_mailbox`. But `shared_mailbox` is a claim about the world — *two people read this
-- desk* — and `jperez@` is evidence against it. The boundary was therefore making every
-- operator assert something false about a named address in order to record something true
-- about its institution, and the assertion would then be read back by everything downstream
-- as the CRM's own belief.
--
-- ── What the new value claims, and what it refuses to claim ────────────────────────────────
--
-- `individual_owner_unknown`: this institution operates this address, and it belongs to one
-- individual whom nobody has recorded yet. Its shape is `person_id IS NULL AND
-- organization_id IS NOT NULL` — the same row shape as `shared_mailbox`, and a different
-- statement about who is on the other end.
--
-- It creates no person and names nobody. It is the truthful neutral state: the institution
-- is recorded because it is known, and the person is absent because they are not. When a
-- person is eventually recorded, the row moves to `work` — which is a promotion of knowledge,
-- not a correction of a false claim.

set role origenlab_owner;

alter table crm.contact_point
  drop constraint contact_point_usage_check;

alter table crm.contact_point
  add constraint contact_point_usage_check check (
    usage in ('personal', 'work', 'shared_mailbox', 'individual_owner_unknown', 'unattributed')
  );

alter table crm.contact_point
  drop constraint contact_point_usage_shape;

alter table crm.contact_point
  add constraint contact_point_usage_shape check (
    case usage
      when 'personal'                 then person_id is not null and organization_id is null
      when 'work'                     then person_id is not null and organization_id is not null
      when 'shared_mailbox'           then person_id is null
      when 'individual_owner_unknown' then person_id is null and organization_id is not null
      when 'unattributed'             then person_id is null and organization_id is null
    end
  );

comment on column crm.contact_point.usage is
  'DOMAIN.md §2.5. shared_mailbox asserts a desk two or more people read; individual_owner_unknown asserts the opposite — one individual owns this address and nobody has recorded who — while still recording the institution that operates it. Neither creates a person. unattributed claims nothing at all, including nothing about the institution.';

reset role;

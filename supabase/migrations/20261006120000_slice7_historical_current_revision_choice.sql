-- Slice 7 — an operator chooses which historical revision of a quote is the current one.
--
-- docs/DOMAIN.md §7 #13; docs/WORKFLOWS.md §1.2. API: `resolve_current_revision`
-- (`apps/api/src/origenlab_api/v2/current_revision.py`).
--
-- A case card says «Hay más de una revisión vigente» (`canonical_undetermined`) when a quote has
-- two or more revisions that are neither void nor superseded. Every one of those is a
-- `historical_import` revision: the historical import numbers revisions in the order it recorded
-- the documents, not the order they were sent, and records a supersession only when the owner
-- named one. The operator settles it by naming the revision that is current; every other current
-- revision of that quote is then superseded *by the chosen one*.
--
-- `quote_revision_supersession_forward` refuses that whenever the chosen revision carries the
-- lower number — and for a historical revision the number says nothing about which came later.
-- This migration:
--
-- 1. **Relaxes the forward rule for `historical_import` only.** A historical revision may be
--    superseded by any other revision of the same quote (the foreign key already says "the same
--    quote"), never by itself. Every V2-authored revision keeps the forward rule unchanged.
--
-- 2. **Replaces `crm.quote_revision_historical_guard` in place** with one added rule, which is
--    what the forward rule used to guarantee for free: a historical revision is superseded only by
--    a revision that is itself current — `sent` or `approved`, not superseded. A supersession chain
--    therefore always ends at a current revision and can never close into a cycle. The superseding
--    row is read `for share`, so two transactions cannot each supersede the other's revision.
--
-- No table, column, grant, policy or event type changes; no function added (the guard is
-- replaced). `quote_revision.superseded` already records the act.
set role origenlab_owner;

alter table crm.quote_revision drop constraint quote_revision_supersession_forward;
alter table crm.quote_revision add constraint quote_revision_supersession_forward check (
  superseded_by_revision_no is null
  or superseded_by_revision_no > revision_no
  or (origin = 'historical_import' and superseded_by_revision_no <> revision_no)
);

comment on constraint quote_revision_supersession_forward on crm.quote_revision is
  'A V2 revision is superseded only by a later one. A historical_import revision may be superseded by any other revision of its quote (its number is import order, not send order); crm.quote_revision_historical_guard requires that revision to be current.';

create or replace function crm.quote_revision_historical_guard() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  superseding record;
begin
  if tg_op = 'DELETE' then
    if old.origin = 'historical_import' then
      raise exception 'crm.quote_revision %: a historical revision is never deleted — void it instead', old.id
        using errcode = 'P0001';
    end if;
    return old;
  end if;

  if old.origin is distinct from new.origin then
    raise exception 'crm.quote_revision %: origin is fixed at insert', old.id using errcode = 'P0001';
  end if;
  if old.origin <> 'historical_import' then
    return new;
  end if;

  if (new.quote_id, new.revision_no, new.pdf_sha256, new.sent_at, new.origin_source_record_id,
      new.quote_currency, new.price_decimals, new.subtotal, new.discount_total, new.tax_base,
      new.tax_total, new.grand_total, new.party_snapshot, new.party_snapshot_version,
      new.created_by_operator_id, new.created_at)
     is distinct from
     (old.quote_id, old.revision_no, old.pdf_sha256, old.sent_at, old.origin_source_record_id,
      old.quote_currency, old.price_decimals, old.subtotal, old.discount_total, old.tax_base,
      old.tax_total, old.grand_total, old.party_snapshot, old.party_snapshot_version,
      old.created_by_operator_id, old.created_at)
  then
    raise exception 'crm.quote_revision %: a historical revision records a document and is immutable', old.id
      using errcode = 'P0001';
  end if;
  if old.superseded_by_revision_no is not null
     and new.superseded_by_revision_no is distinct from old.superseded_by_revision_no then
    raise exception 'crm.quote_revision %: supersession is recorded once', old.id using errcode = 'P0001';
  end if;
  if old.superseded_by_revision_no is null and new.superseded_by_revision_no is not null then
    select r.status, r.superseded_by_revision_no
      into superseding
      from crm.quote_revision r
     where r.quote_id = new.quote_id and r.revision_no = new.superseded_by_revision_no
       for share;
    if found and (superseding.status not in ('approved', 'sent')
                  or superseding.superseded_by_revision_no is not null) then
      raise exception 'crm.quote_revision %: superseded only by a current revision; revision % is %',
        old.id, new.superseded_by_revision_no,
        case when superseding.superseded_by_revision_no is not null then 'superseded'
             else superseding.status end
        using errcode = 'P0001';
    end if;
  end if;
  if new.status <> old.status and not (old.status = 'sent' and new.status = 'void') then
    raise exception 'crm.quote_revision %: a historical revision moves only sent → void, not % → %',
      old.id, old.status, new.status
      using errcode = 'P0001';
  end if;
  return new;
end
$$;

comment on function crm.quote_revision_historical_guard() is
  'A historical_import revision is immutable except supersession (once, and only by a current revision) and sent → void; never deleted. SECURITY INVOKER.';

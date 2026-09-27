-- Slice 5 — an archived campaign is history: nothing edits, reopens, extends or deletes it.
--
-- docs/DOMAIN.md §7 #20; docs/WORKFLOWS.md §W4 (the campaign lifecycle).
--
-- Every V1 campaign is imported as `archived` (docs/DATA.md §7.6): it predates approval, frozen
-- content and audience criteria, and the table's shape constraints carve it out of all three for
-- exactly that reason. Until now nothing stopped a later statement from rewriting one — flipping
-- it back to `draft`, renaming it, changing a recipient's state or an attempt's outcome, or
-- adding recipients to it — and the CRM Marketing section shows these rows as the real send
-- history. This migration makes that history immutable in the database, not only in the API:
--
--   1. outbound.campaign: a row whose status is `archived` is never updated or deleted. A row is
--      born `archived` only by the historical importer, which runs as the owner.
--   2. outbound.campaign_recipient and outbound.send_attempt: a row of an archived campaign is
--      never updated or deleted, and a new one is added only by the importer (the owner) — the
--      importer inserts the campaign first and then its audience and attempts.
--
-- What it deliberately leaves open: outbound.campaign_reply stays insertable, because a reply to
-- a historical campaign that arrives (or is synchronized) later is new evidence about it, not an
-- edit of it. No grant, policy or send flag moves.

set role origenlab_owner;

create function outbound.archived_campaign_immutable() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  v_campaign_id uuid;
  v_status text;
begin
  if tg_table_name = 'campaign' then
    if tg_op = 'INSERT' then
      if new.status = 'archived' and current_user <> 'origenlab_owner' then
        raise exception 'a campaign is born archived only by the historical importer'
          using errcode = 'P0001';
      end if;
      return new;
    end if;
    if old.status = 'archived' then
      raise exception 'campaign % is archived: its history is immutable (no edit, reopen or delete)', old.id
        using errcode = 'P0001';
    end if;
    return case when tg_op = 'DELETE' then old else new end;
  end if;

  -- campaign_recipient / send_attempt: the parent campaign decides. A transactional attempt has
  -- no campaign and is not history of one.
  v_campaign_id := case when tg_op = 'INSERT' then new.campaign_id else old.campaign_id end;
  if v_campaign_id is not null then
    select c.status into v_status from outbound.campaign c where c.id = v_campaign_id;
    if v_status = 'archived' then
      if tg_op <> 'INSERT' then
        raise exception '% of archived campaign % is immutable', tg_table_name, v_campaign_id
          using errcode = 'P0001';
      end if;
      if current_user <> 'origenlab_owner' then
        raise exception 'archived campaign % takes no new %; only the historical importer adds them',
          v_campaign_id, tg_table_name
          using errcode = 'P0001';
      end if;
    end if;
  end if;
  return case when tg_op = 'DELETE' then old else new end;
end;
$$;

comment on function outbound.archived_campaign_immutable() is
  'Refuses any UPDATE or DELETE of an archived campaign, its recipients and its send attempts, and any INSERT into one unless the historical importer (origenlab_owner) is writing it. Replies stay insertable. SECURITY INVOKER.';

create trigger campaign_archived_immutable
  before insert or update or delete on outbound.campaign
  for each row execute function outbound.archived_campaign_immutable();

create trigger campaign_recipient_archived_immutable
  before insert or update or delete on outbound.campaign_recipient
  for each row execute function outbound.archived_campaign_immutable();

create trigger send_attempt_archived_immutable
  before insert or update or delete on outbound.send_attempt
  for each row execute function outbound.archived_campaign_immutable();

reset role;

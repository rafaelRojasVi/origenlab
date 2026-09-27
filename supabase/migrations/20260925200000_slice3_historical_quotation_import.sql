-- Slice 3 — historical quotations: a quotation that was already sent before V2 existed.
--
-- docs/DOMAIN.md §3.5, §7 #12–#13; docs/DATA.md §8; owner gates G1–G5/2026-09-25.1.
--
-- Slice 0 shaped crm.quote and crm.quote_revision for quotations V2 itself authors: a number
-- V2 minted, a revision that is priced, approved and then sent through V2's own send path.
-- None of that is true of the 138 quotations the owner has confirmed from the sent mailbox.
-- Their numbers were typed by hand into a PDF, their totals were never parsed, nobody
-- recorded an approval, and the message that carried them is a Gmail message V2 never sent.
--
-- Recording them under the authored rules would mean inventing a total, an approver and a
-- send attempt. Refusing them would leave the CRM blind to its own history. This migration
-- adds a second, explicit origin instead, with its own shape:
--
--   1. crm.quote.number_origin — 'minted' (V2's own serial, globally unique as before) or
--      'printed_historical' (the number exactly as printed). Gate G1: a printed number is
--      unique per opportunity, not globally, because the same serial really was printed on
--      two different clients' documents (01125, 01135, 01230, 01242, …) and renumbering
--      either would falsify the document. A minted number stays globally unique.
--   2. crm.quote_revision.origin — 'authored' (unchanged rules) or 'historical_import'. A
--      historical revision is the document itself: pdf_sha256, sent_at and the evidence
--      record it came from are required; totals, approval, snapshot and currency are
--      allowed to be absent because the document was never parsed for them. It is born
--      'sent' and may only ever become 'void' (the append-only rollback) or be superseded.
--   3. evidence.assertion may resolve to a quote or a quote revision.
--   4. One new audit event, 'quote_revision.historical_recorded'.
--
-- No row changes. No grant widens. No RLS policy relaxes. The send flags do not move.

set role origenlab_owner;

-- ── 1. crm.quote: where the number came from ────────────────────────────────────────────────

alter table crm.quote
  add column number_origin text not null default 'minted';

alter table crm.quote
  add constraint quote_number_origin_check check (number_origin in ('minted', 'printed_historical'));

comment on column crm.quote.number_origin is
  'minted: V2''s own serial, globally unique. printed_historical: the number exactly as printed on a pre-V2 document, unique per opportunity only (gate G1) — never normalised, suffixed or renumbered.';

alter table crm.quote drop constraint quote_number_key;

-- A minted number is still unique across the whole CRM.
create unique index quote_number_minted_key on crm.quote (quote_number) where number_origin = 'minted';

-- Every number, whatever its origin, is unique within one opportunity. With the revision's
-- pdf_sha256 this is the G1 identity (printed number, opportunity, document).
alter table crm.quote
  add constraint quote_opportunity_number_key unique (opportunity_id, quote_number);

-- ── 2. crm.quote_revision: an imported document is not an authored revision ─────────────────

alter table crm.quote_revision
  add column origin text not null default 'authored',
  add column origin_source_record_id uuid references evidence.source_record (id);

create index quote_revision_origin_source_record_idx on crm.quote_revision (origin_source_record_id)
  where origin_source_record_id is not null;

alter table crm.quote_revision
  add constraint quote_revision_origin_check check (origin in ('authored', 'historical_import'));

comment on column crm.quote_revision.origin is
  'authored: priced, approved and sent through V2. historical_import: a pre-V2 document recorded as found — pdf_sha256, sent_at and origin_source_record_id required; totals, approval, party snapshot and currency may be absent because nobody parsed them.';

-- Currency and decimals are facts about a priced revision. An imported PDF has not been read
-- for either, so they may be absent there and only there.
alter table crm.quote_revision alter column quote_currency drop not null;
alter table crm.quote_revision alter column price_decimals drop not null;

alter table crm.quote_revision
  add constraint quote_revision_currency_required_when_authored
    check (origin = 'historical_import' or (quote_currency is not null and price_decimals is not null));

-- The shape of a historical revision: it is the document, and it was sent.
alter table crm.quote_revision
  add constraint quote_revision_historical_shape check (
    origin <> 'historical_import' or (
      status in ('sent', 'void')
      and pdf_sha256 is not null
      and sent_at is not null
      and origin_source_record_id is not null
      and sent_attempt_id is null
      and approved_at is null
      and approved_by_operator_id is null
    )
  );

-- The four authored-only rules, restated so that they bind 'authored' exactly as before and
-- leave a historical revision to the shape above.
alter table crm.quote_revision drop constraint quote_revision_totals_present_once_approved;
alter table crm.quote_revision add constraint quote_revision_totals_present_once_approved check (
  origin = 'historical_import' or status not in ('approved', 'sent') or (
    subtotal is not null and discount_total is not null and tax_base is not null
    and tax_total is not null and grand_total is not null and totals_computed_at is not null
  )
);

alter table crm.quote_revision drop constraint quote_revision_snapshot_present_once_approved;
alter table crm.quote_revision add constraint quote_revision_snapshot_present_once_approved check (
  origin = 'historical_import' or status not in ('approved', 'sent') or party_snapshot is not null
);

alter table crm.quote_revision drop constraint quote_revision_approval_shape;
alter table crm.quote_revision add constraint quote_revision_approval_shape check (
  origin = 'historical_import' or status not in ('approved', 'sent')
    or (approved_at is not null and approved_by_operator_id is not null)
);

alter table crm.quote_revision drop constraint quote_revision_sent_evidence_shape;
alter table crm.quote_revision add constraint quote_revision_sent_evidence_shape check (
  case
    when origin = 'historical_import'
      -- The carrying Gmail message is evidence (origin_source_record_id), not a comms.message
      -- V2 sent; sent_message_id may name one later, once comms holds it.
      then sent_attempt_id is null and num_nonnulls(sent_message_id) <= 1
    when status = 'sent'
      then pdf_sha256 is not null and sent_at is not null and num_nonnulls(sent_attempt_id, sent_message_id) = 1
    else sent_attempt_id is null and sent_message_id is null and sent_at is null
  end
);

-- One document is one revision of a quote, never two.
create unique index quote_revision_historical_document_key
  on crm.quote_revision (quote_id, pdf_sha256) where origin = 'historical_import';

-- A historical revision is immutable. The only moves are: supersession recorded once, and
-- sent → void (the append-only rollback). Nothing is ever deleted.
create function crm.quote_revision_historical_guard() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
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
  if new.status <> old.status and not (old.status = 'sent' and new.status = 'void') then
    raise exception 'crm.quote_revision %: a historical revision moves only sent → void, not % → %',
      old.id, old.status, new.status
      using errcode = 'P0001';
  end if;
  return new;
end
$$;

comment on function crm.quote_revision_historical_guard() is
  'A historical_import revision is immutable except supersession (once) and sent → void; never deleted. SECURITY INVOKER.';

revoke all on function crm.quote_revision_historical_guard() from public, anon, authenticated, service_role;

create trigger quote_revision_historical_guard
  before update or delete on crm.quote_revision
  for each row execute function crm.quote_revision_historical_guard();

-- ── 3. evidence.assertion may resolve to what it evidences ──────────────────────────────────

alter table evidence.assertion drop constraint assertion_resolved_kind_check;
alter table evidence.assertion add constraint assertion_resolved_kind_check check (resolved_kind is null or resolved_kind in (
  'organization', 'person', 'contact_point', 'affiliation', 'address',
  'organization_relationship', 'opportunity', 'opportunity_participant', 'contact_control',
  'quote', 'quote_revision'
));

-- ── 4. The audit event ──────────────────────────────────────────────────────────────────────
--
-- 'quote.created' already exists and is reused for the quote. A historical revision is not a
-- transition of anything, so it gets its own name rather than borrowing
-- 'quote_revision.transitioned', which the sent → void rollback does use.

alter table crm.domain_event drop constraint domain_event_type_check;
alter table crm.domain_event add constraint domain_event_type_check check (event_type in (
  'organization.created', 'organization.confirmed', 'organization.merged',
  'person.created', 'person.confirmed', 'person.merged',
  'affiliation.opened', 'affiliation.closed',
  'address.added', 'address.superseded',
  'contact_point.created', 'contact_point.confirmed',
  'relationship.changed',
  'opportunity.created', 'opportunity.staged', 'opportunity.organization_set', 'opportunity.closed',
  'participant.added', 'participant.linked', 'participant.primary_changed', 'participant.ended',
  'case_organization.added', 'case_organization.role_confirmed', 'case_organization.ended',
  'case_interest.added',
  'case_evidence.linked',
  'task.created', 'task.completed', 'task.cancelled',
  'activity.linked',
  'quote.created', 'quote_revision.transitioned', 'quote_revision.superseded',
  'quote_revision.historical_recorded',
  'campaign.transitioned', 'campaign.approved', 'campaign.override_granted', 'campaign.dry_run_recorded',
  'send_attempt.submission_changed', 'send_attempt.delivery_changed',
  'contact_control.added', 'contact_control.revoked',
  'send_control.changed',
  'operator.changed',
  'assertion.promoted', 'source_record.quarantined', 'source_record.migration_manifest_recorded',
  'source_record.review_noted',
  'product.created'
));

reset role;

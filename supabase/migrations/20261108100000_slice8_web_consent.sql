-- Slice 8 — web quote form consent: crm.consent (DOMAIN.md §7, #72).
--
-- Ley 21.719 (in force 1 December 2026) asks for an explicit, provable consent for each purpose a
-- person's data is used for. The public quote form carries a consent checkbox whose text is
-- versioned (`consent-v1-2026-11`); the mail rules that turn a web-form message into a case record it
-- here, and the newsletter sign-up will do the same.
--
--   * #72 crm.consent — one row per acceptance: the address, the purpose (quote_request or
--     newsletter), the version of the text that was shown, when it was accepted, where it came from
--     (source) and the message it arrived with (source_record_id). Never updated, never deleted: a
--     withdrawal is a new row of its own, not an edit.
--   * email is plain text held lower-case by a check, not citext: no migration here installs citext
--     and the migrator cannot create an extension, so a text column that refuses upper case gives
--     the same uniqueness without an owner-run step.
--   * person_id is a foreign key to the durable crm.person (nullable: the form may arrive before
--     anyone is matched). source_record_id carries no foreign key — it is a logical id into the
--     rebuildable evidence layer, and no durable table takes a key into that.

set role origenlab_owner;

create table crm.consent (
  id uuid primary key default gen_random_uuid(),
  email text not null,
  person_id uuid references crm.person (id),
  purpose text not null,
  text_version text not null,
  accepted_at timestamptz not null,
  source text not null,
  source_record_id uuid,
  created_at timestamptz not null default now(),
  constraint consent_email_lower check (email = lower(email)),
  constraint consent_purpose_check check (purpose in ('quote_request', 'newsletter')),
  constraint consent_email_nonblank check (length(btrim(email)) > 0),
  constraint consent_text_version_nonblank check (length(btrim(text_version)) > 0),
  constraint consent_source_nonblank check (length(btrim(source)) > 0)
);

comment on table crm.consent is
  'DOMAIN.md §7 #72 — explicit consent record per Ley 21.719 (1 Dec 2026): one row per (email, purpose, text_version, source_record_id). Never updated or deleted; consent withdrawal is a future revocation row. purpose in (quote_request, newsletter). Inserted by mail_rules web-form branch (source=web_quote_form) or newsletter sign-up.';

create index consent_person_idx on crm.consent (person_id);

-- The same acceptance, replayed from the same message, is recorded once.
create unique index consent_idempotent_idx
  on crm.consent (email, purpose, text_version, source_record_id)
  where source_record_id is not null;

-- Append-only: no UPDATE, no DELETE ever, not even by the owner — a withdrawal is a new row.
create trigger consent_never_updated
  before update on crm.consent
  for each row execute function platform.reject_mutation('append-only: a withdrawal is a new row, not an edit');

create trigger consent_never_deleted
  before delete on crm.consent
  for each row execute function platform.reject_mutation('append-only: a withdrawal is a new row, rows are never deleted');

-- ── grants ───────────────────────────────────────────────────────────────────────────────────
revoke all on crm.consent from public, anon, authenticated, service_role;
grant select, insert on crm.consent to origenlab_api;

-- ── RLS ──────────────────────────────────────────────────────────────────────────────────────
alter table crm.consent enable row level security;

create policy origenlab_api_select on crm.consent for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.consent for insert to origenlab_api with check (true);

reset role;

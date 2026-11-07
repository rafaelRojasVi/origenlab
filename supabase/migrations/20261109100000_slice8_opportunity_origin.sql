-- Slice 8 — crm.opportunity.origin: the channel a case came from (DOMAIN.md §7.16).
--
-- Knowing which channel produces cases (a mail, the web form, a WhatsApp message, a campaign reply,
-- Mercado Público, a manufacturer, a recommendation, a phone call, the old labdelivery business)
-- is what lets the owner see where sales come from. One nullable text column, closed by a check
-- to nine values:
--
--   correo, web, whatsapp, campana, mercado_publico, fabricante, recomendacion, llamada,
--   ex_labdelivery
--
--   * Nullable, no default: null means "not recorded". Every case that exists today stays null; a
--     backfill is a script the owner runs on purpose (scripts/backfill_opportunity_origin.py), never
--     part of a migration.
--   * It is not origin_source_record_id: that column points at the one evidence record a case was
--     promoted from; origin is the channel, a closed vocabulary an operator can set or correct.
--   * No new grant: origenlab_api already has table-level select/insert/update on crm.opportunity
--     and the worker select, so no policy, trigger or domain event changes here. The event that
--     records a change of origin arrives with the command that makes it.
--   * The partial index serves "cases by origin"; cases with no origin are never listed by it.

set role origenlab_owner;

alter table crm.opportunity
  add column origin text;

alter table crm.opportunity
  add constraint opportunity_origin_check check (
    origin in (
      'correo', 'web', 'whatsapp', 'campana',
      'mercado_publico', 'fabricante', 'recomendacion',
      'llamada', 'ex_labdelivery'
    )
  );

comment on column crm.opportunity.origin is
  'The channel the case came from: correo, web, whatsapp, campana, mercado_publico, fabricante, recomendacion, llamada or ex_labdelivery. Null = not recorded. DOMAIN.md §7.16.';

create index opportunity_origin_idx on crm.opportunity (origin) where origin is not null;

reset role;

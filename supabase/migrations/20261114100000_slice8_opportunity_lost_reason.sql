-- Slice 8 — crm.opportunity.lost_reason: why a case was lost or abandoned, as a closed vocabulary
-- (owner decision 2026-11-10, DOMAIN.md §7.19).
--
-- `close_reason` stays the operator's sentence («Sin presupuesto: gerencia no autorizó…», the mail
-- rules' «otro proveedor», a script's note). It cannot be counted. The owner wants to learn why
-- cases are lost — precio, otro proveedor, sin presupuesto, sin respuesta, no era real — so every
-- closing records one code next to the text:
--
--   precio · otro_proveedor · sin_presupuesto · sin_respuesta · no_era_real · plazo_entrega ·
--   cambio_necesidad · duplicado
--
--   * The owner named the first five; `plazo_entrega` and `cambio_necesidad` are chips the team
--     already uses, and `duplicado` is how a case opened twice is closed (never a loss to learn from).
--   * Nullable: an open case has none; a closed case from before this migration keeps null when its
--     text matches no code (the backfill below maps the chips and the rules' phrases). No CHECK ties
--     it to the stage: `advance-case-stage` refuses a closing without one from now on.
--   * One writer: `advance-case-stage` (apps/api, v2/case_commands.py), explicit in the body or
--     derived from `close_reason` the same way as this backfill. The `opportunity.staged` /
--     `opportunity.closed` payloads carry it; no new event type.
--   * No new grant: origenlab_api already has table-level select/insert/update on crm.opportunity.

set role origenlab_owner;

alter table crm.opportunity
  add column lost_reason text;

alter table crm.opportunity
  add constraint opportunity_lost_reason_check check (
    lost_reason in (
      'precio', 'otro_proveedor', 'sin_presupuesto', 'sin_respuesta',
      'no_era_real', 'plazo_entrega', 'cambio_necesidad', 'duplicado'
    )
  );

comment on column crm.opportunity.lost_reason is
  'Why the case was lost or abandoned, coded: precio, otro_proveedor, sin_presupuesto, sin_respuesta, no_era_real, plazo_entrega, cambio_necesidad or duplicado. Null = open, or closed before 2026-11-14 with a text no code matches. DOMAIN.md §7.19.';

create index opportunity_lost_reason_idx on crm.opportunity (lost_reason) where lost_reason is not null;

-- Backfill from the text: the dashboard's chips (with or without a detail after the colon), «otro
-- proveedor» anywhere in the text, and the phrases the mail rules write (R6 «otro proveedor», R19 «el cliente no sigue (lectura del
-- correo)»). Anything else stays null; `discarded_by_correction` is not a loss.
update crm.opportunity
   set lost_reason = case
         when close_reason ilike 'sin respuesta%'            then 'sin_respuesta'
         when close_reason ilike 'sin presupuesto%'          then 'sin_presupuesto'
         when close_reason ilike 'no es una solicitud%'      then 'no_era_real'
         when close_reason ilike 'compró a otro proveedor%'
           or close_reason ilike 'compro a otro proveedor%'
           or close_reason ilike 'eligieron otro proveedor%'
           or close_reason ilike 'otro proveedor%'
           or close_reason ilike 'el cliente no sigue%'
           or close_reason ilike '%otro proveedor%'          then 'otro_proveedor'
         when close_reason ilike 'cambió la necesidad%'
           or close_reason ilike 'cambio la necesidad%'      then 'cambio_necesidad'
         when close_reason ilike 'plazo de entrega%'         then 'plazo_entrega'
         when close_reason ilike 'precio%'                   then 'precio'
         when close_reason ilike 'duplicado%'                then 'duplicado'
       end
 where stage in ('lost', 'abandoned')
   and lost_reason is null
   and close_reason is not null;

reset role;

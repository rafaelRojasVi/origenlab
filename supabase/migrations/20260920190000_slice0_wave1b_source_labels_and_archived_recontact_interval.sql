-- Slice 0 corrective — two additive changes that let the Wave 1A/1B historical import
-- (docs/DATA.md §7.6) represent data the canonical documents already describe.
--
-- Neither changes an existing row, widens a grant, relaxes RLS or touches a send flag.
-- Both were measured as blockers by the importer's dry run and decided by the owner on
-- 2026-09-20; docs/DATA.md §7.6.4 records why the existing model could not represent the
-- data without them.

set role origenlab_owner;

-- ── A. outbound.contact_control: the Wave 1B source labels ──────────────────────────────────────
--
-- docs/DATA.md §7.5.1 requires that "Wave 1B rows load with their own source labels
-- (wave1b_prior_contact, wave1b_block), so provenance stays separable after the load". The
-- Slice 0 vocabulary predates the Wave 1B extract and lists only the four wave1a_* labels,
-- so the loader had no correct label to use: loading a Wave 1B row as wave1a_* would
-- misattribute its provenance, which is the one thing that sentence exists to prevent.
--
-- The list stays closed. Adding a wave is still a migration, by design.
alter table outbound.contact_control
  drop constraint contact_control_source_check;

alter table outbound.contact_control
  add constraint contact_control_source_check check (source in (
    'wave1a_union', 'wave1a_rfc2047_addendum', 'wave1a_suppression', 'wave1a_investigation',
    'wave1b_prior_contact', 'wave1b_block',
    'send_accepted', 'ndr_handler', 'complaint_handler', 'unsubscribe_handler', 'operator_command'
  ));

comment on column outbound.contact_control.source is
  'Closed provenance vocabulary. The wave1a_* and wave1b_* labels keep each migration wave''s rows separable after the load (docs/DATA.md §7.1, §7.5.1); the rest name the runtime handler that wrote the row.';

-- ── B. outbound.campaign: recontact_interval_days is a live-campaign policy ──────────────────────
--
-- The column is a forward-looking eligibility knob: outbound_v2.eligibility reads it to
-- decide whether enough time has passed to contact someone again. An archived V1 campaign
-- will never send, so the knob has no meaning for one — and V1 has no recontact-interval
-- concept to migrate (docs/DATA.md §7.1 records zero cooldown rows carried from V1 for
-- exactly this reason). Any value written for the three historical campaigns would be
-- invented, and would read as recorded V1 policy to every later query.
--
-- This is the fourth carve-out the table makes for these same campaigns, alongside
-- campaign_approval_shape, campaign_content_shape and campaign_audience_criteria_shape.
-- A campaign that can still send is unaffected: the value remains mandatory for it.
alter table outbound.campaign
  alter column recontact_interval_days drop not null;

alter table outbound.campaign
  add constraint campaign_recontact_interval_shape check (
    status in ('archived', 'cancelled') or recontact_interval_days is not null
  );

comment on column outbound.campaign.recontact_interval_days is
  'Days that must pass before an address may be contacted again. Mandatory for every campaign that can still send; NULL is permitted only for archived or cancelled campaigns, which never send and — for the migrated V1 campaigns — have no V1 value to carry (docs/DATA.md §7.6.4).';

reset role;

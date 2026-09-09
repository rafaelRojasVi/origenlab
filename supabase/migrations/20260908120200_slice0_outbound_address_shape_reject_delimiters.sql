-- Slice 0 / M10d — address shape: a header delimiter is not part of an address.
--
-- Corrective, additive-forward: 20260905230814 stays as shipped; this migration drops and
-- re-adds the two address CHECK constraints with a tightened pattern.
--
-- The original pattern, '^[^@\s]+@[^@\s]+\.[^@\s]+$', excludes only '@' and whitespace, so a
-- raw RFC 822 fragment an adapter forgot to extract passes it whole: '<sales@steinlite.com>'
-- has no space and one '@', matches, and would be frozen — and later sent — with the angle
-- brackets still attached. Measured against real archive header strings, 78 of 2,192 distinct
-- values had this shape. Excluding '<', '>', ',', ';' and '"' from both the local and the
-- domain part closes it: those characters only ever appear in a display-name or a
-- comma/semicolon-joined recipient list, never inside one mailbox.
--
-- The same pattern is the application's ADDRESS_SHAPE_PATTERN
-- (apps/email-pipeline/src/origenlab_email_pipeline/outbound_v2/eligibility.py); the two are
-- kept identical by tests/outbound_v2/test_address_shape.py, so an address the database
-- would reject can never reach a freeze plan.
--
-- Tightening only ever rejects more, so the re-added constraints are validated against
-- existing rows: a row that no longer satisfies the shape fails this migration rather than
-- surviving as a NOT VALID exception. Nothing here grants a send capability.

set role origenlab_owner;

alter table outbound.campaign_recipient
  drop constraint campaign_recipient_address_shape;
alter table outbound.campaign_recipient
  add constraint campaign_recipient_address_shape check (
    address_norm = lower(address_norm) and address_norm ~ '^[^@\s<>,;"]+@[^@\s<>,;"]+\.[^@\s<>,;"]+$'
  );

alter table outbound.send_attempt
  drop constraint send_attempt_address_shape;
alter table outbound.send_attempt
  add constraint send_attempt_address_shape check (
    address_norm = lower(address_norm) and address_norm ~ '^[^@\s<>,;"]+@[^@\s<>,;"]+\.[^@\s<>,;"]+$'
  );

reset role;

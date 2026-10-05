-- Slice 7 / catalog 1a — catalog.product extensions (#26), catalog.product_image (#50),
-- catalog.supplier_product observation extensions (#27), catalog.supplier_terms (#51), notes on
-- products (crm.note #44), pricing exchange rates (#52), costing parameters (#53), quote-document
-- lines as durable evidence (#54) and the domain-event vocabulary they need. Synthetic fixtures only.
begin;
create extension if not exists pgtap with schema extensions;
select plan(43);

-- product extensions
select has_column('catalog', 'product', 'model_key', 'product.model_key exists');
select has_column('catalog', 'product', 'search_tsv', 'product.search_tsv exists');
select col_not_null('catalog', 'product', 'version', 'product.version not null');
select is((select attgenerated::text from pg_attribute
            where attrelid = 'catalog.product'::regclass and attname = 'model_key'), 's', 'model_key is a stored generated column');
select ok(exists (select 1 from pg_constraint where conname = 'product_kind_check'), 'product_kind check');
select ok(exists (select 1 from pg_constraint where conname = 'product_content_origin_check'), 'content_origin check');
select ok(exists (select 1 from pg_constraint where conname = 'product_specs_array'), 'specs is an array');
select ok(exists (select 1 from pg_constraint where conname = 'product_confirmation_shape'), 'confirmation shape');
select ok(exists (select 1 from pg_constraint where conname = 'product_origin_country_shape'), 'ISO country shape');

-- generated model_key normalises case, spaces, dashes, dots, slashes. The fixture is written as
-- the owner; the assertion runs back as the session user, which can reach pgTAP in `extensions`.
set local role origenlab_owner;
insert into crm.organization (id, kind, name, confirmation)
  values ('00000000-0000-4000-8000-0000000000a1', 'company', 'ACME Test', 'confirmed');
insert into catalog.product (manufacturer_organization_id, model_number, name)
  values ('00000000-0000-4000-8000-0000000000a1', 'Sonic-100 x/2.5', 'Synthetic');
reset role;
select is((select model_key from catalog.product where model_number = 'Sonic-100 x/2.5'), 'SONIC100X25', 'model_key normalised');

-- product_image
select has_table('catalog', 'product_image', 'product_image exists');
select ok(exists (select 1 from pg_constraint where conname = 'product_image_status_check'), 'image status check');
select ok(exists (select 1 from pg_constraint where conname = 'product_image_source_check'), 'image source check');
select ok(exists (select 1 from pg_trigger where tgname = 'product_image_never_deleted'), 'images never deleted');

-- supplier_product extensions
select has_column('catalog', 'supplier_product', 'price_kind', 'supplier_product.price_kind');
select ok(exists (select 1 from pg_constraint where conname = 'supplier_product_price_kind_check'), 'price_kind check');
-- The key keeps its Slice 0 name, so its definition is what proves it was replaced.
select ok(pg_get_constraintdef((select oid from pg_constraint where conname = 'supplier_product_observation_key'))
            like 'UNIQUE NULLS NOT DISTINCT (supplier_organization_id, product_id, as_of, price_kind, min_qty)',
  'observation unique key covers price_kind and min_qty, nulls not distinct');
select ok(exists (select 1 from pg_trigger where tgname = 'supplier_product_append_only'), 'still append-only');

-- supplier_terms
select has_table('catalog', 'supplier_terms', 'supplier_terms exists');
select ok(exists (select 1 from pg_constraint where conname = 'supplier_terms_route_check'), 'route check');
select ok(exists (select 1 from pg_trigger where tgname = 'supplier_terms_never_deleted'), 'terms never deleted');

-- notes on products
select ok(pg_get_constraintdef((select oid from pg_constraint where conname = 'note_subject_kind_check')) like '%product%',
  'note subject_kind accepts product');

-- events
select ok(pg_get_constraintdef((select oid from pg_constraint where conname = 'domain_event_type_check')) like '%product.cost_recorded%',
  'event vocabulary extended');
select ok(pg_get_constraintdef((select oid from pg_constraint where conname = 'domain_event_type_check')) like '%organization.supplier_terms_set%',
  'supplier terms event');

-- re-commented tables
select ok(obj_description('catalog.supplier_product'::regclass, 'pg_class') like '%price_kind, min_qty) nulls not distinct%',
  'supplier_product comment names the widened observation key');
select ok(obj_description('crm.note'::regclass, 'pg_class') like '%product%', 'note comment names products as a subject');

-- fx_rate (#52)
select has_table('catalog', 'fx_rate', 'fx_rate exists');
select ok(exists (select 1 from pg_constraint where conname = 'fx_rate_provider_check'), 'fx provider check');
select ok(exists (select 1 from pg_trigger where tgname = 'fx_rate_append_only'), 'fx append-only');

-- cost_parameter (#53)
select has_table('catalog', 'cost_parameter', 'cost_parameter exists');
select ok(exists (select 1 from pg_trigger where tgname = 'cost_parameter_append_only'), 'parameters append-only');
-- The repository is public: only public/regulatory values are seeded. Negotiated rates, fees,
-- markups and margins are set per environment by an operator, never shipped in a migration.
select is((select count(distinct key)::int from catalog.cost_parameter), 5, '5 seeded parameter keys');
select set_eq($$ select key from catalog.cost_parameter $$,
  array['iva_rate', 'duty_rate_general', 'duty_rate_fta', 'courier_threshold_usd_fob', 'insurance_pct_of_fob'],
  'only the public/regulatory parameters are seeded');
select ok(not exists (select 1 from catalog.cost_parameter where key = 'dhl_import_discount_pct'),
  'the negotiated DHL import discount has no seed row');
select is((select value_numeric from catalog.cost_parameter where key = 'iva_rate'), 0.19::numeric, 'iva seeded');

-- quote documents (#54)
select ok(pg_get_constraintdef((select oid from pg_constraint where conname = 'source_record_kind_check')) like '%quote_document%',
  'quote_document evidence kind');
select has_table('evidence', 'document_line', 'document_line exists');
select ok(exists (select 1 from pg_constraint where conname = 'document_line_check_status_check'), 'check_status check');
select ok(exists (select 1 from pg_trigger where tgname = 'document_line_review_guard'), 'review guard trigger');
select ok(exists (select 1 from pg_trigger where tgname = 'document_line_never_deleted'), 'lines never deleted');
select ok(pg_get_constraintdef((select oid from pg_constraint where conname = 'domain_event_aggregate_kind_check')) like '%cost_parameter%',
  'cost_parameter aggregate');

-- The review guard, exercised as the owner so the trigger (not a grant) is what speaks. pgTAP is
-- reachable as the owner only through this transaction-scoped USAGE grant (rolled back below).
-- A fresh test database holds no operator, so a synthetic one is written first.
grant usage on schema extensions to origenlab_owner;
set local role origenlab_owner;
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status)
  values ('00000000-0000-4000-8000-0000000000d9', '00000000-0000-4000-8000-0000000000f9',
          'catalog.reviewer@example.test', 'Catalog Reviewer', 'admin', 'active');
insert into evidence.source_record (id, kind, dedupe_key, payload)
  values ('00000000-0000-4000-8000-0000000000d1', 'quote_document', 'quote_document:test', '{"printed_quote_number":"T-1"}');
insert into evidence.document_line (source_record_id, line_no, currency, extractor, check_status) values
  ('00000000-0000-4000-8000-0000000000d1', 1, 'CLP', 'test', 'verified'),
  ('00000000-0000-4000-8000-0000000000d1', 2, 'CLP', 'test', 'disputed');
select throws_ok(
  $$ update evidence.document_line set check_status = 'reviewed', reviewed_by_operator_id = '00000000-0000-4000-8000-0000000000d9',
       reviewed_at = now() where line_no = 1 $$,
  '23514', null, 'a verified line cannot be reviewed');
select lives_ok(
  $$ update evidence.document_line set check_status = 'reviewed', reviewed_by_operator_id = '00000000-0000-4000-8000-0000000000d9',
       reviewed_at = now(), review_note = 'synthetic review', updated_at = now() where line_no = 2 $$,
  'a disputed line can be reviewed');
reset role;

select * from finish();
rollback;

-- Slice 7 / catalog 1a — catalog.product extensions (#26), catalog.product_image (#50),
-- catalog.supplier_product observation extensions (#27), catalog.supplier_terms (#51), notes on
-- products (crm.note #44) and the domain-event vocabulary they need. Synthetic fixtures only.
begin;
create extension if not exists pgtap with schema extensions;
select plan(24);

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

select * from finish();
rollback;

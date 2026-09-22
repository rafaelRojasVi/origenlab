-- Slice 2 — the two evidence provenance kinds a Gmail/Drive staging pass needs, and the one
-- assertion kind a Drive document produces.
--
-- docs/DOMAIN.md §5 (evidence and promotion), §7; docs/DATA.md §2, §3, §8.
--
-- Additive only. No existing row changes, no grant widens, no RLS policy relaxes, no send
-- flag moves, and no table gains a column. The three vocabularies below stay closed: adding
-- a source of evidence is still a migration, by design.

set role origenlab_owner;

-- ── A. evidence.source_record: Gmail messages and Drive files as acquired records ───────────────
--
-- The Slice 0 vocabulary predates any mailbox or document evidence pass and names only the
-- workbook, the ChileCompra feed and the V1 migration's own manifests. A staging pass over
-- Gmail or Drive therefore had no correct kind to write: reusing 'workbook_import' would
-- record a message as a spreadsheet row, which is exactly the misattribution the closed list
-- exists to prevent.
--
-- Both kinds are *acquisition* labels. They say where a record came from and nothing about
-- whether anybody believes it: review_status stays 'pending' and every assertion derived
-- from one stays 'unresolved' until an operator decides. Neither kind may reach crm.* except
-- through the promotion path that already exists (docs/DOMAIN.md §5).
alter table evidence.source_record
  drop constraint source_record_kind_check;

alter table evidence.source_record
  add constraint source_record_kind_check check (kind in (
    'workbook_import', 'chilecompra_notice', 'migration_manifest',
    'v1_parse_failure', 'v1_evidence_edge', 'v1_supplier_candidate', 'v1_historical_quote_candidate',
    'gmail_message', 'drive_file'
  ));

comment on column evidence.source_record.kind is
  'Closed acquisition vocabulary: where this record came from. gmail_message and drive_file are staged, never-trusted observations — they arrive review_status = ''pending'' and reach crm.* only through operator-reviewed promotion (docs/DOMAIN.md §5).';

-- ── B. evidence.assertion: a document that names a commercial subject ───────────────────────────
--
-- A Drive file is evidence that a document exists and refers to something — an organization,
-- a quote number, a tender. That is a different observation from 'organization_name' (a name
-- was written down) and from 'historical_quote_candidate' (a quote may have existed): it
-- carries a document identity, and its value payload holds the file's own reference.
--
-- A Gmail message needs no new assertion kind. The facts a message yields are addresses and
-- names, which 'contact_address' and 'organization_name' already describe correctly; only
-- the provenance differs, and that is what part A records.
alter table evidence.assertion
  drop constraint assertion_kind_check;

alter table evidence.assertion
  add constraint assertion_kind_check check (kind in (
    'organization_name', 'contact_address', 'postal_address', 'affiliation',
    'contacted_address', 'supplier_candidate', 'historical_quote_candidate',
    'document_reference'
  ));

comment on column evidence.assertion.kind is
  'Closed observation vocabulary. document_reference records that a stored document names a commercial subject; the document''s own identity lives in value, and resolution stays ''unresolved'' until an operator links it.';

reset role;

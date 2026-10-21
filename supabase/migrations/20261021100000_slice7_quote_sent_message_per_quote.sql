-- One email may send several quotes (owner 2026-10-21). Tatiana attaches two Cotizador PDFs — two
-- quotes of the same request, or a revision and a second quote — to one message; rule R16 marks each
-- revision sent with that message as its `sent_message_id`. The slice-0 key allowed a message to be
-- the sending evidence of one revision in the whole table, so the second was refused.
--
--   * quote_revision_sent_message_key: unique (sent_message_id) → unique (sent_message_id, quote_id).
--     One message still sends at most one revision of a quote (a revision and its successor are never
--     both sent by the same email). The message id leads, so the key stays the index of the
--     `quote_revision_sent_message_fkey` foreign key (supabase/tests/090_foreign_key_indexes.sql).
--   * sent_attempt_id keeps its own unique key: a V2 send attempt carries one revision.

set role origenlab_owner;

alter table crm.quote_revision drop constraint quote_revision_sent_message_key;
alter table crm.quote_revision
  add constraint quote_revision_sent_message_key unique (sent_message_id, quote_id);

comment on constraint quote_revision_sent_message_key on crm.quote_revision is
  'A Gmail message is the sending evidence of at most one revision per quote; one message may send several quotes (R16, 2026-10-21).';

reset role;

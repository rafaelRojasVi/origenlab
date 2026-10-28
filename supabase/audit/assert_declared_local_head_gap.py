#!/usr/bin/env python3
"""Assert the exact, reviewed gap between frozen Slice 0 and current local head.

The Slice 0 audit baseline intentionally describes the frozen hosted foundation:
33 tables / 127 policies / 102 FKs.

Current repository head is later and intentionally carries the commercial-case
schema, the historical-quotation import, the slice-5 campaign schema, W10 and
the campaign safety blocks (outbound.campaign_block), the shared Workspace
sign-in tables (platform.auth_principal, platform.operator_profile,
platform.auth_event, platform.auth_session), the slice-6 campaign-content
archive and CRM authoring tables, and the slice-7 catalog tables
(catalog.product_image, catalog.supplier_terms, catalog.fx_rate,
catalog.cost_parameter) and quote-document lines (evidence.document_line).
Therefore a local audit of current head must conclude LOCAL_FAIL — but only for
the exact reviewed post-Slice-0 additions.

It also carries one reviewed *removal*: 20260928192000 revoked the runtime API
role's INSERT and UPDATE on platform.operator, so the baseline's two policies
that served them are absent — exactly those two, and nothing else.

This checker does not regenerate, weaken or modify the baseline.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


EXPECTED_BLOCKING = ["a04", "a05", "a08", "a09", "a10"]

EXPECTED_EXTRA_TABLES = {
    "opportunity_organization",
    "opportunity_interest",
    "opportunity_evidence",
    # 20260928100000_slice5_campaign_block.sql — DOMAIN.md §7 #37.
    "campaign_block",
    # 20260928180000_slice1_shared_workspace_operator_profiles.sql — DOMAIN.md §7 #38–#40.
    "auth_principal",
    "operator_profile",
    "auth_event",
    # 20260928191000_slice1_revocable_profile_sessions.sql — DOMAIN.md §7 #41.
    "auth_session",
    # 20260930120000_slice6_campaign_content_archive_and_crm_authoring.sql — DOMAIN.md §7 #42–#45.
    "campaign_content",
    "campaign_content_message",
    "note",
    "organization_product_line",
    # 20261005134832_slice7_catalog_products_suppliers.sql — DOMAIN.md §7 #50–#51.
    "product_image",
    "supplier_terms",
    # 20261005140628_slice7_catalog_pricing_inputs_document_lines.sql — DOMAIN.md §7 #52–#54.
    "fx_rate",
    "cost_parameter",
    "document_line",
    # 20261006180200_slice4_triage_review.sql — DOMAIN.md §7 #55.
    "triage_review",
    # 20261012120000_slice7_case_documents.sql — DOMAIN.md §7 #56.
    "opportunity_document",
    # 20261013120000_slice7_catalog_product_document.sql — DOMAIN.md §7 #57.
    "product_document",
    # 20261015120000_slice7_case_memory.sql — DOMAIN.md §7 #58.
    "subject_memory",
    # 20261016200000_slice7_catalog_product_model_alias.sql — DOMAIN.md §7 #59.
    "product_model_alias",
    # 20261020100000_slice7_tender_bid_preparation.sql — DOMAIN.md §7 #60–#63.
    "bidder_profile",
    "bidder_document",
    "bid_preparation",
    "model_usage",
    # 20261020180000_slice7_notice_relevance_review.sql — DOMAIN.md §7 #64.
    "notice_review",
    # 20261023100000_slice7_prospect_findings.sql — DOMAIN.md §7 #65.
    "prospect_review",
    # 20261025100000_slice7_prospect_edit.sql — DOMAIN.md §7 #66.
    "prospect_edit",
    # 20261026100000_slice7_prospect_search.sql — DOMAIN.md §7 #67.
    "prospect_search_run",
    # 20261028100000_slice7_hoy_todo.sql — DOMAIN.md §7 #68.
    "todo",
    # 20261029100000_slice7_person_name.sql — DOMAIN.md §7 #69.
    "person_name",
}

# Functions added after Slice 0, by migration. The audit reports only a count
# here; which functions they are, and that each is SECURITY INVOKER with
# search_path = pg_catalog and no EXECUTE for a Data-API-facing role, is pinned
# by pgTAP (supabase/tests/010_inventory.sql, 063_commercial_case_commands.sql,
# 064_historical_quotation_import.sql, 065-068 for the slice-5 campaign
# triggers, 069 for W10, 071 for the campaign blocks, 072 for the archived-campaign guard and
# 091 for the document-line review guard). This file refuses any count other than 3 + len(these).
# The one exception to SECURITY INVOKER is EXPECTED_SECURITY_DEFINER below.
SLICE0_FUNCTION_COUNT = 3

# Tables that exist in the baseline but gained a policy afterwards: a09 may name them among the extra
# policies without them being new tables (a08). crm.quote_line: the API delete policy, 20261016100000;
# procurement.notice: the API insert policy, 20261020100000.
EXPECTED_EXTRA_POLICY_ONLY_TABLES = {"quote_line", "notice"}

POST_SLICE0_FUNCTIONS = {
    # 20260922170000_slice3_crm_commercial_case.sql
    "crm.opportunity_organization_supplier_exception_required",
    "crm.opportunity_organization_exception_immutable",
    "crm.opportunity_requesting_institution_agrees",
    "crm.opportunity_interest_manufacturer_agrees",
    "crm.opportunity_evidence_link_immutable",
    # 20260922200000_slice3_commercial_case_commands.sql
    "crm.opportunity_stage_guard",
    # 20260925200000_slice3_historical_quotation_import.sql — trigger function
    # behind crm.quote_revision's BEFORE UPDATE OR DELETE row trigger.
    "crm.quote_revision_historical_guard",
    # 20260927120000_slice5_campaign_draft_authoring.sql
    "outbound.campaign_content_draft_only",
    # 20260927180000_slice5_campaign_audience_freeze.sql (the snapshot guard is
    # replaced, not added, by 20260927200000_slice5_w12_recontact_review.sql)
    "outbound.campaign_freeze_facts_write_once",
    "outbound.campaign_recipient_snapshot_guard",
    # 20260927220000_slice5_campaign_planning.sql
    "outbound.campaign_planning_guard",
    "outbound.campaign_planning_absent_at_insert",
    # 20260927230000_slice5_w10_unsubscribe.sql — the permanence trigger guard,
    # the send-time contract (both INVOKER) and the closed-list writer below.
    "outbound.unsubscribe_permanent",
    "outbound.marketing_contact_refusals",
    "outbound.add_contact_control",
    "outbound.add_unsubscribe_contact_control",
    # 20260928100000_slice5_campaign_block.sql — the block's lifecycle guard,
    # the campaign-level refusals and their enforcement trigger (all INVOKER).
    "outbound.campaign_block_guard",
    "outbound.campaign_hold_refusals",
    "outbound.campaign_hold_guard",
    # 20260928090000_slice5_archived_campaign_immutable.sql — the INVOKER trigger
    # function that keeps an archived campaign, its audience and attempts as imported.
    "outbound.archived_campaign_immutable",
    # 20260928180000_slice1_shared_workspace_operator_profiles.sql — the three
    # INVOKER trigger functions that bump a row's version on every
    # security-relevant change (pgTAP: 079_shared_workspace_profiles.sql).
    "platform.operator_security_version",
    "platform.auth_principal_security_version",
    "platform.operator_profile_security_version",
    # 20260928191000_slice1_revocable_profile_sessions.sql — the INVOKER trigger
    # function that keeps a session's identity fixed, never extends it and makes a
    # revocation final (pgTAP: 081_revocable_profile_sessions.sql).
    "platform.auth_session_guard",
    # 20260928193000_slice1_lockout_clear_audit.sql — the INVOKER trigger function
    # that refuses a lockout.cleared audit event from the runtime role (pgTAP 073).
    "platform.auth_event_actor_guard",
    # 20260929100000_slice1_pin_attempt_begin_finish.sql — the two closed-list
    # definers below, which replaced 20260928194000's platform.record_pin_attempt
    # (dropped), and the INVOKER HMAC-SHA256 helper finish uses (pgTAP 075).
    "platform.hmac_sha256",
    "platform.begin_pin_attempt",
    "platform.finish_pin_attempt",
    # 20260930120000_slice6_campaign_content_archive_and_crm_authoring.sql — the
    # INVOKER immutability guard for campaign_content (owner-INSERT-only) and the
    # INVOKER note guard (no DELETE, immutable body/author/subject, version advance).
    "outbound.campaign_content_immutable",
    "crm.note_guard",
    # 20261005140628_slice7_catalog_pricing_inputs_document_lines.sql — the INVOKER
    # trigger guard that lets a document line change only by one review of a
    # disputed line (pgTAP: 091_catalog_1a.sql).
    "evidence.document_line_review_guard",
    # 20261012120000_slice7_case_documents.sql — the INVOKER trigger guard that lets a case
    # document change only by one removal (pgTAP: 093_case_documents.sql).
    "crm.opportunity_document_immutable",
    # 20261016100000_slice7_quote_authoring.sql — the INVOKER trigger guard that lets a quote line
    # change only while its revision is an authored draft (pgTAP: 097_quote_authoring.sql).
    "crm.quote_line_authored_draft_only",
    # Same migration — the INVOKER trigger guard that freezes an authored revision once it is
    # approved, sent or void (WORKFLOWS §W3b; pgTAP: 097_quote_authoring.sql).
    "crm.quote_revision_authored_frozen",
    # 20261016200000_slice7_catalog_product_model_alias.sql — the INVOKER trigger guard that keeps
    # one active alias per maker and key, never a model of the same maker, and lets an alias change
    # only by being retired once (pgTAP: 098_catalog_product_model_alias.sql).
    "catalog.product_model_alias_guard",
    # 20261020100000_slice7_tender_bid_preparation.sql — the INVOKER trigger guard that keeps a
    # case's kind (sale or tender) fixed, and the one that lets a bidder document change only by
    # being retired once (pgTAP: 099_tender_bid_preparation.sql).
    "crm.opportunity_kind_fixed",
    "crm.bidder_document_retire_once",
}

# The closed SECURITY DEFINER list of ARCHITECTURE.md §6.2, as built so far: its
# first entry, from 20260927230000_slice5_w10_unsubscribe.sql, and its second and
# third, from 20260929100000_slice1_pin_attempt_begin_finish.sql (which dropped
# 20260928194000's platform.record_pin_attempt). a05 must report exactly
# these entries beyond the (empty) Slice 0 baseline, with this owner, signature
# and pinned search_path — anything else is refused.
EXPECTED_SECURITY_DEFINERS = (
    # The audit sorts the entries as JSON text, so by their argument lists first.
    # It renders the whole argument list, OUT parameters included.
    '{"arguments":"p_attempt_id uuid, p_principal_id uuid, p_operator_id uuid, '
    'p_candidate_proof bytea, OUT selected boolean, '
    'OUT reason text",'
    '"name":"finish_pin_attempt","owner":"origenlab_owner",'
    '"proconfig":"search_path=pg_catalog","schema":"platform"}',
    '{"arguments":"p_kind text, p_purpose text, p_address text, p_reason text, '
    'p_operator_id uuid, p_command_receipt_id uuid, p_evidence jsonb",'
    '"name":"add_contact_control","owner":"origenlab_owner",'
    '"proconfig":"search_path=pg_catalog","schema":"outbound"}',
    '{"arguments":"p_principal_id uuid, p_operator_id uuid, OUT attempt_id uuid, '
    'OUT refused boolean, OUT memory_kib integer, OUT iterations integer, '
    'OUT lanes integer, OUT salt text, OUT hash_length integer, OUT nonce bytea",'
    '"name":"begin_pin_attempt","owner":"origenlab_owner",'
    '"proconfig":"search_path=pg_catalog","schema":"platform"}',
)
EXPECTED_SECURITY_DEFINER = "; ".join(EXPECTED_SECURITY_DEFINERS)

EXPECTED_FUNCTION_COUNT = SLICE0_FUNCTION_COUNT + len(POST_SLICE0_FUNCTIONS)

# Baseline policies deliberately removed, as (schema, table, policy, command, roles).
# 20260928192000_slice1_runtime_operator_write_revoked.sql: the runtime role reads
# operators and never writes one.
EXPECTED_ABSENT_POLICIES = {
    ("platform", "operator", "origenlab_api_insert", "INSERT", "{origenlab_api}"),
    ("platform", "operator", "origenlab_api_update", "UPDATE", "{origenlab_api}"),
}

# Foreign keys added after the commercial-case tables. The count and its index
# coverage are reviewed by supabase/tests/090_foreign_key_indexes.sql.
COMMERCIAL_CASE_FOREIGN_KEY_COUNT = 118

POST_COMMERCIAL_CASE_FOREIGN_KEYS = {
    # 20260925200000_slice3_historical_quotation_import.sql, covered by the
    # partial index quote_revision_origin_source_record_idx.
    "crm.quote_revision.origin_source_record_id -> evidence.source_record.id",
    # 20260927180000_slice5_campaign_audience_freeze.sql, covered by the plain
    # index campaign_recipient_frozen_against_campaign_idx on the same columns.
    "outbound.campaign_recipient.(campaign_id, content_sha256, policy_version)"
    " -> outbound.campaign.(id, content_sha256, audience_policy_version)",
    # 20260928100000_slice5_campaign_block.sql, each covered by its own plain
    # index (campaign_block_campaign_idx, campaign_block_placed_by_operator_idx,
    # campaign_block_lifted_by_operator_idx).
    "outbound.campaign_block.campaign_id -> outbound.campaign.id",
    "outbound.campaign_block.placed_by_operator_id -> platform.operator.id",
    "outbound.campaign_block.lifted_by_operator_id -> platform.operator.id",
    # 20260928180000_slice1_shared_workspace_operator_profiles.sql, each covered
    # by a plain index: the composite key by operator_profile_operator_kind_key,
    # principal_id by operator_profile_principal_key_key, and the three audit
    # keys by auth_event_{principal,operator,previous_operator}_idx.
    "platform.operator_profile.(operator_id, operator_sign_in_kind)"
    " -> platform.operator.(id, sign_in_kind)",
    "platform.operator_profile.principal_id -> platform.auth_principal.id",
    "platform.auth_event.principal_id -> platform.auth_principal.id",
    "platform.auth_event.operator_id -> platform.operator.id",
    "platform.auth_event.previous_operator_id -> platform.operator.id",
    # 20260928191000_slice1_revocable_profile_sessions.sql, each covered by a
    # plain index (auth_session_principal_idx, auth_session_operator_idx).
    "platform.auth_session.principal_id -> platform.auth_principal.id",
    "platform.auth_session.operator_id -> platform.operator_profile.operator_id",
    # 20260928195000_slice1_unified_auth_sessions.sql, covered by the plain index
    # auth_session_account_operator_idx on the same two columns.
    "platform.auth_session.(account_operator_id, sign_in_kind)"
    " -> platform.operator.(id, sign_in_kind)",
    # 20260930120000_slice6_campaign_content_archive_and_crm_authoring.sql —
    # lifecycle columns on existing tables and the four new tables.
    # All sixteen are covered by plain unconditional indexes.
    "crm.organization.organization_archived_by_operator_id_fkey",
    "crm.person.person_archived_by_operator_id_fkey",
    "crm.contact_point.contact_point_deactivated_by_operator_id_fkey",
    "crm.organization_domain.organization_domain_removed_by_operator_id_fkey",
    "crm.external_identifier.external_identifier_removed_by_operator_id_fkey",
    "crm.note.note_author_operator_id_fkey",
    "crm.note.note_archived_by_operator_id_fkey",
    "crm.note.note_revision_of_note_id_fkey",
    "crm.note.note_root_note_id_fkey",
    "crm.organization_product_line.organization_product_line_organization_id_fkey",
    "crm.organization_product_line.organization_product_line_linked_by_operator_id_fkey",
    "crm.organization_product_line.organization_product_line_unlinked_by_operator_id_fkey",
    "outbound.campaign_content.campaign_content_campaign_id_fkey",
    "outbound.campaign_content.campaign_content_origin_source_record_id_fkey",
    "outbound.campaign_content_message.campaign_content_message_campaign_content_id_fkey",
    "outbound.campaign_content_message.campaign_content_message_send_attempt_id_fkey",
    # 20261005134832_slice7_catalog_products_suppliers.sql — one new column on each
    # of catalog.product and catalog.supplier_product, and the two new tables. All
    # six are covered by plain unconditional indexes: product_content_confirmed_by_idx,
    # product_image_product_idx (product_id leads it), product_image_created_by_idx,
    # supplier_product_recorded_by_idx, supplier_terms_pkey (the key is the
    # referencing column) and supplier_terms_updated_by_idx.
    "catalog.product.product_content_confirmed_by_operator_id_fkey",
    "catalog.product_image.product_image_product_id_fkey",
    "catalog.product_image.product_image_created_by_operator_id_fkey",
    "catalog.supplier_product.supplier_product_recorded_by_operator_id_fkey",
    "catalog.supplier_terms.supplier_terms_supplier_organization_id_fkey",
    "catalog.supplier_terms.supplier_terms_updated_by_operator_id_fkey",
    # 20261005140628_slice7_catalog_pricing_inputs_document_lines.sql — the three new
    # tables. All four are covered by plain unconditional indexes: fx_rate_recorded_by_idx,
    # cost_parameter_set_by_idx, document_line_key (the unique key that source_record_id
    # leads) and document_line_reviewed_by_idx.
    "catalog.fx_rate.fx_rate_recorded_by_operator_id_fkey",
    "catalog.cost_parameter.cost_parameter_set_by_operator_id_fkey",
    "evidence.document_line.document_line_source_record_id_fkey",
    "evidence.document_line.document_line_reviewed_by_operator_id_fkey",
    # 20261006180200_slice4_triage_review.sql — both covered by plain unconditional indexes:
    # triage_review_assertion_idx (assertion_id leads it) and triage_review_operator_idx.
    "evidence.triage_review.triage_review_assertion_id_fkey",
    "evidence.triage_review.triage_review_reviewed_by_operator_id_fkey",
    # 20261012120000_slice7_case_documents.sql — all five covered by plain unconditional
    # indexes: opportunity_document_{opportunity,quote_revision,source_record,attached_by,
    # removed_by}_idx.
    "crm.opportunity_document.opportunity_document_opportunity_id_fkey",
    "crm.opportunity_document.opportunity_document_quote_revision_id_fkey",
    "crm.opportunity_document.opportunity_document_source_record_id_fkey",
    "crm.opportunity_document.opportunity_document_attached_by_operator_id_fkey",
    "crm.opportunity_document.opportunity_document_removed_by_operator_id_fkey",
    # 20261013120000_slice7_catalog_product_document.sql — all four covered by plain unconditional
    # indexes: product_document_{product,supplier,origin,created_by}_idx.
    "catalog.product_document.product_document_product_id_fkey",
    "catalog.product_document.product_document_supplier_organization_id_fkey",
    "catalog.product_document.product_document_origin_source_record_id_fkey",
    "catalog.product_document.product_document_created_by_operator_id_fkey",
    # 20261014100000_slice7_catalog_product_merge.sql — covered by the plain product_merged_into_idx.
    "catalog.product.product_merged_into_product_id_fkey",
    # 20261015120000_slice7_case_memory.sql — covered by the plain subject_memory_source_record_idx.
    "evidence.subject_memory.subject_memory_source_record_id_fkey",
    # 20261016100000_slice7_quote_authoring.sql — both covered by plain unconditional indexes:
    # quote_line_supplier_organization_idx and quote_line_cost_observation_idx.
    "crm.quote_line.quote_line_supplier_organization_id_fkey",
    "crm.quote_line.quote_line_cost_observation_id_fkey",
    # 20261016200000_slice7_catalog_product_model_alias.sql — all four covered by plain unconditional
    # indexes: product_model_alias_{product,origin,created_by,retired_by}_idx.
    "catalog.product_model_alias.product_model_alias_product_id_fkey",
    "catalog.product_model_alias.product_model_alias_origin_source_record_id_fkey",
    "catalog.product_model_alias.product_model_alias_created_by_operator_id_fkey",
    "catalog.product_model_alias.product_model_alias_retired_by_operator_id_fkey",
    # 20261020100000_slice7_tender_bid_preparation.sql — all eight covered by plain unconditional
    # indexes: bidder_profile_set_by_idx, bidder_document_{added_by,retired_by}_idx,
    # bid_preparation_{opportunity,based_on,quote_revision,requested_by}_idx and
    # model_usage_bid_preparation_idx.
    "crm.bidder_profile.bidder_profile_set_by_operator_id_fkey",
    "crm.bidder_document.bidder_document_added_by_operator_id_fkey",
    "crm.bidder_document.bidder_document_retired_by_operator_id_fkey",
    "evidence.bid_preparation.bid_preparation_opportunity_id_fkey",
    "evidence.bid_preparation.bid_preparation_based_on_preparation_id_fkey",
    "evidence.bid_preparation.bid_preparation_quote_revision_id_fkey",
    "evidence.bid_preparation.bid_preparation_requested_by_operator_id_fkey",
    "platform.model_usage.model_usage_bid_preparation_id_fkey",
    # 20261020180000_slice7_notice_relevance_review.sql — both covered by plain unconditional
    # indexes: notice_review_notice_idx (notice_id leads it) and notice_review_operator_idx.
    "crm.notice_review.notice_review_notice_id_fkey",
    "crm.notice_review.notice_review_reviewed_by_operator_id_fkey",
    # 20261023100000_slice7_prospect_findings.sql — both covered by plain unconditional
    # indexes: prospect_review_record_idx (source_record_id leads it) and prospect_review_operator_idx.
    "crm.prospect_review.prospect_review_source_record_id_fkey",
    "crm.prospect_review.prospect_review_reviewed_by_operator_id_fkey",
    # 20261025100000_slice7_prospect_edit.sql — both covered by plain unconditional
    # indexes: prospect_edit_record_idx (source_record_id leads it) and prospect_edit_operator_idx.
    "crm.prospect_edit.prospect_edit_source_record_id_fkey",
    "crm.prospect_edit.prospect_edit_edited_by_operator_id_fkey",
    # 20261026100000_slice7_prospect_search.sql — both covered by conditional partial indexes
    # (prospect_search_run_requested_by_idx and model_usage_prospect_search_run_idx, both
    # WHERE col IS NOT NULL), so implied-partial coverage — not counted unconditionally.
    "evidence.prospect_search_run.prospect_search_run_requested_by_operator_id_fkey",
    "platform.model_usage.model_usage_prospect_search_run_id_fkey",
    # 20261028100000_slice7_hoy_todo.sql — created_by_operator_id is covered by a plain
    # unconditional index (todo_created_by_operator_idx); opportunity_id by a conditional partial
    # one (todo_opportunity_idx, WHERE opportunity_id IS NOT NULL), so implied-partial — not
    # counted unconditionally.
    "crm.todo.todo_opportunity_id_fkey",
    "crm.todo.todo_created_by_operator_id_fkey",
    # 20261029100000_slice7_person_name.sql — person_id is covered by a plain unconditional index
    # (person_name_person_idx); origin_source_record_id by a partial one (WHERE ... IS NOT NULL),
    # so implied-partial — not counted unconditionally.
    "crm.person_name.person_name_person_id_fkey",
    "crm.person_name.person_name_origin_source_record_id_fkey",
}

EXPECTED_FOREIGN_KEY_COUNT = COMMERCIAL_CASE_FOREIGN_KEY_COUNT + len(
    POST_COMMERCIAL_CASE_FOREIGN_KEYS
)

# Foreign keys covered by a non-partial index. The historical-origin key is
# covered only by a partial index; the slice-5 freeze key, the three
# campaign-block keys, the eight sign-in keys, the sixteen slice-6 keys and the
# ten slice-7 catalog and document-line keys and the later slice-7 keys (case documents, product
# documents, merge, memory, quote authoring, model aliases, tender bid preparation) by plain ones.
COMMERCIAL_CASE_COVERED_UNCONDITIONALLY = 86
POST_COMMERCIAL_CASE_COVERED_UNCONDITIONALLY = 73
EXPECTED_COVERED_UNCONDITIONALLY = (
    COMMERCIAL_CASE_COVERED_UNCONDITIONALLY
    + POST_COMMERCIAL_CASE_COVERED_UNCONDITIONALLY
)

EXPECTED_SUMMARIES = {
    "a04": {
        "relation_count": 65,
    },
    "a05": {
        "function_count": EXPECTED_FUNCTION_COUNT,
        "security_definer_count": len(EXPECTED_SECURITY_DEFINERS),
    },
    "a08": {
        "table_count": 65,
        "schema_count": 7,
    },
    "a09": {
        "policy_count": 230,
    },
    "a10": {
        "foreign_key_count": EXPECTED_FOREIGN_KEY_COUNT,
        "covered_count": EXPECTED_FOREIGN_KEY_COUNT,
        "covered_unconditionally": EXPECTED_COVERED_UNCONDITIONALLY,
    },
}


def refuse(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def main() -> int:
    if len(sys.argv) != 2:
        refuse("usage: assert_declared_local_head_gap.py REPORT.json")

    path = Path(sys.argv[1])
    report = json.loads(path.read_text(encoding="utf-8"))

    run = report.get("run") or {}
    if run.get("mode") != "local":
        refuse(f"report mode is {run.get('mode')!r}, expected 'local'")
    if run.get("simulated") is not False:
        refuse("report is simulated")
    if run.get("hosted_contacted") is not False:
        refuse("a local-gap report claims a hosted project was contacted")

    verdict = report.get("verdict") or {}
    if verdict.get("verdict") != "LOCAL_FAIL":
        refuse(
            f"verdict is {verdict.get('verdict')!r}, "
            "expected the frozen baseline to reject current local head"
        )

    if verdict.get("blocking_checks") != EXPECTED_BLOCKING:
        refuse(
            f"blocking checks are {verdict.get('blocking_checks')!r}; "
            f"expected {EXPECTED_BLOCKING!r}"
        )

    if verdict.get("incomplete_required_checks"):
        refuse(
            "required checks are incomplete: "
            f"{verdict['incomplete_required_checks']!r}"
        )

    checks = {
        entry["id"]: entry
        for entry in report.get("checks", [])
        if isinstance(entry, dict) and "id" in entry
    }

    # Every required check outside the declared schema-drift set must still PASS.
    for entry in report.get("checks", []):
        if (
            entry.get("required") is True
            and entry.get("id") not in EXPECTED_BLOCKING
            and entry.get("status") != "PASS"
        ):
            refuse(
                f"required check {entry.get('id')} is "
                f"{entry.get('status')}, expected PASS"
            )

    for check_id, wanted in EXPECTED_SUMMARIES.items():
        if check_id not in checks:
            refuse(f"report has no {check_id}")

        summary = checks[check_id].get("summary") or {}
        for key, expected in wanted.items():
            observed = summary.get(key)
            if observed != expected:
                refuse(
                    f"{check_id}.{key}: observed {observed!r}, "
                    f"expected {expected!r}"
                )

    # The inventory delta must be precisely the reviewed tables of
    # EXPECTED_EXTRA_TABLES: the three commercial-case tables,
    # outbound.campaign_block, the four sign-in tables, the four slice-6 tables,
    # the five slice-7 catalog and evidence tables, crm.notice_review, crm.prospect_review, crm.prospect_edit,
    # evidence.prospect_search_run and crm.todo.
    a08_findings = checks["a08"].get("findings") or []
    if len(a08_findings) != 2:
        refuse(f"a08 has unexpected findings: {a08_findings!r}")

    a08_text = "\n".join(a08_findings)
    if "baseline are absent here" in a08_text:
        refuse("a08 reports a table missing from current head")

    # Like a09 below, the audit lists at most twelve entries and then "...", and the
    # twenty-six extra tables do not fit. What it names must all be reviewed
    # tables; only an untruncated list must name each of them, and a truncated
    # one must name exactly the twelve the audit prints — so a name the pattern
    # cannot read is refused rather than skipped. The exact count bounds what the
    # truncation hides, and supabase/tests/010_inventory.sql pins every table by
    # name.
    a08_extra = [f for f in a08_findings if "present here and not in the baseline" in f]
    if len(a08_extra) != 1:
        refuse("a08 does not report the extra tables")
    a08_named = re.findall(r'"table":"([a-z0-9_]+)"', a08_extra[0])
    if a08_extra[0].rstrip().endswith("..."):
        if len(a08_named) != 12:
            refuse(f"a08 is truncated but names {len(a08_named)} tables, expected twelve")
    else:
        for table in sorted(EXPECTED_EXTRA_TABLES):
            if f'"table":"{table}"' not in a08_text:
                refuse(f"a08 does not name expected table {table}")

    unreviewed_tables = sorted(set(a08_named) - EXPECTED_EXTRA_TABLES)
    if unreviewed_tables:
        refuse(f"a08 names unreviewed tables: {unreviewed_tables!r}")

    if "32 entr(y|ies) are present here and not in the baseline" not in a08_text:
        refuse("a08 does not report exactly thirty-two extra tables")

    # The 93 policy additions must belong to those same twenty-nine reviewed tables, plus one each on
    # two existing tables (crm.quote_line and procurement.notice, below):
    # four each for the three commercial-case tables and campaign_block (api
    # select/insert/update, worker select), one each for auth_principal and
    # operator_profile (api select: their throttle is written by
    # platform.finish_pin_attempt, 20260929100000), two for auth_event (api
    # select, insert), three for auth_session (api select, insert, update), two
    # each for campaign_content and campaign_content_message (api and worker
    # select), four each for note and organization_product_line (api
    # select/insert/update, worker select), three each for product_image and
    # supplier_terms (api select/insert/update), five for document_line (api
    # select/insert/update; worker select/insert since 20261012100000) and two each for
    # fx_rate and cost_parameter (api select, insert: both are append-only), three for
    # triage_review (api select, insert; worker select) and four for opportunity_document
    # (api select/insert/update, worker select; 20261012120000), three for product_document
    # (api select/insert/update; 20261013120000), three for subject_memory (api select; worker
    # select/insert; 20261015120000), one delete on quote_line (api; 20261016100000 — an existing
    # Slice 0 table, so not on a reviewed new table), four for product_model_alias (api
    # select/insert/update, worker select; 20261016200000) and, from 20261020100000, three for
    # bidder_profile (api select/insert, worker select), four for bidder_document (api
    # select/insert/update, worker select), four for bid_preparation (api select/insert, worker
    # select/update), three for model_usage (worker select/insert, api select), one worker select
    # on product_document and one api insert on notice (an existing Slice 0 table); and from
    # 20261020180000, three for notice_review (api select/insert, worker select); and from
    # 20261023100000, three for prospect_review (api select/insert, worker select); and from
    # 20261025100000, three for prospect_edit (api select/insert, worker select); from
    # 20261026100000, five for prospect_search_run; and from 20261028100000, three for todo
    # (api select/insert/update); and from 20261029100000, four for person_name
    # (api select/insert/update, worker select).
    a09_findings = checks["a09"].get("findings") or []
    if len(a09_findings) != 3:
        refuse(f"a09 has unexpected findings: {a09_findings!r}")

    absent = [f for f in a09_findings if "in the baseline are absent here" in f]
    if len(absent) != 1 or "2 entr(y|ies) in the baseline are absent here: " not in absent[0]:
        refuse("a09 does not report exactly the two reviewed absent policies")
    try:
        removed = {
            (e["schema"], e["table"], e["policy"], e["command"], e["roles"])
            for e in map(json.loads, absent[0].split(" are absent here: ", 1)[1].split("; "))
        }
    except (ValueError, KeyError, TypeError):
        refuse(f"a09's absent policies do not parse: {absent[0]!r}")
    if removed != EXPECTED_ABSENT_POLICIES:
        refuse(f"a09 reports unreviewed absent policies: {sorted(removed)!r}")

    extra = [f for f in a09_findings if "present here and not in the baseline" in f]
    if len(extra) != 1:
        refuse("a09 does not report the extra policies")
    a09_text = extra[0]
    if "105 entr(y|ies) are present here and not in the baseline" not in a09_text:
        refuse("a09 does not report exactly one hundred and one extra policies")

    # The audit lists at most twelve entries and then "...", so a long delta cannot name every
    # table. What it does name must all be reviewed tables; only an untruncated list must
    # name each of them, and a truncated one must name exactly the twelve policies the audit
    # prints — so a table name the pattern cannot read is refused rather than skipped (the
    # same rule as a08). The exact count above bounds what the truncation hides.
    a09_named = re.findall(r'"table":"([a-z0-9_]+)"', a09_text)
    if a09_text.rstrip().endswith("...") and len(a09_named) != 12:
        refuse(f"a09 is truncated but names {len(a09_named)} tables, expected twelve")
    named = set(a09_named)
    unexpected = sorted(named - EXPECTED_EXTRA_TABLES - EXPECTED_EXTRA_POLICY_ONLY_TABLES)
    if unexpected:
        refuse(f"a09 names policies on unreviewed tables: {unexpected!r}")
    if not a09_text.rstrip().endswith("..."):
        for table in sorted(EXPECTED_EXTRA_TABLES - named):
            refuse(f"a09 does not name expected table {table}")

    # The other blockers are census changes only. An exact finding list is
    # what keeps this strict: a forbidden EXECUTE grant, an effective EXECUTE
    # for a Data-API-facing role or a SECURITY DEFINER function other than the
    # reviewed one would each add or change an a05 finding, and an uncovered
    # foreign key an a10 finding.
    expected_findings = {
        "a04": ["relations in scope: observed 65, expected 34"],
        "a05": [
            "SECURITY DEFINER functions (the closed list of ARCHITECTURE.md §6.2): "
            f"{len(EXPECTED_SECURITY_DEFINERS)} entr(y|ies) are present here and not in the baseline: "
            + EXPECTED_SECURITY_DEFINER,
            f"functions in scope: observed {EXPECTED_FUNCTION_COUNT}, "
            f"expected {SLICE0_FUNCTION_COUNT}",
        ],
        "a10": [f"foreign keys: observed {EXPECTED_FOREIGN_KEY_COUNT}, expected 102"],
    }

    for check_id, expected in expected_findings.items():
        findings = checks[check_id].get("findings") or []
        if findings != expected:
            refuse(f"{check_id} findings changed: {findings!r}")

    print(
        "ok: current local head differs from frozen Slice 0 only by the "
        "reviewed commercial-case, historical-quotation, slice-5 campaign, W10 "
        "unsubscribe, campaign-block, shared-sign-in, slice-6 campaign-content-archive "
        "and crm-authoring, slice-7 catalog and document-line, notice-relevance-review, "
        "prospect-findings, prospect-edit and prospect-search-run schema delta, "
        "less the revoked runtime writes on platform.operator"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

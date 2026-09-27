#!/usr/bin/env python3
"""Assert the exact, reviewed gap between frozen Slice 0 and current local head.

The Slice 0 audit baseline intentionally describes the frozen hosted foundation:
33 tables / 127 policies / 102 FKs.

Current repository head is later and intentionally carries the commercial-case
schema, the historical-quotation import, the slice-5 campaign schema, W10 and
the campaign safety blocks (outbound.campaign_block). Therefore a local audit of current
head must conclude LOCAL_FAIL — but only for the exact reviewed post-Slice-0
additions.

This checker does not regenerate, weaken or modify the baseline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


EXPECTED_BLOCKING = ["a04", "a05", "a08", "a09", "a10"]

EXPECTED_EXTRA_TABLES = {
    "opportunity_organization",
    "opportunity_interest",
    "opportunity_evidence",
    # 20260927234500_slice5_campaign_block.sql — DOMAIN.md §7 #37.
    "campaign_block",
}

# Functions added after Slice 0, by migration. The audit reports only a count
# here; which functions they are, and that each is SECURITY INVOKER with
# search_path = pg_catalog and no EXECUTE for a Data-API-facing role, is pinned
# by pgTAP (supabase/tests/010_inventory.sql, 063_commercial_case_commands.sql,
# 064_historical_quotation_import.sql, 065-068 for the slice-5 campaign
# triggers, 069 for W10, 071 for the campaign blocks and 072 for the archived-campaign guard). This file refuses any count other than 3 + len(these).
# The one exception to SECURITY INVOKER is EXPECTED_SECURITY_DEFINER below.
SLICE0_FUNCTION_COUNT = 3

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
    # 20260927234500_slice5_campaign_block.sql — the block's lifecycle guard,
    # the campaign-level refusals and their enforcement trigger (all INVOKER).
    "outbound.campaign_block_guard",
    "outbound.campaign_hold_refusals",
    "outbound.campaign_hold_guard",
    # 20260928090000_slice5_archived_campaign_immutable.sql — the INVOKER trigger
    # function that keeps an archived campaign, its audience and attempts as imported.
    "outbound.archived_campaign_immutable",
}

# The closed SECURITY DEFINER list of ARCHITECTURE.md §6.2, as built so far: its
# first entry, from 20260927230000_slice5_w10_unsubscribe.sql. a05 must report
# exactly this one entry beyond the (empty) Slice 0 baseline, with this owner,
# signature and pinned search_path — anything else is refused.
EXPECTED_SECURITY_DEFINER = (
    '{"arguments":"p_kind text, p_purpose text, p_address text, p_reason text, '
    'p_operator_id uuid, p_command_receipt_id uuid, p_evidence jsonb",'
    '"name":"add_contact_control","owner":"origenlab_owner",'
    '"proconfig":"search_path=pg_catalog","schema":"outbound"}'
)

EXPECTED_FUNCTION_COUNT = SLICE0_FUNCTION_COUNT + len(POST_SLICE0_FUNCTIONS)

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
    # 20260927234500_slice5_campaign_block.sql, each covered by its own plain
    # index (campaign_block_campaign_idx, campaign_block_placed_by_operator_idx,
    # campaign_block_lifted_by_operator_idx).
    "outbound.campaign_block.campaign_id -> outbound.campaign.id",
    "outbound.campaign_block.placed_by_operator_id -> platform.operator.id",
    "outbound.campaign_block.lifted_by_operator_id -> platform.operator.id",
}

EXPECTED_FOREIGN_KEY_COUNT = COMMERCIAL_CASE_FOREIGN_KEY_COUNT + len(
    POST_COMMERCIAL_CASE_FOREIGN_KEYS
)

# Foreign keys covered by a non-partial index. The historical-origin key is
# covered only by a partial index; the slice-5 freeze key and the three
# campaign-block keys by plain ones.
COMMERCIAL_CASE_COVERED_UNCONDITIONALLY = 86
POST_COMMERCIAL_CASE_COVERED_UNCONDITIONALLY = 4
EXPECTED_COVERED_UNCONDITIONALLY = (
    COMMERCIAL_CASE_COVERED_UNCONDITIONALLY
    + POST_COMMERCIAL_CASE_COVERED_UNCONDITIONALLY
)

EXPECTED_SUMMARIES = {
    "a04": {
        "relation_count": 38,
    },
    "a05": {
        "function_count": EXPECTED_FUNCTION_COUNT,
        "security_definer_count": 1,
    },
    "a08": {
        "table_count": 37,
        "schema_count": 7,
    },
    "a09": {
        "policy_count": 143,
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

    # The inventory delta must be precisely the three commercial-case tables and
    # outbound.campaign_block.
    a08_findings = checks["a08"].get("findings") or []
    if len(a08_findings) != 2:
        refuse(f"a08 has unexpected findings: {a08_findings!r}")

    a08_text = "\n".join(a08_findings)
    if "baseline are absent here" in a08_text:
        refuse("a08 reports a table missing from current head")

    for table in EXPECTED_EXTRA_TABLES:
        if f'"table":"{table}"' not in a08_text:
            refuse(f"a08 does not name expected table {table}")

    if "4 entr(y|ies) are present here and not in the baseline" not in a08_text:
        refuse("a08 does not report exactly four extra tables")

    # The 16 policy additions must belong to those same four reviewed tables:
    # four each (api select/insert/update, worker select).
    a09_findings = checks["a09"].get("findings") or []
    if len(a09_findings) != 2:
        refuse(f"a09 has unexpected findings: {a09_findings!r}")

    a09_text = "\n".join(a09_findings)
    if "baseline are absent here" in a09_text:
        refuse("a09 reports a baseline policy missing from current head")

    if "16 entr(y|ies) are present here and not in the baseline" not in a09_text:
        refuse("a09 does not report exactly sixteen extra policies")

    for table in EXPECTED_EXTRA_TABLES:
        if f'"table":"{table}"' not in a09_text:
            refuse(f"a09 does not name expected table {table}")

    # The other blockers are census changes only. An exact finding list is
    # what keeps this strict: a forbidden EXECUTE grant, an effective EXECUTE
    # for a Data-API-facing role or a SECURITY DEFINER function other than the
    # reviewed one would each add or change an a05 finding, and an uncovered
    # foreign key an a10 finding.
    expected_findings = {
        "a04": ["relations in scope: observed 38, expected 34"],
        "a05": [
            "SECURITY DEFINER functions (the closed list of ARCHITECTURE.md §6.2): "
            "1 entr(y|ies) are present here and not in the baseline: "
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
        "unsubscribe and campaign-block schema delta"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Operator command: import a complete attachment ZIP already on local disk.

The operator must have already downloaded the ZIP themselves, through their
own browser, from Mercado Público's own UI -- this command performs zero
network I/O and never touches ``ViewAttachment.aspx`` or any reCAPTCHA flow.
It requires an explicit ``--tender-code`` and an explicit ``--zip`` path; it
never infers or searches for either.

This command only imports, validates, and reports (extraction + T1 run in
memory purely to surface a preview in the printed summary). It never calls
``publish_tender_terms`` -- publication stays a separate, explicit step via
the existing tender-terms publish workflow.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.chilecompra_anexo_evidence.acquisition_provenance import (
    build_acquisition_provenance,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (
    ACQUISITION_SOURCE_OPERATOR_COMPLETE_BUNDLE,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.operator_import import (
    OperatorZipAttachmentSource,
    OperatorZipImportError,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.planner import build_tender_bundle
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.constants import (
    FACT_STATE_CONFLICTING,
    FACT_STATE_DERIVED,
    FACT_STATE_EXPLICIT,
    FACT_STATE_NOT_EXPLICITLY_FOUND,
    FACT_STATE_UNKNOWN,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.extract import (
    extract_tender_terms,
)


@dataclass(frozen=True)
class ImportTenderAnnexBundleOptions:
    tender_code: str
    zip_path: Path
    declare_complete: bool = False
    json_output: bool = False


def _iso_now(now: datetime) -> str:
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _fact_state_counts(facts: tuple) -> dict[str, int]:
    counts = {
        FACT_STATE_EXPLICIT: 0,
        FACT_STATE_DERIVED: 0,
        FACT_STATE_NOT_EXPLICITLY_FOUND: 0,
        FACT_STATE_UNKNOWN: 0,
        FACT_STATE_CONFLICTING: 0,
    }
    for fact in facts:
        counts[fact.state] = counts.get(fact.state, 0) + 1
    return counts


def build_import_tender_annex_bundle_result(
    options: ImportTenderAnnexBundleOptions,
    *,
    now_fn: Any = None,
) -> dict[str, Any]:
    """Pure domain result for one operator ZIP import.

    No printing, no argparse coupling, no CLI-specific exit codes -- this is
    the single reusable seam a future HTTP adapter (e.g. a
    ``POST /operator/procurement/tenders/{tender_code}/annex-bundle`` route)
    calls to get the exact same payload shape the CLI prints as JSON. Both
    outcomes (``"rejected"`` and ``"imported"``) are represented as data in
    the returned dict rather than as a raised exception, so callers never
    need CLI-specific error handling to consume this.
    """
    now = (now_fn or (lambda: datetime.now(timezone.utc)))()
    acquired_at = _iso_now(now)

    source = OperatorZipAttachmentSource(
        zip_path=options.zip_path,
        tender_code=options.tender_code,
        declare_complete=options.declare_complete,
    )

    try:
        bundle = build_tender_bundle(options.tender_code, source)
    except OperatorZipImportError as exc:
        return {
            "command": "import-tender-annex-bundle",
            "tender_code": options.tender_code,
            "zip_path": str(options.zip_path),
            "declare_complete": options.declare_complete,
            "result": "rejected",
            "error": str(exc),
            "published": False,
            "persisted": False,
        }

    provenance = build_acquisition_provenance(
        bundle,
        acquisition_source=ACQUISITION_SOURCE_OPERATOR_COMPLETE_BUNDLE,
        acquired_at=acquired_at,
        operator_declared_complete=options.declare_complete,
    )

    terms_bundle = extract_tender_terms(bundle)
    fact_counts = _fact_state_counts(terms_bundle.tender_facts)

    import_result = source.loaded_import_result()

    return {
        "command": "import-tender-annex-bundle",
        "result": "imported",
        "tender_code": options.tender_code,
        "zip_path": str(options.zip_path),
        "zip_sha256": import_result.zip_sha256,
        "declare_complete": options.declare_complete,
        "provenance": provenance.to_dict(),
        "bundle_complete": bundle.bundle_complete,
        "incomplete_reason_codes": list(bundle.incomplete_reason_codes),
        "attachments_discovered": bundle.attachments_discovered,
        "attachments_downloaded": bundle.attachments_downloaded,
        "rejected_zip_entries": list(import_result.rejected_entries),
        "tender_facts_extracted": len(terms_bundle.tender_facts),
        "tender_facts_by_state": fact_counts,
        "coverage_status": terms_bundle.coverage.fact_coverage_status,
        "contact_authorization": terms_bundle.contact_authorization,
        "outreach_authorization": terms_bundle.outreach_authorization,
        "published": False,
        "persisted": False,
    }


def _print_text_report(payload: dict[str, Any]) -> None:
    print("import-tender-annex-bundle")
    print(f"tender_code={payload['tender_code']}")
    print(f"zip_path={payload['zip_path']}")

    if payload["result"] == "rejected":
        print("result=rejected")
        print(f"error={payload['error']}")
        return

    print(f"zip_sha256={payload['zip_sha256']}")
    print(f"declare_complete={'true' if payload['declare_complete'] else 'false'}")
    print(f"completeness_state={payload['provenance']['completeness_state']}")
    print(f"completeness_reason={payload['provenance']['completeness_reason']}")
    print(f"bundle_complete={'true' if payload['bundle_complete'] else 'false'}")
    print(f"attachments_discovered={payload['attachments_discovered']}")
    print(f"attachments_downloaded={payload['attachments_downloaded']}")
    print(f"rejected_zip_entries={len(payload['rejected_zip_entries'])}")
    for line in payload["rejected_zip_entries"]:
        print(f"  rejected: {line}")
    print(f"tender_facts_extracted={payload['tender_facts_extracted']}")
    for state, count in sorted(payload["tender_facts_by_state"].items()):
        print(f"  facts[{state}]={count}")
    print(f"coverage_status={payload['coverage_status']}")
    print(f"contact_authorization={'true' if payload['contact_authorization'] else 'false'}")
    print(f"outreach_authorization={'true' if payload['outreach_authorization'] else 'false'}")
    print(
        "Not published. Run the existing tender-terms publish workflow "
        "separately (publish_tender_terms) to promote this into "
        "reports/out/active/current/tender_terms/."
    )


def run_import_tender_annex_bundle(
    options: ImportTenderAnnexBundleOptions,
    *,
    now_fn: Any = None,
) -> int:
    """Thin CLI adapter: format ``build_import_tender_annex_bundle_result`` for stdout."""
    payload = build_import_tender_annex_bundle_result(options, now_fn=now_fn)

    if options.json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_text_report(payload)

    return 2 if payload["result"] == "rejected" else 0


def parse_import_tender_annex_bundle_args(argv: list[str]) -> ImportTenderAnnexBundleOptions:
    parser = argparse.ArgumentParser(prog="import-tender-annex-bundle", add_help=True)
    parser.add_argument(
        "--tender-code",
        required=True,
        help="Exact tender code (e.g. 2410-66-LP26); never inferred or looked up",
    )
    parser.add_argument(
        "--zip",
        dest="zip_path",
        required=True,
        type=Path,
        help="Path to a ZIP already downloaded by the operator through their own browser",
    )
    parser.add_argument(
        "--declare-complete",
        action="store_true",
        help=(
            "Explicit operator assertion that this ZIP is Mercado Público's complete "
            "attachment inventory for this tender. Never inferred from file count, "
            "filenames, or any other source's result count -- without this flag, "
            "completeness stays unknown/incomplete regardless of how many files the "
            "ZIP contains."
        ),
    )
    parser.add_argument("--json", dest="json_output", action="store_true", help="Emit structured JSON")
    ns = parser.parse_args(argv)
    return ImportTenderAnnexBundleOptions(
        tender_code=ns.tender_code,
        zip_path=ns.zip_path,
        declare_complete=ns.declare_complete,
        json_output=ns.json_output,
    )


def print_import_tender_annex_bundle_help() -> None:
    print(
        "import-tender-annex-bundle — import an operator-supplied complete attachment ZIP\n\n"
        "  uv run origenlab import-tender-annex-bundle --tender-code 2410-66-LP26 "
        "--zip /path/to/file.zip\n"
        "  uv run origenlab import-tender-annex-bundle --tender-code 2410-66-LP26 "
        "--zip /path/to/file.zip --declare-complete\n\n"
        "Validates the ZIP in memory (zip-slip, symlink, encrypted-entry, and zip-bomb "
        "safety bounds), runs it through the existing extraction and ANEXO-T1 pipelines "
        "unchanged, and prints a summary. Never launches or automates a browser, never "
        "makes a network request, and never publishes -- run the existing tender-terms "
        "publish workflow separately.\n"
    )

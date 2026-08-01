#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY: fixture/offline only. Never authenticated Mercado Público requests.
# Never --apply. Never reads or prints CHILECOMPRA_API_TICKET values.
# -----------------------------------------------------------------------------
"""Build a PR5B acquisition snapshot from fixtures or offline JSON artifacts."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.chilecompra_api import TICKET_ENV_VAR  # noqa: E402
from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (  # noqa: E402
    digest_difference_notes,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.provenance import (  # noqa: E402
    provenance_document,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.redaction import (  # noqa: E402
    assert_no_ticket_leak,
    is_ticket_configured_boolean_only,
    ticket_safety_flags,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.snapshot import (  # noqa: E402
    build_acquisition_snapshot,
    snapshot_manifest,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.walkthrough import (  # noqa: E402
    build_walkthrough_cases,
    render_walkthrough_markdown,
)


def _ticket_configured_boolean_only() -> bool:
    """Boolean-only check — never returns, prints, hashes, or passes the ticket value."""
    env_val = os.environ.get(TICKET_ENV_VAR)
    if is_ticket_configured_boolean_only(env_val):
        return True
    for p in (_ROOT / ".env", Path.home() / ".config/origenlab/.env"):
        if not p.is_file():
            continue
        for line in p.read_text(errors="ignore").splitlines():
            if line.strip().startswith("#"):
                continue
            if line.strip().startswith(f"{TICKET_ENV_VAR}="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if is_ticket_configured_boolean_only(val):
                    return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-kind",
        choices=("ticket_summary", "ticket_detail", "ocds", "walkthrough"),
        required=True,
    )
    parser.add_argument("--fixture-path", type=Path, default=None)
    parser.add_argument("--offline-artifact-path", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--tender-code", default=None)
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--month", type=int, default=1)
    parser.add_argument("--range-start", type=int, default=1)
    parser.add_argument("--range-end", type=int, default=1)
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=_ROOT / "tests/fixtures/commercial_procurement_acquisition",
    )
    # Explicitly reject network/apply modes.
    parser.add_argument("--apply", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--network", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--ticket", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.apply or args.network or args.ticket is not None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "authenticated_or_apply_mode_rejected_in_pr5b",
                }
            )
        )
        return 2

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    ticket_configured = _ticket_configured_boolean_only()
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    if args.source_kind == "walkthrough":
        bundle = build_walkthrough_cases(args.fixtures_dir)
        md = render_walkthrough_markdown(bundle)
        (out / "DATA_WALKTHROUGH.json").write_text(
            json.dumps(bundle, indent=2, sort_keys=True, default=str) + "\n"
        )
        (out / "DATA_WALKTHROUGH.md").write_text(md)
        (out / "PROVENANCE.json").write_text(
            json.dumps(provenance_document(), indent=2, sort_keys=True) + "\n"
        )
        (out / "DIGEST_NOTES.json").write_text(
            json.dumps(digest_difference_notes(), indent=2, sort_keys=True) + "\n"
        )
        proof = {
            **ticket_safety_flags(ticket_configured=ticket_configured),
            "production_apply": False,
            "production_mutation": False,
            "scheduler_changed": False,
            "generated_at_utc": now,
            "fixture_origin": bundle["fixture_origin"],
        }
        (out / "NO_NETWORK_PROOF.json").write_text(
            json.dumps(proof, indent=2, sort_keys=True) + "\n"
        )
        (out / "NO_MUTATION_PROOF.json").write_text(
            json.dumps({**proof, "status": "NO_MUTATION"}, indent=2, sort_keys=True)
            + "\n"
        )
        summary = {
            "ok": True,
            "mode": "walkthrough",
            "out_dir": str(out),
            "cases": list(bundle["cases"].keys()),
            "authenticated_request_performed": False,
        }
        blob = "\n".join(p.read_text(encoding="utf-8") for p in out.glob("*.json"))
        assert_no_ticket_leak(blob)
        print(json.dumps(summary, sort_keys=True))
        return 0

    src = args.fixture_path or args.offline_artifact_path
    if src is None or not src.is_file():
        print(json.dumps({"ok": False, "error": "fixture_or_offline_path_required"}))
        return 2
    payload = json.loads(src.read_text(encoding="utf-8"))
    raw = src.read_bytes()
    origin = (
        "fixture"
        if args.fixture_path
        else "offline_artifact_unsanitized_local_only"
    )
    snap = build_acquisition_snapshot(
        source_kind=args.source_kind,
        payload=payload,
        fixture_origin=origin,
        acquired_at_utc=now,
        original_bytes=raw,
        tender_code=args.tender_code,
        year=args.year,
        month=args.month,
        range_start=args.range_start,
        range_end=args.range_end,
    )
    manifest = snapshot_manifest(snap, ticket_configured=ticket_configured)
    (out / "SNAPSHOT_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    (out / "QUERY.json").write_text(
        json.dumps(snap.query.to_dict(), indent=2, sort_keys=True) + "\n"
    )
    (out / "PAGE_INDEX.json").write_text(
        json.dumps([p.to_dict() for p in snap.pages], indent=2, sort_keys=True) + "\n"
    )
    with (out / "SOURCE_OBSERVATIONS.jsonl").open("w", encoding="utf-8") as fh:
        for o in snap.source_observations:
            fh.write(json.dumps(o.to_dict(), sort_keys=True) + "\n")
    with (out / "TENDER_OBSERVATIONS.jsonl").open("w", encoding="utf-8") as fh:
        for t in snap.tender_observations:
            fh.write(json.dumps(t.to_dict(), sort_keys=True) + "\n")
    with (out / "LINE_OBSERVATIONS.jsonl").open("w", encoding="utf-8") as fh:
        for line in snap.line_observations:
            fh.write(json.dumps(line.to_dict(), sort_keys=True) + "\n")
    (out / "PARSER_DIAGNOSTICS.json").write_text(
        json.dumps(snap.diagnostics, indent=2, sort_keys=True) + "\n"
    )
    (out / "FINGERPRINTS.json").write_text(
        json.dumps(
            {
                "source_fingerprint": snap.source_fingerprint,
                "normalized_semantic_digest": snap.normalized_semantic_digest,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    proof = {
        **ticket_safety_flags(ticket_configured=ticket_configured),
        "production_apply": False,
        "production_mutation": False,
        "input_path": str(src.name),
        "generated_at_utc": now,
    }
    (out / "NO_NETWORK_PROOF.json").write_text(
        json.dumps(proof, indent=2, sort_keys=True) + "\n"
    )
    (out / "NO_MUTATION_PROOF.json").write_text(
        json.dumps({**proof, "status": "NO_MUTATION"}, indent=2, sort_keys=True) + "\n"
    )
    summary = {
        "ok": True,
        "snapshot_id": snap.snapshot_id,
        "completeness_status": snap.completeness_status,
        "source_fingerprint": snap.source_fingerprint,
        "normalized_semantic_digest": snap.normalized_semantic_digest,
        "counts": {
            "pages": len(snap.pages),
            "source_observations": len(snap.source_observations),
            "tender_observations": len(snap.tender_observations),
            "line_observations": len(snap.line_observations),
        },
        "authenticated_request_performed": False,
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

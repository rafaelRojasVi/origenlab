#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY: production SQLite opened mode=ro + query_only only.
# Case D uses disposable fixture apply (allow_fixture_apply=True).
# Case A disposable materialization writes a sliced plan to temp SQLite only.
# Never runs production --apply. Output is deterministically redacted.
# -----------------------------------------------------------------------------
"""Generate PR4 commercial procurement data walkthrough (markdown + JSON)."""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.commercial_identity.builder import (  # noqa: E402
    require_explicit_sqlite_path,
)
from origenlab_email_pipeline.commercial_procurement.builder import (  # noqa: E402
    parse_as_of_date,
)
from origenlab_email_pipeline.commercial_procurement.walkthrough import (  # noqa: E402
    AS_OF_DEFAULT,
    build_walkthrough,
    render_markdown,
    write_json_artifacts,
)
from origenlab_email_pipeline.commercial_procurement.walkthrough_redaction import (  # noqa: E402
    assert_no_pii_leaks,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-path", type=Path, required=True)
    parser.add_argument("--as-of-date", default=AS_OF_DEFAULT.isoformat())
    parser.add_argument("--write-markdown", action="store_true")
    parser.add_argument("--write-json", action="store_true")
    parser.add_argument(
        "--markdown-path",
        type=Path,
        default=(
            _ROOT
            / "reports/out/active/current/commercial_procurement_data_walkthrough_2026-07-30"
            / "COMMERCIAL_PROCUREMENT_DATA_WALKTHROUGH_PR4.md"
        ),
    )
    parser.add_argument(
        "--json-dir",
        type=Path,
        default=(
            _ROOT
            / "reports/out/active/current/commercial_procurement_data_walkthrough_2026-07-30"
        ),
    )
    args = parser.parse_args(argv)
    path = require_explicit_sqlite_path(args.sqlite_path)
    as_of = parse_as_of_date(args.as_of_date)

    with tempfile.TemporaryDirectory(prefix="proc_walkthrough_") as tmp:
        tmp_path = Path(tmp)
        synthetic_db = tmp_path / "synthetic_case_d.sqlite"
        case_a_db = tmp_path / "case_a_disposable.sqlite"
        bundle = build_walkthrough(
            sqlite_path=path,
            as_of=as_of,
            synthetic_db=synthetic_db,
            case_a_disposable_db=case_a_db,
        )

    md = render_markdown(bundle)
    forbidden = bundle.forbidden_domains
    assert_no_pii_leaks(md, forbidden_domains=forbidden)
    for payload in (
        bundle.summary,
        bundle.case_a,
        bundle.case_b,
        bundle.case_c,
        bundle.case_d,
    ):
        assert_no_pii_leaks(json_dumps_safe(payload), forbidden_domains=forbidden)

    if args.write_markdown:
        args.markdown_path.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_path.write_text(md, encoding="utf-8")
        print(f"wrote_markdown={args.markdown_path}")
    if args.write_json:
        write_json_artifacts(bundle, args.json_dir)
        print(f"wrote_json_dir={args.json_dir}")

    print(
        "generated_at_utc="
        f"{datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')}"
    )
    print(f"case_a_route={bundle.case_a['selected_key']['link_route']}")
    print(f"case_b_conflict={bundle.case_b['selected_key']['conflict_id']}")
    print(f"case_c_field={bundle.case_c['selected_key']['both_equal_origin_field']}")
    print(f"case_d_status={bundle.case_d['resolution']['status']}")
    print(
        "production_pr4_tables="
        f"{bundle.summary['production_pr4_tables_after_walkthrough']}"
    )
    return 0


def json_dumps_safe(payload: object) -> str:
    import json

    return json.dumps(payload, sort_keys=True, default=str)


if __name__ == "__main__":
    raise SystemExit(main())

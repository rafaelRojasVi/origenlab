#!/usr/bin/env python3
"""Turn a research run's `suggestions.json` into the reviewed file the API reads.

    uv run python scripts/build_org_suggestions.py \
        --in  ~/data/origenlab-v2-migration/audits/org-enrichment-20261004/suggestions.json \
        --out ~/data/origenlab-v2-migration/audits/org-enrichment-20261004/org-suggestions.json

The output names customers and this repository is public: an output path inside the repository is
refused (exit 2). The file is validated with the API's own validator before it is written (mode
0600); the summary printed is counts only, never a name.

`rut_source` is `official` when a source that shows the RUT is the institution's own site (its
website or email domain) or a `gob.cl` / `sii.cl` page, and `directory` otherwise: the card then
asks for an SII check and «Aplicar todo» skips it. A RUT with a wrong check digit, and a `type`
that is not a CRM `kind` token, are dropped and counted.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from origenlab_api.v2.org_web_suggestions import FILE_VERSION, OrgSuggestionsError, rut_key, valid_rut, validate_file

REPO_ROOT = Path(__file__).resolve().parents[3]
_EMPTY = frozenset({"", "none", "null", "n/a", "-", "—"})
_KIND = re.compile(r"^[a-z][a-z_]{0,39}$")
_STATE_HOSTS = ("gob.cl", "sii.cl")


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in _EMPTY else text


def _host(url: str | None) -> str:
    return ((urlparse(url).hostname or "") if url else "").lower().removeprefix("www.")


def _under(host: str, domain: str) -> bool:
    return bool(domain) and (host == domain or host.endswith("." + domain))


def rut_source(entry: dict[str, Any]) -> str:
    own = [d for d in (_host(_clean(entry.get("website"))), (_clean(entry.get("email_domain")) or "").lower()) if d]
    for source in entry.get("sources") or []:
        if "rut" not in str(source.get("shows") or "").lower():
            continue
        host = _host(source.get("url"))
        if any(_under(host, d) for d in (*_STATE_HOSTS, *own)):
            return "official"
    return "directory"


def build(research: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, int]]:
    counts = {"organizations": 0, "rut_official": 0, "rut_directory": 0,
              "rut_dropped_check_digit": 0, "type_dropped_shape": 0}
    organizations: dict[str, Any] = {}
    for entry in research:
        rut, source = _clean(entry.get("rut")), None
        if rut is not None:
            rut = rut_key(rut)
            if valid_rut(rut):
                source = rut_source(entry)
                counts[f"rut_{source}"] += 1
            else:
                counts["rut_dropped_check_digit"] += 1
                rut = None
        kind = _clean(entry.get("type"))
        if kind is not None and not _KIND.match(kind):
            counts["type_dropped_shape"] += 1
            kind = None
        domain = _clean(entry.get("email_domain"))
        organizations[str(entry["organization_id"]).strip().lower()] = {
            "display_name": _clean(entry.get("display_name")),
            "legal_name": _clean(entry.get("legal_name")),
            "rut": rut,
            "rut_source": source,
            "website": _clean(entry.get("website")),
            "email_domain": domain.lower() if domain else None,
            "type": kind,
            "city": _clean(entry.get("city")),
            "region": _clean(entry.get("region")),
            "confidence": str(entry.get("confidence") or "").strip(),
            "sources": [
                {"url": str(s.get("url") or "").strip(), "shows": _clean(s.get("shows"))}
                for s in entry.get("sources") or []
            ],
            "notes": _clean(entry.get("notes")),
        }
        counts["organizations"] += 1
    data = {"version": FILE_VERSION, "organizations": organizations}
    validate_file(data)
    return data, counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--in", dest="source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    out, root = args.out.expanduser().resolve(), REPO_ROOT.resolve()
    if out == root or root in out.parents:
        print("refused: the output names customers and must live outside the repository (it is public)",
              file=sys.stderr)
        return 2
    try:
        research = json.loads(args.source.expanduser().read_text(encoding="utf-8"))
        if not isinstance(research, list):
            raise OrgSuggestionsError("the research file must be a JSON list")
        data, counts = build(research)
    except (OSError, ValueError, KeyError) as exc:
        print(f"refused: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    out.chmod(0o600)
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

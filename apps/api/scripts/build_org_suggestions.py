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
asks for an SII check and «Aplicar todo» skips it. A RUT with a wrong check digit, a `type`
that is not a CRM `kind` token, and a free-mail `email_domain` (gmail.com, …) are dropped and
counted.

    uv run python scripts/build_org_suggestions.py --check PATH

loads a built (possibly hand-edited) file with the API's own validator and prints the same
summary, or the first error: exit 0 if valid, 1 if not. Run it after any hand edit, before upload.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from origenlab_api.v2.org_web_suggestions import (
    _KIND,
    FILE_VERSION,
    OrgSuggestionsError,
    rut_key,
    valid_rut,
    validate_file,
)
from origenlab_api.v2.quote_crm_promotion import FREE_MAIL_DOMAINS

REPO_ROOT = Path(__file__).resolve().parents[3]
_EMPTY = frozenset({"", "none", "null", "n/a", "-", "—"})
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
              "rut_dropped_check_digit": 0, "type_dropped_shape": 0, "domain_dropped_free_mail": 0}
    organizations: dict[str, Any] = {}
    for entry in research:
        domain = _clean(entry.get("email_domain"))
        if domain is not None and domain.lower() in FREE_MAIL_DOMAINS:
            # A mail provider's domain belongs to no institution (and would vouch for a RUT source).
            counts["domain_dropped_free_mail"] += 1
            domain, entry = None, {**entry, "email_domain": None}
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


def summary(data: dict[str, Any]) -> dict[str, int]:
    """Counts of a validated file (the drop counters exist only at build time)."""
    entries = validate_file(data).values()
    return {
        "organizations": len(entries),
        "rut_official": sum(e["rut_source"] == "official" for e in entries),
        "rut_directory": sum(e["rut_source"] == "directory" for e in entries),
    }


def _write_private(path: Path, text: str) -> None:
    """Created 0600 — never readable by others, not even for an instant."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)  # an existing, looser file keeps its old mode otherwise
        handle.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--in", dest="source", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--check", type=Path, metavar="PATH", help="validate a built file; build nothing")
    args = parser.parse_args(argv)
    if args.check is not None:
        try:
            data = json.loads(args.check.expanduser().read_text(encoding="utf-8"))
            print(json.dumps(summary(data), sort_keys=True))
        except (OSError, ValueError) as exc:  # OrgSuggestionsError is a ValueError
            print(f"invalid: {exc.__class__.__name__}: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.source is None or args.out is None:
        parser.error("--in and --out are required (or --check PATH)")
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
    _write_private(out, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

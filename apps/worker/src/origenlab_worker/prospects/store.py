"""Prospect findings into evidence: one `prospect_finding` source record per finding and the
assertions a later `contact-prospect` promotion resolves. Rerunning a file writes nothing new."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from origenlab_worker.prospects.finding import FindingRefused, ProspectFinding, dedupe_key, fold, parse_finding
from origenlab_worker.prospects.logo import fetch_logo

PROSPECTS_REQUIRED_PRIVILEGES: tuple[tuple[str, str], ...] = (
    ("evidence.source_record", "SELECT"), ("evidence.source_record", "INSERT"),
    ("evidence.assertion", "SELECT"), ("evidence.assertion", "INSERT"),
)
PROSPECTS_REQUIRED_POLICIES: frozenset[tuple[str, str, str]] = frozenset({
    ("evidence", "source_record", "origenlab_worker_select"),
    ("evidence", "source_record", "origenlab_worker_insert"),
    ("evidence", "assertion", "origenlab_worker_select"),
    ("evidence", "assertion", "origenlab_worker_insert"),
})

_INSERT_RECORD = """
insert into evidence.source_record (kind, dedupe_key, payload, source_uri, review_status)
values ('prospect_finding', %s, %s, %s, 'pending')
on conflict (dedupe_key) do nothing
returning id::text
"""
_INSERT_ASSERTION = """
insert into evidence.assertion (source_record_id, kind, value_norm, value)
values (%s, %s, %s, %s)
on conflict (source_record_id, kind, value_norm) do nothing
"""
_EXISTS = "select 1 from evidence.source_record where dedupe_key = %s"

Fetch = Callable[[str], tuple[str, bytes] | None]


def _payload(f: ProspectFinding, logo: tuple[str, bytes] | None) -> dict[str, Any]:
    body = asdict(f)
    body["awarded_on"] = f.awarded_on.isoformat() if f.awarded_on else None
    body["equipment"] = list(f.equipment)
    body["keywords"] = list(f.keywords)
    body["sources"] = [[fact, url] for fact, url in f.sources]
    body["logo"] = ({"content_type": logo[0], "b64": base64.b64encode(logo[1]).decode("ascii")}
                    if logo else None)
    # Strip None optional fields to keep payloads compact; hidden_reason kept when set
    for key in ("salutation_sex", "program", "hidden_reason"):
        if body.get(key) is None:
            body.pop(key, None)
    return body


def store_finding(cur: Any, f: ProspectFinding, logo: tuple[str, bytes] | None) -> Literal["inserted", "unchanged"]:
    first_source = f.sources[0][1] if f.sources else f.email_source_url or f.website
    cur.execute(_INSERT_RECORD, (dedupe_key(f), json.dumps(_payload(f, logo), ensure_ascii=False), first_source))
    row = cur.fetchone()
    if row is None:
        return "unchanged"
    record_id = row[0]
    org = {"organization_name": f.organization_name}
    rows = [("organization_name", fold(f.organization_name), org),
            ("affiliation", fold(f"{f.person_name or ''}|{f.organization_name}"),
             {"person_name": f.person_name, "organization_name": f.organization_name, "role": f.role, "unit": f.unit})]
    if f.email:
        rows.append(("contact_address", f.email, {"address": f.email, "source_url": f.email_source_url,
                                                  "source_year": f.email_source_year}))
    for kind, value_norm, value in rows:
        cur.execute(_INSERT_ASSERTION, (record_id, kind, value_norm, json.dumps(value, ensure_ascii=False)))
    return "inserted"


def load_findings(conn: Any, path: Path, *, apply: bool, fetch: Fetch = fetch_logo) -> dict[str, Any]:
    """Read a JSON-lines file of findings; dry run counts, `apply` writes each in its own transaction."""
    findings: list[ProspectFinding] = []
    refused: list[dict[str, Any]] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            findings.append(parse_finding(json.loads(line)))
        except FindingRefused as exc:
            refused.append({"line": number, "reason": exc.reason})
        except (ValueError, TypeError):
            refused.append({"line": number, "reason": "not_json_object"})
    if not apply:
        with conn.cursor() as cur:
            new = 0
            for f in findings:
                cur.execute(_EXISTS, (dedupe_key(f),))
                new += cur.fetchone() is None
        return {"valid": len(findings), "would_insert": new, "refused": refused}
    counts = {"inserted": 0, "unchanged": 0}
    for f in findings:
        logo = fetch(f.website) if f.website else None
        with conn.transaction(), conn.cursor() as cur:
            counts[store_finding(cur, f, logo)] += 1
    return {**counts, "refused": refused}

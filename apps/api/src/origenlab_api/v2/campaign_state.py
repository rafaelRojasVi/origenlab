"""Campaign HTML-content state helpers.

Pure functions that take a psycopg cursor and return the HTML state of a campaign (e.g.
``sent_html_archived``, ``not_recovered``).  Extracted from
:mod:`origenlab_api.v2.crm_workspace` so they live next to
:mod:`origenlab_api.v2.campaign_history` where they conceptually belong.

No imports from :mod:`origenlab_api.v2.crm_workspace` — callers hold the transaction.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any


def _compute_campaign_content_state(
    cur: Any,
    campaign_id: str,
    campaign_row: Mapping[str, Any],
) -> tuple[str, str | None, list[dict], dict | None]:
    """Return (html_state, html, contents[], recovery) for a campaign that has no body_html.

    Reads ``outbound.campaign_content`` and ``outbound.campaign_content_message``.  The
    caller must own the read-only cursor and must have already confirmed ``body_html`` is
    ``None`` on the campaign row.  This helper must not be called when ``body_html`` is set
    (the fingerprint path handles that case in ``campaign_archive``).
    """
    # Content rows for this campaign (immutable; SELECT-only)
    cur.execute(
        """
        select cc.id::text, cc.content_kind, cc.variant_no, cc.subject, cc.preheader,
               cc.body_html, cc.body_html_sha256, cc.body_html_normalized_sha256,
               cc.message_count, cc.first_sent_at::text, cc.last_sent_at::text,
               cc.attribution_method, cc.attribution_confidence, cc.attribution_policy_version,
               cc.unmatched_attempt_count,
               (select count(*)::int from outbound.campaign_content_message ccm
                 where ccm.campaign_content_id = cc.id
                   and ccm.send_attempt_id is not null) as linked_attempts
          from outbound.campaign_content cc
         where cc.campaign_id = %s
         order by cc.content_kind, cc.variant_no
        """,
        (campaign_id,),
    )
    cols = [d[0] for d in cur.description]
    raw_contents = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    # Verify each content row: sha256(body_html) == body_html_sha256.
    # Build a separate contents list (body_html excluded) and keep a parallel map of
    # body_html by id so the html-state logic can still read it without touching raw_contents.
    html_by_id: dict[str, str | None] = {}
    contents: list[dict[str, Any]] = []
    for row in raw_contents:
        body = row.get("body_html") or ""
        actual = hashlib.sha256(body.encode()).hexdigest()
        verified = actual == row["body_html_sha256"]
        html_by_id[row["id"]] = row["body_html"] if verified else None
        content_row = dict(row)
        # The variant's HTML travels only when its stored hash recomputes; a mismatch ships null.
        content_row["body_html"] = row["body_html"] if verified else None
        content_row["hash_verified"] = verified
        contents.append(content_row)

    # Recovery metadata: from the origin source_record of any content row for this campaign
    recovery: dict[str, Any] | None = None
    if raw_contents:
        cur.execute(
            """
            select distinct on (sr.id)
                   cc.attribution_policy_version as policy_version,
                   sum(cc.message_count) over () as matched_messages,
                   max(cc.unmatched_attempt_count) over () as unmatched_attempts,
                   sr.payload_sha256 as manifest_sha256
              from outbound.campaign_content cc
              join evidence.source_record sr on sr.id = cc.origin_source_record_id
             where cc.campaign_id = %s
             order by sr.id
             limit 1
            """,
            (campaign_id,),
        )
        rec_row = cur.fetchone()
        if rec_row:
            recovery = {
                "policy_version": rec_row[0],
                "matched_messages": int(rec_row[1]) if rec_row[1] is not None else 0,
                "unmatched_attempts": int(rec_row[2]) if rec_row[2] is not None else 0,
                "manifest_sha256": rec_row[3],
            }

    sent_html_rows = [c for c in contents if c["content_kind"] == "sent_html"]
    draft_rows = [c for c in contents if c["content_kind"] == "historical_draft"]

    if sent_html_rows:
        # Use variant 1; html is None when hash does not verify.
        v1 = next((c for c in sent_html_rows if c["variant_no"] == 1), sent_html_rows[0])
        html_state = "sent_html_archived" if v1["hash_verified"] else "fingerprint_mismatch"
        html = html_by_id.get(v1["id"]) if v1["hash_verified"] else None
        return html_state, html, contents, recovery

    if draft_rows:
        v1_draft = next((c for c in draft_rows if c["variant_no"] == 1), draft_rows[0])
        v1_raw = next((r for r in raw_contents if r["id"] == v1_draft["id"]), None)
        html_state = "historical_draft"
        html = v1_raw["body_html"] if v1_raw else None
        return html_state, html, contents, recovery

    # No content rows — check for an ambiguous attribution source record
    v1_id = campaign_row.get("v1_campaign_id")
    if v1_id:
        cur.execute(
            """
            select 1 from evidence.source_record sr
             where sr.kind = 'migration_manifest'
               and sr.dedupe_key like %s
               and sr.payload->>'decision' = 'ambiguous'
             limit 1
            """,
            (f"migration_manifest:campaign-content-recovery:{v1_id}:%",),
        )
        if cur.fetchone():
            return "ambiguous_attribution", None, contents, recovery

    # Archived imported campaign with no content → not_recovered; native → not_archived
    origin = campaign_row.get("origin", "")
    status = campaign_row.get("status", "")
    if origin == "imported_v1" and status == "archived":
        return "not_recovered", None, contents, recovery

    return ("not_frozen" if status == "draft" else "not_archived"), None, contents, recovery


def _campaign_content_list_state(
    cur: Any,
    campaign_ids: list[str],
) -> dict[str, tuple[bool, str]]:
    """Bulk-compute (has_html_from_content, html_state_stub) for the marketing list.

    Returns a dict keyed by campaign_id. ``has_html`` is True when there is at least one
    ``sent_html`` content row. ``html_state_stub`` is ``'sent_html_archived'`` or
    ``'historical_draft'`` when content exists; callers must fall back to the campaign-row
    logic for campaigns with no content rows.
    """
    if not campaign_ids:
        return {}
    cur.execute(
        """
        select campaign_id::text,
               bool_or(content_kind = 'sent_html') as has_sent_html,
               bool_or(content_kind = 'historical_draft') as has_draft
          from outbound.campaign_content
         where campaign_id = any(%s::uuid[])
         group by 1
        """,
        (campaign_ids,),
    )
    result: dict[str, tuple[bool, str]] = {}
    for cid, has_sent, has_draft in cur.fetchall():
        if has_sent:
            result[cid] = (True, "sent_html_archived")
        elif has_draft:
            result[cid] = (False, "historical_draft")
    return result

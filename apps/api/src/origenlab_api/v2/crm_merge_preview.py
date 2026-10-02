"""Merge-preview computation for people (read path shared with the command boundary).

Both the read (``GET /v2/workspace/people/merge-preview``) and the command
(``merge-people``) derive the same preview and digest from one function each.  The command
module imports from here — the read module does too — so neither duplicates the logic.

The digest is the SHA-256 of the canonical JSON of the five scalar inputs that determine
what the merge will do: ``loser_id``, ``loser_version``, ``winner_id``, ``winner_version``
and ``moves``.  If any of those change between preview and confirm the command refuses with
``preview_changed``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────


def merge_preview(cur: Any, loser_id: str, winner_id: str) -> dict[str, Any]:
    """Compute what a merge of loser → winner would move, without writing anything.

    Returns the full CONTRACT shape (loser, winner, moves, conflicts,
    preview_sha256).  ``cur`` must be an open read-only cursor; the caller owns
    the transaction.

    Errors (same person, person not found, person already merged) are surfaced
    as ``ValueError`` with a ``code`` attribute; the route turns them into the
    appropriate HTTP error.
    """
    if loser_id == winner_id:
        err = ValueError("same_person")
        err.code = "same_person"  # type: ignore[attr-defined]
        raise err

    cur.execute(
        """
        select id::text, display_name, version, merged_into_person_id::text
          from crm.person where id = any(%s::uuid[])
        """,
        ([loser_id, winner_id],),
    )
    rows: dict[str, dict[str, Any]] = {}
    for pid, display_name, version, merged_into in cur.fetchall():
        rows[pid] = {"id": pid, "display_name": display_name, "version": int(version),
                     "merged_into": merged_into}

    for pid, code in ((loser_id, "person_not_found"), (winner_id, "person_not_found")):
        if pid not in rows:
            err = ValueError(code)
            err.code = code  # type: ignore[attr-defined]
            raise err

    for pid, code in ((loser_id, "merged_person"),):
        if rows[pid]["merged_into"] is not None:
            err = ValueError(code)
            err.code = code  # type: ignore[attr-defined]
            raise err

    # ── moves ────────────────────────────────────────────────────────────────
    cur.execute(
        "select count(*)::int from crm.contact_point where person_id = %s::uuid",
        (loser_id,),
    )
    cp_count: int = cur.fetchone()[0]

    cur.execute(
        # Only open affiliations are moved; closed ones stay on the loser.
        "select count(*)::int from crm.affiliation where person_id = %s::uuid and valid_to is null",
        (loser_id,),
    )
    aff_count: int = cur.fetchone()[0]

    cur.execute(
        # All participants (active and closed) are repointed, matching the command.
        "select count(*)::int from crm.opportunity_participant where person_id = %s::uuid",
        (loser_id,),
    )
    opp_count: int = cur.fetchone()[0]

    cur.execute(
        "select count(*)::int from outbound.campaign_recipient where person_id = %s::uuid",
        (loser_id,),
    )
    cr_count: int = cur.fetchone()[0]

    cur.execute(
        "select count(*)::int from crm.note"
        " where subject_kind = 'person' and subject_id = %s::uuid",
        (loser_id,),
    )
    note_count: int = cur.fetchone()[0]

    moves = {
        "contact_points": cp_count,
        "affiliations": aff_count,
        "opportunity_participants": opp_count,
        "campaign_recipients": cr_count,
        "notes": note_count,
    }

    # ── conflicts: affiliations to the same organization ────────────────────
    cur.execute(
        """
        select coalesce(o.name, la.organization_id::text) as org_name
          from crm.affiliation la
          join crm.affiliation wa
               on wa.organization_id = la.organization_id
              and wa.person_id = %s::uuid
              and wa.valid_to is null
          left join crm.organization o on o.id = la.organization_id
         where la.person_id = %s::uuid and la.valid_to is null
        """,
        (winner_id, loser_id),
    )
    conflicts = [
        f"Vínculo a '{r[0]}' existe en ambas personas; se cerrará el del perdedor"
        for r in cur.fetchall()
    ]

    loser_row = rows[loser_id]
    winner_row = rows[winner_id]

    preview: dict[str, Any] = {
        "loser":  {"id": loser_id,  "display_name": loser_row["display_name"],  "version": loser_row["version"]},
        "winner": {"id": winner_id, "display_name": winner_row["display_name"], "version": winner_row["version"]},
        "moves": moves,
        "conflicts": conflicts,
    }
    preview["preview_sha256"] = merge_preview_digest(preview)
    return preview


def merge_preview_digest(preview: dict[str, Any]) -> str:
    """SHA-256 of canonical JSON of {loser_id, loser_version, winner_id, winner_version, moves}.

    The command confirms the preview by sending this digest; if any of the five
    inputs has changed since the operator loaded the preview, the digest changes
    and the command refuses with ``preview_changed``.
    """
    payload = {
        "loser_id":       preview["loser"]["id"],
        "loser_version":  preview["loser"]["version"],
        "winner_id":      preview["winner"]["id"],
        "winner_version": preview["winner"]["version"],
        "moves":          preview["moves"],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()

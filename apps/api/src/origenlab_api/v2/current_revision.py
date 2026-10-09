"""«Elegir revisión vigente» — of a quote's current revisions, the operator names the current one.

A case card says «Hay más de una revisión vigente» (`canonical_undetermined`, `crm_workspace.py`)
when a quote on it has two or more revisions that are neither void nor superseded. That happens to
historical revisions: the import numbers them in the order it recorded the documents and records a
supersession only when the owner named one. This module settles it: every *other* current
revision of the quote is superseded by the chosen one (`superseded_by_revision_no`,
`superseded_at`, one `quote_revision.superseded` event each), in the case command's transaction
and under its receipt, and the case version advances.

A historical revision may be superseded by a lower-numbered one: migration
`20261006120000_slice7_historical_current_revision_choice.sql` relaxes the forward rule for
`historical_import` only, and its guard requires the superseding revision to be current, so a
chain never closes into a cycle. A V2-authored revision keeps the forward rule, and a draft is
never superseded; both are refused here by name before the database would.

Nothing is voided and nothing is deleted. A superseded revision stays `sent` forever.
"""

from __future__ import annotations

from typing import Any

from origenlab_api.v2.case_commands import RESOLVE_CURRENT_REVISION
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity


def resolve_current_revision(
    repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
) -> dict[str, Any]:
    """«Elegir revisión vigente»: every other current revision is superseded by the chosen one.

    Refusals, in the order an operator meets them: the quote is not on this case
    (`quote_not_on_case`, 404) or has no such revision (`quote_revision_not_on_case`, 404);
    the chosen revision is not `sent`, or is already superseded (`quote_revision_not_current`,
    409); it is already the only current one (`nothing_to_resolve`, 409); another current
    revision cannot be superseded by it — a draft, or a V2-authored revision with a higher
    number, which the forward rule protects (`revision_cannot_be_superseded`, 409).
    """
    case = repo._live_case(cur, fields["opportunity_id"], fields["opportunity_version"])
    cur.execute(
        """
        select id::text as id, opportunity_id::text as opportunity_id, quote_number
          from crm.quote where id = %s for update
        """,
        (fields["quote_id"],),
    )
    quote = repo._row(cur)
    if quote is None or quote["opportunity_id"] != case["id"]:
        raise CommandRefused(404, "quote_not_on_case", "that quote is not on this case")
    cur.execute(
        """
        select id::text as id, revision_no, status, origin, superseded_by_revision_no
          from crm.quote_revision where quote_id = %s order by revision_no for update
        """,
        (quote["id"],),
    )
    revisions = [
        dict(zip([d[0] for d in cur.description], row, strict=True)) for row in cur.fetchall()
    ]
    chosen = next((r for r in revisions if int(r["revision_no"]) == fields["revision_no"]), None)
    if chosen is None:
        raise CommandRefused(404, "quote_revision_not_on_case", "that quote has no such revision")
    if chosen["status"] != "sent" or chosen["superseded_by_revision_no"] is not None:
        raise CommandRefused(
            409, "quote_revision_not_current", "only a sent, current revision can be the current one"
        )
    others = [
        r for r in revisions
        if r is not chosen and r["status"] != "void" and r["superseded_by_revision_no"] is None
    ]
    if not others:
        raise CommandRefused(
            409, "nothing_to_resolve", "this revision is already the quote's only current revision"
        )
    stuck = [
        r for r in others
        if r["status"] not in ("approved", "sent")
        or (r["origin"] != "historical_import" and int(r["revision_no"]) > int(chosen["revision_no"]))
    ]
    if stuck:
        raise CommandRefused(
            409,
            "revision_cannot_be_superseded",
            "revision(s) "
            + ", ".join(str(r["revision_no"]) for r in stuck)
            + " cannot be superseded by this one: a draft is not superseded, and a revision "
            "V2 authored is superseded only by a later one",
        )

    events: list[str] = []
    superseded: list[int] = []
    for r in others:
        cur.execute(
            """
            update crm.quote_revision
               set superseded_by_revision_no = %s, superseded_at = now(),
                   version = version + 1, updated_at = now()
             where id = %s and superseded_by_revision_no is null
            """,
            (int(chosen["revision_no"]), r["id"]),
        )
        if cur.rowcount != 1:  # pragma: no cover - the rows are locked above
            raise CommandRefused(409, "quote_revision_version_conflict", "the quote changed; re-read it")
        superseded.append(int(r["revision_no"]))
        events.append(repo._append_event(
            cur, aggregate_kind="quote_revision", aggregate_id=r["id"],
            event_type="quote_revision.superseded",
            payload={
                "quote_id": quote["id"],
                "quote_number": quote["quote_number"],
                "opportunity_id": case["id"],
                "revision_no": int(r["revision_no"]),
                "superseded_by_revision_no": int(chosen["revision_no"]),
                "chosen_as_current": True,
                "note": fields["note"],
            },
            operator=operator, receipt_id=receipt_id,
        ))
    return {
        "command": RESOLVE_CURRENT_REVISION,
        "opportunity_id": case["id"],
        "opportunity_version": repo.bump_case_version(cur, case),
        "quote_id": quote["id"],
        "quote_number": quote["quote_number"],
        "current_revision_no": int(chosen["revision_no"]),
        "superseded_revision_nos": superseded,
        "created": [],
        "event_ids": events,
    }

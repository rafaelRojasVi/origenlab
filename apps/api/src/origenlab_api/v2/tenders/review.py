"""`review-notice`: a person's verdict on one notice of «En Mercado Público» (DOMAIN.md §7 #64).

`interesa` keeps the notice at the top of the list, marked; `no_es_nuestro` takes it off the list
(it stays under «Descartadas»). A new verdict on the same notice replaces the old one for the page —
`crm.notice_review` is append-only, so the history stays. The machine's match
(`procurement.notice.relevance`) is never changed by it: machine evidence proposes, the verdict is
the human record.

Refusals:

| Status | Code | When |
|---|---|---|
| 404 | `notice_not_found` | no notice has this id |
| 409 | `notice_already_reviewed` | the notice's latest verdict is already this one |

Event: `notice.reviewed` (aggregate `notice`), payload `{verdict, previous_verdict, reason}`.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from origenlab_api.v2.commands import CommandRefused, as_uuid
from origenlab_api.v2.identity import OperatorIdentity

REVIEW_NOTICE = "review-notice"
VERDICTS: tuple[str, ...] = ("interesa", "no_es_nuestro")


class ReviewNoticeBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    notice_id: str
    verdict: Literal["interesa", "no_es_nuestro"]
    reason: Annotated[str, Field(max_length=500)] | None = None


def prepare_review_notice(body: ReviewNoticeBody) -> dict[str, Any]:
    notice_id = as_uuid(body.notice_id, "notice_id")
    return {"notice_id": notice_id, "verdict": body.verdict, "reason": body.reason or None}


def review_notice(
    repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
) -> dict[str, Any]:
    cur.execute("select id::text from procurement.notice where id = %s::uuid for update", (fields["notice_id"],))
    row = cur.fetchone()
    if row is None:
        raise CommandRefused(404, "notice_not_found", "No existe esa licitación.")
    notice_id = row[0]
    cur.execute(
        "select verdict from crm.notice_review where notice_id = %s::uuid order by reviewed_at desc, id desc limit 1",
        (notice_id,),
    )
    latest = cur.fetchone()
    previous = latest[0] if latest else None
    if previous == fields["verdict"]:
        label = "te interesa" if previous == "interesa" else "no es nuestra"
        raise CommandRefused(409, "notice_already_reviewed", f"Esta licitación ya está marcada como que {label}.")
    cur.execute(
        "insert into crm.notice_review (notice_id, verdict, reason, reviewed_by_operator_id) "
        "values (%s::uuid, %s, %s, %s::uuid) returning id::text",
        (notice_id, fields["verdict"], fields["reason"], operator.operator_id),
    )
    (review_id,) = cur.fetchone()
    event_id = repo._append_event(
        cur, aggregate_kind="notice", aggregate_id=notice_id, event_type="notice.reviewed",
        payload={"verdict": fields["verdict"], "previous_verdict": previous, "reason": fields["reason"]},
        operator=operator, receipt_id=receipt_id,
    )
    return {
        "command": REVIEW_NOTICE,
        "notice_id": notice_id,
        "review_id": review_id,
        "verdict": fields["verdict"],
        "previous_verdict": previous,
        "created": ["notice_review"],
        "event_ids": [event_id],
    }


__all__ = ["REVIEW_NOTICE", "VERDICTS", "ReviewNoticeBody", "prepare_review_notice", "review_notice"]

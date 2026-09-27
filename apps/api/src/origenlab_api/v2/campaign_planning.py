"""Campaign planning — the internal day (and optional time) an operator intends to send on.

One command, `set-campaign-planning`, on the same `CommandTransaction` as every other V2
command (one transaction, one receipt per `Idempotency-Key`, one `crm.domain_event` per change):

* a day and optional time → `campaign.planning_set` (the previous planning is in the payload,
  so a change is audited as what it was and what it became);
* no day → `campaign.planning_cleared`;
* the same planning again → nothing is written and no event is appended.

**Planning is metadata. It schedules nothing.** The command writes three columns of
`outbound.campaign` — `planned_for_date`, `planned_for_at`, `planning_version` — and the audit
stream, and nothing else: no status, no approval, no recipient, no send attempt, no job. No
code in this repository reads the planned date to act on it, and `outbound.campaign_planning_guard`
refuses a statement that changes planning together with the status. The dashboard labels the
field «Planificación interna · no programa el envío».

**Time zones live in PostgreSQL.** The day is a calendar day in America/Santiago; the instant is
stored in UTC. Both conversions — "today" in Santiago, and a Santiago wall time to UTC — are done
by the database, whose time-zone rules are the ones the guard checks against, so the API image
does not need a tz database of its own and the two can never disagree.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from origenlab_api.v2.command_core import CommandTransaction
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity

SET_CAMPAIGN_PLANNING = "set-campaign-planning"

PLANNING_TIME_ZONE = "America/Santiago"
#: Statuses a campaign can be planned in: not yet sent. The database guard holds the same list.
PLANNABLE_STATUSES = ("draft", "audience_frozen")
#: A planned day further out than this is a typo, not a plan.
MAX_PLANNING_HORIZON_DAYS = 1_100
PLANNING_LABEL = "Planificación interna · no programa el envío"


class SetCampaignPlanningBody(BaseModel):
    """The planning an operator chose. No status, send or approval field exists to send."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    campaign_id: UUID
    expected_planning_version: Annotated[int, Field(ge=0)]
    #: The day in America/Santiago; null clears the planning.
    planned_for_date: dt.date | None = None
    #: Optional wall-clock time in America/Santiago, `HH:MM`.
    planned_for_time: Annotated[str | None, Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")] = None

    @field_validator("planned_for_time", mode="before")
    @classmethod
    def _blank_is_absent(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value


def validated_planning(body: SetCampaignPlanningBody) -> dict[str, Any]:
    if body.planned_for_time is not None and body.planned_for_date is None:
        raise CommandRefused(422, "time_without_date", "a planned time needs a planned day")
    fields = body.model_dump(mode="json")
    fields["command"] = SET_CAMPAIGN_PLANNING
    return fields


_UTC_TEXT = """to_char({col} at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')"""


class V2CampaignPlanningRepository(CommandTransaction):
    """The planning command. Writes only three planning columns and the audit stream."""

    def _storage(self, cur: Any) -> dict[str, str]:
        cur.execute("select current_database()")
        return {"table": "outbound.campaign", "database": cur.fetchone()[0]}

    def _set(self, cur: Any, operator: OperatorIdentity, f: dict[str, Any], receipt_id: str) -> dict[str, Any]:
        cur.execute(
            f"""
            select id::text as id, status, planning_version,
                   planned_for_date::text as planned_for_date,
                   {_UTC_TEXT.format(col="planned_for_at")} as planned_for_at
              from outbound.campaign where id = %s for update
            """,
            (f["campaign_id"],),
        )
        current = self._row(cur)
        if current is None:
            raise CommandRefused(404, "campaign_not_found", "no such campaign")
        if current["status"] not in PLANNABLE_STATUSES:
            raise CommandRefused(
                409, "campaign_not_plannable",
                f"the campaign is '{current['status']}'; only a draft or an audience-frozen campaign "
                "can be planned",
            )
        if current["planning_version"] != f["expected_planning_version"]:
            raise CommandRefused(
                409, "stale_planning_version",
                f"the planning is at version {current['planning_version']}, not "
                f"{f['expected_planning_version']}; someone changed it after you opened it — reload",
            )

        day, wall = f["planned_for_date"], f["planned_for_time"]
        cur.execute(
            f"""
            with chosen as (
              select %(day)s::date as day,
                     case when %(wall)s::time is null then null
                          else (%(day)s::date + %(wall)s::time) at time zone '{PLANNING_TIME_ZONE}' end as at
            )
            select (now() at time zone '{PLANNING_TIME_ZONE}')::date::text as today,
                   day is not null and day < (now() at time zone '{PLANNING_TIME_ZONE}')::date as in_past,
                   day is not null and day > (now() at time zone '{PLANNING_TIME_ZONE}')::date + {MAX_PLANNING_HORIZON_DAYS} as too_far,
                   at is not null and (at at time zone '{PLANNING_TIME_ZONE}') <> (day + %(wall)s::time) as nonexistent,
                   {_UTC_TEXT.format(col="at")} as at_utc
              from chosen
            """,
            {"day": day, "wall": wall},
        )
        resolved = self._row(cur)
        assert resolved is not None  # noqa: S101
        if resolved["in_past"]:
            raise CommandRefused(
                422, "planned_in_past",
                f"{day} is before today ({resolved['today']}, {PLANNING_TIME_ZONE}); plan a future day",
            )
        if resolved["too_far"]:
            raise CommandRefused(
                422, "planned_too_far", f"a planned day is at most {MAX_PLANNING_HORIZON_DAYS} days ahead",
            )
        if resolved["nonexistent"]:
            raise CommandRefused(
                422, "planned_time_nonexistent",
                f"{wall} does not exist on {day} in {PLANNING_TIME_ZONE} (clock change); choose another time",
            )
        target = {"planned_for_date": day, "planned_for_at": resolved["at_utc"]}
        before = {"planned_for_date": current["planned_for_date"], "planned_for_at": current["planned_for_at"]}

        if target == before:
            return self._answer(cur, current["id"], current["status"], current["planning_version"], target, changed=False)

        cur.execute(
            """
            update outbound.campaign
               set planned_for_date = %s, planned_for_at = %s::timestamptz,
                   planning_version = planning_version + 1
             where id = %s and planning_version = %s
            returning planning_version
            """,
            (day, resolved["at_utc"], current["id"], current["planning_version"]),
        )
        row = self._row(cur)
        assert row is not None  # noqa: S101 - the row is locked and its version checked
        self._append_event(
            cur,
            aggregate_kind="campaign",
            aggregate_id=current["id"],
            event_type="campaign.planning_set" if day is not None else "campaign.planning_cleared",
            payload={
                "from": before,
                "to": target,
                "time_zone": PLANNING_TIME_ZONE,
                "from_planning_version": current["planning_version"],
                "to_planning_version": row["planning_version"],
                "schedules_send": False,
            },
            operator=operator,
            receipt_id=receipt_id,
        )
        return self._answer(cur, current["id"], current["status"], row["planning_version"], target, changed=True)

    def _answer(self, cur: Any, campaign_id: str, status: str, version: int, target: dict[str, Any],
                *, changed: bool) -> dict[str, Any]:
        return {
            "campaign_id": campaign_id,
            "status": status,
            "planning_version": version,
            **target,
            "time_zone": PLANNING_TIME_ZONE,
            "changed": changed,
            "schedules_send": False,
            "label": PLANNING_LABEL,
            "storage": self._storage(cur),
        }

    _HANDLERS = {SET_CAMPAIGN_PLANNING: _set}

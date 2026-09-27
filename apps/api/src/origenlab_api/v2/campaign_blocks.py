"""Campaign safety blocks — the admin-only pause every campaign boundary is refused by.

Two commands on the same `CommandTransaction` as every other V2 command (one transaction, one
receipt per `Idempotency-Key`, one `crm.domain_event` per change):

* `block-campaign` — places an active `outbound.campaign_block` on one campaign or on every
  campaign → `campaign_block.placed`;
* `unblock-campaign` — lifts one active block, with its own reason → `campaign_block.lifted`.

**Admin only, twice.** The route refuses anyone but an active `admin` (sales and viewers are
403), and `outbound.campaign_block_guard` refuses the row again unless the operator it names is
an active admin in `platform.operator`. **Compare-and-set:** placing quotes the target's
`block_version` (how many times that target has been blocked or unblocked, derived from its
rows) and lifting quotes the block's `version`; a target that moved since the operator looked
is refused. A reason is mandatory both ways.

**A block writes one row and one event, and nothing else.** No campaign status, recipient,
frozen snapshot, send attempt, contact control or send flag moves; nothing is enqueued or sent,
and nothing talks to Gmail. What a block *does* is refuse: the database triggers
(`outbound.campaign_hold_guard`) refuse freezing, approving, activating, recording a dry run or
an approval, reserving a recipient and creating or dispatching a send attempt while it is
active, and `outbound.marketing_contact_refusals` reports it to every future send step. The
API's own reads (`read_campaign_holds`) surface the same refusals from
`outbound.campaign_hold_refusals`, so the dashboard and the database cannot disagree.

**Nothing expires a block.** There is no expiry column. Only `unblock-campaign` lifts one, and
the migration-seeded September wave-2 incident hold (scope `legacy_campaign`) is lifted the same
way or not at all.

The commands are mounted only with `ORIGENLAB_V2_CAMPAIGN_BLOCKS_ENABLED=true` (default off).
Enforcement and the read of the current holds are not behind the switch: a block that exists is
always shown and always refused.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict, Field, field_validator

from origenlab_api.v2.command_core import CommandTransaction
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity

BLOCK_CAMPAIGN = "block-campaign"
UNBLOCK_CAMPAIGN = "unblock-campaign"

MAX_REASON_CHARS = 2000
_KEY_PATTERN = r"^[a-z0-9][a-z0-9._-]{2,79}$"

#: `outbound.campaign_hold_refusals` codes, labelled for the dashboard.
HOLD_REFUSAL_LABEL = {
    "all_campaigns_blocked": "Todas las campañas están bloqueadas",
    "campaign_blocked": "Campaña bloqueada",
    "campaign_paused": "Campaña en pausa",
    "campaign_unknown": "Campaña desconocida",
}
SCOPE_LABEL = {
    "campaign": "Esta campaña",
    "all_campaigns": "Todas las campañas",
    "legacy_campaign": "Campaña del registro V1",
}
#: What a block refuses, shown next to every active one.
BLOCK_EFFECT = (
    "Mientras el bloqueo esté activo se rechaza congelar la audiencia, aprobar, activar, registrar una "
    "prueba en seco, reservar destinatarios y crear o despachar envíos. El bloqueo no envía, no encola, "
    "no toca Gmail ni modifica la audiencia congelada. Sólo un administrador lo levanta, con un motivo; "
    "no vence."
)
#: Fields a viewer does not see: a viewer sees whether something is held, never why or by whom.
VIEWER_HIDDEN_FIELDS = frozenset({"reason", "lift_reason", "placed_by", "lifted_by"})


def _reason(value: Any) -> Any:
    if isinstance(value, str) and not value.strip(" \t\r\n"):
        raise ValueError("a reason is required and cannot be blank")
    return value


class BlockCampaignBody(BaseModel):
    """Block one campaign, or every campaign. No field starts, sends or schedules anything."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    scope: Literal["campaign", "all_campaigns"]
    campaign_id: UUID | None = None
    #: The target's block_version the admin was shown (0 for a target never blocked).
    expected_block_version: Annotated[int, Field(ge=0)]
    reason: Annotated[str, Field(min_length=1, max_length=MAX_REASON_CHARS)]
    #: An optional incident or ticket key, e.g. `incident_hold_september_2026`.
    reference: Annotated[str | None, Field(pattern=_KEY_PATTERN)] = None

    _reason_not_blank = field_validator("reason", mode="before")(_reason)

    @field_validator("reference", mode="before")
    @classmethod
    def _blank_reference_is_absent(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class UnblockCampaignBody(BaseModel):
    """Lift one active block. A separate, explicit decision with its own reason."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    block_id: UUID
    #: The block's version the admin was shown: 1 for an active block.
    expected_version: Annotated[int, Field(ge=1)]
    reason: Annotated[str, Field(min_length=1, max_length=MAX_REASON_CHARS)]

    _reason_not_blank = field_validator("reason", mode="before")(_reason)


def block_fields(body: BlockCampaignBody) -> dict[str, Any]:
    if body.scope == "campaign" and body.campaign_id is None:
        raise CommandRefused(422, "campaign_required", "a campaign block names the campaign it blocks")
    if body.scope == "all_campaigns" and body.campaign_id is not None:
        raise CommandRefused(422, "campaign_not_allowed", "an all-campaigns block names no campaign")
    fields = body.model_dump(mode="json")
    fields["command"] = BLOCK_CAMPAIGN
    return fields


def unblock_fields(body: UnblockCampaignBody) -> dict[str, Any]:
    fields = body.model_dump(mode="json")
    fields["command"] = UNBLOCK_CAMPAIGN
    return fields


# ------------------------------------------------------------------ reads

_UTC = """to_char({col} at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')"""

_BLOCK_COLUMNS = f"""
    b.id::text as block_id, b.scope, b.campaign_id::text as campaign_id, b.legacy_campaign_key,
    b.reason, b.reference, {_UTC.format(col="b.placed_at")} as placed_at, b.placed_by_kind,
    coalesce(po.display_name, case b.placed_by_kind when 'migrator' then 'Migración' end) as placed_by,
    {_UTC.format(col="b.lifted_at")} as lifted_at, lo.display_name as lifted_by, b.lift_reason,
    b.version, b.lifted_at is null as active
"""


def _rows(cur: Any) -> list[dict[str, Any]]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _labelled(block: dict[str, Any]) -> dict[str, Any]:
    return {**block, "scope_label": SCOPE_LABEL.get(block["scope"], block["scope"])}


def read_campaign_holds(cur: Any, *, lifted_limit: int = 20) -> dict[str, Any]:
    """Every active block, the latest lifted ones, and each target's compare-and-set version.

    `by_campaign` answers, for every V2 campaign, what `outbound.campaign_hold_refusals` says
    right now — the same function the triggers enforce — plus its campaign-scope block and
    block_version. `all_campaigns` is the global block and its version.
    """
    cur.execute(
        f"""
        select {_BLOCK_COLUMNS}
          from outbound.campaign_block b
          left join platform.operator po on po.id = b.placed_by_operator_id
          left join platform.operator lo on lo.id = b.lifted_by_operator_id
         where b.lifted_at is null
         order by b.placed_at, b.id
        """
    )
    active = [_labelled(b) for b in _rows(cur)]
    cur.execute(
        f"""
        select {_BLOCK_COLUMNS}
          from outbound.campaign_block b
          left join platform.operator po on po.id = b.placed_by_operator_id
          left join platform.operator lo on lo.id = b.lifted_by_operator_id
         where b.lifted_at is not null
         order by b.lifted_at desc, b.id
         limit %s
        """,
        (lifted_limit,),
    )
    lifted = [_labelled(b) for b in _rows(cur)]
    cur.execute(
        """
        select c.id::text as campaign_id, outbound.campaign_hold_refusals(c.id) as refusals,
               (select count(*) + count(b.lifted_at) from outbound.campaign_block b
                 where b.scope = 'campaign' and b.campaign_id = c.id)::int as block_version
          from outbound.campaign c
        """
    )
    per_campaign = _rows(cur)
    cur.execute(
        """
        select (count(*) + count(lifted_at))::int from outbound.campaign_block where scope = 'all_campaigns'
        """
    )
    all_version = cur.fetchone()[0]

    active_by_campaign = {b["campaign_id"]: b for b in active if b["scope"] == "campaign"}
    global_block = next((b for b in active if b["scope"] == "all_campaigns"), None)
    by_campaign = {
        r["campaign_id"]: {
            "held": bool(r["refusals"]),
            "refusals": [{"code": c, "label": HOLD_REFUSAL_LABEL.get(c, c)} for c in r["refusals"]],
            "block": active_by_campaign.get(r["campaign_id"]),
            "block_version": r["block_version"],
        }
        for r in per_campaign
    }
    return {
        "all_campaigns": {"blocked": global_block is not None, "block": global_block, "block_version": all_version},
        "legacy": [b for b in active if b["scope"] == "legacy_campaign"],
        "active": active,
        "recently_lifted": lifted,
        "by_campaign": by_campaign,
        "effect": BLOCK_EFFECT,
        "expires": False,
    }


def campaign_hold(holds: dict[str, Any], campaign_id: str) -> dict[str, Any]:
    """One campaign's slice of `read_campaign_holds`, with the global block alongside it."""
    mine = holds["by_campaign"].get(campaign_id) or {
        "held": False, "refusals": [], "block": None, "block_version": 0,
    }
    return {**mine, "all_campaigns": holds["all_campaigns"], "effect": holds["effect"], "expires": False}


def redact_for_viewer(value: Any) -> Any:
    """A viewer sees that something is held, its scope and since when — never why or by whom."""
    if isinstance(value, dict):
        out = {k: redact_for_viewer(v) for k, v in value.items() if k not in VIEWER_HIDDEN_FIELDS}
        if "block_id" in value:
            out["redacted"] = True
        return out
    if isinstance(value, list):
        return [redact_for_viewer(v) for v in value]
    return value


# ------------------------------------------------------------------ the commands

class V2CampaignBlockRepository(CommandTransaction):
    """`block-campaign` and `unblock-campaign`. Writes one `outbound.campaign_block` row (or its
    lift) and one audit event; nothing else."""

    def _lock_target(self, cur: Any, scope: str, campaign_id: str | None) -> None:
        # Serialises two admins acting on one target, so the version read below is the one the
        # insert is based on. The unique partial index is the backstop.
        cur.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"outbound.campaign_block:{scope}:{campaign_id or '*'}",),
        )

    def _target_version(self, cur: Any, scope: str, campaign_id: str | None) -> tuple[int, dict[str, Any] | None]:
        cur.execute(
            f"""
            select (select (count(*) + count(lifted_at))::int from outbound.campaign_block
                     where scope = %(scope)s and campaign_id is not distinct from %(campaign)s::uuid
                       and legacy_campaign_key is null) as block_version,
                   (select row_to_json(x) from (
                      select {_BLOCK_COLUMNS}
                        from outbound.campaign_block b
                        left join platform.operator po on po.id = b.placed_by_operator_id
                        left join platform.operator lo on lo.id = b.lifted_by_operator_id
                       where b.scope = %(scope)s and b.campaign_id is not distinct from %(campaign)s::uuid
                         and b.legacy_campaign_key is null and b.lifted_at is null) x) as active
            """,
            {"scope": scope, "campaign": campaign_id},
        )
        row = self._row(cur)
        assert row is not None  # noqa: S101
        return row["block_version"], row["active"]

    def _block_row(self, cur: Any, block_id: str) -> dict[str, Any] | None:
        cur.execute(
            f"""
            select {_BLOCK_COLUMNS}
              from outbound.campaign_block b
              left join platform.operator po on po.id = b.placed_by_operator_id
              left join platform.operator lo on lo.id = b.lifted_by_operator_id
             where b.id = %s
            """,
            (block_id,),
        )
        row = self._row(cur)
        return _labelled(row) if row else None

    def _refusals(self, cur: Any, campaign_id: str | None) -> list[str]:
        if campaign_id is None:
            return []
        cur.execute("select outbound.campaign_hold_refusals(%s)", (campaign_id,))
        return list(cur.fetchone()[0])

    def _guarded(self, cur: Any, sql: str, params: Any) -> dict[str, Any] | None:
        """Run a block write; the database's own refusals become refusals, not 500s.

        The whole command transaction is rolled back on the way out, receipt included."""
        try:
            cur.execute(sql, params)
        except psycopg.errors.InsufficientPrivilege as exc:
            raise CommandRefused(403, "role_may_not_block", "only an active admin places or lifts a campaign block") from exc
        except psycopg.errors.UniqueViolation as exc:
            raise CommandRefused(409, "already_blocked", "that target already has an active block") from exc
        except (psycopg.errors.RaiseException, psycopg.errors.CheckViolation) as exc:
            raise CommandRefused(409, "block_refused_by_database",
                                 f"the database refused the block (SQLSTATE {exc.sqlstate}); nothing was written") from exc
        return self._row(cur)

    def _block(self, cur: Any, operator: OperatorIdentity, f: dict[str, Any], receipt_id: str) -> dict[str, Any]:
        scope, campaign_id = f["scope"], f["campaign_id"]
        self._lock_target(cur, scope, campaign_id)
        campaign = None
        if scope == "campaign":
            cur.execute("select id::text as id, name, status from outbound.campaign where id = %s", (campaign_id,))
            campaign = self._row(cur)
            if campaign is None:
                raise CommandRefused(404, "campaign_not_found", "no such campaign")
        version, active = self._target_version(cur, scope, campaign_id)
        if version != f["expected_block_version"]:
            raise CommandRefused(
                409, "stale_block_version",
                f"that target's blocks are at version {version}, not {f['expected_block_version']}; "
                "someone blocked or unblocked it after you looked — reload",
            )
        if active is not None:
            raise CommandRefused(409, "already_blocked", "that target already has an active block")

        row = self._guarded(
            cur,
            """
            insert into outbound.campaign_block (scope, campaign_id, reason, reference, placed_by_kind, placed_by_operator_id)
            values (%s, %s, %s, %s, 'operator', %s)
            returning id::text as id
            """,
            (scope, campaign_id, f["reason"], f["reference"], operator.operator_id),
        )
        assert row is not None  # noqa: S101
        block = self._block_row(cur, row["id"])
        assert block is not None  # noqa: S101
        self._append_event(
            cur,
            aggregate_kind="campaign_block",
            aggregate_id=block["block_id"],
            event_type="campaign_block.placed",
            payload={
                "scope": scope,
                "campaign_id": campaign_id,
                "campaign_status": campaign["status"] if campaign else None,
                "reason": f["reason"],
                "reference": f["reference"],
                "from_block_version": version,
                "to_block_version": version + 1,
                "enqueues": False,
                "sends": False,
                "modifies_frozen_evidence": False,
            },
            operator=operator,
            receipt_id=receipt_id,
        )
        return self._answer(cur, block, campaign_id, block_version=version + 1)

    def _unblock(self, cur: Any, operator: OperatorIdentity, f: dict[str, Any], receipt_id: str) -> dict[str, Any]:
        cur.execute(
            "select id::text as id, scope, campaign_id::text as campaign_id, legacy_campaign_key, version, "
            "lifted_at is null as active from outbound.campaign_block where id = %s for update",
            (f["block_id"],),
        )
        current = self._row(cur)
        if current is None:
            raise CommandRefused(404, "block_not_found", "no such campaign block")
        if not current["active"]:
            raise CommandRefused(409, "block_already_lifted", "that block was already lifted; a lifted block is immutable")
        if current["version"] != f["expected_version"]:
            raise CommandRefused(
                409, "stale_version",
                f"the block is at version {current['version']}, not {f['expected_version']}; reload",
            )
        if current["scope"] != "legacy_campaign":
            self._lock_target(cur, current["scope"], current["campaign_id"])
        before, _ = (self._target_version(cur, current["scope"], current["campaign_id"])
                     if current["scope"] != "legacy_campaign" else (None, None))
        self._guarded(
            cur,
            """
            update outbound.campaign_block
               set lifted_at = now(), lifted_by_operator_id = %s, lift_reason = %s, version = version + 1
             where id = %s and version = %s and lifted_at is null
            returning version
            """,
            (operator.operator_id, f["reason"], current["id"], current["version"]),
        )
        block = self._block_row(cur, current["id"])
        assert block is not None  # noqa: S101
        self._append_event(
            cur,
            aggregate_kind="campaign_block",
            aggregate_id=current["id"],
            event_type="campaign_block.lifted",
            payload={
                "scope": current["scope"],
                "campaign_id": current["campaign_id"],
                "legacy_campaign_key": current["legacy_campaign_key"],
                "reason": f["reason"],
                "from_version": current["version"],
                "to_version": block["version"],
                "from_block_version": before,
                "to_block_version": None if before is None else before + 1,
                "sends": False,
            },
            operator=operator,
            receipt_id=receipt_id,
        )
        return self._answer(cur, block, current["campaign_id"],
                            block_version=None if before is None else before + 1)

    def _answer(self, cur: Any, block: dict[str, Any], campaign_id: str | None, *, block_version: int | None) -> dict[str, Any]:
        refusals = self._refusals(cur, campaign_id)
        cur.execute("select current_database()")
        return {
            "block": block,
            "block_version": block_version,
            "campaign_refusals": refusals,
            "effect": BLOCK_EFFECT,
            "expires": False,
            "enqueues": False,
            "sends": False,
            "storage": {"table": "outbound.campaign_block", "database": cur.fetchone()[0]},
        }

    _HANDLERS = {BLOCK_CAMPAIGN: _block, UNBLOCK_CAMPAIGN: _unblock}

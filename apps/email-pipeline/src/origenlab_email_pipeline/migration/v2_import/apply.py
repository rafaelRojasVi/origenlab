"""The transactional, idempotent write path.

Everything here is deliberately narrow:

* it writes only to a :class:`~.target.LocalTarget` — a disposable loopback database;
* it writes only the tables a plan marks applicable, never a table a structural gap blocks;
* it writes inside **one** transaction, so a failure leaves nothing behind;
* it writes with `ON CONFLICT DO NOTHING` on the natural key the database already enforces,
  so re-running the same batch creates zero duplicates;
* it **never** deletes, truncates, updates or replaces an existing row. An import adds
  historical evidence; it does not overwrite anything an operator decided.

It also refuses to start when the target's schema is not the one the plan was built for.
A loader that adapts to whatever schema it finds is how a safety set silently loads into
the wrong shape, so the fingerprint check fails closed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from origenlab_email_pipeline.migration.v2_import.plan import (
    IMPLEMENTED_CONTACT_CONTROL_SOURCES,
    ImportPlan,
    PlannedRow,
)
from origenlab_email_pipeline.migration.v2_import.target import LocalTarget

#: Tables the apply path is permitted to write. `crm.*` is absent by design: promotion is
#: an operator command (`docs/MIGRATION.md` §5 slice 2), never an import step.
WRITABLE_TABLES: frozenset[str] = frozenset(
    {
        "evidence.source_record",
        "evidence.assertion",
        "outbound.contact_control",
        "comms.mailbox",
        "outbound.campaign",
        "outbound.campaign_recipient",
        "outbound.send_attempt",
    }
)

#: Tables whose presence the fingerprint check requires before anything is written.
REQUIRED_TABLES: tuple[tuple[str, str], ...] = (
    ("evidence", "source_record"),
    ("evidence", "assertion"),
    ("outbound", "contact_control"),
    ("comms", "mailbox"),
    ("outbound", "campaign"),
    ("outbound", "campaign_recipient"),
    ("outbound", "send_attempt"),
    ("crm", "contact_point"),
    ("crm", "person"),
    ("crm", "organization"),
)


class ApplyRefused(Exception):
    """The apply path refused to write. Nothing was committed."""


@dataclass(frozen=True)
class ApplyResult:
    """What one apply run actually wrote."""

    inserted: dict[str, int]
    skipped_existing: dict[str, int]
    blocked_tables: dict[str, str]
    crm_rows_after: int

    @property
    def total_inserted(self) -> int:
        return sum(self.inserted.values())

    def to_report(self) -> dict[str, Any]:
        return {
            "inserted": dict(sorted(self.inserted.items())),
            "skipped_existing": dict(sorted(self.skipped_existing.items())),
            "blocked_tables": dict(sorted(self.blocked_tables.items())),
            "crm_rows_after": self.crm_rows_after,
            "total_inserted": self.total_inserted,
        }


def _require_psycopg() -> Any:
    """Import psycopg, with a message that says how to get it."""
    try:
        import psycopg
    except ModuleNotFoundError as exc:  # pragma: no cover - environment-dependent
        raise ApplyRefused(
            "psycopg is required for --apply. Install the optional postgres group: "
            "`uv sync --group postgres` in apps/email-pipeline"
        ) from exc
    return psycopg


def assert_schema_matches(conn: Any) -> None:
    """Refuse unless the target carries the V2 schema this plan was built for.

    Two things are checked: that every table the plan touches (and the `crm.*` tables it
    must leave empty) exists, and that `outbound.contact_control`'s `source` vocabulary is
    the one :mod:`.plan` compiled against. The second is what turns the Wave 1B structural
    gap from a silent constraint violation into an explicit, named refusal.

    Raises:
        ApplyRefused: a required table is missing, or the source vocabulary has drifted.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            select table_schema || '.' || table_name
              from information_schema.tables
             where table_schema || '.' || table_name = any(%s)
               and table_type = 'BASE TABLE'
            """,
            ([f"{s}.{t}" for s, t in REQUIRED_TABLES],),
        )
        found = {r[0] for r in cur.fetchall()}
    missing = [f"{s}.{t}" for s, t in REQUIRED_TABLES if f"{s}.{t}" not in found]
    if missing:
        raise ApplyRefused(
            "schema mismatch: the target is missing " + ", ".join(missing)
            + ". This importer writes only to a database carrying the Slice 0 V2 schema."
        )

    with conn.cursor() as cur:
        cur.execute(
            """
            select pg_get_constraintdef(oid)
              from pg_constraint
             where conname = 'contact_control_source_check'
            """
        )
        row = cur.fetchone()
    if row is None:
        raise ApplyRefused(
            "schema mismatch: outbound.contact_control has no contact_control_source_check "
            "constraint; the source vocabulary this importer compiled against is absent"
        )
    definition = str(row[0])
    unknown = sorted(s for s in IMPLEMENTED_CONTACT_CONTROL_SOURCES if f"'{s}'" not in definition)
    if unknown:
        raise ApplyRefused(
            "schema mismatch: contact_control_source_check does not accept "
            + ", ".join(unknown)
            + "; the importer's source vocabulary and the database's have drifted"
        )


def _count_crm_rows(conn: Any) -> int:
    """Count business rows across every `crm.*` table.

    The importer asserts this is unchanged by an apply. It is the cheapest possible proof
    that no address became a person, an organization or a prospect.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select table_name from information_schema.tables where table_schema = 'crm' "
            "and table_type = 'BASE TABLE' order by table_name"
        )
        tables = [r[0] for r in cur.fetchall()]
        total = 0
        for table in tables:
            cur.execute(f'select count(*) from crm."{table}"')  # noqa: S608 - catalogue name
            total += int(cur.fetchone()[0])
    return total


def _insert_source_records(cur: Any, rows: Sequence[PlannedRow]) -> int:
    inserted = 0
    for row in rows:
        c = row.columns
        cur.execute(
            """
            insert into evidence.source_record (kind, dedupe_key, payload, payload_sha256,
                                                review_status)
            values (%s, %s, %s::jsonb, %s, %s)
            on conflict (dedupe_key) do nothing
            """,
            (c["kind"], c["dedupe_key"], c["payload"], c["payload_sha256"], c["review_status"]),
        )
        inserted += cur.rowcount
    return inserted


def _insert_assertions(cur: Any, rows: Sequence[PlannedRow], source_record_id: str) -> int:
    inserted = 0
    for row in rows:
        c = row.columns
        cur.execute(
            """
            insert into evidence.assertion (source_record_id, kind, value_norm, value, resolution)
            values (%s, %s, %s, %s::jsonb, %s)
            on conflict (source_record_id, kind, value_norm) do nothing
            """,
            (source_record_id, c["kind"], c["value_norm"], c["value"], c["resolution"]),
        )
        inserted += cur.rowcount
    return inserted


def _insert_contact_controls(cur: Any, rows: Sequence[PlannedRow]) -> int:
    inserted = 0
    for row in rows:
        c = row.columns
        cur.execute(
            """
            insert into outbound.contact_control (scope, value_norm, kind, purpose, reason,
                                                  source, needs_review)
            values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (scope, value_norm, kind, purpose) do nothing
            """,
            (
                c["scope"],
                c["value_norm"],
                c["kind"],
                c["purpose"],
                c["reason"],
                c["source"],
                c["needs_review"],
            ),
        )
        inserted += cur.rowcount
    return inserted


def _insert_mailboxes(cur: Any, rows: Sequence[PlannedRow]) -> int:
    inserted = 0
    for row in rows:
        c = row.columns
        cur.execute(
            """
            insert into comms.mailbox (address_norm, display_name, provider,
                                       is_production_sender, authorization_state)
            values (%s, %s, %s, %s, %s)
            on conflict (address_norm) do nothing
            """,
            (
                c["address_norm"],
                c["display_name"],
                c["provider"],
                c["is_production_sender"],
                c["authorization_state"],
            ),
        )
        inserted += cur.rowcount
    return inserted


def _insert_campaigns(
    cur: Any, rows: Sequence[PlannedRow], source_record_id: str
) -> tuple[int, dict[str, str]]:
    """Insert campaigns and return the V1 id → V2 uuid map the child rows need.

    `outbound.campaign` has **no natural-key unique constraint** — its only key is the
    surrogate `id` — so `ON CONFLICT` cannot make this idempotent. The campaign is therefore
    looked up before it is written, on `(mailbox_id, name)`: the pair a V1 campaign
    deterministically produces. Without this a second run would create a second campaign and
    a second copy of its whole audience and ledger.
    """
    ids: dict[str, str] = {}
    inserted = 0
    for row in rows:
        c = row.columns
        cur.execute(
            "select id from comms.mailbox where address_norm = %s", (c["sender_address_norm"],)
        )
        mailbox = cur.fetchone()
        if mailbox is None:
            raise ApplyRefused(
                f"campaign {c['v1_campaign_id']!r} names a mailbox that was not written"
            )
        cur.execute(
            "select id from outbound.campaign where mailbox_id = %s and name = %s",
            (mailbox[0], c["name"]),
        )
        existing = cur.fetchone()
        if existing is not None:
            ids[c["v1_campaign_id"]] = existing[0]
            continue
        cur.execute(
            """
            insert into outbound.campaign (name, status, mailbox_id, max_sends,
                                           recontact_interval_days, policy_include_suppliers,
                                           origin_source_record_id)
            values (%s, %s, %s, %s, %s, %s, %s)
            returning id
            """,
            (
                c["name"],
                c["status"],
                mailbox[0],
                c["max_sends"],
                c["recontact_interval_days"],
                c["policy_include_suppliers"],
                source_record_id,
            ),
        )
        ids[c["v1_campaign_id"]] = cur.fetchone()[0]
        inserted += 1
    return inserted, ids


def _insert_campaign_recipients(
    cur: Any, rows: Sequence[PlannedRow], campaign_ids: dict[str, str]
) -> tuple[int, dict[tuple[str, str], str]]:
    """Insert the frozen audience and return the (V1 campaign, address) → uuid map.

    `(campaign_id, address_norm)` is unique, so `ON CONFLICT DO NOTHING` is enough here once
    the campaign id itself is stable.
    """
    ids: dict[tuple[str, str], str] = {}
    inserted = 0
    for row in rows:
        c = row.columns
        campaign_id = campaign_ids.get(c["v1_campaign_id"])
        if campaign_id is None:
            raise ApplyRefused(f"recipient references unknown campaign {c['v1_campaign_id']!r}")
        cur.execute(
            """
            insert into outbound.campaign_recipient (campaign_id, address_norm, state,
                                                     exclusion_reasons)
            values (%s, %s, %s, %s)
            on conflict (campaign_id, address_norm) do nothing
            returning id
            """,
            (campaign_id, c["address_norm"], c["state"], c["exclusion_reasons"]),
        )
        found = cur.fetchone()
        if found is not None:
            inserted += 1
            ids[(c["v1_campaign_id"], c["address_norm"])] = found[0]
            continue
        cur.execute(
            "select id from outbound.campaign_recipient "
            "where campaign_id = %s and address_norm = %s",
            (campaign_id, c["address_norm"]),
        )
        existing = cur.fetchone()
        if existing is None:  # pragma: no cover - the insert above guarantees one exists
            raise ApplyRefused("a campaign recipient could not be read back")
        ids[(c["v1_campaign_id"], c["address_norm"])] = existing[0]
    return inserted, ids


def _insert_send_attempts(
    cur: Any,
    rows: Sequence[PlannedRow],
    campaign_ids: dict[str, str],
    recipient_ids: dict[tuple[str, str], str],
    mailbox_by_campaign: dict[str, str],
    source_record_id: str,
) -> tuple[int, int]:
    """Insert the send ledger. Returns ``(inserted, orphaned)``.

    `outbound.send_attempt` has no natural key either: a migrated V1 row mints no RFC 822
    id (`docs/DATA.md` §7.1), and one recipient may legitimately have several attempts. The
    ledger is therefore reconciled **per recipient by count** — if V1 records M attempts for
    a recipient and the table already holds N, only the missing M − N are written. That is
    idempotent without inventing a key and without collapsing genuine repeat attempts.

    An attempt whose address is not in its campaign's frozen audience has no recipient row
    to hang off, and `send_attempt_purpose_reference_shape` requires one for a marketing
    attempt. Such a row is counted as orphaned and skipped rather than forced.
    """
    inserted = 0
    orphaned = 0

    by_recipient: dict[str, list[PlannedRow]] = {}
    for row in rows:
        c = row.columns
        recipient_id = recipient_ids.get((c["v1_campaign_id"], c["address_norm"]))
        if recipient_id is None or campaign_ids.get(c["v1_campaign_id"]) is None:
            orphaned += 1
            continue
        by_recipient.setdefault(recipient_id, []).append(row)

    for recipient_id, planned in by_recipient.items():
        cur.execute(
            "select count(*) from outbound.send_attempt where campaign_recipient_id = %s",
            (recipient_id,),
        )
        already = int(cur.fetchone()[0])
        for row in planned[already:]:
            c = row.columns
            cur.execute(
                """
                insert into outbound.send_attempt (purpose, campaign_id, campaign_recipient_id,
                                                   mailbox_id, address_norm, submission_state,
                                                   delivery_state, error_class, accepted_at,
                                                   origin_source_record_id)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    c["purpose"],
                    campaign_ids[c["v1_campaign_id"]],
                    recipient_id,
                    mailbox_by_campaign[c["v1_campaign_id"]],
                    c["address_norm"],
                    c["submission_state"],
                    c["delivery_state"],
                    c["error_class"],
                    c["accepted_at"],
                    source_record_id,
                ),
            )
            inserted += 1
    return inserted, orphaned


def apply_plan(plan: ImportPlan, target: LocalTarget) -> ApplyResult:
    """Write the applicable part of ``plan`` to ``target`` in one transaction.

    Args:
        plan: a plan built by :func:`~.plan.build_plan`.
        target: a validated loopback target. Callers obtain it from
            :func:`~.target.assert_local_target`; this function never parses a DSN itself,
            so there is exactly one place the boundary can be checked.

    Returns:
        An :class:`ApplyResult` recording what was inserted and what already existed.

    Raises:
        ApplyRefused: the schema does not match, or the apply would change `crm.*`.
    """
    psycopg = _require_psycopg()

    inserted: dict[str, int] = {}
    blocked = {t.table: (t.blocked_by or "") for t in plan.blocked_tables}

    with psycopg.connect(target.dsn, autocommit=False) as conn:
        assert_schema_matches(conn)
        crm_before = _count_crm_rows(conn)

        with conn.cursor() as cur:
            # The manifest source record every assertion hangs off. Assertions carry a NOT
            # NULL source_record_id, so the manifest is written first and its id reused.
            records = plan.table("evidence.source_record").rows
            inserted["evidence.source_record"] = _insert_source_records(cur, records)

            manifest_key = plan.anchor_dedupe_key
            if not manifest_key:
                raise ApplyRefused("the plan carries no migration manifest source record")
            cur.execute(
                "select id from evidence.source_record where dedupe_key = %s", (manifest_key,)
            )
            found = cur.fetchone()
            if found is None:  # pragma: no cover - the insert above guarantees it
                raise ApplyRefused("the migration manifest source record could not be read back")
            source_record_id = found[0]

            assertion_rows = [
                r for r in plan.table("evidence.assertion").rows if r.columns.get("_blocked_by") is None
            ]
            inserted["evidence.assertion"] = _insert_assertions(
                cur, assertion_rows, source_record_id
            )

            control_rows = [
                r
                for r in plan.table("outbound.contact_control").rows
                if r.columns.get("_blocked_by") is None
            ]
            blocked_controls = len(plan.table("outbound.contact_control").rows) - len(control_rows)
            if blocked_controls:
                blocked["outbound.contact_control (wave 1B rows)"] = (
                    f"{blocked_controls} rows blocked by contact_control_wave1b_source"
                )
            inserted["outbound.contact_control"] = _insert_contact_controls(cur, control_rows)

            # Campaign history, in dependency order: a mailbox before its campaign, a
            # campaign before its audience, an audience row before its attempts.
            def applicable(name: str) -> list[PlannedRow]:
                table = plan.table(name)
                if not table.applicable:
                    return []
                return [r for r in table.rows if r.columns.get("_blocked_by") is None]

            inserted["comms.mailbox"] = _insert_mailboxes(cur, applicable("comms.mailbox"))
            campaign_rows = applicable("outbound.campaign")
            inserted["outbound.campaign"], campaign_ids = _insert_campaigns(
                cur, campaign_rows, source_record_id
            )
            mailbox_by_campaign: dict[str, str] = {}
            for row in campaign_rows:
                cur.execute(
                    "select id from comms.mailbox where address_norm = %s",
                    (row.columns["sender_address_norm"],),
                )
                mailbox = cur.fetchone()
                if mailbox is not None:
                    mailbox_by_campaign[row.columns["v1_campaign_id"]] = mailbox[0]

            (
                inserted["outbound.campaign_recipient"],
                recipient_ids,
            ) = _insert_campaign_recipients(
                cur, applicable("outbound.campaign_recipient"), campaign_ids
            )
            inserted["outbound.send_attempt"], orphaned = _insert_send_attempts(
                cur,
                applicable("outbound.send_attempt"),
                campaign_ids,
                recipient_ids,
                mailbox_by_campaign,
                source_record_id,
            )
            if orphaned:
                blocked["outbound.send_attempt (no audience row)"] = (
                    f"{orphaned} V1 attempts whose address is not in their campaign's "
                    "frozen audience; a marketing attempt requires a recipient row"
                )

        crm_after = _count_crm_rows(conn)
        if crm_after != crm_before:
            raise ApplyRefused(
                f"the apply changed crm.* row count from {crm_before} to {crm_after}; "
                "an import never creates a person, organization or prospect"
            )
        conn.commit()

    planned = {
        name: len(
            [
                r
                for r in plan.table(name).rows
                if plan.table(name).applicable and r.columns.get("_blocked_by") is None
            ]
        )
        for name in inserted
    }
    skipped = {name: planned[name] - inserted.get(name, 0) for name in planned}

    return ApplyResult(
        inserted=inserted,
        skipped_existing=skipped,
        blocked_tables=blocked,
        crm_rows_after=crm_after,
    )


__all__ = ["ApplyRefused", "ApplyResult", "WRITABLE_TABLES", "apply_plan", "assert_schema_matches"]

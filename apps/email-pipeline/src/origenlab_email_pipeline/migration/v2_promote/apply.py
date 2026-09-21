"""The transactional, idempotent write path for promotion.

Narrow in the same way the import's apply path is narrow:

* it writes only to a loopback database, resolved by the import's own target guard;
* it writes inside **one** transaction, so a failure leaves nothing behind;
* it **never** updates or deletes a ``crm`` row. Promotion adds rows; an operator's later
  edit is theirs and is never overwritten by a re-run;
* it writes a ``crm.domain_event`` for every row it creates, with ``actor_kind = 'migrator'``,
  so the audit stream is complete and says plainly that a migration — not a person — made
  these rows.

Idempotency is identifier-shaped, and deliberately so. Every promoted row carries an
identifier derived from the evidence by :func:`~.plan.deterministic_id`, so "already
promoted" is decided by reading that exact identifier back, not by matching a business key
that could coincide with something an operator created. A row that exists with the planned
identifier is compared field by field and left alone; a row that exists with *different*
immutable fields aborts the run rather than being adopted.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from origenlab_email_pipeline.migration.v2_import.apply import (
    IMPORT_ROLE,
    ApplyRefused,
    _require_psycopg,
    assume_import_role,
)
from origenlab_email_pipeline.migration.v2_import.target import (
    LocalTarget,
    neutralized_libpq_environment,
)
from origenlab_email_pipeline.migration.v2_promote.plan import (
    AssertionRow,
    PlannedContactPoint,
    PlannedOrganization,
    PlannedReview,
    PromotionPlan,
)

#: Tables promotion may write. Every other table — and in particular `crm.person`,
#: `crm.affiliation`, `crm.organization_relationship` and `crm.opportunity` — is absent by
#: design, because each would assert an identity or a relationship the evidence never
#: recorded.
WRITABLE_TABLES: frozenset[str] = frozenset(
    {
        "crm.contact_point",
        "crm.organization",
        "crm.domain_event",
        "evidence.assertion",
    }
)


@dataclass
class PromotionResult:
    organizations_created: int = 0
    organizations_already_present: int = 0
    contact_points_created: int = 0
    contact_points_already_present: int = 0
    assertions_promoted: int = 0
    assertions_marked_ambiguous: int = 0
    assertions_already_resolved: int = 0
    events_written: int = 0
    persons_created: int = 0
    crm_person_rows_after: int = 0
    notes: list[str] = field(default_factory=list)

    def to_report(self) -> dict[str, Any]:
        return {
            "organizations_created": self.organizations_created,
            "organizations_already_present": self.organizations_already_present,
            "contact_points_created": self.contact_points_created,
            "contact_points_already_present": self.contact_points_already_present,
            "assertions_promoted": self.assertions_promoted,
            "assertions_marked_ambiguous": self.assertions_marked_ambiguous,
            "assertions_already_resolved": self.assertions_already_resolved,
            "events_written": self.events_written,
            "persons_created": self.persons_created,
            "crm_person_rows_after": self.crm_person_rows_after,
            "notes": list(self.notes),
        }


def read_assertions(conn: Any) -> list[AssertionRow]:
    """Read every assertion promotion could act on."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select id::text, kind, value_norm, value, resolution, source_record_id::text
              from evidence.assertion
             where kind in ('contacted_address', 'organization_name', 'supplier_candidate')
             order by kind, value_norm, id
            """
        )
        return [
            AssertionRow(
                id=row[0],
                kind=row[1],
                value_norm=row[2],
                value=row[3],
                resolution=row[4],
                source_record_id=row[5],
            )
            for row in cur.fetchall()
        ]


def _next_seq(cur: Any, aggregate_kind: str, aggregate_id: str) -> int:
    cur.execute(
        "select coalesce(max(seq), 0) + 1 from crm.domain_event "
        "where aggregate_kind = %s and aggregate_id = %s",
        (aggregate_kind, aggregate_id),
    )
    return int(cur.fetchone()[0])


def _write_event(
    cur: Any,
    *,
    aggregate_kind: str,
    aggregate_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> int:
    """Append one migration event. Returns the number of rows written (0 or 1)."""
    seq = _next_seq(cur, aggregate_kind, aggregate_id)
    cur.execute(
        """
        insert into crm.domain_event
            (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
        values (%s, %s, %s, %s, 1, %s::jsonb, 'migrator')
        on conflict (aggregate_kind, aggregate_id, seq) do nothing
        """,
        (aggregate_kind, aggregate_id, seq, event_type, _json(payload)),
    )
    return cur.rowcount or 0


def _json(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _insert_organizations(
    cur: Any, rows: Sequence[PlannedOrganization], result: PromotionResult
) -> None:
    for row in rows:
        cur.execute(
            "select name, kind, confirmation from crm.organization where id = %s",
            (row.organization_id,),
        )
        existing = cur.fetchone()
        if existing is not None:
            stored_name, stored_kind, _ = existing
            if stored_name != row.name or stored_kind != row.kind:
                raise ApplyRefused(
                    f"crm.organization {row.organization_id} exists with different immutable "
                    f"fields (name/kind); refusing to adopt it"
                )
            result.organizations_already_present += 1
            continue

        cur.execute(
            """
            insert into crm.organization
                (id, kind, name, confirmation, origin_source_record_id, note)
            values (%s, %s, %s, 'machine_proposed', %s, %s)
            """,
            (
                row.organization_id,
                row.kind,
                row.name,
                row.origin_source_record_id,
                (
                    "promoted from migration evidence (observed campaign institution name). "
                    "kind is 'unknown' because DOMAIN.md 2.1 leaves the vocabulary open and "
                    "the evidence records no kind; confirmation is machine_proposed pending "
                    "operator review"
                ),
            ),
        )
        result.organizations_created += 1
        result.events_written += _write_event(
            cur,
            aggregate_kind="organization",
            aggregate_id=row.organization_id,
            event_type="organization.created",
            payload={
                "source": "v2_promote",
                "observed_name": row.name,
                "similarity_key": row.similarity_key,
                "kind": row.kind,
                "confirmation": "machine_proposed",
            },
        )


def _insert_contact_points(
    cur: Any, rows: Sequence[PlannedContactPoint], result: PromotionResult
) -> None:
    for row in rows:
        cur.execute(
            "select usage, person_id, organization_id from crm.contact_point where id = %s",
            (row.contact_point_id,),
        )
        existing = cur.fetchone()
        if existing is not None:
            stored_usage, _, _ = existing
            if stored_usage != row.usage:
                raise ApplyRefused(
                    f"crm.contact_point {row.contact_point_id} exists with usage "
                    f"'{stored_usage}', planned '{row.usage}'; refusing to adopt it"
                )
            result.contact_points_already_present += 1
            continue

        # A contact point already existing under a *different* identifier for the same
        # (kind, value_norm) is an operator's row, not ours. The unique constraint would
        # reject the insert; catching it here turns a constraint error into a clear refusal.
        cur.execute(
            "select id::text from crm.contact_point where kind = 'email' and value_norm = %s",
            (row.value_norm,),
        )
        clash = cur.fetchone()
        if clash is not None:
            raise ApplyRefused(
                f"an email contact point for this address already exists as {clash[0]}, which "
                f"is not the identifier promotion mints ({row.contact_point_id}); it was "
                "created by something other than this migration and is left untouched"
            )

        cur.execute(
            """
            insert into crm.contact_point
                (id, kind, value_norm, value_display, person_id, organization_id,
                 usage, confirmation, origin_source_record_id)
            values (%s, 'email', %s, %s, null, null, %s, 'machine_proposed', %s)
            """,
            (
                row.contact_point_id,
                row.value_norm,
                row.value_norm,
                row.usage,
                row.origin_source_record_id,
            ),
        )
        result.contact_points_created += 1
        result.events_written += _write_event(
            cur,
            aggregate_kind="contact_point",
            aggregate_id=row.contact_point_id,
            event_type="contact_point.created",
            payload={
                "source": "v2_promote",
                "usage": row.usage,
                "reason": row.reason,
                "is_role_mailbox": row.is_role_mailbox,
                "is_public_domain": row.is_public_domain,
                "looks_personal": row.looks_personal,
                "person_created": False,
                "organization_attached": False,
                "confirmation": "machine_proposed",
            },
        )


def _resolve_assertion(
    cur: Any,
    *,
    assertion_id: str,
    resolved_kind: str,
    resolved_id: str,
    result: PromotionResult,
) -> None:
    cur.execute(
        """
        update evidence.assertion
           set resolution = 'promoted',
               resolved_kind = %s,
               resolved_id = %s,
               resolved_at = now()
         where id = %s and resolution = 'unresolved'
        """,
        (resolved_kind, resolved_id, assertion_id),
    )
    if cur.rowcount:
        result.assertions_promoted += 1
        result.events_written += _write_event(
            cur,
            aggregate_kind="assertion",
            aggregate_id=assertion_id,
            event_type="assertion.promoted",
            payload={
                "source": "v2_promote",
                "resolved_kind": resolved_kind,
                "resolved_id": resolved_id,
            },
        )
    else:
        result.assertions_already_resolved += 1


def _mark_ambiguous(cur: Any, rows: Sequence[PlannedReview], result: PromotionResult) -> None:
    for row in rows:
        cur.execute(
            """
            update evidence.assertion
               set resolution = 'ambiguous',
                   ambiguity_note = %s
             where id = %s and resolution = 'unresolved'
            """,
            (row.note, row.assertion_id),
        )
        if cur.rowcount:
            result.assertions_marked_ambiguous += 1
        else:
            result.assertions_already_resolved += 1


def apply_promotion(plan: PromotionPlan, target: LocalTarget) -> PromotionResult:
    """Write the plan in one transaction, or write nothing."""
    psycopg = _require_psycopg()
    result = PromotionResult()

    with neutralized_libpq_environment(), psycopg.connect(
        target.dsn, autocommit=False
    ) as conn:
        assume_import_role(conn)
        with conn.cursor() as cur:
            _insert_organizations(cur, plan.organizations, result)
            for org in plan.organizations:
                _resolve_assertion(
                    cur,
                    assertion_id=org.assertion_id,
                    resolved_kind="organization",
                    resolved_id=org.organization_id,
                    result=result,
                )

            _insert_contact_points(cur, plan.contact_points, result)
            for cp in plan.contact_points:
                _resolve_assertion(
                    cur,
                    assertion_id=cp.assertion_id,
                    resolved_kind="contact_point",
                    resolved_id=cp.contact_point_id,
                    result=result,
                )

            _mark_ambiguous(cur, plan.reviews, result)

            # The invariant this whole module exists to preserve, asserted inside the
            # transaction so a violation rolls everything back rather than being reported
            # after the fact.
            cur.execute("select count(*) from crm.person")
            result.crm_person_rows_after = int(cur.fetchone()[0])
            if result.crm_person_rows_after != 0:
                raise ApplyRefused(
                    f"crm.person holds {result.crm_person_rows_after} rows after promotion; "
                    "this migration creates no person and must not have caused any to exist"
                )

            cur.execute(
                "select count(*) from crm.contact_point "
                "where person_id is not null or organization_id is not null"
            )
            attached = int(cur.fetchone()[0])
            if attached:
                raise ApplyRefused(
                    f"{attached} promoted contact points carry a person or organization; "
                    "promotion attaches neither, because a domain is never an identity key"
                )
        conn.commit()

    result.notes.append(
        "no crm.person row was created: the migration evidence records no display name for "
        "any person, and deriving one from an address local part would be an identity "
        "inference rather than evidence"
    )
    result.notes.append(
        "no contact point was attached to an organization: DOMAIN.md 2.2 makes a domain a "
        "routing hint, never an identity key on its own"
    )
    result.notes.append(
        "no consent, permission or subscription fact was created: email presence is never "
        "marketing permission"
    )
    return result


__all__ = [
    "IMPORT_ROLE",
    "WRITABLE_TABLES",
    "ApplyRefused",
    "PromotionResult",
    "apply_promotion",
    "read_assertions",
]

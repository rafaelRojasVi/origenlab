"""Attach the marketing history to canonical CRM identity.

The import lands campaigns, their frozen audiences, the send ledger and every suppression
fact in ``outbound.*``, all keyed by **address**. Promotion then creates the canonical
channel rows in ``crm.contact_point``. This stage joins the two: it fills
``outbound.campaign_recipient.contact_point_id`` so a campaign, a delivery, a reply and an
attribution can be read from a canonical identity rather than from a string.

What it links, and what it deliberately does not
------------------------------------------------

* **``contact_point_id`` only.** ``person_id`` and ``organization_id`` stay NULL, because the
  promotion attached neither — the evidence records no person's name, and a domain is never
  an identity key. Filling them here would smuggle in the identity decision that promotion
  correctly refused.
* **``outbound.contact_control`` is not touched.** A suppression is a fact about an
  *address*, not about an identity: it must keep working for an address whose owner is
  unknown, was never promoted, or is later merged. The table has no identity column by
  design, and that design is correct.
* **No domain event is written.** ``crm.domain_event`` records human commercial truth. This
  is a deterministic join over data that already exists — re-derivable at any time from the
  address, and carrying no decision anybody made.

Addresses with no canonical channel
-----------------------------------

Some recipients cannot be linked, and that is a correct outcome rather than a gap. A
recipient who was snapshotted into a frozen audience but never actually contacted produces
no ``contacted_address`` assertion, so promotion created no contact point for them: there is
no contact evidence, so there is no canonical channel. They are counted and reported, never
invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from origenlab_email_pipeline.migration.v2_import.apply import (
    ApplyRefused,
    _require_psycopg,
    assume_import_role,
)
from origenlab_email_pipeline.migration.v2_import.target import (
    LocalTarget,
    neutralized_libpq_environment,
)


@dataclass
class MarketingLinkResult:
    recipients_total: int = 0
    recipients_already_linked: int = 0
    recipients_linked_now: int = 0
    recipients_without_canonical_channel: int = 0
    attempts_reachable_from_identity: int = 0
    suppressions_matching_a_canonical_channel: int = 0
    notes: list[str] = field(default_factory=list)

    def to_report(self) -> dict[str, Any]:
        return {
            "recipients_total": self.recipients_total,
            "recipients_already_linked": self.recipients_already_linked,
            "recipients_linked_now": self.recipients_linked_now,
            "recipients_without_canonical_channel": self.recipients_without_canonical_channel,
            "attempts_reachable_from_identity": self.attempts_reachable_from_identity,
            "suppressions_matching_a_canonical_channel": (
                self.suppressions_matching_a_canonical_channel
            ),
            "notes": list(self.notes),
        }


#: The one statement that performs the linkage.
#:
#: `contact_point_id is null` makes it idempotent without a second bookkeeping table: a row
#: already linked is not touched, so a re-run reports zero and an operator's manual link is
#: never overwritten. The join is on the normalized address, which both sides already
#: normalize identically — `campaign_recipient_address_shape` and
#: `contact_point_value_norm_shape` enforce the same lower-cased form.
_LINK_SQL = """
update outbound.campaign_recipient r
   set contact_point_id = cp.id,
       updated_at = now()
  from crm.contact_point cp
 where cp.kind = 'email'
   and cp.value_norm = r.address_norm
   and r.contact_point_id is null
"""


def link_marketing(target: LocalTarget) -> MarketingLinkResult:
    """Link the marketing history to canonical channels, in one transaction."""
    psycopg = _require_psycopg()
    result = MarketingLinkResult()

    with neutralized_libpq_environment(), psycopg.connect(
        target.dsn, autocommit=False
    ) as conn:
        assume_import_role(conn)
        with conn.cursor() as cur:
            cur.execute("select count(*), count(contact_point_id) from outbound.campaign_recipient")
            total, already = cur.fetchone()
            result.recipients_total = int(total)
            result.recipients_already_linked = int(already)

            cur.execute(_LINK_SQL)
            result.recipients_linked_now = cur.rowcount or 0

            cur.execute(
                "select count(*) from outbound.campaign_recipient where contact_point_id is null"
            )
            result.recipients_without_canonical_channel = int(cur.fetchone()[0])

            # Attribution reach: how much of the send ledger is now addressable from a
            # canonical identity rather than from a string.
            cur.execute(
                """
                select count(*)
                  from outbound.send_attempt a
                  join outbound.campaign_recipient r on r.id = a.campaign_recipient_id
                 where r.contact_point_id is not null
                """
            )
            result.attempts_reachable_from_identity = int(cur.fetchone()[0])

            # Suppression reach is reported, never written: contact_control stays keyed by
            # address on purpose.
            cur.execute(
                """
                select count(*)
                  from outbound.contact_control c
                  join crm.contact_point cp
                    on cp.kind = 'email' and cp.value_norm = c.value_norm
                 where c.scope = 'address'
                """
            )
            result.suppressions_matching_a_canonical_channel = int(cur.fetchone()[0])

            # The invariant promotion established must survive this stage.
            cur.execute(
                "select count(*) from outbound.campaign_recipient "
                "where person_id is not null or organization_id is not null"
            )
            attached = int(cur.fetchone()[0])
            if attached:
                raise ApplyRefused(
                    f"{attached} campaign recipients carry a person or organization; this "
                    "stage links a channel only, and neither identity was established"
                )

            cur.execute("select count(*) from crm.person")
            persons = int(cur.fetchone()[0])
            if persons:
                raise ApplyRefused(
                    f"crm.person holds {persons} rows; no stage of this migration creates one"
                )
        conn.commit()

    result.notes.append(
        "only contact_point_id was linked: person and organization stay unset because the "
        "evidence establishes neither"
    )
    result.notes.append(
        "outbound.contact_control was not modified — a suppression is a fact about an "
        "address and must keep working for an address whose owner is unknown"
    )
    result.notes.append(
        "recipients without a canonical channel were snapshotted into an audience but never "
        "contacted, so no contact evidence and therefore no channel exists for them"
    )
    return result


def render_console(result: MarketingLinkResult) -> str:
    lines = ["marketing linkage:"]
    for key, value in result.to_report().items():
        if key == "notes":
            continue
        lines.append(f"  {key:<46} {value:>8}")
    for note in result.notes:
        lines.append(f"  note: {note}")
    return "\n".join(lines)

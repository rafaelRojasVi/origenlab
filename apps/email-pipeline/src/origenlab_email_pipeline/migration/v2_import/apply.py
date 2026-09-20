"""The transactional, idempotent write path.

Everything here is deliberately narrow:

* it writes only to a :class:`~.target.LocalTarget` — a disposable loopback database;
* it writes only the tables a plan marks applicable, never a table a structural gap blocks;
* it writes inside **one** transaction, so a failure leaves nothing behind;
* it **never** removes, truncates, overwrites or replaces an existing row. An import adds
  historical evidence; it does not overwrite anything an operator decided.

It also refuses to start when the target's schema is not the one the plan was built for.
A loader that adapts to whatever schema it finds is how a safety set silently loads into
the wrong shape, so the fingerprint check fails closed.

**Idempotency here is provenance-scoped, not key-shaped.** "This row is already imported"
is a claim about history, and a weak business key cannot establish it: an address, a
campaign name or a row count can coincide with something an operator created for an
unrelated reason. Every path that treats an existing row as already-imported therefore
reads the row back and compares the immutable fields the plan specifies, and refuses when
they differ rather than adopting the row:

* `evidence.source_record` — the `dedupe_key` is matched, then `kind`, `payload_sha256` and
  the payload itself are compared. `review_status` is excluded: it is operator state that
  legitimately advances after a load.
* `outbound.campaign` — has no natural key at all, so an imported campaign is identified by
  its **migration provenance** (`origin_source_record_id`) together with its deterministic
  identity (`mailbox_id`, `name`). Exactly one match is required and every immutable field
  is compared. A campaign that an operator happens to have given the same name on the same
  mailbox carries no `origin_source_record_id` from this run, so it is never found, never
  reused, and never receives an imported recipient or attempt.
* `outbound.campaign_recipient` — the natural key locates the row; `state` and
  `exclusion_reasons` are then compared before any attempt is attached to it.
* `outbound.send_attempt` — has no natural key either, so the ledger is reconciled as a
  **multiset scoped to `origin_source_record_id`**: attempts carrying another origin are
  invisible to the import, the existing imported multiset must be a subset of the planned
  one, and only the exact difference is written.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from origenlab_email_pipeline.migration.v2_import.plan import (
    IMPLEMENTED_CONTACT_CONTROL_SOURCES,
    ImportPlan,
    PlannedRow,
)
from origenlab_email_pipeline.migration.v2_import.target import (
    LocalTarget,
    neutralized_libpq_environment,
)

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

#: The role every write is made under. The seven tables are owned by `origenlab_owner`
#: and carry no grant for the Supabase CLI's `postgres` login, so a migration-time load
#: takes the same step every migration in `supabase/migrations/` takes: an explicit
#: `set local role`. `supabase/roles.sql` grants `postgres` and `origenlab_migrator` a
#: SET-only, non-inheriting membership for exactly this. `set local` is used, so the role
#: is scoped to the import's single transaction and released with it.
IMPORT_ROLE: str = "origenlab_owner"

#: The immutable columns that make up a migrated send attempt's canonical signature. The
#: ledger is reconciled by comparing multisets of these tuples, so every field an import
#: fixes must appear here: a field left out could differ without being noticed.
SEND_ATTEMPT_SIGNATURE_COLUMNS: tuple[str, ...] = (
    "purpose",
    "campaign_id",
    "campaign_recipient_id",
    "mailbox_id",
    "address_norm",
    "submission_state",
    "delivery_state",
    "error_class",
    "accepted_at",
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
    provenance_notes: dict[str, int] = field(default_factory=dict)

    @property
    def total_inserted(self) -> int:
        return sum(self.inserted.values())

    def to_report(self) -> dict[str, Any]:
        return {
            "inserted": dict(sorted(self.inserted.items())),
            "skipped_existing": dict(sorted(self.skipped_existing.items())),
            "blocked_tables": dict(sorted(self.blocked_tables.items())),
            "provenance_notes": dict(sorted(self.provenance_notes.items())),
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


def assume_import_role(conn: Any) -> None:
    """Take the owning role for this transaction, or refuse with the reason.

    Raises:
        ApplyRefused: the connected login is not a member of :data:`IMPORT_ROLE`. Without
            it every write fails one statement at a time; failing here says once, and in
            one sentence, which login the operator needs.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(f"set local role {IMPORT_ROLE}")
    except Exception as exc:  # noqa: BLE001 - re-raised as the boundary's own refusal
        raise ApplyRefused(
            f"cannot take the {IMPORT_ROLE} role on this target: {exc}. The V2 tables are "
            f"owned by {IMPORT_ROLE} and grant nothing to the Supabase CLI's postgres "
            "login; connect as a login holding a SET membership of it (supabase/roles.sql "
            "grants one to postgres and to origenlab_migrator)"
        ) from exc


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


def _drift(table: str, identity: str, field_name: str, planned: Any, stored: Any) -> ApplyRefused:
    """Build the refusal raised when an existing row is not the row the plan describes.

    ``identity`` names the row by its migration identity — a dedupe key, a campaign name, a
    row ordinal — never by an address, so a refusal can be printed on a console.
    """
    return ApplyRefused(
        f"provenance drift in {table} ({identity}): {field_name} is {stored!r} in the "
        f"database but {planned!r} in the plan. An existing row that differs from the plan "
        "is not the row this import wrote, so it is refused rather than reused. Nothing "
        "was committed."
    )


def _insert_source_records(cur: Any, rows: Sequence[PlannedRow]) -> int:
    """Insert the artifact manifests, verifying any that are already present.

    A `dedupe_key` collision means either "this import already ran" or "something else
    claimed this key". The two are told apart by reading the row back: `kind`,
    `payload_sha256` and the payload must all equal the plan. `review_status` is
    deliberately not compared — it is operator state that advances after a load, and an
    operator who has reviewed a manifest must not thereby break a re-run.
    """
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
        if cur.rowcount:
            inserted += 1
            continue
        cur.execute(
            """
            select kind, payload_sha256, payload = %s::jsonb
              from evidence.source_record
             where dedupe_key = %s
            """,
            (c["payload"], c["dedupe_key"]),
        )
        existing = cur.fetchone()
        if existing is None:  # pragma: no cover - the insert above guarantees one exists
            raise ApplyRefused("a source record could not be read back")
        identity = f"dedupe_key {c['dedupe_key']}"
        if existing[0] != c["kind"]:
            raise _drift("evidence.source_record", identity, "kind", c["kind"], existing[0])
        if existing[1] != c["payload_sha256"]:
            raise _drift(
                "evidence.source_record", identity, "payload_sha256",
                c["payload_sha256"], existing[1],
            )
        if not existing[2]:
            raise _drift(
                "evidence.source_record", identity, "payload",
                "the planned manifest", "a different manifest body",
            )
    return inserted


def _insert_assertions(cur: Any, rows: Sequence[PlannedRow], source_record_id: str) -> int:
    """Insert the evidence trail, verifying any assertion already present under this run.

    The conflict key carries `source_record_id`, so a collision is always a row this import
    wrote; `value` and `resolution` are still compared, because a changed mapping would
    otherwise be reported as "already imported".
    """
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
        if cur.rowcount:
            inserted += 1
            continue
        cur.execute(
            """
            select resolution, value = %s::jsonb
              from evidence.assertion
             where source_record_id = %s and kind = %s and value_norm = %s
            """,
            (c["value"], source_record_id, c["kind"], c["value_norm"]),
        )
        existing = cur.fetchone()
        if existing is None:  # pragma: no cover - the insert above guarantees one exists
            raise ApplyRefused("an assertion could not be read back")
        identity = f"kind {c['kind']}"
        if existing[0] != c["resolution"]:
            raise _drift("evidence.assertion", identity, "resolution", c["resolution"], existing[0])
        if not existing[1]:
            raise _drift(
                "evidence.assertion", identity, "value",
                "the planned assertion value", "a different value",
            )
    return inserted


def _insert_contact_controls(cur: Any, rows: Sequence[PlannedRow]) -> tuple[int, int]:
    """Insert the safety rows. Returns ``(inserted, provenance_divergences)``.

    The conflict key `(scope, value_norm, kind, purpose)` is the control's whole *effect*,
    so a skipped row already does exactly what the planned row would do, and re-recording it
    is not required. Two things are still checked on the existing row:

    * `needs_review` — a planned row that asks for operator review must not be silently
      satisfied by a row that does not carry the flag. That loses a safety obligation, so it
      is a refusal.
    * `source` and `reason` — provenance, which a row written by another route legitimately
      differs on. The import must not overwrite an operator's record of why an address is
      blocked, so a difference is counted and reported rather than refused.
    """
    inserted = 0
    divergences = 0
    for index, row in enumerate(rows):
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
        if cur.rowcount:
            inserted += 1
            continue
        cur.execute(
            """
            select needs_review, source, reason
              from outbound.contact_control
             where scope = %s and value_norm = %s and kind = %s and purpose = %s
            """,
            (c["scope"], c["value_norm"], c["kind"], c["purpose"]),
        )
        existing = cur.fetchone()
        if existing is None:  # pragma: no cover - the insert above guarantees one exists
            raise ApplyRefused("a contact control could not be read back")
        identity = f"{c['scope']}/{c['kind']}/{c['purpose']} row {index}"
        if c["needs_review"] and not existing[0]:
            raise _drift(
                "outbound.contact_control", identity, "needs_review",
                c["needs_review"], existing[0],
            )
        if existing[1] != c["source"] or existing[2] != c["reason"]:
            divergences += 1
    return inserted, divergences


def _insert_mailboxes(cur: Any, rows: Sequence[PlannedRow]) -> int:
    """Insert historical sender mailboxes, verifying any that already exist.

    `address_norm` is the mailbox's whole identity, so a collision is the same mailbox and
    reusing it is correct. `provider` is compared because it is immutable for a given
    address; `is_production_sender` and `authorization_state` are not, because they are
    operator state this importer never writes and an operator may legitimately have
    advanced.
    """
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
        if cur.rowcount:
            inserted += 1
            continue
        cur.execute(
            "select provider from comms.mailbox where address_norm = %s", (c["address_norm"],)
        )
        existing = cur.fetchone()
        if existing is None:  # pragma: no cover - the insert above guarantees one exists
            raise ApplyRefused("a mailbox could not be read back")
        if existing[0] != c["provider"]:
            raise _drift(
                "comms.mailbox", "a historical sender", "provider", c["provider"], existing[0]
            )
    return inserted


def _insert_campaigns(
    cur: Any, rows: Sequence[PlannedRow], source_record_id: str
) -> tuple[int, dict[str, str]]:
    """Insert campaigns and return the V1 id → V2 uuid map the child rows need.

    `outbound.campaign` has **no natural-key unique constraint** — its only key is the
    surrogate `id` — so `ON CONFLICT` cannot make this idempotent, and the pair
    `(mailbox_id, name)` is not a safe substitute: it is a business label an operator may
    reuse. An imported campaign is therefore identified by its **migration provenance**,
    `origin_source_record_id`, together with that deterministic identity. Exactly one match
    is required, and every immutable field is compared before the campaign is reused.

    A pre-existing campaign that shares the mailbox and the name but carries a different
    origin (or none) is not a match, so it is left exactly as it was and receives no
    imported recipient and no imported attempt.
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
            """
            select id, name, status, mailbox_id, max_sends, recontact_interval_days,
                   policy_include_suppliers, origin_source_record_id
              from outbound.campaign
             where origin_source_record_id = %s and mailbox_id = %s and name = %s
             order by id
            """,
            (source_record_id, mailbox[0], c["name"]),
        )
        matches = cur.fetchall()
        if len(matches) > 1:
            raise ApplyRefused(
                f"campaign {c['v1_campaign_id']!r} matches {len(matches)} imported campaigns "
                "under one migration origin; the import identity is ambiguous and nothing "
                "was committed"
            )
        if matches:
            existing = matches[0]
            identity = f"v1 campaign {c['v1_campaign_id']}"
            for position, (field_name, planned) in enumerate(
                (
                    ("name", c["name"]),
                    ("status", c["status"]),
                    ("mailbox_id", mailbox[0]),
                    ("max_sends", c["max_sends"]),
                    ("recontact_interval_days", c["recontact_interval_days"]),
                    ("policy_include_suppliers", c["policy_include_suppliers"]),
                    ("origin_source_record_id", source_record_id),
                ),
                start=1,
            ):
                if existing[position] != planned:
                    raise _drift(
                        "outbound.campaign", identity, field_name, planned, existing[position]
                    )
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

    `(campaign_id, address_norm)` is unique, and `campaign_id` is already provenance-scoped
    by :func:`_insert_campaigns`, so a conflict is a row from an earlier run of this import.
    It is still read back and compared: `state` and `exclusion_reasons` are the historical
    facts the row exists to carry, and an attempt must never be attached to a recipient row
    that disagrees with the plan.
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
            """
            select id, state, exclusion_reasons
              from outbound.campaign_recipient
             where campaign_id = %s and address_norm = %s
            """,
            (campaign_id, c["address_norm"]),
        )
        existing = cur.fetchone()
        if existing is None:  # pragma: no cover - the insert above guarantees one exists
            raise ApplyRefused("a campaign recipient could not be read back")
        identity = f"v1 campaign {c['v1_campaign_id']}, audience row"
        if existing[1] != c["state"]:
            raise _drift(
                "outbound.campaign_recipient", identity, "state", c["state"], existing[1]
            )
        if list(existing[2] or []) != list(c["exclusion_reasons"]):
            raise _drift(
                "outbound.campaign_recipient", identity, "exclusion_reasons",
                list(c["exclusion_reasons"]), list(existing[2] or []),
            )
        ids[(c["v1_campaign_id"], c["address_norm"])] = existing[0]
    return inserted, ids


def _canonical_instants(cur: Any, stamps: Sequence[str]) -> dict[str, Any]:
    """Map each planned timestamp literal to the instant PostgreSQL reads it as.

    A planned `accepted_at` is the text V1 recorded; a stored one comes back from the driver
    as an instant. Comparing them as written would make two spellings of the same moment
    look like drift, so the database itself canonicalizes the planned side, in one round
    trip, before any signature is built.
    """
    if not stamps:
        return {}
    cur.execute(
        "select s, s::timestamptz from unnest(%s::text[]) as s", (sorted(set(stamps)),)
    )
    return {r[0]: r[1] for r in cur.fetchall()}


def _insert_send_attempts(
    cur: Any,
    rows: Sequence[PlannedRow],
    campaign_ids: dict[str, str],
    recipient_ids: dict[tuple[str, str], str],
    mailbox_by_campaign: dict[str, str],
    source_record_id: str,
) -> tuple[int, int, int]:
    """Insert the send ledger. Returns ``(inserted, orphaned, foreign_origin_attempts)``.

    `outbound.send_attempt` has no natural key: a migrated V1 row mints no RFC 822 id
    (`docs/DATA.md` §7.1), and one recipient may legitimately have several attempts. The
    ledger is therefore reconciled as a **multiset scoped to this migration's origin**:

    1. only attempts carrying `origin_source_record_id = source_record_id` are considered,
       so an attempt written by anything else is invisible to the import and cannot make a
       historical attempt look already-present;
    2. each attempt on both sides is reduced to its canonical signature — every immutable
       imported field, listed in :data:`SEND_ATTEMPT_SIGNATURE_COLUMNS`;
    3. if the existing imported multiset is not a subset of the planned one, the ledger in
       the database is not this plan's ledger, and the run is refused;
    4. otherwise exactly the multiset difference is written, so a repeated apply writes
       nothing and a partially present ledger is completed rather than doubled.

    An attempt whose address is not in its campaign's frozen audience has no recipient row
    to hang off, and `send_attempt_purpose_reference_shape` requires one for a marketing
    attempt. Such a row is counted as orphaned and skipped rather than forced.
    """
    inserted = 0
    orphaned = 0
    foreign_origin = 0

    by_recipient: dict[str, list[PlannedRow]] = {}
    for row in rows:
        c = row.columns
        recipient_id = recipient_ids.get((c["v1_campaign_id"], c["address_norm"]))
        if recipient_id is None or campaign_ids.get(c["v1_campaign_id"]) is None:
            orphaned += 1
            continue
        by_recipient.setdefault(recipient_id, []).append(row)

    instants = _canonical_instants(
        cur,
        [
            r.columns["accepted_at"]
            for rs in by_recipient.values()
            for r in rs
            if r.columns["accepted_at"] is not None
        ],
    )

    for recipient_id, planned_rows in by_recipient.items():
        planned: list[tuple[Any, ...]] = []
        for row in planned_rows:
            c = row.columns
            accepted_at = c["accepted_at"]
            planned.append(
                (
                    c["purpose"],
                    campaign_ids[c["v1_campaign_id"]],
                    recipient_id,
                    mailbox_by_campaign[c["v1_campaign_id"]],
                    c["address_norm"],
                    c["submission_state"],
                    c["delivery_state"],
                    c["error_class"],
                    None if accepted_at is None else instants[accepted_at],
                )
            )

        cur.execute(
            """
            select purpose, campaign_id, campaign_recipient_id, mailbox_id, address_norm,
                   submission_state, delivery_state, error_class, accepted_at,
                   origin_source_record_id is not distinct from %s
              from outbound.send_attempt
             where campaign_recipient_id = %s
            """,
            (source_record_id, recipient_id),
        )
        existing: Counter[tuple[Any, ...]] = Counter()
        for record in cur.fetchall():
            if record[-1]:
                existing[tuple(record[:-1])] += 1
            else:
                foreign_origin += 1

        surplus = existing - Counter(planned)
        if surplus:
            raise ApplyRefused(
                f"send-ledger drift: {sum(surplus.values())} attempt(s) already recorded "
                f"under this migration origin for one audience row are not in the plan "
                "(the stored ledger is not a subset of the planned one). An import "
                "completes a ledger; it never reconciles one by removing a row, so the run "
                "is refused and nothing was committed."
            )

        remaining = existing.copy()
        for signature in planned:
            if remaining[signature] > 0:
                remaining[signature] -= 1
                continue
            cur.execute(
                """
                insert into outbound.send_attempt (purpose, campaign_id, campaign_recipient_id,
                                                   mailbox_id, address_norm, submission_state,
                                                   delivery_state, error_class, accepted_at,
                                                   origin_source_record_id)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (*signature, source_record_id),
            )
            inserted += 1
    return inserted, orphaned, foreign_origin


def apply_plan(plan: ImportPlan, target: LocalTarget) -> ApplyResult:
    """Write the applicable part of ``plan`` to ``target`` in one transaction.

    Args:
        plan: a plan built by :func:`~.plan.build_plan`.
        target: a validated loopback target. Callers obtain it from
            :func:`~.target.assert_local_target`; this function never parses a DSN itself,
            so there is exactly one place the boundary can be checked. The libpq routing
            environment is neutralized for the lifetime of the connection, so no `PG*`
            variable can send the validated DSN somewhere else.

    Returns:
        An :class:`ApplyResult` recording what was inserted and what already existed.

    Raises:
        ApplyRefused: the schema does not match, an existing row under the import's
            provenance differs from the plan, or the apply would change `crm.*`.
    """
    psycopg = _require_psycopg()

    inserted: dict[str, int] = {}
    notes: dict[str, int] = {}
    blocked = {t.table: (t.blocked_by or "") for t in plan.blocked_tables}

    with neutralized_libpq_environment(), psycopg.connect(
        target.dsn, autocommit=False
    ) as conn:
        assume_import_role(conn)
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
            control_count, control_divergences = _insert_contact_controls(cur, control_rows)
            inserted["outbound.contact_control"] = control_count
            if control_divergences:
                notes["contact_control_provenance_divergence"] = control_divergences

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
            (
                inserted["outbound.send_attempt"],
                orphaned,
                foreign_origin,
            ) = _insert_send_attempts(
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
            if foreign_origin:
                notes["send_attempts_with_another_origin_left_untouched"] = foreign_origin

        crm_after = _count_crm_rows(conn)
        if crm_after != crm_before:
            raise ApplyRefused(
                f"the apply changed crm.* row count from {crm_before} to {crm_after}; "
                "an import never creates a person, organization or prospect"
            )
        conn.commit()

    planned_counts = {
        name: len(
            [
                r
                for r in plan.table(name).rows
                if plan.table(name).applicable and r.columns.get("_blocked_by") is None
            ]
        )
        for name in inserted
    }
    skipped = {name: planned_counts[name] - inserted.get(name, 0) for name in planned_counts}

    return ApplyResult(
        inserted=inserted,
        skipped_existing=skipped,
        blocked_tables=blocked,
        crm_rows_after=crm_after,
        provenance_notes=notes,
    )


__all__ = [
    "IMPORT_ROLE",
    "SEND_ATTEMPT_SIGNATURE_COLUMNS",
    "WRITABLE_TABLES",
    "ApplyRefused",
    "ApplyResult",
    "apply_plan",
    "assert_schema_matches",
    "assume_import_role",
]

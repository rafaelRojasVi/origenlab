"""The transactional, idempotent write path for Gmail/Drive evidence staging.

What this writes: `evidence.source_record` and `evidence.assertion`. That is the whole list.

What it may never write, in any mode, with any flag:

* **`crm.*`.** Staging creates review candidates, never identities. The `crm` row count is
  read before and after inside the same transaction and a change rolls the whole staging
  back — the same proof the Wave 1A/1B importer takes, for the same reason.
* **`outbound.*`.** A staged observation is not a contact, not a suppression and not
  permission to send. Nothing here touches the send flags or the contact controls.
* **a resolution.** Every assertion lands `unresolved` and every record lands `pending`.
  The tool has no flag that promotes, links, merges or confirms anything: promotion is
  `promote_evidence_into_crm.py`, run separately, by a human who looked.

Idempotency is provenance-shaped. A record is identified by its `dedupe_key`
(`<source_kind>:<external_id>`), which the database holds unique; an assertion by
`(source_record_id, kind, value_norm)`, which the database also holds unique. A record
already staged is read back and compared field by field, and one whose immutable fields
differ aborts the run instead of being adopted or overwritten.
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
from origenlab_email_pipeline.migration.v2_evidence_stage.manifest import Manifest

#: The only two tables staging may write.
WRITABLE_TABLES: frozenset[str] = frozenset({"evidence.source_record", "evidence.assertion"})

#: Immutable once staged. A manifest that disagrees with an already-staged record on any of
#: these is describing a *different* record under the same identity, which is a mistake to
#: report rather than a difference to reconcile.
IMMUTABLE_RECORD_FIELDS: tuple[str, ...] = ("kind", "source_uri")


@dataclass
class StageResult:
    source_records_created: int = 0
    source_records_already_present: int = 0
    assertions_created: int = 0
    assertions_already_present: int = 0
    crm_rows_before: int = 0
    crm_rows_after: int = 0
    notes: list[str] = field(default_factory=list)

    def to_report(self) -> dict[str, Any]:
        return {
            "source_records_created": self.source_records_created,
            "source_records_already_present": self.source_records_already_present,
            "assertions_created": self.assertions_created,
            "assertions_already_present": self.assertions_already_present,
            "crm_rows_before": self.crm_rows_before,
            "crm_rows_after": self.crm_rows_after,
            "notes": list(self.notes),
        }


def _count_crm_rows(conn: Any) -> int:
    """Count business rows across every `crm.*` table.

    Cheapest possible proof that no staged observation became a person, an organization or
    an opportunity.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select table_name from information_schema.tables where table_schema = 'crm' "
            "and table_type = 'BASE TABLE' order by table_name"
        )
        tables = [row[0] for row in cur.fetchall()]
        total = 0
        for table in tables:
            cur.execute(f'select count(*) from crm."{table}"')  # noqa: S608 - catalogue name
            total += int(cur.fetchone()[0])
    return total


def assert_schema_accepts_staging(conn: Any) -> None:
    """Refuse unless the target carries the Gmail/Drive vocabulary this tool writes.

    Without migration `20260921120000` the two kinds violate a CHECK constraint, and the
    driver's message names a constraint rather than the thing the operator has to do. This
    turns that into one sentence.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conname = 'source_record_kind_check'"
        )
        row = cur.fetchone()
        if row is None:
            raise ApplyRefused(
                "the target has no evidence.source_record — it does not carry the Slice 0 schema"
            )
        definition = row[0]
        missing = [k for k in ("gmail_message", "drive_file") if f"'{k}'" not in definition]
        if missing:
            raise ApplyRefused(
                f"the target's source_record kind vocabulary lacks {', '.join(missing)}. "
                "Apply supabase/migrations/20260921120000_slice2_gmail_drive_evidence_kinds.sql "
                "to this database first (supabase/scripts/dev_db.sh migrate)"
            )

        cur.execute(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conname = 'assertion_kind_check'"
        )
        assertion_row = cur.fetchone()
        if assertion_row is None or "'document_reference'" not in assertion_row[0]:
            raise ApplyRefused(
                "the target's assertion kind vocabulary lacks document_reference. Apply "
                "supabase/migrations/20260921120000_slice2_gmail_drive_evidence_kinds.sql first"
            )


def apply_staging(manifest: Manifest, target: LocalTarget) -> StageResult:
    """Stage ``manifest`` into ``target`` in one transaction.

    Args:
        manifest: a manifest already validated by
            :func:`~.manifest.load_manifest`. This function performs no validation of its
            own, so there is exactly one place a manifest is judged.
        target: a validated loopback target from
            :func:`~..v2_import.target.assert_local_target`. This function never parses a
            DSN, so there is exactly one place the database boundary is checked.

    Raises:
        ApplyRefused: the schema lacks the staging vocabulary, an already-staged record
            differs from the manifest, or the staging would change `crm.*`.
    """
    psycopg = _require_psycopg()
    result = StageResult()

    with neutralized_libpq_environment(), psycopg.connect(
        target.dsn, autocommit=False
    ) as conn:
        assume_import_role(conn)
        assert_schema_accepts_staging(conn)
        result.crm_rows_before = _count_crm_rows(conn)

        with conn.cursor() as cur:
            for record in manifest.records:
                cur.execute(
                    "select id, kind, source_uri from evidence.source_record "
                    "where dedupe_key = %s",
                    (record.dedupe_key,),
                )
                existing = cur.fetchone()
                if existing is None:
                    cur.execute(
                        """
                        insert into evidence.source_record
                            (kind, dedupe_key, payload, source_uri, acquired_at, review_status)
                        values (%s, %s, %s::jsonb, %s, coalesce(%s, now()), 'pending')
                        returning id
                        """,
                        (
                            record.source_kind,
                            record.dedupe_key,
                            _json(record.payload),
                            record.source_uri,
                            record.acquired_at,
                        ),
                    )
                    source_record_id = cur.fetchone()[0]
                    result.source_records_created += 1
                else:
                    source_record_id = existing[0]
                    stored = {"kind": existing[1], "source_uri": existing[2]}
                    planned = {"kind": record.source_kind, "source_uri": record.source_uri}
                    for name in IMMUTABLE_RECORD_FIELDS:
                        if stored[name] != planned[name]:
                            raise ApplyRefused(
                                f"{record.dedupe_key}: already staged with {name}="
                                f"{stored[name]!r}, manifest says {planned[name]!r}. "
                                "Staging never overwrites an acquired record; supersede it "
                                "instead, or correct the manifest"
                            )
                    result.source_records_already_present += 1

                for observation in record.observations:
                    cur.execute(
                        """
                        insert into evidence.assertion
                            (source_record_id, kind, value_norm, value, resolution)
                        values (%s, %s, %s, %s::jsonb, 'unresolved')
                        on conflict (source_record_id, kind, value_norm) do nothing
                        returning id
                        """,
                        (
                            source_record_id,
                            observation.kind,
                            observation.value_norm,
                            _json(
                                {
                                    "observed_value": observation.value,
                                    "provider": manifest.provider,
                                    "external_id": record.external_id,
                                    **(observation.value_payload or {}),
                                }
                            ),
                        ),
                    )
                    if cur.fetchone() is None:
                        result.assertions_already_present += 1
                    else:
                        result.assertions_created += 1

        result.crm_rows_after = _count_crm_rows(conn)
        if result.crm_rows_after != result.crm_rows_before:
            raise ApplyRefused(
                f"staging changed crm.* from {result.crm_rows_before} to "
                f"{result.crm_rows_after} rows. Staging creates review candidates and never "
                "identities; the transaction is rolled back"
            )
        conn.commit()

    return result


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


__all__ = [
    "IMMUTABLE_RECORD_FIELDS",
    "WRITABLE_TABLES",
    "ApplyRefused",
    "StageResult",
    "apply_staging",
    "assert_schema_accepts_staging",
]

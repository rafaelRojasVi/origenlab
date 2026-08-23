"""SQLite PR3 commercial opportunity repository (read-only)."""

from __future__ import annotations

from typing import Any

from origenlab_api.repositories.commercial_opportunities import (
    map_conflict_row,
    map_evidence_row,
    map_event_row,
    map_opportunity_row,
)
from origenlab_api.schemas.commercial_opportunities import (
    CommercialOpportunitiesMeta,
    CommercialOpportunityDetailMeta,
    CommercialOpportunityDetailResponse,
    CommercialOpportunityItem,
)
from origenlab_api.settings import Settings
from origenlab_api.sqlite_ro import open_operator_sqlite

_REQUIRED_TABLES = (
    "commercial_opportunity",
    "commercial_opportunity_event",
    "commercial_opportunity_evidence",
    "commercial_opportunity_conflict",
    "commercial_identity_contact",
    "commercial_identity_account",
)

_FILTER_COLUMNS = {
    "canonical_stage": "o.canonical_stage",
    "record_kind": "o.record_kind",
    "review_status": "o.review_status",
    "account_id": "o.account_id",
    "primary_contact_id": "o.primary_contact_id",
}


def _tables_available(conn: Any) -> bool:
    for table in _REQUIRED_TABLES:
        row = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type='table' AND name=?
            LIMIT 1
            """,
            (table,),
        ).fetchone()
        if row is None:
            return False
    return True


def _where_clause(
    *,
    canonical_stage: str | None,
    record_kind: str | None,
    review_status: str | None,
    account_id: str | None,
    primary_contact_id: str | None,
) -> tuple[str, list[Any]]:
    filters = {
        "canonical_stage": canonical_stage,
        "record_kind": record_kind,
        "review_status": review_status,
        "account_id": account_id,
        "primary_contact_id": primary_contact_id,
    }

    clauses: list[str] = []
    params: list[Any] = []

    for name, raw in filters.items():
        value = (raw or "").strip()
        if not value:
            continue
        clauses.append(f"{_FILTER_COLUMNS[name]} = ?")
        params.append(value)

    if not clauses:
        return "", params
    return " WHERE " + " AND ".join(clauses), params


_PARENT_SELECT = """
SELECT
  o.*,
  c.normalized_email AS contact_display_email,
  a.primary_domain AS account_display_domain
FROM commercial_opportunity AS o
LEFT JOIN commercial_identity_contact AS c
  ON c.contact_id = o.primary_contact_id
LEFT JOIN commercial_identity_account AS a
  ON a.account_id = o.account_id
"""


class SqliteCommercialOpportunityRepository:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def list_commercial(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        canonical_stage: str | None = None,
        record_kind: str | None = None,
        review_status: str | None = None,
        account_id: str | None = None,
        primary_contact_id: str | None = None,
    ) -> tuple[list[CommercialOpportunityItem], CommercialOpportunitiesMeta]:
        cap = max(1, min(int(limit), 200))
        skip = max(0, int(offset))
        sqlite_path = self._settings.resolved_sqlite_path()

        if not sqlite_path.is_file():
            return [], CommercialOpportunitiesMeta(
                data_source="sqlite_pr3",
                count=0,
                total_count=0,
                limit=cap,
                offset=skip,
                reduced_mode=True,
                note="SQLite database file not found.",
            )

        conn = open_operator_sqlite(sqlite_path, settings=self._settings)
        try:
            if not _tables_available(conn):
                return [], CommercialOpportunitiesMeta(
                    data_source="sqlite_pr3",
                    count=0,
                    total_count=0,
                    limit=cap,
                    offset=skip,
                    reduced_mode=True,
                    note="Commercial opportunity PR2/PR3 read model is not available.",
                )

            where_sql, params = _where_clause(
                canonical_stage=canonical_stage,
                record_kind=record_kind,
                review_status=review_status,
                account_id=account_id,
                primary_contact_id=primary_contact_id,
            )

            count_row = conn.execute(
                "SELECT COUNT(*) FROM commercial_opportunity AS o" + where_sql,
                params,
            ).fetchone()
            total_count = int(count_row[0]) if count_row else 0

            rows = conn.execute(
                _PARENT_SELECT
                + where_sql
                + """
                ORDER BY
                  (o.last_activity_at IS NULL) ASC,
                  o.last_activity_at DESC,
                  o.opportunity_id ASC
                LIMIT ? OFFSET ?
                """,
                [*params, cap, skip],
            ).fetchall()

            items = [map_opportunity_row(dict(row)) for row in rows]
            return items, CommercialOpportunitiesMeta(
                data_source="sqlite_pr3",
                count=len(items),
                total_count=total_count,
                limit=cap,
                offset=skip,
                reduced_mode=False,
                note="",
            )
        finally:
            conn.close()

    def get_commercial_detail(
        self,
        opportunity_id: str,
    ) -> CommercialOpportunityDetailResponse | None:
        sqlite_path = self._settings.resolved_sqlite_path()
        if not sqlite_path.is_file():
            return None

        conn = open_operator_sqlite(sqlite_path, settings=self._settings)
        try:
            if not _tables_available(conn):
                return None

            row = conn.execute(
                _PARENT_SELECT + " WHERE o.opportunity_id = ? LIMIT 1",
                (opportunity_id,),
            ).fetchone()
            if row is None:
                return None

            event_rows = conn.execute(
                """
                SELECT *
                FROM commercial_opportunity_event
                WHERE opportunity_id = ?
                ORDER BY
                  (event_at IS NULL) ASC,
                  event_at DESC,
                  event_id ASC
                """,
                (opportunity_id,),
            ).fetchall()

            evidence_rows = conn.execute(
                """
                SELECT *
                FROM commercial_opportunity_evidence
                WHERE opportunity_id = ?
                ORDER BY
                  (evidence_at IS NULL) ASC,
                  evidence_at DESC,
                  evidence_id ASC
                """,
                (opportunity_id,),
            ).fetchall()

            conflict_rows = conn.execute(
                """
                SELECT *
                FROM commercial_opportunity_conflict
                WHERE opportunity_id = ?
                ORDER BY conflict_id ASC
                """,
                (opportunity_id,),
            ).fetchall()

            return CommercialOpportunityDetailResponse(
                meta=CommercialOpportunityDetailMeta(
                    data_source="sqlite_pr3",
                    read_only=True,
                ),
                opportunity=map_opportunity_row(dict(row)),
                events=[map_event_row(dict(item)) for item in event_rows],
                evidence=[map_evidence_row(dict(item)) for item in evidence_rows],
                conflicts=[map_conflict_row(dict(item)) for item in conflict_rows],
            )
        finally:
            conn.close()

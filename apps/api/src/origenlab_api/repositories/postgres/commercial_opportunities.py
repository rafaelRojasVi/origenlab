"""Postgres ARCH-2A commercial opportunity projection repository."""

from __future__ import annotations

from typing import Any

from origenlab_api.repositories.commercial_opportunities import (
    map_conflict_row,
    map_evidence_row,
    map_event_row,
    map_opportunity_row,
)
from origenlab_api.repositories.postgres.common import (
    postgres_connection,
    require_psycopg,
)
from origenlab_api.schemas.commercial_opportunities import (
    CommercialOpportunitiesMeta,
    CommercialOpportunityDetailMeta,
    CommercialOpportunityDetailResponse,
    CommercialOpportunityItem,
)
from origenlab_api.settings import Settings


_LIST_SQL = """
SELECT *
FROM api.v_commercial_opportunity
WHERE (%(canonical_stage)s::text IS NULL OR canonical_stage = %(canonical_stage)s)
  AND (%(record_kind)s::text IS NULL OR record_kind = %(record_kind)s)
  AND (%(review_status)s::text IS NULL OR review_status = %(review_status)s)
  AND (%(account_id)s::text IS NULL OR account_id = %(account_id)s)
  AND (
    %(primary_contact_id)s::text IS NULL
    OR primary_contact_id = %(primary_contact_id)s
  )
ORDER BY last_activity_at DESC NULLS LAST, opportunity_id ASC
LIMIT %(limit)s OFFSET %(offset)s
"""

_COUNT_SQL = """
SELECT COUNT(*)
FROM api.v_commercial_opportunity
WHERE (%(canonical_stage)s::text IS NULL OR canonical_stage = %(canonical_stage)s)
  AND (%(record_kind)s::text IS NULL OR record_kind = %(record_kind)s)
  AND (%(review_status)s::text IS NULL OR review_status = %(review_status)s)
  AND (%(account_id)s::text IS NULL OR account_id = %(account_id)s)
  AND (
    %(primary_contact_id)s::text IS NULL
    OR primary_contact_id = %(primary_contact_id)s
  )
"""


def _filter_value(value: str | None) -> str | None:
    stripped = (value or "").strip()
    return stripped or None


class PostgresCommercialOpportunityRepository:
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
        params: dict[str, Any] = {
            "limit": cap,
            "offset": skip,
            "canonical_stage": _filter_value(canonical_stage),
            "record_kind": _filter_value(record_kind),
            "review_status": _filter_value(review_status),
            "account_id": _filter_value(account_id),
            "primary_contact_id": _filter_value(primary_contact_id),
        }

        pg = require_psycopg()
        with postgres_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(_COUNT_SQL, params)
                count_row = cur.fetchone()
                total_count = int(count_row["count"]) if count_row else 0

                cur.execute(_LIST_SQL, params)
                items = [map_opportunity_row(dict(row)) for row in cur.fetchall()]

        return items, CommercialOpportunitiesMeta(
            data_source="postgres_mirror",
            count=len(items),
            total_count=total_count,
            limit=cap,
            offset=skip,
            reduced_mode=False,
            note="",
        )

    def get_commercial_detail(
        self,
        opportunity_id: str,
    ) -> CommercialOpportunityDetailResponse | None:
        pg = require_psycopg()

        with postgres_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM api.v_commercial_opportunity
                    WHERE opportunity_id = %(opportunity_id)s
                    LIMIT 1
                    """,
                    {"opportunity_id": opportunity_id},
                )
                parent = cur.fetchone()
                if parent is None:
                    return None

                cur.execute(
                    """
                    SELECT *
                    FROM api.v_commercial_opportunity_event
                    WHERE opportunity_id = %(opportunity_id)s
                    ORDER BY event_at DESC NULLS LAST, event_id ASC
                    """,
                    {"opportunity_id": opportunity_id},
                )
                events = [map_event_row(dict(row)) for row in cur.fetchall()]

                cur.execute(
                    """
                    SELECT *
                    FROM api.v_commercial_opportunity_evidence
                    WHERE opportunity_id = %(opportunity_id)s
                    ORDER BY evidence_at DESC NULLS LAST, evidence_id ASC
                    """,
                    {"opportunity_id": opportunity_id},
                )
                evidence = [map_evidence_row(dict(row)) for row in cur.fetchall()]

                cur.execute(
                    """
                    SELECT *
                    FROM api.v_commercial_opportunity_conflict
                    WHERE opportunity_id = %(opportunity_id)s
                    ORDER BY conflict_id ASC
                    """,
                    {"opportunity_id": opportunity_id},
                )
                conflicts = [map_conflict_row(dict(row)) for row in cur.fetchall()]

        return CommercialOpportunityDetailResponse(
            meta=CommercialOpportunityDetailMeta(
                data_source="postgres_mirror",
                read_only=True,
            ),
            opportunity=map_opportunity_row(dict(parent)),
            events=events,
            evidence=evidence,
            conflicts=conflicts,
        )

"""«Licitaciones» reads (spec 2026-10-19 §6.10): the list, one tender's page, and Hoy's closing signal.

A tender is a `crm.opportunity` of `kind = 'tender'` (`v2/case_kind.py` keeps it out of every general
read; these are the tender-only counterpart). Its notice is the origin evidence
(`crm.opportunity_evidence`, relation `origin`), which gives the code, the title and the closing time.

* **The list** carries no bidder data, so any signed-in operator reads it.
* **The page** carries the anexos, the machine's readings and fillings, the offer and the bidder's
  readiness, so a `viewer` is refused by the route (403 `role_may_not_view_bidder`, spec §6.3).
* **`tenders_closing`** is Hoy's only tender signal: tenders still `preparando` or `postulada` whose
  notice closes tomorrow, Chilean calendar.

Amounts are the offer quote's neto (its current revision), else the notice's `MontoEstimado`.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from origenlab_api.v2.model_spend import spend_summary
from origenlab_api.v2.tenders.bidder import current_bidder_profile
from origenlab_api.v2.tenders.codes import portal_url

SANTIAGO = ZoneInfo("America/Santiago")
TENDER_STATUSES: tuple[str, ...] = ("preparando", "postulada", "adjudicada", "perdida")
OPEN_TENDER_STATUSES: tuple[str, ...] = ("preparando", "postulada")
PHASES: tuple[str, ...] = ("read", "fill")

# One row per tender case. The notice is the case's origin evidence; the offer is the newest quote
# on the case, at its current revision (not superseded, not void).
_TENDER_ROWS = """
select o.id::text as id, o.version as version, o.tender_status as status, o.title as case_title,
       n.id::text as notice_id, n.codigo_externo as code, n.title as notice_title, n.closes_at,
       n.head as head, n.source_status as source_status,
       org.id::text as institution_id, org.name as institution_name,
       org.confirmation as institution_confirmation,
       q.id::text as quote_id, q.quote_number as offer_quote_number,
       r.id::text as revision_id, r.status as revision_status, r.quote_currency as revision_currency,
       r.tax_base as revision_tax_base, r.tax_total as revision_tax_total,
       r.grand_total as revision_grand_total,
       (select sum(l.line_total_qc) from crm.quote_line l where l.quote_revision_id = r.id) as lines_total
  from crm.opportunity o
  left join lateral (
        select n2.* from crm.opportunity_evidence e
          join procurement.notice n2 on n2.id = e.notice_id
         where e.opportunity_id = o.id and e.relation = 'origin' and e.unlinked_at is null
         order by e.linked_at desc limit 1) n on true
  left join crm.organization org on org.id = o.organization_id
  left join lateral (
        select q2.* from crm.quote q2 where q2.opportunity_id = o.id
         order by q2.created_at desc, q2.id desc limit 1) q on true
  left join lateral (
        select r2.* from crm.quote_revision r2
         where r2.quote_id = q.id and r2.superseded_by_revision_no is null and r2.status <> 'void'
         order by r2.revision_no desc limit 1) r on true
 where o.kind = 'tender'
"""

_ORDER = """
 order by (o.tender_status in ('preparando', 'postulada')) desc, n.closes_at asc nulls last, o.id
"""


def _row_dicts(cur: Any) -> list[dict[str, Any]]:
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def _number(value: Any) -> int | float | None:
    """A Decimal as a JSON number: an integer when it is one (CLP), else a float."""
    if value is None:
        return None
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    return int(dec) if dec == dec.to_integral_value() else float(dec)


def _estimated(head: Any) -> tuple[int | float | None, str]:
    """`head.MontoEstimado` and its currency; unreadable or non-positive is no estimate."""
    head = head if isinstance(head, dict) else {}
    raw = head.get("MontoEstimado")
    try:
        value = Decimal(str(raw)) if raw is not None and not isinstance(raw, bool) else None
    except Exception:  # noqa: BLE001 - any unreadable figure is simply no estimate
        value = None
    if value is None or not value.is_finite() or value <= 0:
        return None, str(head.get("Moneda") or "CLP")
    return _number(value), str(head.get("Moneda") or "CLP")


def _offer_neto(row: dict[str, Any]) -> Decimal | None:
    """The offer revision's neto: the frozen `tax_base`, else — a draft — the sum of its lines."""
    if row["revision_id"] is None:
        return None
    if row["revision_tax_base"] is not None:
        return row["revision_tax_base"]
    return row["lines_total"]


def _amount(row: dict[str, Any]) -> dict[str, Any] | None:
    neto = _offer_neto(row)
    if neto is not None:
        return {"neto": _number(neto), "currency": row["revision_currency"], "source": "oferta"}
    estimate, currency = _estimated(row["head"])
    if estimate is not None:
        return {"neto": estimate, "currency": currency, "source": "estimado"}
    return None


def _instant(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def tender_row(row: dict[str, Any], preparation: dict[str, str | None]) -> dict[str, Any]:
    """`TenderRow` (spec §6.10) from one `_TENDER_ROWS` row and the latest status of each phase."""
    return {
        "id": row["id"],
        "version": int(row["version"]),
        "code": row["code"],
        "title": row["notice_title"] or row["case_title"],
        "institution": {
            "id": row["institution_id"],
            "name": row["institution_name"],
            "confirmation": row["institution_confirmation"],
        },
        "closes_at": _instant(row["closes_at"]),
        "status": row["status"],
        "amount": _amount(row),
        "offer_quote_number": row["offer_quote_number"] if row["revision_id"] is not None else None,
        "preparation": {"read": preparation.get("read"), "fill": preparation.get("fill")},
    }


def head_summary(row: dict[str, Any]) -> dict[str, Any]:
    """What the notice head says that the page shows: description, estimate, currency, status."""
    head = row["head"] if isinstance(row["head"], dict) else {}
    estimate, currency = _estimated(head)
    return {
        "description": head.get("Descripcion"),
        "estimated_amount": estimate,
        "currency": currency,
        "source_status": row["source_status"] or head.get("Estado"),
    }


def tomorrow_in_santiago(now: datetime) -> date:
    return (now.astimezone(SANTIAGO) + timedelta(days=1)).date()


def tenders_closing(cur: Any, now: datetime) -> list[dict[str, Any]]:
    """Hoy's only tender signal: `preparando` or `postulada` tenders whose notice closes tomorrow."""
    tomorrow = tomorrow_in_santiago(now)
    cur.execute(TENDERS_CLOSING_SQL, {"tomorrow": tomorrow})
    return closing_rows(_row_dicts(cur))


TENDERS_CLOSING_SQL = """
select o.id::text as id, n.codigo_externo as code, coalesce(n.title, o.title) as title, n.closes_at
  from crm.opportunity o
  join lateral (
        select n2.* from crm.opportunity_evidence e
          join procurement.notice n2 on n2.id = e.notice_id
         where e.opportunity_id = o.id and e.relation = 'origin' and e.unlinked_at is null
         order by e.linked_at desc limit 1) n on true
 where o.kind = 'tender' and o.tender_status in ('preparando', 'postulada')
   and (n.closes_at at time zone 'America/Santiago')::date = %(tomorrow)s
 order by n.closes_at, o.id
"""


def closing_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"id": r["id"], "code": r["code"], "title": r["title"], "closes_at": _instant(r["closes_at"])}
            for r in rows]


class TenderReadRepository:
    """Read-only queries behind `/v2/tenders`. Same `(connect, dsn)` wiring as the other readers."""

    def __init__(self, connect: Any, dsn: str, statement_timeout_ms: int = 30_000) -> None:
        self._connect = connect
        self._dsn = dsn
        self._statement_timeout_ms = statement_timeout_ms

    @contextmanager
    def _read(self) -> Iterator[Any]:
        with self._connect(self._dsn, autocommit=False) as conn:
            with conn.cursor() as cur:
                cur.execute("set transaction read only")
                cur.execute(f"set local statement_timeout = {int(self._statement_timeout_ms)}")
                try:
                    yield cur
                finally:
                    conn.rollback()

    def _latest_preparations(self, cur: Any, ids: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
        """The newest `evidence.bid_preparation` row per case and phase."""
        if not ids:
            return {}
        cur.execute(
            """
            select distinct on (opportunity_id, phase)
                   opportunity_id::text as opportunity_id, phase, id::text as id, status,
                   requested_at, started_at, finished_at, failure_code, annex_document_ids,
                   based_on_preparation_id::text as based_on_preparation_id,
                   quote_revision_id::text as quote_revision_id, result, outputs
              from evidence.bid_preparation
             where opportunity_id = any(%s::uuid[])
             order by opportunity_id, phase, requested_at desc, id desc
            """,
            (ids,),
        )
        found: dict[str, dict[str, dict[str, Any]]] = {}
        for r in _row_dicts(cur):
            found.setdefault(r["opportunity_id"], {})[r["phase"]] = r
        return found

    def list_tenders(self, status: str | None = None) -> dict[str, Any]:
        with self._read() as cur:
            cur.execute(_TENDER_ROWS + _ORDER)
            rows = _row_dicts(cur)
            prep = self._latest_preparations(cur, [r["id"] for r in rows])
        counts = {s: 0 for s in TENDER_STATUSES}
        for r in rows:
            counts[r["status"]] += 1
        shown = [r for r in rows if status is None or r["status"] == status]
        tenders = [
            tender_row(r, {p: (prep.get(r["id"], {}).get(p) or {}).get("status") for p in PHASES}) for r in shown
        ]
        return {"tenders": tenders, "counts": counts}

    def page(self, tender_id: str, now: datetime) -> dict[str, Any] | None:
        """One tender's page, or None when the id is not a tender."""
        with self._read() as cur:
            cur.execute(_TENDER_ROWS + " and o.id = %s::uuid", (tender_id,))
            rows = _row_dicts(cur)
            if not rows:
                return None
            row = rows[0]
            prep = self._latest_preparations(cur, [row["id"]]).get(row["id"], {})
            cur.execute(
                """
                select id::text as id, original_filename as filename, content_type, byte_size, sha256,
                       source, attached_at
                  from crm.opportunity_document
                 where opportunity_id = %s::uuid and kind = 'tender_annex' and removed_at is null
                 order by attached_at, id
                """,
                (tender_id,),
            )
            annexes = [{**a, "attached_at": _instant(a["attached_at"])} for a in _row_dicts(cur)]
            spend = spend_summary(cur, "licitaciones", now)
            bidder_ready = current_bidder_profile(cur) is not None
        tender = {
            **tender_row(row, {p: (prep.get(p) or {}).get("status") for p in PHASES}),
            "portal_url": portal_url(row["code"]) if row["code"] else None,
            "notice_head_summary": head_summary(row),
        }
        offer = None
        if row["revision_id"] is not None:
            neto = _offer_neto(row)
            offer = {
                "quote_id": row["quote_id"],
                "revision_id": row["revision_id"],
                "number": row["offer_quote_number"],
                "status": row["revision_status"],
                "neto": _number(neto),
                "iva": _number(row["revision_tax_total"]),
                "total": _number(row["revision_grand_total"]),
            }
        return {
            "tender": tender,
            "annexes": annexes,
            "preparations": {p: _preparation(prep.get(p)) for p in PHASES},
            "offer_quote": offer,
            "spend": spend,
            "bidder_ready": bidder_ready,
        }


def _preparation(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "phase": row["phase"],
        "status": row["status"],
        "requested_at": _instant(row["requested_at"]),
        "started_at": _instant(row["started_at"]),
        "finished_at": _instant(row["finished_at"]),
        "failure_code": row["failure_code"],
        "annex_document_ids": [str(a) for a in row["annex_document_ids"]],
        "based_on_preparation_id": row["based_on_preparation_id"],
        "quote_revision_id": row["quote_revision_id"],
        "result": row["result"],
        "outputs": row["outputs"],
    }

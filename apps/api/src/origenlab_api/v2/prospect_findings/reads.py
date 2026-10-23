"""Read-only queries behind `GET /v2/prospect-findings`. Same `(connect, dsn)` wiring as the other readers.

Phase 1 classifies every unresolved finding per request (a few hundred at most); the list, its views
and its counts are computed from that one pass."""

from __future__ import annotations

import base64
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from origenlab_api.v2.prospect_findings.classify import classify

SANTIAGO = ZoneInfo("America/Santiago")
View = Literal["nuevos", "conocidos", "mas_tarde", "contactados", "descartados"]
VIEWS: tuple[str, ...] = ("nuevos", "conocidos", "mas_tarde", "contactados", "descartados")
PAGE = 25

_FINDINGS = """
select s.id::text as id, s.payload, s.acquired_at,
       r.verdict, r.reason, r.remind_on, r.reviewed_at
  from evidence.source_record s
  left join lateral (
        select x.verdict, x.reason, x.remind_on, x.reviewed_at
          from crm.prospect_review x
         where x.source_record_id = s.id
         order by x.reviewed_at desc, x.id desc limit 1) r on true
 where s.kind = 'prospect_finding' and not s.is_quarantined and s.superseded_by_source_record_id is null
"""
_HISTORY = """
select x.verdict, x.reason, x.remind_on, x.reviewed_at, op.display_name as reviewed_by
  from crm.prospect_review x left join platform.operator op on op.id = x.reviewed_by_operator_id
 where x.source_record_id = %s order by x.reviewed_at, x.id
"""
_LINKS = ("website", "linkedin_person", "linkedin_org", "orcid", "scholar")


def _day(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _instant(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _item(row: dict[str, Any], c: dict[str, Any], today: date) -> dict[str, Any]:
    p = row["payload"] or {}
    verdict = row["verdict"]
    return {
        "id": row["id"], "person_name": p.get("person_name"), "role": p.get("role"),
        "organization_name": p.get("organization_name"), "unit": p.get("unit"), "source": p.get("source"),
        "fund_label": p.get("fund_label"), "amount_label": p.get("amount_label"), "awarded_on": p.get("awarded_on"),
        "why_now": p.get("why_now"), "equipment": list(p.get("equipment") or []), "email": p.get("email"),
        "email_source_url": p.get("email_source_url"), "email_source_year": p.get("email_source_year"),
        "phone": p.get("phone"), "links": {k: p.get(k) for k in _LINKS}, "has_logo": bool(p.get("logo")),
        "confidence": p.get("confidence"), "score": int(p.get("score") or 0), "state": c["state"],
        "reasons": c["reasons"], "organization_candidates": c["organization_candidates"],
        "latest_verdict": None if verdict is None else {
            "verdict": verdict, "reason": row["reason"], "remind_on": _day(row["remind_on"]),
            "reviewed_at": _instant(row["reviewed_at"])},
        "returns_today": verdict == "mas_tarde" and row["remind_on"] is not None and row["remind_on"] <= today,
        "acquired_at": _instant(row["acquired_at"]),
    }


def view_of(item: dict[str, Any]) -> str | None:
    """Which list a classified finding belongs to; `None` for a blocked one (never listed)."""
    if item["state"] == "blocked":
        return None
    verdict = (item["latest_verdict"] or {}).get("verdict")
    if verdict == "contactar":
        return "contactados"
    if verdict == "descartar":
        return "descartados"
    if verdict == "mas_tarde" and not item["returns_today"]:
        return "mas_tarde"
    return "conocidos" if item["state"] == "known" else "nuevos"


def _order(item: dict[str, Any]) -> tuple[Any, ...]:
    return (not item["returns_today"], -item["score"], -(datetime.fromisoformat(item["acquired_at"]).timestamp()))


class ProspectFindingReadRepository:
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

    @staticmethod
    def _rows(cur: Any, sql: str, params: Any = None) -> list[dict[str, Any]]:
        cur.execute(sql, params)
        names = [d.name for d in cur.description]
        return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]

    def _classified(self, cur: Any, extra_sql: str = "", params: Any = None) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        today = datetime.now(SANTIAGO).date()
        out = []
        for row in self._rows(cur, _FINDINGS + extra_sql, params):
            p = row["payload"] or {}
            c = classify(cur, email=p.get("email"), organization_name=p.get("organization_name") or "",
                         website=p.get("website"))
            out.append((row, _item(row, c, today)))
        return out

    def list(self, view: str, *, limit: int = PAGE, offset: int = 0, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        month_start = now.astimezone(SANTIAGO).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        with self._read() as cur:
            items = [item for _, item in self._classified(cur)]
        by_view: dict[str | None, list[dict[str, Any]]] = {}
        for item in items:
            by_view.setdefault(view_of(item), []).append(item)
        nuevos = by_view.get("nuevos", [])
        counts = {
            "nuevos": sum(1 for i in nuevos if datetime.fromisoformat(i["acquired_at"]) > now - timedelta(days=1)),
            "por_revisar": len(nuevos),
            "contactados_mes": sum(1 for i in by_view.get("contactados", [])
                                   if datetime.fromisoformat(i["latest_verdict"]["reviewed_at"]) >= month_start),
            "vuelven_hoy": sum(1 for i in nuevos + by_view.get("conocidos", []) if i["returns_today"]),
            "omitidos_bajas": len(by_view.get(None, [])),
        }
        shown = sorted(by_view.get(view, []), key=_order)
        page = shown[offset:offset + limit]
        return {"counts": counts, "items": page, "has_more": offset + limit < len(shown)}

    def one(self, finding_id: str) -> dict[str, Any] | None:
        with self._read() as cur:
            found = self._classified(cur, " and s.id = %s", (finding_id,))
            if not found or found[0][1]["state"] == "blocked":
                return None
            row, item = found[0]
            history = self._rows(cur, _HISTORY, (finding_id,))
        p = row["payload"] or {}
        return {**item, "project_title": p.get("project_title"), "sources": p.get("sources") or [],
                "history": [{**h, "remind_on": _day(h["remind_on"]), "reviewed_at": _instant(h["reviewed_at"])}
                            for h in history]}

    def logo(self, finding_id: str) -> tuple[str, bytes] | None:
        with self._read() as cur:
            cur.execute("select payload->'logo' from evidence.source_record where id = %s and kind = 'prospect_finding'",
                        (finding_id,))
            row = cur.fetchone()
        logo = row[0] if row else None
        if not logo or logo.get("content_type") not in {"image/png", "image/jpeg", "image/webp", "image/x-icon"}:
            return None
        return logo["content_type"], base64.b64decode(logo["b64"])

"""Read-only queries behind `GET /v2/prospect-findings`. Same `(connect, dsn)` wiring as the other readers.

Phase 1 classifies every unresolved finding per request (a few hundred at most); the list, its views
and its counts are computed from that one pass."""

from __future__ import annotations

import base64
import re
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from origenlab_api.v2.prospect_findings.classify import classify_many
from origenlab_api.v2.prospect_findings.commands import apply_edits

SANTIAGO = ZoneInfo("America/Santiago")
View = Literal["nuevos", "conocidos", "mas_tarde", "contactados", "descartados"]
VIEWS: tuple[str, ...] = ("nuevos", "conocidos", "mas_tarde", "contactados", "descartados")
PAGE = 25

_FINDINGS = """
select s.id::text as id, s.payload - 'logo' as payload, (s.payload ? 'logo' and s.payload->'logo' <> 'null'::jsonb) as has_logo,
       s.acquired_at,
       r.verdict, r.reason, r.remind_on, r.reviewed_at,
       e.edits, e.edited_at, e.edited_by
  from evidence.source_record s
  left join lateral (
        select x.verdict, x.reason, x.remind_on, x.reviewed_at
          from crm.prospect_review x
         where x.source_record_id = s.id
         order by x.reviewed_at desc, x.id desc limit 1) r on true
  -- A person's corrections (`crm.prospect_edit`), oldest first; `apply_edits` reads them over the payload.
  left join lateral (
        select jsonb_agg(x.changes order by x.edited_at, x.id) as edits, max(x.edited_at) as edited_at,
               (array_agg(op.display_name order by x.edited_at desc, x.id desc))[1] as edited_by
          from crm.prospect_edit x left join platform.operator op on op.id = x.edited_by_operator_id
         where x.source_record_id = s.id) e on true
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
        "email_source_note": p.get("email_source_note"),
        "phone": p.get("phone"), "links": {k: p.get(k) for k in _LINKS}, "has_logo": bool(row.get("has_logo")),
        "confidence": p.get("confidence"), "score": int(p.get("score") or 0), "state": c["state"],
        "reasons": c["reasons"], "organization_candidates": c["organization_candidates"],
        "latest_verdict": None if verdict is None else {
            "verdict": verdict, "reason": row["reason"], "remind_on": _day(row["remind_on"]),
            "reviewed_at": _instant(row["reviewed_at"])},
        "returns_today": verdict == "mas_tarde" and row["remind_on"] is not None and row["remind_on"] <= today,
        "acquired_at": _instant(row["acquired_at"]),
        # Who last corrected the card, and when (`crm.prospect_edit`); null when nobody has.
        "edited": None if row.get("edited_at") is None else {
            "at": _instant(row["edited_at"]), "by": row.get("edited_by")},
    }


def view_of(item: dict[str, Any]) -> str | None:
    """Which list a classified finding belongs to; `None` for a blocked one (never listed)."""
    if item["state"] == "blocked" or item.get("folded_into"):
        return None
    if item.get("discarded_elsewhere"):
        return "descartados"
    verdict = (item["latest_verdict"] or {}).get("verdict")
    if verdict == "contactar":
        return "contactados"
    if verdict == "descartar":
        return "descartados"
    if verdict == "mas_tarde" and not item["returns_today"]:
        return "mas_tarde"
    return "conocidos" if item["state"] == "known" else "nuevos"


def _identity(item: dict[str, Any]) -> str:
    """The same person across sources: the email, else the person's and institution's folded names."""
    if item["email"]:
        return "email:" + item["email"].strip().lower()
    fold = lambda t: re.sub(r"\s+", " ", unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode()).strip().lower()  # noqa: E731
    return f"name:{fold(item['person_name'] or '')}|{fold(item['organization_name'])}"


def merge_same_person(items: list[dict[str, Any]]) -> None:
    """Spec §6.3: one person found by two sources is one card. A discard of any of them discards the
    rest; among the ones still to review only the best is listed, the others are folded into it."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if item["state"] != "blocked":
            groups.setdefault(_identity(item), []).append(item)
    for group in groups.values():
        if len(group) < 2:
            continue
        verdicts = {(i["latest_verdict"] or {}).get("verdict") for i in group}
        open_ones = [i for i in group if view_of(i) in {"nuevos", "conocidos"}]
        if "descartar" in verdicts:
            for i in open_ones:
                i["discarded_elsewhere"] = True
            continue
        if len(open_ones) > 1:
            best = sorted(open_ones, key=_order)[0]
            others = [i for i in open_ones if i is not best]
            for i in others:
                i["folded_into"] = best["id"]
            best["reasons"] = [*best["reasons"], f"También en {len(others)} fuente{'s' if len(others) > 1 else ''} más"]


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
        rows = self._rows(cur, _FINDINGS + extra_sql, params)
        for r in rows:
            r["payload"] = apply_edits(r["payload"] or {}, r.get("edits"))
        found = classify_many(cur, [{
            "email": (r["payload"] or {}).get("email"),
            "organization_name": (r["payload"] or {}).get("organization_name") or "",
            "website": (r["payload"] or {}).get("website"),
            "block_only": r["verdict"] in {"contactar", "descartar"},
        } for r in rows])
        return [(row, _item(row, c, today)) for row, c in zip(rows, found, strict=True)]

    def list(self, view: str, *, limit: int = PAGE, offset: int = 0, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        month_start = now.astimezone(SANTIAGO).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        with self._read() as cur:
            items = [item for _, item in self._classified(cur)]
        merge_same_person(items)
        by_view: dict[str | None, list[dict[str, Any]]] = {}
        for item in items:
            by_view.setdefault(view_of(item), []).append(item)
        by_view.pop(None, None)
        blocked = sum(1 for i in items if i["state"] == "blocked")
        nuevos = by_view.get("nuevos", [])
        counts = {
            "nuevos": sum(1 for i in nuevos if datetime.fromisoformat(i["acquired_at"]) > now - timedelta(days=1)),
            "por_revisar": len(nuevos),
            "contactados_mes": sum(1 for i in by_view.get("contactados", [])
                                   if datetime.fromisoformat(i["latest_verdict"]["reviewed_at"]) >= month_start),
            "vuelven_hoy": sum(1 for i in nuevos + by_view.get("conocidos", []) if i["returns_today"]),
            "omitidos_bajas": blocked,
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

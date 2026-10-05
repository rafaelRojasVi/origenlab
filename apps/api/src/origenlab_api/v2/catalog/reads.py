"""Catalog read model: product search and detail, supplier terms, cost parameters, stored FX.

Plain SQL over the durable `catalog.*` tables as the runtime role. Money is `Decimal` in the
database and a string in every payload. Cost redaction for the viewer role is the route's job
(`redaction.redact_costs`); nothing here knows about roles.
"""
from __future__ import annotations

import datetime as dt
import statistics
import uuid
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

from origenlab_api.v2.catalog.keys import model_key

_PRODUCT_COLUMNS_SKIPPED = {"search_tsv"}

_SEARCH_WHERE = """ where p.active
   and (%(q)s::text is null
        or p.model_key like '%%' || %(qkey)s || '%%'
        or p.search_tsv @@ websearch_to_tsquery('spanish', %(q)s))
   and (%(supplier)s::uuid is null or exists (select 1 from catalog.supplier_product x
                                               where x.product_id = p.id and x.supplier_organization_id = %(supplier)s))
   and (%(kind)s::text is null or p.product_kind = %(kind)s)
"""

_SEARCH_SQL_TEMPLATE = """
select p.id, p.model_number, p.model_key, p.name, p.name_es, p.category_es, p.product_kind, p.content_origin,
       (p.content_confirmed_at is not null) as confirmed,
       m.id as manufacturer_id, m.name as manufacturer_name,
       (select i.id from catalog.product_image i where i.product_id = p.id and i.status <> 'hidden'
         order by i.sort_order, i.created_at limit 1) as primary_image_id,
       c.id as cost_id, c.price, c.currency, c.price_kind, c.as_of, c.is_stale,
       s.id as supplier_id, s.name as supplier_name
  from catalog.product p
  join crm.organization m on m.id = p.manufacturer_organization_id
  left join lateral (
       select sp.* from catalog.supplier_product sp
        where sp.product_id = p.id
        order by sp.is_stale asc, sp.as_of desc,
                 (sp.price_kind in ('supplier_offer','order_confirmation','purchase_order','costing_sheet','negotiated')) desc,
                 sp.id desc
        limit 1) c on true
  left join crm.organization s on s.id = c.supplier_organization_id
 {where}
 order by (p.model_key = %(qkey)s) desc, p.model_key, p.id
 limit %(limit)s offset %(offset)s
"""


_SEARCH_SQL = _SEARCH_SQL_TEMPLATE.replace(" {where}", _SEARCH_WHERE.rstrip("\n"))
_COUNT_SQL = (
    "select count(*) from catalog.product p " + _SEARCH_WHERE
)


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


_MEDIAN_STATUSES = ("verified", "reviewed", "single_source")
_MONEY_STEP = Decimal("0.0001")


def _like_escape(key: str) -> str:
    return key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class V2CatalogReads:
    def __init__(self, connect: Any, dsn: str, statement_timeout_ms: int = 30_000) -> None:
        self._connect = connect
        self._dsn = dsn
        self._statement_timeout_ms = statement_timeout_ms

    @contextmanager
    def _read(self):  # type: ignore[no-untyped-def]
        with self._connect(self._dsn, autocommit=False) as conn:
            with conn.cursor() as cur:
                cur.execute("set transaction read only")
                cur.execute(f"set local statement_timeout = {int(self._statement_timeout_ms)}")
                try:
                    yield cur
                finally:
                    conn.rollback()

    @staticmethod
    def _rows(cur: Any) -> list[dict[str, Any]]:
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    # -- products

    def search_products(self, q: str | None, supplier_id: uuid.UUID | None, kind: str | None,
                        limit: int, offset: int) -> dict[str, Any]:
        text = q.strip() if q and q.strip() else None
        qkey = model_key(text) if text else None
        params = {"q": text, "qkey": _like_escape(qkey) if qkey else None, "supplier": supplier_id,
                  "kind": kind, "limit": limit, "offset": offset}
        with self._read() as cur:
            cur.execute(_SEARCH_SQL, params)
            rows = self._rows(cur)
            cur.execute(_COUNT_SQL, {k: v for k, v in params.items() if k not in ("limit", "offset")})
            total = cur.fetchone()[0]
        items = []
        for r in rows:
            cost = None
            if r["cost_id"] is not None:
                cost = {"price": r["price"], "currency": r["currency"], "price_kind": r["price_kind"],
                        "as_of": r["as_of"], "is_stale": r["is_stale"],
                        "supplier": {"id": r["supplier_id"], "display_name": r["supplier_name"]}}
            items.append(_plain({
                "id": r["id"], "model_number": r["model_number"], "model_key": r["model_key"],
                "name": r["name"], "name_es": r["name_es"], "category_es": r["category_es"],
                "product_kind": r["product_kind"],
                "manufacturer": {"id": r["manufacturer_id"], "display_name": r["manufacturer_name"]},
                "content_origin": r["content_origin"], "confirmed": r["confirmed"],
                "primary_image_id": r["primary_image_id"], "current_cost": cost,
            }))
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def product_detail(self, product_id: uuid.UUID, include_hidden: bool = False) -> dict[str, Any] | None:
        """The product with its images, costs, terms and notes.

        Hidden images are left out unless `include_hidden` (the route asks for them for the
        roles that may un-hide one); every image carries its `status` either way.
        """
        with self._read() as cur:
            cur.execute(
                "select p.*, m.name as manufacturer_name from catalog.product p "
                "join crm.organization m on m.id = p.manufacturer_organization_id where p.id = %s",
                (product_id,),
            )
            found = self._rows(cur)
            if not found:
                return None
            product = {k: v for k, v in found[0].items() if k not in _PRODUCT_COLUMNS_SKIPPED}
            manufacturer_name = product.pop("manufacturer_name")
            product["manufacturer"] = {"id": product["manufacturer_organization_id"],
                                       "display_name": manufacturer_name}
            cur.execute(
                "select id, storage_path, sha256, content_type, width_px, height_px, sort_order, caption_es, "
                "source, source_url, status, version, created_at from catalog.product_image "
                "where product_id = %s and (%s or status <> 'hidden') order by sort_order, created_at, id",
                (product_id, include_hidden),
            )
            product["images"] = self._rows(cur)
            cur.execute(
                "select sp.id, sp.as_of, sp.price, sp.currency, sp.price_kind, sp.list_price, sp.discount_pct, "
                "sp.incoterm, sp.valid_until, sp.min_qty, sp.is_stale, sp.source_document, sp.provenance_note, "
                "sp.created_at, o.id as supplier_id, o.name as supplier_name "
                "from catalog.supplier_product sp join crm.organization o on o.id = sp.supplier_organization_id "
                "where sp.product_id = %s order by sp.as_of desc, sp.created_at desc, sp.id desc",
                (product_id,),
            )
            history = []
            for r in self._rows(cur):
                r["supplier"] = {"id": r.pop("supplier_id"), "display_name": r.pop("supplier_name")}
                history.append(r)
            product["cost_history"] = history
            supplier_ids = list(dict.fromkeys(h["supplier"]["id"] for h in history))
            terms: list[dict[str, Any]] = []
            if supplier_ids:
                cur.execute(
                    "select t.*, o.name as supplier_name from catalog.supplier_terms t "
                    "join crm.organization o on o.id = t.supplier_organization_id "
                    "where t.supplier_organization_id = any(%s) order by o.name, t.supplier_organization_id",
                    (supplier_ids,),
                )
                terms = [self._terms_row(r) for r in self._rows(cur)]
            product["supplier_terms"] = terms
            cur.execute(
                "select id, body, author_operator_id, revision_no, created_at from crm.note "
                "where subject_kind = 'product' and subject_id = %s and status <> 'archived' "
                "order by created_at desc, id desc",
                (product_id,),
            )
            product["notes"] = self._rows(cur)
            product["price_history"] = self._price_history(cur, product["model_key"], 20)
            product["kit_suggestions"] = self._kit_suggestions(cur, product["model_key"], Decimal("0.30"))
        return _plain(product)

    # -- quoted-price history and kits (evidence.document_line; 1b adds authored quote lines)

    def price_history(self, model_key_: str, limit: int = 20) -> dict[str, Any]:
        """What quote documents charged for a model, newest first, with the median of the last five.

        Optional lines are left out. Disputed lines are listed but never enter the median, which
        takes only single-unit lines of a trustworthy status, in the currency of the newest one.
        """
        with self._read() as cur:
            return _plain(self._price_history(cur, model_key_, limit))

    def kit_suggestions(self, model_key_: str, min_share: Decimal = Decimal("0.30")) -> list[dict[str, Any]]:
        """Other models that share quote documents with this one, by share of those documents."""
        with self._read() as cur:
            return _plain(self._kit_suggestions(cur, model_key_, min_share))

    def _price_history(self, cur: Any, key: str | None, limit: int) -> dict[str, Any]:
        key = model_key(key)
        if key is None:
            return {"model_key": None, "items": [], "median_last_5": None}
        base = (
            "select s.payload->>'quote_date' as date, l.line_total, l.qty, l.unit_price, l.currency, "
            "l.check_status, s.payload->>'client_type' as client_type, "
            "s.payload->>'printed_quote_number' as document_number "
            "from evidence.document_line l join evidence.source_record s on s.id = l.source_record_id "
            "where l.model_key = %s and s.kind = 'quote_document' and not l.optional "
        )
        order = "order by s.payload->>'quote_date' desc nulls last, l.line_no desc, l.id desc "
        cur.execute(base + order + "limit %s", (key, limit))
        items = [{**r, "source": "documento"} for r in self._rows(cur)]
        cur.execute(
            base + "and l.line_total is not null and (l.qty is null or l.qty = 1) and l.check_status = any(%s) "
            + order + "limit 5", (key, list(_MEDIAN_STATUSES)),
        )
        recent = self._rows(cur)
        median = None
        if recent:
            totals = [r["line_total"] for r in recent if r["currency"] == recent[0]["currency"]]
            median = str(Decimal(statistics.median(totals)).quantize(_MONEY_STEP))
        return {"model_key": key, "items": items, "median_last_5": median}

    def _kit_suggestions(self, cur: Any, key: str | None, min_share: Decimal) -> list[dict[str, Any]]:
        key = model_key(key)
        if key is None:
            return []
        cur.execute(
            "with docs as (select distinct l.source_record_id from evidence.document_line l "
            "  join evidence.source_record s on s.id = l.source_record_id "
            "  where l.model_key = %s and s.kind = 'quote_document') "
            "select (select count(*) from docs) as total, l.model_key, min(l.brand) as brand, "
            "count(distinct l.source_record_id) as n "
            "from evidence.document_line l join docs d on d.source_record_id = l.source_record_id "
            "where l.model_key is not null and l.model_key <> %s group by l.model_key",
            (key, key),
        )
        out = []
        for r in self._rows(cur):
            share = Decimal(r["n"]) / Decimal(r["total"])
            if share >= min_share:
                out.append({"model_key": r["model_key"], "brand": r["brand"],
                            "share": share.quantize(_MONEY_STEP)})
        out.sort(key=lambda e: (-e["share"], e["model_key"]))
        return out[:10]

    def product_exists(self, product_id: uuid.UUID) -> bool:
        with self._read() as cur:
            cur.execute("select exists (select 1 from catalog.product where id = %s)", (product_id,))
            return bool(cur.fetchone()[0])

    def image(self, image_id: uuid.UUID) -> dict[str, Any] | None:
        """One image's object and status, hidden or not — whether to sign it is the route's call."""
        with self._read() as cur:
            cur.execute(
                "select id, product_id, storage_bucket, storage_path, content_type, status, version "
                "from catalog.product_image where id = %s",
                (image_id,),
            )
            rows = self._rows(cur)
        return _plain(rows[0]) if rows else None

    # -- suppliers

    @staticmethod
    def _terms_row(r: dict[str, Any]) -> dict[str, Any]:
        r["supplier"] = {"id": r["supplier_organization_id"], "display_name": r.pop("supplier_name")}
        return r

    def supplier_terms(self, organization_id: uuid.UUID) -> dict[str, Any] | None:
        with self._read() as cur:
            cur.execute(
                "select t.*, o.name as supplier_name from catalog.supplier_terms t "
                "join crm.organization o on o.id = t.supplier_organization_id "
                "where t.supplier_organization_id = %s",
                (organization_id,),
            )
            rows = self._rows(cur)
        return _plain(self._terms_row(rows[0])) if rows else None

    # -- parameters and FX

    def parameters(self) -> dict[str, Any]:
        with self._read() as cur:
            cur.execute(
                "select p.id, p.key, p.value_numeric, p.value_json, p.valid_from, p.reason, p.created_at, "
                "p.set_by_operator_id, op.display_name as set_by from catalog.cost_parameter p "
                "left join platform.operator op on op.id = p.set_by_operator_id "
                "order by p.valid_from desc, p.created_at desc, p.id desc"
            )
            rows = self._rows(cur)
        now = dt.datetime.now(dt.timezone.utc)
        history, current = [], {}
        for r in rows:
            # `id` is what set-cost-parameter takes back as `expected_current_id`.
            entry = {"id": r["id"], "key": r["key"],
                     "value": r["value_numeric"] if r["value_numeric"] is not None else r["value_json"],
                     "valid_from": r["valid_from"], "set_by": r["set_by"], "reason": r["reason"]}
            history.append(entry)
            if r["valid_from"] <= now and r["key"] not in current:
                current[r["key"]] = {k: v for k, v in entry.items() if k != "key"}
        return _plain({"current": current, "history": history})

    def stored_fx(self, currency: str, on: dt.date) -> dict[str, Any] | None:
        with self._read() as cur:
            cur.execute(
                "select id, rate_date, currency, clp_per_unit, source, provider, reason, created_at "
                "from catalog.fx_rate where currency = %(c)s and rate_date = ("
                "  select max(rate_date) from catalog.fx_rate where currency = %(c)s and rate_date <= %(on)s) "
                "order by (source = 'manual') desc, (provider = 'bde') desc, created_at desc, id desc limit 1",
                {"c": currency, "on": on},
            )
            rows = self._rows(cur)
        return _plain(rows[0]) if rows else None

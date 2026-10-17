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

from origenlab_api.v2.catalog import aliases
from origenlab_api.v2.catalog.keys import model_key

_PRODUCT_COLUMNS_SKIPPED = {"search_tsv"}

# A model typed as printed finds the product through an active alias too (DOMAIN.md §7 #59).
_SEARCH_WHERE = """ where p.active
   and (%(q)s::text is null
        or p.model_key like '%%' || %(qkey)s || '%%'
        or exists (select 1 from catalog.product_model_alias a where a.product_id = p.id and a.retired_at is null
                    and a.alias_key like '%%' || %(qkey)s || '%%')
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
 order by (p.model_key = %(qkey)s or exists (select 1 from catalog.product_model_alias a where a.product_id = p.id
           and a.retired_at is null and a.alias_key = %(qkey)s)) desc, p.model_key, p.id
 limit %(limit)s offset %(offset)s
"""


_SEARCH_SQL = _SEARCH_SQL_TEMPLATE.replace(" {where}", _SEARCH_WHERE.rstrip("\n"))
_COUNT_SQL = (
    "select count(*) from catalog.product p " + _SEARCH_WHERE
)


#: The positional columns of `GET /v2/catalog/index` — no cost field, ever (Catálogo v2, spec D1).
INDEX_FIELDS = ("id", "model_number", "model_key", "name", "category_es", "kind", "manufacturer",
                "supplier_ids", "image_id", "has_datasheet", "name_en")
DRIVE_VIEW_URL = "https://drive.google.com/file/d/{}/view"

# Changes whenever a product, a cost observation, an image or a document is written: the
# dashboard keeps its index until this moves.
_VERSION_SQL = """
select md5(concat_ws('|',
  (select count(*) || ':' || coalesce(max(updated_at)::text, '') from catalog.product),
  (select count(*) || ':' || coalesce(max(created_at)::text, '') from catalog.supplier_product),
  (select count(*) || ':' || coalesce(max(updated_at)::text, '') from catalog.product_image),
  (select count(*) || ':' || coalesce(max(updated_at)::text, '') from catalog.product_document)))
"""

_INDEX_SQL = """
with sup as (
  -- Every product's suppliers in ONE pass: through a product merge (its twin's observations count)
  -- and through an organization merge (the kept organization). A per-product `or … in (subquery)`
  -- here defeated the index and timed out on 10,000 products (2026-10-14).
  select coalesce(pm.merged_into_product_id, sp.product_id) as pid,
         array_agg(distinct coalesce(so.merged_into_organization_id, so.id)::text) as ids
    from catalog.supplier_product sp
    join catalog.product pm on pm.id = sp.product_id
    join crm.organization so on so.id = sp.supplier_organization_id
   group by 1
)
select p.id::text, p.model_number, p.model_key, coalesce(p.name_es, p.name, p.model_number), p.category_es,
       p.product_kind, m.name, coalesce(sup.ids, '{}'),
       (select i.id::text from catalog.product_image i where i.product_id = p.id and i.status <> 'hidden'
         order by i.sort_order, i.created_at, i.id limit 1),
       exists (select 1 from catalog.product_document d where d.product_id = p.id
                and d.kind = 'datasheet' and d.status = 'active'),
       -- The supplier's (usually English) name, searched too; null when it adds nothing.
       case when p.name_es is not null and p.name is distinct from p.name_es then p.name end
  from catalog.product p
  join crm.organization m on m.id = p.manufacturer_organization_id
  left join sup on sup.pid = p.id
 where p.active
 order by p.model_key, p.id
"""


def _sales_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """«Ventas» on the product page: how often the model was quoted, at what prices, how often it sold.

    Prices are single-unit lines (no quantity, or one) in the currency of the newest such line;
    disputed readings are left out of the range. «Vendido» counts a line only when its quote is the
    one its case was won with (`crm.opportunity.won_quote_id`), never a quote a won case passed over.
    """
    documents = {r["document_id"] for r in rows}
    priced = [r for r in rows if r["line_total"] is not None and (r["qty"] is None or r["qty"] == 1)
              and r["check_status"] != "disputed"]
    currency = priced[0]["currency"] if priced else None
    same = [r for r in priced if r["currency"] == currency]
    won_docs = {r["document_id"] for r in rows if r.get("outcome") == "won"}
    totals = [r["line_total"] for r in same]
    last = same[0] if same else None
    return _plain({
        "times_quoted": len(documents),
        "times_won": len(won_docs),
        "currency": currency,
        "min": min(totals) if totals else None,
        "max": max(totals) if totals else None,
        "median": Decimal(statistics.median(totals)).quantize(_MONEY_STEP) if totals else None,
        "last": {"date": last["date"], "price": last["line_total"]} if last else None,
        "won_prices": [r["line_total"] for r in same if r.get("outcome") == "won"],
        "institutions": len({r["institution"] for r in rows if r.get("institution")}),
    })


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


# The supplier_product rows of a product and of the twins merged into it (two `%s`: the product id).
_OWN_PRODUCT_ROWS = ("(sp.product_id = %s or sp.product_id in "
                     "(select id from catalog.product where merged_into_product_id = %s))")


class V2CatalogReads:
    def __init__(self, connect: Any, dsn: str, statement_timeout_ms: int = 30_000) -> None:
        self._connect = connect
        self._dsn = dsn
        self._statement_timeout_ms = statement_timeout_ms

    @contextmanager
    def _read(self):  # type: ignore[no-untyped-def]
        with self._connect(self._dsn, autocommit=False) as conn:
            with conn.cursor() as cur:
                # One round trip for both (no parameters, so one simple-protocol message).
                cur.execute(f"set transaction read only; set local statement_timeout = {int(self._statement_timeout_ms)}")
                try:
                    yield cur
                finally:
                    conn.rollback()

    @staticmethod
    def _rows(cur: Any) -> list[dict[str, Any]]:
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def _batch(self, cur: Any, queries: dict[str, tuple[str, tuple[Any, ...]]]) -> dict[str, list[dict[str, Any]]]:
        """Independent queries sent together in one pipeline: one round trip to the database for
        all of them instead of one each (the API sits ~180 ms from it). Same transaction as `cur`."""
        conn = cur.connection
        cursors = {}
        with conn.pipeline():
            for name, (sql, params) in queries.items():
                cursors[name] = conn.cursor()
                cursors[name].execute(sql, params)
        try:
            return {name: self._rows(c) for name, c in cursors.items()}
        finally:
            for c in cursors.values():
                c.close()

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
        """The product with its images, costs, terms, notes, quoted prices, kits and documents.

        Hidden images are left out unless `include_hidden` (the route asks for them for the
        roles that may un-hide one); every image carries its `status` either way. Two round trips
        after the set-up: the product, then everything else in one pipeline.
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
            key = model_key(product["model_key"])
            rows = self._batch(cur, {
                "images": (
                    "select id, storage_path, sha256, content_type, width_px, height_px, sort_order, caption_es, "
                    "source, source_url, status, version, created_at from catalog.product_image "
                    "where product_id = %s and (%s or status <> 'hidden') order by sort_order, created_at, id",
                    (product_id, include_hidden)),
                "costs": (
                    "select sp.id, sp.as_of, sp.price, sp.currency, sp.price_kind, sp.list_price, sp.discount_pct, "
                    "sp.incoterm, sp.valid_until, sp.min_qty, sp.is_stale, sp.source_document, sp.provenance_note, "
                    "sp.created_at, o.id as supplier_id, o.name as supplier_name "
                    "from catalog.supplier_product sp join crm.organization raw on raw.id = sp.supplier_organization_id "
                    "join crm.organization o on o.id = coalesce(raw.merged_into_organization_id, raw.id) "
                    f"where {_OWN_PRODUCT_ROWS} order by sp.as_of desc, sp.created_at desc, sp.id desc",
                    (product_id, product_id)),
                # The terms of every supplier that lists it (through organization merges).
                "terms": (
                    "select t.*, o.name as supplier_name from catalog.supplier_terms t "
                    "join crm.organization raw on raw.id = t.supplier_organization_id "
                    "join crm.organization o on o.id = coalesce(raw.merged_into_organization_id, raw.id) "
                    "where o.id in (select coalesce(so.merged_into_organization_id, so.id) "
                    "  from catalog.supplier_product sp join crm.organization so on so.id = sp.supplier_organization_id "
                    f"  where {_OWN_PRODUCT_ROWS}) order by o.name, t.supplier_organization_id",
                    (product_id, product_id)),
                "notes": (
                    "select id, body, author_operator_id, revision_no, created_at from crm.note "
                    "where subject_kind = 'product' and subject_id = %s and status <> 'archived' "
                    "order by created_at desc, id desc",
                    (product_id,)),
                **self._price_history_queries(key, product_id),
                **self._kit_queries(key, product_id),
                **self._documents_queries(product_id),
            })
        product["images"] = rows["images"]
        history = []
        for r in rows["costs"]:
            r["supplier"] = {"id": r.pop("supplier_id"), "display_name": r.pop("supplier_name")}
            history.append(r)
        product["cost_history"] = history
        product["supplier_terms"] = [self._terms_row(r) for r in rows["terms"]]
        product["notes"] = rows["notes"]
        # As many quotes as the panel lists; `costs` stays — the route turns it into margins
        # (and the redaction drops it for a viewer).
        product["price_history"] = self._price_history_from(key, rows, 100)
        product["kit_suggestions"] = self._kit_from(key, rows, Decimal("0.30"))
        product["documents"] = self._documents_from(rows)
        return _plain(product)

    # -- Catálogo v2: the browser's search index, batch image paths, Drive documents

    def catalog_version(self) -> str:
        with self._read() as cur:
            cur.execute(_VERSION_SQL)
            return cur.fetchone()[0]

    def catalog_index(self, known_version: str | None = None) -> dict[str, Any]:
        """Every active product as a positional row (`INDEX_FIELDS`, no cost), or `unchanged`
        when the caller already holds `known_version`."""
        with self._read() as cur:
            cur.execute(_VERSION_SQL)
            version = cur.fetchone()[0]
            if known_version == version:
                return {"version": version, "unchanged": True}
            cur.execute(_INDEX_SQL)
            items = [[*r[:7], list(r[7]), *r[8:]] for r in cur.fetchall()]
        return {"version": version, "fields": list(INDEX_FIELDS), "suppliers": self.suppliers()["items"],
                "items": items}

    def images(self, image_ids: list[uuid.UUID], include_hidden: bool) -> dict[str, str]:
        """id → storage path for the ids that exist and, unless `include_hidden`, are not hidden."""
        with self._read() as cur:
            cur.execute("select id::text, storage_path from catalog.product_image "
                        "where id = any(%s) and (%s or status <> 'hidden')", (image_ids, include_hidden))
            return {r[0]: r[1] for r in cur.fetchall()}

    @staticmethod
    def _documents_queries(product_id: uuid.UUID) -> dict[str, tuple[str, tuple[Any, ...]]]:
        """The product's own data sheets, and the active price lists of the suppliers that list it."""
        return {
            "datasheets": ("select id, title, drive_file_id, as_of from catalog.product_document "
                           "where kind = 'datasheet' and status = 'active' and product_id = %s "
                           "order by as_of desc nulls last, title, id", (product_id,)),
            "price_lists": ("select d.id, d.title, d.drive_file_id, d.as_of, o.id as supplier_id, o.name as supplier_name "
                            "from catalog.product_document d join crm.organization raw on raw.id = d.supplier_organization_id "
                            "join crm.organization o on o.id = coalesce(raw.merged_into_organization_id, raw.id) "
                            "where d.kind = 'price_list' and d.status = 'active' and o.id in "
                            "(select coalesce(so.merged_into_organization_id, so.id) from catalog.supplier_product sp "
                            " join crm.organization so on so.id = sp.supplier_organization_id "
                            f" where {_OWN_PRODUCT_ROWS}) "
                            "order by o.name, d.as_of desc nulls last, d.id", (product_id, product_id)),
        }

    @staticmethod
    def _documents_from(rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        sheets = [{"id": r["id"], "title": r["title"], "url": DRIVE_VIEW_URL.format(r["drive_file_id"]),
                   "as_of": r["as_of"]} for r in rows["datasheets"]]
        lists = [{"id": r["id"], "title": r["title"], "url": DRIVE_VIEW_URL.format(r["drive_file_id"]),
                  "as_of": r["as_of"], "supplier": {"id": r["supplier_id"], "display_name": r["supplier_name"]}}
                 for r in rows["price_lists"]]
        return {"datasheets": sheets, "price_lists": lists}

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
        return self._price_history_from(key, self._batch(cur, self._price_history_queries(key)) if key else {}, limit)

    @staticmethod
    def _line_keys(product_id: uuid.UUID | None) -> str:
        """The model keys whose lines are about `%(key)s`: of the product when one is given, else of
        what the key names (its own model and its active aliases, unless ambiguous)."""
        if product_id is not None:
            return aliases.line_keys_of_product("%(product_id)s", "%(key)s")
        return aliases.line_keys_of_key("%(key)s")

    @classmethod
    def _price_history_queries(cls, key: str | None,
                               product_id: uuid.UUID | None = None) -> dict[str, tuple[str, Any]]:
        if key is None:
            return {}
        keys = cls._line_keys(product_id)
        params = {"key": key, "product_id": product_id}
        # Each quoted line with the CRM quote of the same printed number, its case, the case's
        # institution (through any merge) and whether that very quote is the one the case won with.
        quoted = (
            "select s.id::text as document_id, s.payload->>'quote_date' as date, l.line_total, l.qty, "
            "l.unit_price, l.currency, l.check_status, s.payload->>'client_type' as client_type, "
            "s.payload->>'printed_quote_number' as document_number, "
            "c.case_id, c.institution, c.outcome "
            "from evidence.document_line l join evidence.source_record s on s.id = l.source_record_id "
            "left join lateral ("
            "  select o.id::text as case_id, root.name as institution, "
            "         case when o.won_quote_id = q.id then 'won' when o.stage = 'won' then 'other_won' "
            "              when o.stage = 'lost' then 'lost' when o.stage = 'abandoned' then 'abandoned' "
            "              else 'open' end as outcome "
            "    from crm.quote q join crm.opportunity o on o.id = q.opportunity_id "
            "    left join crm.organization org on org.id = o.organization_id "
            "    left join crm.organization root on root.id = coalesce(org.merged_into_organization_id, org.id) "
            "   where q.quote_number = s.payload->>'printed_quote_number' "
            "   order by (o.won_quote_id = q.id) desc nulls last, o.created_at limit 1) c on true "
            f"where l.model_key in ({keys}) and s.kind = 'quote_document' and not l.optional "
            "order by s.payload->>'quote_date' desc nulls last, l.line_no desc, l.id desc"
        )
        # What the model cost us, every observation (twins merged into it share its model_key; an
        # unambiguous alias names no other product): the route turns it into each quote's margin.
        # Cost data: redacted for a viewer.
        costs = ("select sp.as_of, sp.price, sp.currency, sp.price_kind, sp.is_stale "
                 "from catalog.supplier_product sp join catalog.product p on p.id = sp.product_id "
                 f"where p.model_key in ({keys}) order by sp.as_of")
        return {"quoted": (quoted, params), "model_costs": (costs, params)}

    def _price_history_from(self, key: str | None, rows: dict[str, list[dict[str, Any]]], limit: int) -> dict[str, Any]:
        if key is None:
            return {"model_key": None, "items": [], "median_last_5": None, "summary": _sales_summary([])}
        quoted = rows["quoted"]
        items = []
        for r in quoted[:limit]:
            case = ({"id": r["case_id"], "institution": r["institution"], "outcome": r["outcome"]}
                    if r["case_id"] else None)
            items.append({k: v for k, v in r.items() if k not in ("case_id", "institution", "outcome", "document_id")}
                         | {"source": "documento", "case": case})
        recent = [r for r in quoted if r["line_total"] is not None and (r["qty"] is None or r["qty"] == 1)
                  and r["check_status"] in _MEDIAN_STATUSES][:5]
        median = None
        if recent:
            totals = [r["line_total"] for r in recent if r["currency"] == recent[0]["currency"]]
            median = str(Decimal(statistics.median(totals)).quantize(_MONEY_STEP))
        return {"model_key": key, "items": _plain(items), "median_last_5": median, "summary": _sales_summary(quoted),
                "costs": _plain(rows["model_costs"])}

    def _kit_suggestions(self, cur: Any, key: str | None, min_share: Decimal) -> list[dict[str, Any]]:
        key = model_key(key)
        return self._kit_from(key, self._batch(cur, self._kit_queries(key)) if key else {}, min_share)

    @classmethod
    def _kit_queries(cls, key: str | None, product_id: uuid.UUID | None = None) -> dict[str, tuple[str, Any]]:
        if key is None:
            return {}
        keys = cls._line_keys(product_id)
        return {"kit": (
            f"with keys as ({keys}), docs as (select distinct l.source_record_id from evidence.document_line l "
            "  join evidence.source_record s on s.id = l.source_record_id "
            "  where l.model_key in (select k from keys) and s.kind = 'quote_document') "
            "select (select count(*) from docs) as total, l.model_key, min(l.brand) as brand, "
            "count(distinct l.source_record_id) as n "
            "from evidence.document_line l join docs d on d.source_record_id = l.source_record_id "
            "where l.model_key is not null and not exists (select 1 from keys where keys.k = l.model_key) "
            "group by l.model_key",
            {"key": key, "product_id": product_id})}

    @staticmethod
    def _kit_from(key: str | None, rows: dict[str, list[dict[str, Any]]], min_share: Decimal) -> list[dict[str, Any]]:
        if key is None:
            return []
        out = []
        for r in rows["kit"]:
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

    def suppliers(self) -> dict[str, Any]:
        """Every organization with at least one cost observation, with its active products by kind.

        The catalog filter's supplier list: one row per supplier, so the dropdown never depends on
        which products a page happened to show. `by_kind` keys are product kinds, `null` unclassified.
        """
        with self._read() as cur:
            cur.execute(
                "select o.id, o.name, p.product_kind, count(distinct p.id) as n "
                "from catalog.supplier_product sp "
                "join crm.organization raw on raw.id = sp.supplier_organization_id "
                "join crm.organization o on o.id = coalesce(raw.merged_into_organization_id, raw.id) "
                "join catalog.product p on p.id = sp.product_id and p.active "
                "group by o.id, o.name, p.product_kind order by o.name, p.product_kind"
            )
            rows = self._rows(cur)
        by_id: dict[Any, dict[str, Any]] = {}
        for r in rows:
            s = by_id.setdefault(r["id"], {"id": r["id"], "display_name": r["name"], "products": 0, "by_kind": {}})
            s["products"] += r["n"]
            s["by_kind"][r["product_kind"] or "unclassified"] = r["n"]
        items = sorted(by_id.values(), key=lambda s: s["display_name"].casefold())
        return _plain({"items": items})

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

    def fx_series(self, currency: str, start: dt.date, end: dt.date) -> dict[dt.date, Decimal]:
        """Every stored day in [start, end] with its rate (a manual one first, then Banco Central)."""
        with self._read() as cur:
            cur.execute(
                "select distinct on (rate_date) rate_date, clp_per_unit from catalog.fx_rate "
                "where currency = %s and rate_date between %s and %s "
                "order by rate_date, (source = 'manual') desc, (provider = 'bde') desc, created_at desc, id desc",
                (currency, start, end))
            return {r[0]: r[1] for r in cur.fetchall()}

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

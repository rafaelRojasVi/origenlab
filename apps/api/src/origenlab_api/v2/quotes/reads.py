"""Quote reads: the Cotizador workspace, the numbering box, the live preview, kit suggestions.

Read-only transactions as the runtime role, like `V2CatalogReads`. Nothing here knows about roles:
the routes refuse a viewer before calling in (`role_may_not_view_costs`), since every answer
carries costs and margins.

**Preview** (spec 2026-10-15 §5, R-A4) prices a draft exactly as `save-quote-draft` would —
`inputs.resolve_draft` + `quoting.engine.compute` + `inputs.result_json` — and writes nothing: no
receipt, no event, no row. Given a `revision_id` it prices with that revision's tax rate, as the
save does, so the preview's totals are the ones the save will store. One read transaction on one
connection, five round trips: the set-up, the parameters with the revision's tax rate (one
pipeline), and the four statements `resolve_draft` needs (products, observations, suppliers,
stored rates).

**Exchange rates.** `resolve_draft` reads the stored rates on this transaction's cursor. Only a
currency with nothing stored in the window reaches the injected resolver — here the full
`PricingFx.rate`, which asks a provider and stores its answer on its own connection (once per
currency and day). Opening a draft or previewing one therefore warms today's rate before the
operator saves; the commands, on a miss, refuse (`inputs.no_provider`).

**Workspace** (`GET /v2/quotes/{id}`): the quote, its case (institution and RUT, the current
participants, «Lo que pidió» from the case memory), every revision with its printed label, and the
current one — a draft comes back as the `QuoteDraft` it was saved from plus its live computation;
an issued revision as its stored totals, lines and frozen `party_snapshot`. Suggestions are only
offered, never added: the products the case's emails mention (`product_mention` assertions) and the
models that usually go with the principal (`kit_suggestions`, R-A5). The principal's quoted-price
history comes from the catalog reads.
"""
from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from origenlab_api.quoting.engine import EngineError, compute
from origenlab_api.v2.catalog.keys import is_labdelivery, model_key
from origenlab_api.v2.catalog.reads import V2CatalogReads, _plain
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.quotes.draft import PreviewQuoteBody, QuoteDraft
from origenlab_api.v2.quotes.inputs import (
    PARAMETERS_SQL,
    FxResolver,
    draft_from_rows,
    parameters_from,
    resolve_draft,
    result_json,
)
from origenlab_api.v2.quotes.numbering import SANTIAGO, numbering_status, revision_label

#: R-A5: a model is suggested beside the principal when it is on at least this share of the past
#: quote documents that carry the principal, and the principal was quoted at least `KIT_MIN_DOCUMENTS` times.
KIT_MIN_SHARE = Decimal("0.30")
KIT_MIN_DOCUMENTS = 3
KIT_LIMIT = 6
MENTION_LIMIT = 12
PRICE_HISTORY_LIMIT = 20

# The documents that quote the principal, then every other model on them with the share of those
# documents it is on; each with the active catalog product of that key (the survivor of a merge),
# when one exists. Optional lines count: an accessory offered as optional is exactly a kit item.
_KIT_SQL = """
with docs as (
  select distinct l.source_record_id
    from evidence.document_line l
    join evidence.source_record s on s.id = l.source_record_id
   where l.model_key = %(key)s and s.kind = 'quote_document'
), total as (select count(*) as n from docs)
select k.model_key, k.n, total.n as total, k.description, p.id::text as product_id,
       coalesce(p.name_es, p.name, p.model_number) as product_name
  from (select l.model_key, count(distinct l.source_record_id) as n,
               min(coalesce(nullif(btrim(l.model), ''), l.description)) as description
          from evidence.document_line l join docs d on d.source_record_id = l.source_record_id
         where l.model_key is not null and l.model_key <> %(key)s
         group by l.model_key) k
  cross join total
  left join lateral (
    select c.* from catalog.product c
     where c.model_key = k.model_key and c.active and c.merged_into_product_id is null
     order by c.created_at, c.id limit 1) p on true
 where total.n >= %(min_docs)s and k.n >= %(min_share)s * total.n
 order by k.n desc, k.model_key
 limit %(limit)s
"""

# The quote, its case, and the case's institution (through a merge) with its first RUT.
_QUOTE_SQL = """
select q.id::text as id, q.quote_number, q.opportunity_id::text as opportunity_id,
       o.title, o.stage, o.closed_at,
       inst.id::text as institution_id, inst.name as institution_name,
       (select e.value_norm from crm.external_identifier e
         where e.organization_id = inst.id and e.scheme = 'rut' order by e.created_at, e.id limit 1) as rut
  from crm.quote q
  join crm.opportunity o on o.id = q.opportunity_id
  left join crm.organization m on m.id = o.organization_id
  left join crm.organization inst on inst.id = coalesce(m.merged_into_organization_id, m.id)
 where q.id = %s::uuid
"""

_REVISIONS_SQL = """
select r.*
  from crm.quote_revision r
 where r.quote_id = %s::uuid
 order by r.revision_no
"""

# The current revision's lines with the model key of each line's product (for the principal's history).
_LINES_SQL = """
select l.*, p.model_key as product_model_key
  from crm.quote_line l
  left join catalog.product p on p.id = l.product_id
 where l.quote_revision_id = (
   select r.id from crm.quote_revision r where r.quote_id = %s::uuid
    order by (r.status in ('draft', 'in_review')) desc, (r.status <> 'void') desc, r.revision_no desc
    limit 1)
 order by l.line_no
"""

# The case's current participants: the person's name and the participant's own email address,
# else the person's (an active one first) — the same address approval prints.
_PARTICIPANTS_SQL = """
select p.id::text as id, coalesce(pe.display_name, cp.value_display) as name, cp.value_norm as email,
       p.is_primary as primary, p.role
  from crm.opportunity_participant p
  left join crm.person pe on pe.id = p.person_id
  left join lateral (
    select c.value_norm, c.value_display from crm.contact_point c
     where c.kind = 'email'
       and (c.id = p.contact_point_id or (p.contact_point_id is null and c.person_id = p.person_id))
     order by (c.status = 'active') desc, c.created_at, c.id
     limit 1) cp on true
 where p.opportunity_id = %s::uuid and p.valid_to is null
 order by p.is_primary desc, (p.role = 'quote_recipient') desc, p.valid_from, p.created_at, p.id
"""

_MEMORY_SQL = """
select sm.memory->>'summary' as summary
  from evidence.subject_memory sm
 where sm.subject_kind = 'opportunity' and sm.subject_id = %s::uuid
 order by sm.version desc
 limit 1
"""

# What the case's live emails name: one row per normalised model, with the catalog product it
# matched (the survivor of a merge, when active — as for kits) when there is one. A link a person removed, or a rejected or
# quarantined record, names nothing.
_MENTIONS_SQL = """
select distinct on (a.value_norm)
       a.value_norm, a.value->>'model' as model, a.value->>'description' as description,
       p.id::text as product_id, coalesce(p.name_es, p.name, p.model_number) as product_name,
       p.model_number as product_model
  from crm.opportunity_evidence oe
  join evidence.source_record s on s.id = oe.source_record_id
  join evidence.assertion a on a.source_record_id = s.id and a.kind = 'product_mention'
  left join catalog.product m
         on m.id = case when a.value->>'catalog_product_id'
                             ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
                        then (a.value->>'catalog_product_id')::uuid end
  left join catalog.product p on p.id = coalesce(m.merged_into_product_id, m.id)
        and p.active and p.merged_into_product_id is null
 where oe.opportunity_id = %s::uuid and oe.unlinked_at is null
   and not s.is_quarantined and s.review_status <> 'rejected'
 order by a.value_norm, (p.id is not null) desc, a.created_at
"""

#: The revision columns `current` carries besides its draft or stored lines.
_REVISION_FIELDS = (
    "id", "revision_no", "status", "origin", "version", "quote_currency", "tax_rate", "subtotal", "tax_total",
    "grand_total", "totals_computed_at", "validity_days", "valid_until", "approved_at", "sent_at",
    "superseded_by_revision_no", "recipient_participant_id", "expense_method", "expense_allocation_mode",
    "principal_rule", "updated_at",
)
#: Stored line columns shown for an issued revision (the draft of a draft is rebuilt instead).
_LINE_FIELDS = (
    "line_no", "line_kind", "description", "product_id", "quantity", "item_type", "cost_currency", "unit_cost",
    "fx_rate", "fx_as_of", "fx_source", "margin_mode", "margin_pct", "unit_price_qc", "line_total_qc",
    "is_principal", "allocated_to_line_no", "expense_method", "supplier_organization_id", "cost_observation_id",
    "line_note_es",
)


def _label(quote_number: str, revision_no: int) -> str | None:
    """The printed label, or None for a historical number that does not follow the series' form."""
    try:
        return revision_label(quote_number, revision_no)
    except ValueError:
        return None


def _santiago_today(now: datetime) -> dt.date:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(SANTIAGO).date()


class V2QuoteReads:
    def __init__(self, connect: Any, dsn: str, *, fx_resolver: FxResolver, statement_timeout_ms: int = 30_000,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._connect = connect
        self._dsn = dsn
        self._fx_resolver = fx_resolver
        self._statement_timeout_ms = statement_timeout_ms
        self._clock = clock
        # Its price-history SQL and shaping; it is never asked to open a transaction of its own here.
        self._catalog = V2CatalogReads(connect, dsn, statement_timeout_ms)

    @contextmanager
    def _read(self):  # type: ignore[no-untyped-def]
        with self._connect(self._dsn, autocommit=False) as conn:
            with conn.cursor() as cur:
                cur.execute(f"set transaction read only; set local statement_timeout = {int(self._statement_timeout_ms)}")
                try:
                    yield cur
                finally:
                    conn.rollback()

    @staticmethod
    def _rows(cur: Any) -> list[dict[str, Any]]:
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def _today(self) -> dt.date:
        return _santiago_today(self._clock())

    # -- numbering

    def numbering(self) -> dict[str, Any]:
        """What Hoy's number box shows: the next number, without reserving it. A bad seed is a 409."""
        with self._read() as cur:
            return numbering_status(cur, self._clock())

    # -- preview

    @staticmethod
    def _params(rows: list[dict[str, Any]]) -> dict[str, Decimal | str]:
        return parameters_from((r["key"], r["value_numeric"], r["value_json"]) for r in rows)

    def _priced(self, cur: Any, draft: QuoteDraft, tax_rate: Decimal | None,
                params: dict[str, Decimal | str]) -> dict[str, Any]:
        params = dict(params)
        if tax_rate is not None:
            params["iva_rate"] = Decimal(tax_rate)
        try:
            resolved = resolve_draft(cur, draft, params, self._fx_resolver, self._today())
            result = compute(resolved.engine_input)
        except EngineError as exc:
            raise CommandRefused(422, exc.code, f"the draft cannot be priced ({exc.code})") from exc
        return result_json(resolved, result)

    def preview(self, body: PreviewQuoteBody) -> dict[str, Any]:
        """The draft priced as a save would price it; nothing is written."""
        revision_id = str(body.revision_id) if body.revision_id else None
        with self._read() as cur:
            queries: dict[str, tuple[str, tuple[Any, ...]]] = {"params": (PARAMETERS_SQL, ())}
            if revision_id is not None:
                queries["revision"] = ("select tax_rate from crm.quote_revision where id = %s::uuid", (revision_id,))
            rows = self._catalog._batch(cur, queries)
            tax_rate = None
            if revision_id is not None:
                if not rows["revision"]:
                    raise CommandRefused(404, "revision_not_found", "no such quote revision")
                tax_rate = rows["revision"][0]["tax_rate"]
            return {"revision_id": revision_id,
                    "computed": self._priced(cur, body.draft, tax_rate, self._params(rows["params"]))}

    # -- kit suggestions (R-A5)

    def kit_suggestions(self, cur: Any, key: str | None) -> list[dict[str, Any]]:
        key = model_key(key)
        if key is None:
            return []
        cur.execute(_KIT_SQL, self._kit_params(key))
        return self._kits_from(self._rows(cur))

    @staticmethod
    def _kit_params(key: str) -> dict[str, Any]:
        return {"key": key, "min_docs": KIT_MIN_DOCUMENTS, "min_share": KIT_MIN_SHARE, "limit": KIT_LIMIT}

    @staticmethod
    def _kits_from(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for r in rows:
            name = r["product_name"] or r["description"] or r["model_key"]
            if is_labdelivery(name):
                continue
            out.append({"model_key": r["model_key"], "product_id": r["product_id"], "name": name,
                        "share": str((Decimal(r["n"]) / Decimal(r["total"])).quantize(Decimal("0.0001"))),
                        "documents": r["n"]})
        return out

    # -- workspace

    def workspace(self, quote_id: uuid.UUID | str) -> dict[str, Any] | None:
        """Everything the Cotizador page opens with, or None when the quote does not exist.

        Round trips after the set-up, all on this one connection: the quote with its case; one
        pipeline (revisions, current lines, participants, memory, mentions, parameters); for a
        draft, `resolve_draft`'s four statements; then one pipeline for the principal's history and kits.
        """
        qid = str(quote_id)
        with self._read() as cur:
            cur.execute(_QUOTE_SQL, (qid,))
            found = self._rows(cur)
            if not found:
                return None
            quote = found[0]
            case_id = quote["opportunity_id"]
            rows = self._catalog._batch(cur, {
                "revisions": (_REVISIONS_SQL, (qid,)),
                "lines": (_LINES_SQL, (qid,)),
                "participants": (_PARTICIPANTS_SQL, (case_id,)),
                "memory": (_MEMORY_SQL, (case_id,)),
                "mentions": (_MENTIONS_SQL, (case_id,)),
                "params": (PARAMETERS_SQL, ()),
            })
            revisions = rows["revisions"]
            current_row = self._current(revisions)
            lines = rows["lines"]
            params = self._params(rows["params"])
            current = self._current_json(cur, quote, current_row, lines, params) if current_row else None

            principal_key = self._principal_key(current, lines)
            history, kits = None, []
            if principal_key is not None:
                extra = self._catalog._batch(cur, {
                    **self._catalog._price_history_queries(principal_key),
                    "kits": (_KIT_SQL, self._kit_params(principal_key)),
                })
                history = self._catalog._price_history_from(principal_key, extra, PRICE_HISTORY_LIMIT)
                kits = self._kits_from(extra["kits"])

        memory = rows["memory"][0]["summary"] if rows["memory"] else None
        return _plain({
            "quote": {"id": quote["id"], "quote_number": quote["quote_number"], "opportunity_id": case_id},
            "case": {
                "id": case_id, "title": quote["title"], "stage": quote["stage"], "closed": quote["closed_at"] is not None,
                "institution": ({"id": quote["institution_id"], "name": quote["institution_name"], "rut": quote["rut"]}
                                if quote["institution_id"] else None),
                "participants": [{k: p[k] for k in ("id", "name", "email", "primary", "role")}
                                 for p in rows["participants"]],
                "lo_que_pidio": memory,
            },
            "revisions": [{"id": r["id"], "revision_no": r["revision_no"],
                           "label": _label(quote["quote_number"], r["revision_no"]), "status": r["status"],
                           "origin": r["origin"], "version": r["version"], "grand_total": r["grand_total"],
                           "approved_at": r["approved_at"], "sent_at": r["sent_at"],
                           "superseded_by_revision_no": r["superseded_by_revision_no"]}
                          for r in revisions],
            "current": current,
            "suggestions": {"mentioned": self._mentions_from(rows["mentions"]), "kits": kits},
            "price_history": history,
        })

    @staticmethod
    def _current(revisions: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The open draft; else the newest revision not voided; else the newest. Same order as `_LINES_SQL`."""
        if not revisions:
            return None
        return max(revisions, key=lambda r: (r["status"] in ("draft", "in_review"), r["status"] != "void",
                                             r["revision_no"]))

    def _current_json(self, cur: Any, quote: dict[str, Any], revision: dict[str, Any],
                      lines: list[dict[str, Any]], params: dict[str, Decimal | str]) -> dict[str, Any]:
        out: dict[str, Any] = {k: revision.get(k) for k in _REVISION_FIELDS}
        out["label"] = _label(quote["quote_number"], revision["revision_no"])
        if revision["origin"] == "authored" and revision["status"] == "draft":
            draft = draft_from_rows(revision, lines)
            out["draft"] = draft.model_dump(mode="json")
            out["computed"] = None
            out["computed_error"] = None
            try:
                out["computed"] = self._priced(cur, draft, revision["tax_rate"], params)
            except CommandRefused as exc:
                # The page still opens: the draft is shown with why it cannot be priced right now.
                out["computed_error"] = {"code": exc.code, "message": exc.message}
        else:
            out["lines"] = [{k: line.get(k) for k in _LINE_FIELDS} for line in lines]
            out["party_snapshot"] = revision.get("party_snapshot")
        return out

    @staticmethod
    def _principal_key(current: dict[str, Any] | None, lines: list[dict[str, Any]]) -> str | None:
        """The model key of the principal line's product: the computed principal of a draft, the stored one otherwise."""
        if current is None:
            return None
        principal = None
        computed = current.get("computed")
        if computed is not None:
            principal = computed.get("principal_line_no")
        elif "lines" in current:
            principal = next((r["line_no"] for r in lines if r["line_kind"] == "item" and r["is_principal"]), None)
        if principal is None:
            return None
        line = next((r for r in lines if r["line_no"] == principal and r["line_kind"] == "item"), None)
        return model_key(line["product_model_key"]) if line and line.get("product_model_key") else None

    @staticmethod
    def _mentions_from(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for r in rows:
            name = r["product_name"] or r["description"] or r["model"] or r["value_norm"]
            model = r["product_model"] or r["model"]
            if is_labdelivery(name) or is_labdelivery(model) or is_labdelivery(r["description"]):
                continue
            out.append({"product_id": r["product_id"], "model": model, "name": name, "model_key": r["value_norm"]})
            if len(out) == MENTION_LIMIT:
                break
        return out

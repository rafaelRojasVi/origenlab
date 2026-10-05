"""What each plan action does to the database: apply, verify, and what a rollback may remove.

Shared by every catalog importer (`_common.py` drives it). The SQL mirrors the catalog commands
(`origenlab_api/v2/catalog/commands.py`) — same columns, same locks, same events through the same
`_append_event` — with three differences that make an import an import:

* rows carry the manifest: `crm.organization.origin_source_record_id` and
  `catalog.supplier_product.origin_source_record_id` point at it, products are
  `content_origin = 'import'`, and **every event payload names it** (`origin_source_record_id`).
  That payload is how a rollback finds the products, terms and parameters it loaded — tables
  with no provenance column — and it never matches a row by name or key;
* an existing row is never touched: a match is `already_present` (or `present_different` when a
  row this plan did not write holds other values), and a rerun inserts nothing; a cost parameter
  this manifest already set is never set again (`kept_later_value` if someone changed it since);
* events carry no command receipt (an import is not an HTTP command), only the operator.

No event payload carries a price, a discount or a parameter value.

Rollback refuses (and lists) a loaded row that changed or is referenced since the apply, a
non-loaded stream (a pre-existing product or supplier) with events after the import's — deleting
them would leave a gap in its `seq` — and any event on the manifest other than its own
`source_record.migration_manifest_recorded`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from origenlab_api.v2.catalog import importing
from origenlab_api.v2.command_core import CommandTransaction
from origenlab_api.v2.identity import OperatorIdentity


class ImportRefused(Exception):
    """The target is not one this plan may be applied to (or rolled back from) as it stands."""


#: The command layer's event append, used as-is (it needs no connection of its own).
_EVENTS = CommandTransaction(connect=None, dsn="")
_MODEL_KEY_SQL = r"upper(regexp_replace(%s, '[\s\-_./]', '', 'g'))"


@dataclass
class Context:
    cur: Any
    operator: OperatorIdentity | None
    manifest_id: str | None
    #: plan key → database id, filled as items are applied or verified (orgs, products).
    ids: dict[str, str] = field(default_factory=dict)
    events: int = 0
    _orgs: list[tuple[str, str, str, str | None]] | None = None


def _row(cur: Any) -> dict[str, Any] | None:
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in cur.description], row, strict=True))


def _event(ctx: Context, aggregate_kind: str, aggregate_id: str, event_type: str, payload: dict[str, Any]) -> None:
    assert ctx.operator is not None and ctx.manifest_id is not None  # noqa: S101 - apply only
    _EVENTS._append_event(ctx.cur, aggregate_kind=aggregate_kind, aggregate_id=aggregate_id, event_type=event_type,
                          payload={**payload, "origin_source_record_id": ctx.manifest_id},
                          operator=ctx.operator, receipt_id=None)
    ctx.events += 1


def _manifest_event(ctx: Context, event_type: str, aggregate_kind: str, aggregate_id: str) -> str | None:
    """This manifest's event of `event_type` on one aggregate, if any."""
    if ctx.manifest_id is None:
        return None
    ctx.cur.execute("select id::text from crm.domain_event where aggregate_kind = %s and aggregate_id = %s::uuid "
                    "and event_type = %s and payload->>'origin_source_record_id' = %s limit 1",
                    (aggregate_kind, aggregate_id, event_type, ctx.manifest_id))
    row = ctx.cur.fetchone()
    return None if row is None else row[0]


# ------------------------------------------------------------------ organizations

def _organizations(ctx: Context) -> list[tuple[str, str, str, str | None]]:
    if ctx._orgs is None:
        ctx.cur.execute("select id::text, name, status, merged_into_organization_id::text from crm.organization")
        ctx._orgs = [tuple(r) for r in ctx.cur.fetchall()]
    return ctx._orgs


def _find_org(ctx: Context, fields: dict[str, Any]) -> str | None:
    """The live organization this item names: by the id the plan provides, else by folded name."""
    orgs = _organizations(ctx)
    if fields.get("organization_id"):
        match = [o for o in orgs if o[0] == fields["organization_id"]]
        if not match:
            raise ImportRefused(f"organization {fields['organization_id']} (given for {fields['name']!r}) "
                                "does not exist")
    else:
        wanted = importing.norm_name(fields["name"])
        match = [o for o in orgs if importing.norm_name(o[1]) == wanted and o[3] is None]
    if len(match) > 1:
        raise ImportRefused(f"{len(match)} organizations are named like {fields['name']!r}; "
                            "plan again with --org-id NAME=UUID")
    if not match:
        return None
    org_id, _, status, merged_into = match[0]
    if merged_into is not None or status != "active":
        raise ImportRefused(f"organization {org_id} ({fields['name']!r}) is merged or archived")
    return org_id


def _apply_create_org(ctx: Context, item: importing.PlanItem) -> str:
    fields = item["fields"]
    found = _find_org(ctx, fields)
    if found is not None:
        ctx.ids[item["key"]] = found
        return "already_present"
    ctx.cur.execute(
        "insert into crm.organization (kind, name, confirmation, origin_source_record_id) "
        "values (%s, %s, 'machine_proposed', %s::uuid) returning id::text as id",
        (fields["kind"], fields["name"], ctx.manifest_id))
    org_id = _row(ctx.cur)["id"]
    _organizations(ctx).append((org_id, fields["name"], "active", None))
    ctx.ids[item["key"]] = org_id
    _event(ctx, "organization", org_id, "organization.created", {"name": fields["name"], "kind": fields["kind"]})
    return "inserted"


def _verify_create_org(ctx: Context, item: importing.PlanItem) -> tuple[str, list[str], str | None]:
    try:
        found = _find_org(ctx, item["fields"])
    except ImportRefused:
        return "different", ["organization"], None
    if found is None:
        return "missing", [], None
    ctx.ids[item["key"]] = found
    return "present", [], found


# ------------------------------------------------------------------ products

def _find_product(ctx: Context, maker_id: str, model_number: str) -> str | None:
    ctx.cur.execute(f"select id::text as id from catalog.product where manufacturer_organization_id = %s::uuid "
                    f"and model_key = {_MODEL_KEY_SQL}", (maker_id, model_number))
    row = _row(ctx.cur)
    return None if row is None else row["id"]


def _apply_create_product(ctx: Context, item: importing.PlanItem) -> str:
    fields = item["fields"]
    maker_id = ctx.ids[fields["manufacturer"]]
    found = _find_product(ctx, maker_id, fields["model_number"])
    if found is not None:
        ctx.ids[item["key"]] = found
        return "already_present"
    ctx.cur.execute(
        "insert into catalog.product (manufacturer_organization_id, model_number, name, description, "
        "content_origin, created_by_operator_id) values (%s::uuid, %s, %s, %s, 'import', %s::uuid) "
        "returning id::text as id",
        (maker_id, fields["model_number"], fields.get("name"), fields.get("description"), ctx.operator.operator_id))
    product_id = _row(ctx.cur)["id"]
    ctx.ids[item["key"]] = product_id
    _event(ctx, "product", product_id, "product.created",
           {"model_number": fields["model_number"], "content_origin": "import"})
    return "inserted"


def _verify_create_product(ctx: Context, item: importing.PlanItem) -> tuple[str, list[str], str | None]:
    maker_id = ctx.ids.get(item["fields"]["manufacturer"])
    found = None if maker_id is None else _find_product(ctx, maker_id, item["fields"]["model_number"])
    if found is None:
        return "missing", [], None
    ctx.ids[item["key"]] = found
    return "present", [], found


# ------------------------------------------------------------------ supplier cost observations

_OBSERVATION_FIELDS = ("price", "currency", "list_price", "discount_pct", "is_stale")
_AS_OF_SQL = "(%s::date)::timestamp at time zone 'UTC'"


def _find_observation(ctx: Context, item: importing.PlanItem) -> dict[str, Any] | None:
    fields = item["fields"]
    supplier, product = ctx.ids.get(fields["supplier"]), ctx.ids.get(fields["product"])
    if supplier is None or product is None:
        return None
    ctx.cur.execute(
        f"select id::text as id, price, currency, list_price, discount_pct, is_stale, "
        f"origin_source_record_id::text as origin from catalog.supplier_product "
        f"where supplier_organization_id = %s::uuid and product_id = %s::uuid and as_of = {_AS_OF_SQL} "
        f"and price_kind = %s and min_qty is null",
        (supplier, product, fields["as_of"], fields["price_kind"]))
    return _row(ctx.cur)


def _apply_add_observation(ctx: Context, item: importing.PlanItem) -> str:
    fields = item["fields"]
    supplier, product = ctx.ids[fields["supplier"]], ctx.ids[fields["product"]]
    # The product owns the event stream appended to below: lock it as record-supplier-cost does.
    ctx.cur.execute("select id from catalog.product where id = %s::uuid for no key update", (product,))
    ctx.cur.execute(
        f"""
        insert into catalog.supplier_product
            (supplier_organization_id, product_id, as_of, price, currency, price_kind, list_price, discount_pct,
             is_stale, source_document, origin_source_record_id, recorded_by_operator_id)
        values (%s::uuid, %s::uuid, {_AS_OF_SQL}, %s::numeric, %s, %s, %s::numeric, %s::numeric, %s, %s,
                %s::uuid, %s::uuid)
        on conflict on constraint supplier_product_observation_key do nothing
        returning id::text as id
        """,
        (supplier, product, fields["as_of"], fields["price"], fields["currency"], fields["price_kind"],
         fields.get("list_price"), fields.get("discount_pct"), bool(fields.get("is_stale")),
         fields.get("source_document"), ctx.manifest_id, ctx.operator.operator_id))
    row = _row(ctx.cur)
    if row is None:
        # An observation with this key exists. Never overwritten (append-only); one this plan did not
        # write with other values is reported, as verify reports it.
        status, _ = _verify_add_observation(ctx, item)[:2]
        return "present_different" if status == "present_different" else "already_present"
    _event(ctx, "product", product, "product.cost_recorded",
           {"supplier_product_id": row["id"], "supplier_organization_id": supplier,
            "price_kind": fields["price_kind"], "currency": fields["currency"]})
    return "inserted"


def _verify_add_observation(ctx: Context, item: importing.PlanItem) -> tuple[str, list[str], str | None]:
    row = _find_observation(ctx, item)
    status, diffs = importing.compare(item["fields"], row, _OBSERVATION_FIELDS)
    if status == "different" and (ctx.manifest_id is None or row["origin"] != ctx.manifest_id):
        status = "present_different"
    return status, diffs, None if row is None else row["id"]


# ------------------------------------------------------------------ supplier terms

_TERMS_FIELDS = ("currency", "route", "map_enforced", "packing_pct")


def _apply_set_terms(ctx: Context, item: importing.PlanItem) -> str:
    """Create the supplier's terms only if it has none; existing terms are the operator's."""
    fields = item["fields"]
    supplier = ctx.ids[fields["supplier"]]
    ctx.cur.execute("select id from crm.organization where id = %s::uuid for no key update", (supplier,))
    ctx.cur.execute(
        "insert into catalog.supplier_terms (supplier_organization_id, currency, origin_country, route, "
        "map_enforced, packing_pct, updated_by_operator_id) values (%s::uuid, %s, %s, %s, %s, %s::numeric, %s::uuid) "
        "on conflict (supplier_organization_id) do nothing returning version",
        (supplier, fields["currency"], fields.get("origin_country"), fields["route"], bool(fields["map_enforced"]),
         fields.get("packing_pct"), ctx.operator.operator_id))
    if _row(ctx.cur) is None:
        # Existing terms are the operator's and are never changed by an import.
        status = _verify_set_terms(ctx, item)[0]
        return "present_different" if status == "present_different" else "already_present"
    _event(ctx, "organization", supplier, "organization.supplier_terms_set",
           {"route": fields["route"], "currency": fields["currency"], "note": None})
    return "inserted"


def _verify_set_terms(ctx: Context, item: importing.PlanItem) -> tuple[str, list[str], str | None]:
    supplier = ctx.ids.get(item["fields"]["supplier"])
    row = None
    if supplier is not None:
        ctx.cur.execute("select currency, route, map_enforced, packing_pct, version from catalog.supplier_terms "
                        "where supplier_organization_id = %s::uuid", (supplier,))
        row = _row(ctx.cur)
    status, diffs = importing.compare(item["fields"], row, _TERMS_FIELDS)
    if status == "different" and (row["version"] > 1 or _manifest_event(
            ctx, "organization.supplier_terms_set", "organization", supplier) is None):
        status = "present_different"  # not written by this plan, or changed by an operator since
    return status, diffs, None if row is None else supplier


# ------------------------------------------------------------------ cost parameters

def _current_parameter(ctx: Context, key: str) -> dict[str, Any] | None:
    ctx.cur.execute(
        "select id::text as id, value_numeric from catalog.cost_parameter where key = %s "
        "and valid_from <= clock_timestamp() order by valid_from desc, created_at desc, id desc limit 1", (key,))
    return _row(ctx.cur)


def _parameter_set_by_manifest(ctx: Context, key: str) -> str | None:
    """The cost_parameter row this manifest set for `key`, found by its event."""
    if ctx.manifest_id is None:
        return None
    ctx.cur.execute("select aggregate_id::text from crm.domain_event where event_type = 'cost_parameter.set' "
                    "and payload->>'origin_source_record_id' = %s and payload->>'key' = %s "
                    "order by seq limit 1", (ctx.manifest_id, key))
    row = ctx.cur.fetchone()
    return None if row is None else row[0]


def _apply_set_cost_parameter(ctx: Context, item: importing.PlanItem) -> str:
    """set-cost-parameter's SQL: the key's advisory lock, a wall-clock valid_from, the event without the value.

    A key this manifest already set is never set again: `already_present` if its row is still the
    current one, `kept_later_value` if someone set the key afterwards — a rerun must not revert an
    operator's later decision.
    """
    fields = item["fields"]
    key = fields["key"]
    ctx.cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"catalog.cost_parameter:{key}",))
    mine = _parameter_set_by_manifest(ctx, key)
    if mine is not None:
        current = _current_parameter(ctx, key)
        return "already_present" if current is not None and current["id"] == mine else "kept_later_value"
    ctx.cur.execute(
        "insert into catalog.cost_parameter (key, value_numeric, valid_from, set_by_operator_id, reason) "
        "values (%s, %s::numeric, clock_timestamp(), %s::uuid, %s) returning id::text as id",
        (key, fields["value"], ctx.operator.operator_id, fields["reason"]))
    row_id = _row(ctx.cur)["id"]
    _event(ctx, "cost_parameter", row_id, "cost_parameter.set", {"key": key, "note": None})
    return "inserted"


def _verify_set_cost_parameter(ctx: Context, item: importing.PlanItem) -> tuple[str, list[str], str | None]:
    key = item["fields"]["key"]
    mine = _parameter_set_by_manifest(ctx, key)
    if mine is None:
        return "missing", [], None
    current = _current_parameter(ctx, key)
    if current is None or current["id"] != mine:
        return "superseded_later", [], mine
    status, diffs = importing.compare(item["fields"], {"value": current["value_numeric"]}, ("value",))
    return status, diffs, mine


_APPLY = {"create_org": _apply_create_org, "create_product": _apply_create_product,
          "add_observation": _apply_add_observation, "set_terms": _apply_set_terms,
          "set_cost_parameter": _apply_set_cost_parameter}
_VERIFY = {"create_org": _verify_create_org, "create_product": _verify_create_product,
           "add_observation": _verify_add_observation, "set_terms": _verify_set_terms,
           "set_cost_parameter": _verify_set_cost_parameter}


def apply_item(ctx: Context, item: importing.PlanItem) -> str:
    return _APPLY[item["action"]](ctx, item)


def verify_item(ctx: Context, item: importing.PlanItem) -> dict[str, Any]:
    status, diffs, row_id = _VERIFY[item["action"]](ctx, item)
    return {"action": item["action"], "key": item["key"], "status": status, "diffs": diffs, "row_id": row_id}


def preflight(cur: Any, plan: importing.Plan) -> None:
    """Refusals found read-only before anything is written: unknown, ambiguous or archived organizations."""
    ctx = Context(cur=cur, operator=None, manifest_id=None)
    for item in plan["items"]:
        if item["action"] == "create_org":
            _find_org(ctx, item["fields"])


def record_manifest_event(ctx: Context, plan: importing.Plan, plan_sha: str) -> int:
    """`source_record.migration_manifest_recorded`, once per manifest, with the rows it describes."""
    ctx.cur.execute("select 1 from crm.domain_event where aggregate_kind = 'source_record' and aggregate_id = %s::uuid "
                    "and event_type = 'source_record.migration_manifest_recorded'", (ctx.manifest_id,))
    if ctx.cur.fetchone() is not None:
        return 0
    _EVENTS._append_event(ctx.cur, aggregate_kind="source_record", aggregate_id=ctx.manifest_id,
                          event_type="source_record.migration_manifest_recorded",
                          payload={"importer": plan["importer"], "plan_sha256": plan_sha, "counts": plan["counts"]},
                          operator=ctx.operator, receipt_id=None)
    return 1


# ------------------------------------------------------------------ rollback

#: Delete order (dependants first). The id column is what identifies a row of that table.
OWNED_TABLES = (
    ("crm.domain_event", "id"),
    ("catalog.supplier_product", "id"),
    ("catalog.supplier_terms", "supplier_organization_id"),
    ("catalog.cost_parameter", "id"),
    ("catalog.product", "id"),
    ("crm.organization", "id"),
    ("evidence.source_record", "id"),
)
#: Mutable tables: a row with a later version or update time changed after the apply.
_VERSIONED = ("crm.organization", "catalog.product", "catalog.supplier_terms")
_NOT_AN_ENTITY_ID = ("crm.domain_event", "catalog.supplier_terms")


def collect_owned(cur: Any, manifest_id: str) -> dict[str, list[str]]:
    """Every row whose provenance is this manifest — by its column or by its own events, never by name."""
    cur.execute("select id::text, event_type, aggregate_id::text from crm.domain_event "
                "where payload->>'origin_source_record_id' = %s "
                "   or (aggregate_kind = 'source_record' and aggregate_id = %s::uuid "
                "       and event_type = 'source_record.migration_manifest_recorded')", (manifest_id, manifest_id))
    events = cur.fetchall()

    def created_by(event_type: str) -> list[str]:
        return sorted({agg for _, et, agg in events if et == event_type})

    cur.execute("select id::text from crm.organization where origin_source_record_id = %s::uuid", (manifest_id,))
    orgs = sorted(r[0] for r in cur.fetchall())
    cur.execute("select id::text from catalog.supplier_product where origin_source_record_id = %s::uuid",
                (manifest_id,))
    observations = sorted(r[0] for r in cur.fetchall())
    return {
        "crm.domain_event": sorted(e[0] for e in events),
        "catalog.supplier_product": observations,
        "catalog.supplier_terms": created_by("organization.supplier_terms_set"),
        "catalog.cost_parameter": created_by("cost_parameter.set"),
        "catalog.product": created_by("product.created"),
        "crm.organization": orgs,
        "evidence.source_record": [manifest_id],
    }


def _id_column(table: str) -> str:
    return dict(OWNED_TABLES)[table]


def _fk_references(cur: Any, tables: list[str]) -> list[tuple[str, str, str]]:
    """(referencing table, column, referenced table) for every single-column foreign key into `tables`."""
    cur.execute(
        """
        select format('%%I.%%I', rn.nspname, rc.relname), format('%%I', a.attname),
               format('%%I.%%I', tn.nspname, tc.relname)
          from pg_constraint c
          join pg_class rc on rc.oid = c.conrelid join pg_namespace rn on rn.oid = rc.relnamespace
          join pg_class tc on tc.oid = c.confrelid join pg_namespace tn on tn.oid = tc.relnamespace
          join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
         where c.contype = 'f' and cardinality(c.conkey) = 1
           and format('%%I.%%I', tn.nspname, tc.relname) = any(%s)
         order by 1, 2
        """,
        (tables,))
    return [tuple(r) for r in cur.fetchall()]


def _references(cur: Any, owned: dict[str, list[str]], targets: dict[str, list[str]]) -> list[dict[str, str]]:
    """Rows outside `owned` that point at a row in `targets`: foreign keys, events, notes, assertions."""
    found: list[dict[str, str]] = []
    for referencing, column, referenced in _fk_references(cur, [t for t, ids in targets.items() if ids]):
        own = owned.get(referencing, [])
        id_col = _id_column(referencing) if referencing in owned else None
        exclude = f" and {id_col}::text <> all(%s)" if id_col else ""
        params: tuple[Any, ...] = (targets[referenced],) + ((own,) if id_col else ())
        cur.execute(f"select distinct {column}::text from {referencing} "  # noqa: S608 - names from pg_catalog
                    f"where {column}::text = any(%s){exclude}", params)
        for (ref_id,) in cur.fetchall():
            found.append({"table": referenced, "id": ref_id, "reason": f"referenced by {referencing}.{column}"})

    # Ids that name an entity of their own. Supplier terms are keyed by their supplier's id, which
    # may be an organization this import did not create; a change to the terms is caught by their
    # version instead.
    every = sorted({i for t, ids in targets.items() if t not in _NOT_AN_ENTITY_ID for i in ids})
    if every:
        cur.execute("select distinct aggregate_kind, aggregate_id::text from crm.domain_event "
                    "where aggregate_id::text = any(%s) and id::text <> all(%s)",
                    (every, owned.get("crm.domain_event", [])))
        found += [{"table": kind, "id": agg, "reason": "has events it did not load"} for kind, agg in cur.fetchall()]
        cur.execute("select distinct subject_kind, subject_id::text from crm.note where subject_id::text = any(%s)",
                    (every,))
        found += [{"table": kind, "id": sid, "reason": "has notes"} for kind, sid in cur.fetchall()]
        cur.execute("select distinct resolved_kind, resolved_id::text from evidence.assertion "
                    "where resolved_id::text = any(%s)", (every,))
        found += [{"table": kind, "id": rid, "reason": "an assertion resolves to it"} for kind, rid in cur.fetchall()]
    products = targets.get("catalog.product", [])
    if products:
        cur.execute("select distinct p.id::text from catalog.product p join evidence.document_line l "
                    "on l.model_key = p.model_key and l.created_at > p.created_at "
                    "where p.id::text = any(%s) and l.source_record_id::text <> all(%s)",
                    (products, owned.get("evidence.source_record", [])))
        found += [{"table": "catalog.product", "id": pid, "reason": "a later document line names its model"}
                  for (pid,) in cur.fetchall()]
    return found


def _later_events_on_other_streams(cur: Any, owned: dict[str, list[str]]) -> list[dict[str, str]]:
    """Streams this import appended to but did not create (a pre-existing product or supplier) that
    have events after the import's: deleting the import's events would leave a gap in their seq."""
    mine = owned.get("crm.domain_event", [])
    if not mine:
        return []
    cur.execute(
        """
        select distinct e.aggregate_kind, e.aggregate_id::text
          from crm.domain_event e
          join crm.domain_event later
            on later.aggregate_kind = e.aggregate_kind and later.aggregate_id = e.aggregate_id
           and later.seq > e.seq and later.id::text <> all(%s)
         where e.id::text = any(%s)
        """,
        (mine, mine))
    return [{"table": kind, "id": agg, "reason": "its event stream has later events than the import's"}
            for kind, agg in cur.fetchall()]


def rollback_blockers(cur: Any, owned: dict[str, list[str]], verified: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Why the loaded rows may not be deleted: changed, referenced, or not as verify finds them.

    The owned rows are locked first, so nothing can start referencing or changing them while the
    rollback decides.
    """
    blockers: list[dict[str, str]] = []
    for table in _VERSIONED:
        ids = owned.get(table, [])
        if not ids:
            continue
        id_col = _id_column(table)
        cur.execute(f"select {id_col}::text, version, updated_at <> created_at from {table} "  # noqa: S608
                    f"where {id_col}::text = any(%s) for update", (ids,))
        for row_id, version, touched in cur.fetchall():
            if version > 1 or touched:
                blockers.append({"table": table, "id": row_id, "reason": "changed since the apply"})
    params = owned.get("catalog.cost_parameter", [])
    if params:
        cur.execute("select mine.id::text from catalog.cost_parameter mine where mine.id::text = any(%s) and exists "
                    "(select 1 from catalog.cost_parameter later where later.key = mine.key "
                    " and later.valid_from > mine.valid_from and later.id::text <> all(%s))", (params, params))
        blockers += [{"table": "catalog.cost_parameter", "id": r[0], "reason": "a later value was set"}
                     for r in cur.fetchall()]
    blockers += _references(cur, owned, owned)
    blockers += _later_events_on_other_streams(cur, owned)

    owned_ids = {i for ids in owned.values() for i in ids}
    for row in verified:
        if row["row_id"] in owned_ids and row["status"] != "present":
            blockers.append({"table": row["action"], "id": row["row_id"],
                             "reason": f"verify finds it {row['status']}: {', '.join(row['diffs'])}"})
    unique: dict[tuple[str, str, str], dict[str, str]] = {(b["table"], b["id"], b["reason"]): b for b in blockers}
    return list(unique.values())


def delete_owned(cur: Any, owned: dict[str, list[str]]) -> dict[str, int]:
    deleted: dict[str, int] = {}
    for table, id_col in OWNED_TABLES:
        ids = owned.get(table, [])
        if not ids:
            continue
        cur.execute(f"delete from {table} where {id_col}::text = any(%s)", (ids,))  # noqa: S608 - fixed names
        if cur.rowcount != len(ids):
            raise ImportRefused(f"{table}: expected to delete {len(ids)} row(s), deleted {cur.rowcount}")
        deleted[table] = cur.rowcount
    return deleted


def dangling_references(cur: Any, owned: dict[str, list[str]]) -> list[dict[str, str]]:
    """After the delete (foreign keys are silent in replica mode): anything still pointing at a deleted row."""
    return _references(cur, {}, owned)

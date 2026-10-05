"""Catalog commands — products, supplier costs and terms, cost parameters, manual FX, document-line review.

Every handler runs inside the one transaction `CommandTransaction` provides: the receipt, the
handler's writes and its single `crm.domain_event` commit together or not at all. A refusal is a
`CommandRefused`; the transaction (receipt included) is rolled back on the way out.

**What the database decides, not this module.** A duplicate model is whatever
`product_one_model_key_per_manufacturer` (or the Slice 0 raw-model key) refuses, and a duplicate
cost observation is whatever `supplier_product_observation_key` refuses. Their unique violations
become the 409s below — never a 500, and never a second normalisation in Python that could
disagree with the generated `model_key`.

**Every handler locks the row that owns the event stream it appends to** (product, organization,
source record) before appending: `_append_event` computes the next `seq` per aggregate, and two
unserialised appends to one stream would collide on `domain_event_aggregate_seq_key` as a 500.
Cost-parameter and FX events each open a new stream (aggregate = the new row).

**Nothing of Labdelivery origin enters the catalog** (spec S5): `V2CatalogRepository.execute`
refuses a command whose submitted text names it — every string the command carries, the note
that would land in the append-only event stream included — before any transaction opens. The
checks inside individual handlers stay as a second line.

**What this module never does:** delete a row; write a price, a discount or a parameter value
into an event payload (the events say *that* a cost or parameter was recorded, the tables say
*what* it was, behind the viewer redaction of the reads); open `platform.command_receipt` or
`crm.domain_event` by name.
"""
from __future__ import annotations

import json
from decimal import Decimal
from collections.abc import Iterator
from typing import Any

import psycopg

from origenlab_api.v2.catalog.keys import LabdeliveryRefused, refuse_labdelivery
from origenlab_api.v2.command_core import CommandTransaction
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity

#: Unique constraints whose violation is a domain answer, by constraint name.
_DUPLICATE_MODEL = frozenset({"product_one_model_key_per_manufacturer", "product_manufacturer_model_key"})
_DUPLICATE_OBSERVATION = frozenset({"supplier_product_observation_key"})
_TERMS_EXIST = frozenset({"supplier_terms_pkey"})
_PARAMETER_CHANGED = frozenset({"cost_parameter_key_valid_from"})

#: The product columns an operator edits, in the order the change list reports them.
_PRODUCT_FIELDS = (
    "manufacturer_organization_id", "model_number", "name", "name_es", "description_es", "product_kind",
    "category_es", "specs", "weight_kg", "length_cm", "width_cm", "height_cm", "origin_country",
    "is_dangerous_goods", "active",
)
#: Changing any of these is new Spanish content: it becomes the operator's and loses its confirmation.
_CONTENT_FIELDS = frozenset({"name_es", "description_es", "specs"})
_NUMERIC_FIELDS = frozenset({"weight_kg", "length_cm", "width_cm", "height_cm"})
_CASTS = {"manufacturer_organization_id": "::uuid", "specs": "::jsonb", **{f: "::numeric" for f in _NUMERIC_FIELDS}}

#: Every observation needs a provenance; without a note, the command itself is it.
_DEFAULT_PROVENANCE = "recorded by an operator (record-supplier-cost)"


def _one(cur: Any) -> dict[str, Any] | None:
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in cur.description], row, strict=True))


def _execute_mapped(cur: Any, sql: str, params: Any, *, unique: dict[frozenset[str], tuple[str, str]],
                    check: tuple[str, str] | None = None) -> None:
    """Run one write; the database's refusals that are domain answers become refusals, not 500s.

    (Not `CommandTransaction._write`, which opens the transaction this runs inside.)

    `unique` maps constraint names to the 409 code and message for that violation; `check`, when
    given, is the 409 for a check violation (a guard trigger refusing the write). A unique or check
    violation that is not mapped is a defect and propagates.
    """
    try:
        cur.execute(sql, params)
    except psycopg.errors.UniqueViolation as exc:
        name = exc.diag.constraint_name
        for names, (code, message) in unique.items():
            if name in names:
                raise CommandRefused(409, code, message) from exc
        raise
    except psycopg.errors.CheckViolation as exc:
        if check is None:
            raise
        raise CommandRefused(409, *check) from exc
    except psycopg.errors.NumericValueOutOfRange as exc:
        raise CommandRefused(422, "value_out_of_range", "a number is too large for its field") from exc


def _live_party(cur: Any, organization_id: str, *, not_found_code: str, what: str,
                appends_to_its_stream: bool = False) -> dict[str, Any]:
    """The manufacturer or supplier organization, locked, only if it is live.

    `FOR SHARE` holds off a concurrent archive or merge until this command commits, without
    serialising two commands that only read the organization. A command that appends to the
    organization's own event stream takes `FOR NO KEY UPDATE` instead, the lock every other
    organization command holds while it appends, so two appends never compute the same seq.
    """
    lock = "for no key update" if appends_to_its_stream else "for share"
    cur.execute(
        f"""
        select id::text as id, name, status, merged_into_organization_id::text as merged_into_organization_id
          from crm.organization
         where id = %s::uuid
           {lock}
        """,
        (organization_id,),
    )
    org = _one(cur)
    if org is None:
        raise CommandRefused(404, not_found_code, f"no such {what}")
    if org["merged_into_organization_id"] is not None:
        raise CommandRefused(409, "organization_merged",
                             f"that {what} has been merged into another; use the surviving organization")
    if org["status"] == "archived":
        raise CommandRefused(409, "archived_subject", f"that {what} is archived")
    return org


def _locked_product(cur: Any, product_id: str) -> dict[str, Any]:
    cur.execute(
        """
        select id::text as id, manufacturer_organization_id::text as manufacturer_organization_id, model_number,
               name, name_es, description_es, product_kind, category_es, specs, weight_kg, length_cm, width_cm,
               height_cm, origin_country, is_dangerous_goods, active, content_origin,
               content_confirmed_at, content_confirmed_by_operator_id::text as content_confirmed_by_operator_id,
               version
          from catalog.product
         where id = %s::uuid
           for update
        """,
        (product_id,),
    )
    product = _one(cur)
    if product is None:
        raise CommandRefused(404, "product_not_found", "no such product")
    return product


def _same(field: str, current: Any, new: Any) -> bool:
    """Whether a submitted value equals the stored one, compared as the column's type."""
    if field in _NUMERIC_FIELDS:
        return current is not None and Decimal(str(new)) == current
    return current == new


# ------------------------------------------------------------------ products

def _handle_create_product(self: "V2CatalogRepository", cur: Any, operator: OperatorIdentity,
                           fields: dict[str, Any], receipt_id: str) -> dict[str, Any]:
    maker = fields["manufacturer_organization_id"]
    _live_party(cur, maker, not_found_code="manufacturer_not_found", what="manufacturer")
    _execute_mapped(
        cur,
        """
        insert into catalog.product
            (manufacturer_organization_id, model_number, name, name_es, description_es, product_kind,
             category_es, specs, weight_kg, length_cm, width_cm, height_cm, origin_country,
             is_dangerous_goods, content_origin, created_by_operator_id)
        values (%s::uuid, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::numeric, %s::numeric, %s::numeric,
                %s::numeric, %s, %s, 'operator', %s::uuid)
        returning id::text as id, version
        """,
        (maker, fields["model_number"], fields.get("name"), fields.get("name_es"), fields.get("description_es"),
         fields.get("product_kind"), fields.get("category_es"), json.dumps(fields.get("specs") or []),
         fields.get("weight_kg"), fields.get("length_cm"), fields.get("width_cm"), fields.get("height_cm"),
         fields.get("origin_country"), bool(fields.get("is_dangerous_goods")), operator.operator_id),
        unique={_DUPLICATE_MODEL: ("duplicate_model", "this manufacturer already has a product with that model")},
    )
    product = _one(cur)
    assert product is not None  # noqa: S101 - `returning` on a successful insert
    self._append_event(cur, aggregate_kind="product", aggregate_id=product["id"], event_type="product.created",
                       payload={"model_number": fields["model_number"], "content_origin": "operator",
                                "note": fields.get("note")},
                       operator=operator, receipt_id=receipt_id)
    return {"ok": True, "product_id": product["id"], "version": product["version"]}


def _handle_update_product(self: "V2CatalogRepository", cur: Any, operator: OperatorIdentity,
                           fields: dict[str, Any], receipt_id: str) -> dict[str, Any]:
    """Change what was submitted and differs; a content change becomes the operator's, unconfirmed.

    A field left out (null) is unchanged. Only values that differ from the stored ones count, so
    a form that re-sends unchanged content does not throw away its confirmation.
    """
    product_id, expected_version = fields["product_id"], fields["expected_version"]
    product = _locked_product(cur, product_id)
    if product["version"] != expected_version:
        raise CommandRefused(409, "stale_version", "the product was modified since you loaded it")

    changes = {f: fields[f] for f in _PRODUCT_FIELDS
               if fields.get(f) is not None and not _same(f, product[f], fields[f])}
    if not changes:
        raise CommandRefused(422, "no_changes", "nothing to update: every submitted value is already stored")
    if "manufacturer_organization_id" in changes:
        _live_party(cur, changes["manufacturer_organization_id"], not_found_code="manufacturer_not_found",
                    what="manufacturer")

    assignments = [f"{f} = %s{_CASTS.get(f, '')}" for f in changes]
    values = [json.dumps(v) if f == "specs" else v for f, v in changes.items()]
    if _CONTENT_FIELDS & changes.keys():
        assignments += ["content_origin = 'operator'", "content_confirmed_by_operator_id = null",
                        "content_confirmed_at = null"]
    _execute_mapped(
        cur,
        f"""
        update catalog.product
           set {", ".join(assignments)}, version = version + 1, updated_at = now()
         where id = %s::uuid and version = %s
        returning version
        """,
        [*values, product_id, expected_version],
        unique={_DUPLICATE_MODEL: ("duplicate_model", "this manufacturer already has a product with that model")},
    )
    updated = _one(cur)
    if updated is None:  # pragma: no cover - the row is locked; kept as the compare-and-set's other half
        raise CommandRefused(409, "stale_version", "concurrent modification")
    self._append_event(cur, aggregate_kind="product", aggregate_id=product_id, event_type="product.updated",
                       payload={"changed": list(changes), "note": fields.get("note")},
                       operator=operator, receipt_id=receipt_id)
    return {"ok": True, "product_id": product_id, "version": updated["version"]}


def _handle_confirm_product_content(self: "V2CatalogRepository", cur: Any, operator: OperatorIdentity,
                                    fields: dict[str, Any], receipt_id: str) -> dict[str, Any]:
    """An operator read the Spanish content and says it is right.

    Already confirmed answers `already_confirmed: true` with no event and no bump, checked before
    the version — the second of two operators clicking gets what they wanted, not a conflict.
    """
    product_id, expected_version = fields["product_id"], fields["expected_version"]
    product = _locked_product(cur, product_id)
    if product["content_confirmed_at"] is not None:
        return {"ok": True, "product_id": product_id, "version": product["version"], "already_confirmed": True,
                "confirmed_by_operator_id": product["content_confirmed_by_operator_id"]}
    if product["version"] != expected_version:
        raise CommandRefused(409, "stale_version", "the product was modified since you loaded it")
    cur.execute(
        """
        update catalog.product
           set content_confirmed_by_operator_id = %s::uuid, content_confirmed_at = now(),
               version = version + 1, updated_at = now()
         where id = %s::uuid and version = %s
        returning version
        """,
        (operator.operator_id, product_id, expected_version),
    )
    updated = _one(cur)
    if updated is None:  # pragma: no cover - the row is locked
        raise CommandRefused(409, "stale_version", "concurrent modification")
    self._append_event(cur, aggregate_kind="product", aggregate_id=product_id,
                       event_type="product.content_confirmed", payload={"note": fields.get("note")},
                       operator=operator, receipt_id=receipt_id)
    return {"ok": True, "product_id": product_id, "version": updated["version"], "already_confirmed": False,
            "confirmed_by_operator_id": operator.operator_id}


# ------------------------------------------------------------------ product images

_DUPLICATE_IMAGE = frozenset({"product_image_path_key"})
#: The image columns an operator edits, in the order the change list reports them.
_IMAGE_FIELDS = ("status", "caption_es", "sort_order")


def _lock_product_stream(cur: Any, product_id: str) -> None:
    """Lock the product row: it owns the product event stream the image events append to.

    `_append_event` computes max(seq) + 1 per aggregate, so two image commands on one product —
    or an image command and a cost, update or confirm — must append in turn, not collide on
    `domain_event_aggregate_seq_key`. NO KEY UPDATE, as `record-supplier-cost` takes, still lets
    the image row's own foreign key to the product take its KEY SHARE.
    """
    cur.execute("select id from catalog.product where id = %s::uuid for no key update", (product_id,))
    if _one(cur) is None:
        raise CommandRefused(404, "product_not_found", "no such product")


def _refuse_labdelivery_image(*parts: str | None) -> None:
    try:
        refuse_labdelivery(*parts)
    except LabdeliveryRefused as exc:
        raise CommandRefused(422, "labdelivery_refused", "an image of Labdelivery origin is refused") from exc


def _handle_add_product_image(self: "V2CatalogRepository", cur: Any, operator: OperatorIdentity,
                              fields: dict[str, Any], receipt_id: str) -> dict[str, Any]:
    """Record an image the route has already put in the private bucket.

    The bytes are in Storage before this transaction opens (their path is their hash, so a
    rollback here leaves an object the next upload of the same bytes reuses). The same bytes
    already on this product hand back that image — `already_present: true`, no second row, no
    event. An operator's upload is the operator's decision, so it is `confirmed`; only machine
    proposals (`created_by_operator_id` null) start `proposed` (spec D4).
    """
    product_id = fields["product_id"]
    _refuse_labdelivery_image(fields.get("caption_es"), fields.get("source_url"))
    _lock_product_stream(cur, product_id)
    cur.execute(
        """
        select id::text as id, version, status from catalog.product_image
         where product_id = %s::uuid and sha256 = %s
         order by created_at, id
         limit 1
        """,
        (product_id, fields["sha256"]),
    )
    existing = _one(cur)
    if existing is not None:
        return {"ok": True, "product_id": product_id, "image_id": existing["id"], "version": existing["version"],
                "status": existing["status"], "already_present": True}
    _execute_mapped(
        cur,
        """
        insert into catalog.product_image
            (product_id, storage_bucket, storage_path, sha256, content_type, sort_order, caption_es, source,
             source_url, status, created_by_operator_id)
        values (%s::uuid, 'catalog', %s, %s, %s,
                (select coalesce(max(sort_order) + 1, 0) from catalog.product_image where product_id = %s::uuid),
                %s, %s, %s, 'confirmed', %s::uuid)
        returning id::text as id, version, status
        """,
        (product_id, fields["storage_path"], fields["sha256"], fields["content_type"], product_id,
         fields.get("caption_es"), fields["source"], fields.get("source_url"), operator.operator_id),
        # Unreachable under the product lock (the select above would have found it); kept as an answer.
        unique={_DUPLICATE_IMAGE: ("duplicate_image", "this product already has that image")},
    )
    image = _one(cur)
    assert image is not None  # noqa: S101 - `returning` on a successful insert
    self._append_event(cur, aggregate_kind="product", aggregate_id=product_id, event_type="product.image_added",
                       payload={"image_id": image["id"], "sha256": fields["sha256"],
                                "content_type": fields["content_type"], "source": fields["source"]},
                       operator=operator, receipt_id=receipt_id)
    return {"ok": True, "product_id": product_id, "image_id": image["id"], "version": image["version"],
            "status": image["status"], "already_present": False}


def _handle_update_product_image(self: "V2CatalogRepository", cur: Any, operator: OperatorIdentity,
                                 fields: dict[str, Any], receipt_id: str) -> dict[str, Any]:
    """Change an image's status (confirm / hide), caption or position; never its bytes.

    A field left out (null) is unchanged; a blank caption clears it. Hiding is how an image
    leaves the catalog — rows are never deleted.
    """
    image_id, expected_version = fields["image_id"], fields["expected_version"]
    _refuse_labdelivery_image(fields.get("caption_es"), fields.get("note"))
    # The image's product never changes (the runtime role cannot update product_id), so it can be
    # read unlocked to find which stream to lock. Product, then image: the order add-product-image
    # takes too, so the two never deadlock.
    cur.execute("select product_id::text as product_id from catalog.product_image where id = %s::uuid", (image_id,))
    found = _one(cur)
    if found is None:
        raise CommandRefused(404, "image_not_found", "no such image")
    product_id = found["product_id"]
    _lock_product_stream(cur, product_id)
    cur.execute(
        "select status, caption_es, sort_order, version from catalog.product_image where id = %s::uuid for update",
        (image_id,),
    )
    image = _one(cur)
    assert image is not None  # noqa: S101 - images are never deleted
    if image["version"] != expected_version:
        raise CommandRefused(409, "stale_version", "the image was modified since you loaded it")

    submitted = {f: fields[f] for f in _IMAGE_FIELDS if fields.get(f) is not None}
    if "caption_es" in submitted:
        submitted["caption_es"] = submitted["caption_es"] or None  # blank clears
    changes = {f: v for f, v in submitted.items() if image[f] != v}
    if not changes:
        raise CommandRefused(422, "no_changes", "nothing to update: every submitted value is already stored")
    _execute_mapped(
        cur,
        f"""
        update catalog.product_image
           set {", ".join(f"{f} = %s" for f in changes)}, version = version + 1, updated_at = now()
         where id = %s::uuid and version = %s
        returning version
        """,
        [*changes.values(), image_id, expected_version],
        unique={},
    )
    updated = _one(cur)
    if updated is None:  # pragma: no cover - the row is locked; kept as the compare-and-set's other half
        raise CommandRefused(409, "stale_version", "concurrent modification")
    self._append_event(cur, aggregate_kind="product", aggregate_id=product_id, event_type="product.image_updated",
                       payload={"image_id": image_id, "changed": list(changes),
                                "status": changes.get("status", image["status"]), "note": fields.get("note")},
                       operator=operator, receipt_id=receipt_id)
    return {"ok": True, "product_id": product_id, "image_id": image_id, "version": updated["version"]}


# ------------------------------------------------------------------ supplier costs and terms

def _handle_record_supplier_cost(self: "V2CatalogRepository", cur: Any, operator: OperatorIdentity,
                                 fields: dict[str, Any], receipt_id: str) -> dict[str, Any]:
    """Append one cost observation. The event carries no money: not the price, the list price,
    the discount, nor the operator's note (which is the observation's provenance instead)."""
    product_id, supplier = fields["product_id"], fields["supplier_organization_id"]
    try:
        refuse_labdelivery(fields.get("source_document"), fields.get("note"))
    except LabdeliveryRefused as exc:
        raise CommandRefused(422, "labdelivery_refused", "a cost of Labdelivery origin is refused") from exc
    # This lock protects the product's event stream (`_append_event` computes max(seq) + 1 per
    # aggregate): two costs on one product, or a cost and an update/confirm (which hold FOR
    # UPDATE), append one after the other instead of colliding on the stream position. NO KEY
    # UPDATE still lets other transactions' foreign keys to the product take their KEY SHARE.
    cur.execute("select id::text as id from catalog.product where id = %s::uuid for no key update", (product_id,))
    if _one(cur) is None:
        raise CommandRefused(404, "product_not_found", "no such product")
    _live_party(cur, supplier, not_found_code="supplier_not_found", what="supplier")
    _execute_mapped(
        cur,
        """
        insert into catalog.supplier_product
            (supplier_organization_id, product_id, as_of, price, currency, price_kind, list_price, discount_pct,
             incoterm, valid_until, min_qty, is_stale, source_document, provenance_note, recorded_by_operator_id)
        values (%s::uuid, %s::uuid, %s::timestamptz, %s::numeric, %s, %s, %s::numeric, %s::numeric,
                %s, %s::date, %s::numeric, %s, %s, %s, %s::uuid)
        returning id::text as id
        """,
        (supplier, product_id, fields["as_of"], fields["price"], fields["currency"], fields["price_kind"],
         fields.get("list_price"), fields.get("discount_pct"), fields.get("incoterm"), fields.get("valid_until"),
         fields.get("min_qty"), bool(fields.get("is_stale")), fields.get("source_document"),
         fields.get("note") or _DEFAULT_PROVENANCE, operator.operator_id),
        unique={_DUPLICATE_OBSERVATION: (
            "duplicate_observation",
            "this supplier already has a cost of that kind for this product at that time and quantity")},
    )
    observation = _one(cur)
    assert observation is not None  # noqa: S101 - `returning` on a successful insert
    self._append_event(cur, aggregate_kind="product", aggregate_id=product_id, event_type="product.cost_recorded",
                       payload={"supplier_product_id": observation["id"], "supplier_organization_id": supplier,
                                "price_kind": fields["price_kind"], "currency": fields["currency"]},
                       operator=operator, receipt_id=receipt_id)
    return {"ok": True, "product_id": product_id, "supplier_product_id": observation["id"]}


_TERMS_COLUMNS = ("currency", "origin_country", "route", "incoterm", "default_discount_pct", "packing_pct",
                  "map_enforced", "default_lead_time_es", "notes")


def _handle_set_supplier_terms(self: "V2CatalogRepository", cur: Any, operator: OperatorIdentity,
                               fields: dict[str, Any], receipt_id: str) -> dict[str, Any]:
    """Create the supplier's terms (no `expected_version`) or replace them whole (with it)."""
    supplier, expected_version = fields["supplier_organization_id"], fields.get("expected_version")
    _live_party(cur, supplier, not_found_code="supplier_not_found", what="supplier", appends_to_its_stream=True)
    cur.execute("select version from catalog.supplier_terms where supplier_organization_id = %s::uuid for update",
                (supplier,))
    existing = _one(cur)
    values = [fields.get(c) for c in _TERMS_COLUMNS]
    values[_TERMS_COLUMNS.index("map_enforced")] = bool(fields.get("map_enforced"))

    if existing is None:
        if expected_version is not None:
            raise CommandRefused(409, "stale_version",
                                 "this supplier has no terms yet; create them without expected_version")
        _execute_mapped(
            cur,
            """
            insert into catalog.supplier_terms
                (supplier_organization_id, currency, origin_country, route, incoterm, default_discount_pct,
                 packing_pct, map_enforced, default_lead_time_es, notes, updated_by_operator_id)
            values (%s::uuid, %s, %s, %s, %s, %s::numeric, %s::numeric, %s, %s, %s, %s::uuid)
            returning version
            """,
            [supplier, *values, operator.operator_id],
            unique={_TERMS_EXIST: ("terms_exist", "this supplier already has terms; update them with expected_version")},
        )
    else:
        if expected_version is None:
            raise CommandRefused(409, "terms_exist", "this supplier already has terms; update them with expected_version")
        if existing["version"] != expected_version:
            raise CommandRefused(409, "stale_version", "the supplier terms were modified since you loaded them")
        _execute_mapped(
            cur,
            """
            update catalog.supplier_terms
               set currency = %s, origin_country = %s, route = %s, incoterm = %s,
                   default_discount_pct = %s::numeric, packing_pct = %s::numeric, map_enforced = %s,
                   default_lead_time_es = %s, notes = %s, updated_by_operator_id = %s::uuid,
                   version = version + 1, updated_at = now()
             where supplier_organization_id = %s::uuid and version = %s
            returning version
            """,
            [*values, operator.operator_id, supplier, expected_version],
            unique={},
        )
    terms = _one(cur)
    if terms is None:  # pragma: no cover - the row is locked
        raise CommandRefused(409, "stale_version", "concurrent modification")
    self._append_event(cur, aggregate_kind="organization", aggregate_id=supplier,
                       event_type="organization.supplier_terms_set",
                       payload={"route": fields["route"], "currency": fields["currency"], "note": fields.get("note")},
                       operator=operator, receipt_id=receipt_id)
    return {"ok": True, "supplier_organization_id": supplier, "version": terms["version"]}


# ------------------------------------------------------------------ pricing inputs

def _handle_set_cost_parameter(self: "V2CatalogRepository", cur: Any, operator: OperatorIdentity,
                               fields: dict[str, Any], receipt_id: str) -> dict[str, Any]:
    """A new current value for one parameter, with its reason; the previous rows stay as history.

    The runtime role may not lock `cost_parameter` rows (it has no UPDATE), so two admins setting
    one key are serialised by a transaction advisory lock on the key; under it, a stale
    `expected_current_id` is refused instead of silently superseded. `valid_from` is the wall
    clock, not the transaction's `now()`, so it is always later than the row it supersedes.
    """
    key, expected_current_id = fields["key"], fields.get("expected_current_id")
    cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"catalog.cost_parameter:{key}",))
    cur.execute(
        """
        select id::text as id from catalog.cost_parameter
         where key = %s and valid_from <= clock_timestamp()
         order by valid_from desc, created_at desc, id desc
         limit 1
        """,
        (key,),
    )
    current = _one(cur)
    if expected_current_id is not None and (current is None or current["id"] != expected_current_id):
        raise CommandRefused(409, "parameter_changed",
                             "this parameter was changed since you loaded it; reload and decide again")
    _execute_mapped(
        cur,
        """
        insert into catalog.cost_parameter (key, value_numeric, valid_from, set_by_operator_id, reason)
        values (%s, %s::numeric, clock_timestamp(), %s::uuid, %s)
        returning id::text as id
        """,
        (key, fields["value"], operator.operator_id, fields["reason"]),
        unique={_PARAMETER_CHANGED: ("parameter_changed", "this parameter was changed at the same instant; retry")},
    )
    row = _one(cur)
    assert row is not None  # noqa: S101 - `returning` on a successful insert
    self._append_event(cur, aggregate_kind="cost_parameter", aggregate_id=row["id"], event_type="cost_parameter.set",
                       payload={"key": key, "note": fields.get("note")}, operator=operator, receipt_id=receipt_id)
    return {"ok": True, "cost_parameter_id": row["id"], "key": key}


def _handle_record_fx_rate(self: "V2CatalogRepository", cur: Any, operator: OperatorIdentity,
                           fields: dict[str, Any], receipt_id: str) -> dict[str, Any]:
    """A manual rate for a day, with its reason. Manual rows are not unique: the newest one wins
    for its day and currency, so a mistake is corrected by recording again."""
    _execute_mapped(
        cur,
        """
        insert into catalog.fx_rate (rate_date, currency, clp_per_unit, source, provider, reason, recorded_by_operator_id)
        values (%s::date, %s, %s::numeric, 'manual', 'operator', %s, %s::uuid)
        returning id::text as id
        """,
        (fields["rate_date"], fields["currency"], fields["clp_per_unit"], fields["reason"], operator.operator_id),
        unique={},
    )
    row = _one(cur)
    assert row is not None  # noqa: S101 - `returning` on a successful insert
    self._append_event(cur, aggregate_kind="fx_rate", aggregate_id=row["id"], event_type="fx_rate.recorded",
                       payload={"currency": fields["currency"], "rate_date": fields["rate_date"], "source": "manual",
                                "note": fields.get("note")},
                       operator=operator, receipt_id=receipt_id)
    return {"ok": True, "fx_rate_id": row["id"]}


# ------------------------------------------------------------------ quote documents

def _handle_review_document_line(self: "V2CatalogRepository", cur: Any, operator: OperatorIdentity,
                                 fields: dict[str, Any], receipt_id: str) -> dict[str, Any]:
    """Settle a disputed line: the operator's numbers (where given) and a review note, once.

    The row lock plus the disputed → reviewed transition is the compare-and-set: a second reviewer
    waits, then finds the line reviewed and is refused. The database guard bounds the same rule.
    """
    line_id = fields["document_line_id"]
    # The line's source record never changes (the review guard freezes it), so it can be read
    # unlocked to find which stream to lock.
    cur.execute("select source_record_id::text as source_record_id from evidence.document_line where id = %s::uuid",
                (line_id,))
    found = _one(cur)
    if found is None:
        raise CommandRefused(404, "document_line_not_found", "no such document line")
    # The source record's event stream is the one appended to below (`_append_event` computes
    # max(seq) + 1 per aggregate): lock its owner row before the line, as the evidence commands
    # lock the record, so two reviews of one document — or a review and a review act — append in
    # turn. Always record, then line: one order, no deadlock.
    cur.execute("select id from evidence.source_record where id = %s::uuid for no key update",
                (found["source_record_id"],))
    cur.execute(
        """
        select id::text as id, source_record_id::text as source_record_id, check_status
          from evidence.document_line
         where id = %s::uuid
           for update
        """,
        (line_id,),
    )
    line = _one(cur)
    assert line is not None  # noqa: S101 - lines are never deleted
    if line["check_status"] != "disputed":
        raise CommandRefused(409, "line_not_disputed", "only a disputed line can be reviewed")
    not_disputed = ("line_not_disputed", "only a disputed line can be reviewed")
    _execute_mapped(
        cur,
        """
        update evidence.document_line
           set qty = coalesce(%s::numeric, qty), unit_price = coalesce(%s::numeric, unit_price),
               line_total = coalesce(%s::numeric, line_total), optional = coalesce(%s::boolean, optional),
               check_status = 'reviewed', reviewed_by_operator_id = %s::uuid, reviewed_at = now(),
               review_note = %s, updated_at = now()
         where id = %s::uuid and check_status = 'disputed'
        returning id
        """,
        (fields.get("qty"), fields.get("unit_price"), fields.get("line_total"), fields.get("optional"),
         operator.operator_id, fields["review_note"], line_id),
        unique={},
        check=not_disputed,  # the review guard says the same thing, in case it ever speaks first
    )
    if _one(cur) is None:  # pragma: no cover - the line is locked and was disputed
        raise CommandRefused(409, *not_disputed)
    self._append_event(cur, aggregate_kind="source_record", aggregate_id=line["source_record_id"],
                       event_type="source_record.document_line_reviewed",
                       payload={"document_line_id": line_id, "note": fields.get("note")},
                       operator=operator, receipt_id=receipt_id)
    return {"ok": True, "document_line_id": line_id, "source_record_id": line["source_record_id"],
            "check_status": "reviewed"}


def _submitted_text(value: Any) -> Iterator[str]:
    """Every string a command's fields carry, however nested (spec items, lists)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _submitted_text(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _submitted_text(item)


class V2CatalogRepository(CommandTransaction):
    """The ten catalog commands, each in one transaction."""

    def execute(self, *, command_name: str, operator: OperatorIdentity, fields: dict[str, Any],
                idempotency_key: str, digest: str) -> dict[str, Any]:
        """Refuse Labdelivery material in any submitted text, then run the command.

        Every string is checked, not a list of free-text fields: a field added later is covered
        without anyone remembering to add it, and identifiers, dates, numbers and closed
        vocabularies cannot spell the guarded names, so checking them costs nothing.
        """
        try:
            refuse_labdelivery(*_submitted_text(fields))
        except LabdeliveryRefused as exc:
            raise CommandRefused(422, "labdelivery_refused", "input of Labdelivery origin is refused") from exc
        return super().execute(command_name=command_name, operator=operator, fields=fields,
                               idempotency_key=idempotency_key, digest=digest)

    _HANDLERS = {
        "create-product": _handle_create_product,
        "update-product": _handle_update_product,
        "confirm-product-content": _handle_confirm_product_content,
        "add-product-image": _handle_add_product_image,
        "update-product-image": _handle_update_product_image,
        "record-supplier-cost": _handle_record_supplier_cost,
        "set-supplier-terms": _handle_set_supplier_terms,
        "set-cost-parameter": _handle_set_cost_parameter,
        "record-fx-rate": _handle_record_fx_rate,
        "review-document-line": _handle_review_document_line,
    }

#!/usr/bin/env python3
"""Create the catalog products OrigenLab has quoted but the catalog lacks, with their makers.

The owner reviewed the lines of the sent quotes that name no catalog product and approved, on
2026-10-16, which become products. The input is a private manifest outside this public repository:

    {"makers":            [{"name": "<maker>", "domain": "<maker.example>" | null}],
     "manufacturer_role": [{"name": "<an existing maker>"}],
     "products":          [{"maker": "<maker>", "model_number": "<model>", "name_es": "<nombre>",
                            "product_kind": "equipment|accessory|consumable|spare_part|service",
                            "reason": "desde cotización"}]}

    uv run python scripts/catalog/import_quote_products.py plan --manifest <private.json> --out <dir>
    uv run python scripts/catalog/import_quote_products.py apply --target-dsn … --admin-dsn … \\
        --operator-email … --plan <dir>/plan.json --plan-sha256 <sha> --out <dir>
    … verify / rollback / --hosted-target as every catalog importer (`_common.py`, OPERATIONS §14).

What the plan does, in one transaction, each change with its event:

* **makers** — each is an organization (kind `manufacturer`), matched to a live organization by its
  folded name (case, accents and punctuation ignored) and **reused** when one exists, created only
  when none does. Every maker gets the `manufacturer` role (`crm.organization_relationship`, the
  row add-organization-classification writes) and, when the manifest gives one, its domain as a
  `shared` `crm.organization_domain` (add-organization-domain's default scope).
* **manufacturer_role** — existing organizations that make what they sell: the `manufacturer` role
  only. They must exist (`must_exist`); one that does not is refused, never created.
* **products** — `catalog.product` with `content_origin = 'import'`, the Spanish name in `name_es`
  and the reason in the `product.created` event (`note`). A product whose maker already carries
  that `model_key` is `already_present` and is not touched. A product's maker is one of the makers
  above or, if listed in neither section, an existing organization (`must_exist`).

A role already in force, a domain already on the organization, a product already present: nothing
is written for it. **No cost is ever written** — a product entry with any other field (a price, a
cost) is refused at plan time. Model aliases are not this importer's. Labdelivery is refused (the
manifest's path and every string the plan would write), as in every importer. Rollback removes
exactly the rows the manifest created (organizations, roles, domains, products) and their events.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from origenlab_api.v2.catalog import importing  # noqa: E402
from origenlab_api.v2.catalog.keys import PRODUCT_KINDS, model_key, refuse_labdelivery  # noqa: E402

import _common  # noqa: E402

IMPORTER = "quote_products"
MAKER_KIND = "manufacturer"
ROLE = "manufacturer"
#: The note on every role this importer opens.
ROLE_NOTE = "fabricante de productos cotizados"
_SECTIONS = {"makers", "manufacturer_role", "products"}
_MAKER_FIELDS = {"name", "domain"}
_ROLE_FIELDS = {"name"}
_PRODUCT_FIELDS = {"maker", "model_number", "name_es", "product_kind", "reason"}
#: `crm.organization_domain.organization_domain_norm_shape`.
_DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")


def _text(value: Any, what: str, *, limit: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} must be a non-blank string")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"{what} is longer than {limit} characters")
    return value


def _entries(document: dict[str, Any], section: str, allowed: set[str]) -> list[dict[str, Any]]:
    entries = document.get(section, [])
    if not isinstance(entries, list):
        raise ValueError(f"{section} must be a list")
    for n, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"{section} #{n} is not an object")
        extra = set(entry) - allowed
        if extra:
            # A price or a cost in a product entry lands here: this importer never writes one.
            raise ValueError(f"{section} #{n}: unexpected field(s) {sorted(extra)}; "
                             f"allowed: {sorted(allowed)} (no cost is ever written)")
    return entries


def _org_key(name: str) -> str:
    return "org:" + importing.norm_name(name)


def manifest_items(document: Any) -> list[importing.PlanItem]:
    """The plan items of a manifest: organizations, roles, domains, products."""
    if not isinstance(document, dict) or set(document) - _SECTIONS or not set(document) & _SECTIONS:
        raise ValueError('the manifest must be {"makers": [...], "manufacturer_role": [...], "products": [...]}')
    items: list[importing.PlanItem] = []
    orgs: dict[str, str] = {}  # org key → name, in the order first planned
    roled: set[str] = set()  # org keys given the role

    def role(key: str) -> None:
        if key in roled:
            return
        roled.add(key)
        items.append({"action": "add_org_role", "key": f"role:{key[4:]}|{ROLE}",
                      "fields": {"organization": key, "role": ROLE, "note": ROLE_NOTE}})

    for n, entry in enumerate(_entries(document, "makers", _MAKER_FIELDS), 1):
        name = _text(entry.get("name"), f"makers #{n}: name")
        domain = entry.get("domain")
        if domain is not None:
            domain = _text(domain, f"makers #{n}: domain", limit=253).lower()
            if not _DOMAIN.match(domain):
                raise ValueError(f"makers #{n}: domain has the wrong shape")
        refuse_labdelivery(name, domain)
        key = _org_key(name)
        if not key[4:]:
            raise ValueError(f"makers #{n}: name has no letters or digits")
        if key in orgs:
            raise ValueError(f"makers #{n}: {name!r} is listed twice")
        orgs[key] = name
        items.append({"action": "create_org", "key": key, "fields": {"name": name, "kind": MAKER_KIND}})
        role(key)
        if domain:
            items.append({"action": "add_org_domain", "key": f"domain:{key[4:]}|{domain}",
                          "fields": {"organization": key, "domain": domain}})

    def existing(name: str) -> str:
        """An organization the plan only names: reused, never created."""
        key = _org_key(name)
        if key not in orgs:
            orgs[key] = name
            items.append({"action": "create_org", "key": key, "fields": {"name": name, "must_exist": True}})
        return key

    for n, entry in enumerate(_entries(document, "manufacturer_role", _ROLE_FIELDS), 1):
        name = _text(entry.get("name"), f"manufacturer_role #{n}: name")
        refuse_labdelivery(name)
        role(existing(name))

    products: set[str] = set()
    for n, entry in enumerate(_entries(document, "products", _PRODUCT_FIELDS), 1):
        maker = _text(entry.get("maker"), f"products #{n}: maker")
        model = _text(entry.get("model_number"), f"products #{n}: model_number", limit=200)
        name_es = _text(entry.get("name_es"), f"products #{n}: name_es", limit=1000)
        reason = _text(entry.get("reason"), f"products #{n}: reason")
        kind = entry.get("product_kind")
        if kind not in PRODUCT_KINDS:
            raise ValueError(f"products #{n}: product_kind must be one of {', '.join(PRODUCT_KINDS)}")
        key = model_key(model)
        if not key:
            raise ValueError(f"products #{n}: model_number has no model key")
        refuse_labdelivery(maker, model, name_es, reason)
        maker_key = existing(maker)
        product = f"product:{maker_key[4:]}|{key}"
        if product in products:
            raise ValueError(f"products #{n}: {maker!r} {model!r} is listed twice (same model key)")
        products.add(product)
        items.append({"action": "create_product", "key": product,
                      "fields": {"manufacturer": maker_key, "model_number": model, "name_es": name_es,
                                 "product_kind": kind, "note": reason}})
    if not products and not roled:
        raise ValueError("the manifest lists nothing to do")
    return items


def build_quote_products_plan(path: Path) -> importing.Plan:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    refuse_labdelivery(str(path.resolve()), path.name)
    try:
        document = json.loads(text)
    except ValueError:
        raise ValueError("the manifest is not JSON") from None
    return importing.build_plan(IMPORTER, [importing.input_record(path, IMPORTER)], manifest_items(document))


def _add_plan_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--manifest", type=Path, required=True, help="the private manifest JSON (outside the repo)")


def main(argv: list[str] | None = None) -> int:
    return _common.main(argv, importer=IMPORTER, description=__doc__ or "", add_plan_arguments=_add_plan_arguments,
                        make_plan=lambda args: build_quote_products_plan(args.manifest), print_keys=True)


if __name__ == "__main__":
    raise SystemExit(main())

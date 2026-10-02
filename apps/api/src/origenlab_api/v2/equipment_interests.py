"""Equipment lines for the CRM cards: the curated supplier directory and who is interested in what.

Both read the one equipment taxonomy Marketing uses (`equipment_taxonomy.json`, generated from
the public website). Its six families are the six equipment lines, one brand each; nothing here
adds a line, a brand or an alias.

* :func:`supplier_directory` — the six brands OrigenLab represents, as the commercial supplier
  directory. It is *curated* (the website publishes it), not detected: the machine-detected
  supplier candidates stay a separate list and are never promoted by a name match here. A match
  is shown as a hint beside the brand, with the candidate's own review state.
* :func:`interest_index` — the evidenced interests `marketing_audience.compose` already derives,
  regrouped per equipment line, per institution and per destination. A person linked in the CRM
  (`person_id`) is kept apart from an address that is only historical evidence. No evidence
  means no entry: a card with nothing here says «Sin información», never "low interest".
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from origenlab_api.v2.equipment_taxonomy import EquipmentTaxonomy


def _brands_named(taxonomy: EquipmentTaxonomy, *texts: str | None) -> set[str]:
    return {m.brand_id for t in texts for m in taxonomy.match(t)}


def supplier_directory(
    taxonomy: EquipmentTaxonomy,
    on_cases: Iterable[Mapping[str, Any]],
    candidates: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """One entry per catalogue brand, in catalogue order, with what the CRM holds about it."""
    on_cases = list(on_cases)
    candidates = list(candidates)
    out = []
    for brand in taxonomy.data["brands"]:
        family = taxonomy.families[brand["family_id"]]
        orgs: dict[str, dict[str, Any]] = {}
        for r in on_cases:
            if brand["id"] in _brands_named(taxonomy, r.get("name")):
                o = orgs.setdefault(
                    r["organization_id"],
                    {"organization_id": r["organization_id"], "name": r["name"],
                     "confirmation": r.get("confirmation"), "roles": [], "cases": 0},
                )
                o["roles"].append(r["role"])
                o["cases"] = max(o["cases"], int(r.get("cases") or 0))
        hints = [
            {
                "domain": c["domain"],
                "trade_name": c.get("trade_name"),
                "resolution": c["review"]["state"] if "review" in c else c.get("resolution"),
                "assertion_id": c.get("assertion_id"),
                "review": c.get("review"),
            }
            for c in candidates
            if brand["id"] in _brands_named(taxonomy, c.get("domain"), c.get("trade_name"))
        ]
        out.append({
            "brand_id": brand["id"],
            "name": brand["name"],
            "page_url": brand.get("page_url"),
            "family": {"id": family["id"], "name": family["name"], "color": family.get("color")},
            "model_count": sum(1 for m in taxonomy.data["models"] if m["brand_id"] == brand["id"]),
            "crm_organizations": list(orgs.values()),
            "candidate_hints": hints,
        })
    return out


def directory_organization_ids(directory: Iterable[Mapping[str, Any]]) -> set[str]:
    return {o["organization_id"] for d in directory for o in d["crm_organizations"]}


def _families(interests: Iterable[Mapping[str, Any]]) -> set[str]:
    return {i["family_id"] for i in interests}


def interest_index(taxonomy: EquipmentTaxonomy, composed: Mapping[str, Any]) -> dict[str, Any]:
    """`compose()` regrouped for cards: per line, per institution, per destination."""
    persons = []
    for p in composed["persons"]:
        persons.append({
            "key": p["key"],
            "address": p["address"],
            "address_ref": p["address_ref"],
            "contact_point_id": p["contact_point_id"],
            "person_id": p["person_id"],
            "display_name": p["display_name"],
            "organization_ids": p["organization_ids"],
            # A person the CRM records, or only an address seen in historical evidence.
            "link": "crm_person" if p["person_id"] else "address_only",
            "interests": p["interests"],
        })
    institutions = [
        {"organization_id": i["organization_id"], "name": i["name"], "interests": i["interests"]}
        for i in composed["institutions"]
    ]
    lines = []
    for fam in taxonomy.data["families"]:
        fid = fam["id"]
        ps = [p for p in persons if fid in _families(p["interests"])]
        insts = [i for i in institutions if fid in _families(i["interests"])]
        lines.append({
            "family_id": fid,
            "name": fam["name"],
            "color": fam.get("color"),
            "brand_ids": [b["id"] for b in taxonomy.data["brands"] if b["family_id"] == fid],
            "crm_people": sum(1 for p in ps if p["link"] == "crm_person"),
            "address_only": sum(1 for p in ps if p["link"] == "address_only"),
            "institutions": len(insts),
        })
    return {"lines": lines, "persons": persons, "institutions": institutions}

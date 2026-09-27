"""The equipment line of each campaign, for the Marketing calendar's line filter.

A line is only ever what was recorded or literally written, and each says which:

* ``audience_criteria`` — the family, brand or model the audience was frozen on;
* ``name_or_subject`` — a catalogue brand or model named in the campaign's name or subject
  (the same matcher as the equipment-interest audience), with the term that matched.

A campaign that has neither has no line; the calendar shows it under «Sin línea» rather than
guessing one.
"""

from __future__ import annotations

from typing import Any

from origenlab_api.v2.equipment_taxonomy import EquipmentTaxonomy


def campaign_lines(taxonomy: EquipmentTaxonomy, campaign: dict[str, Any]) -> list[dict[str, Any]]:
    criteria = campaign.get("audience_criteria") or {}
    family = criteria.get("family_id")
    if not family and criteria.get("model_id") in taxonomy.models:
        family = taxonomy.models[criteria["model_id"]]["family_id"]
    if not family and criteria.get("brand_id") in taxonomy.brands:
        family = taxonomy.brands[criteria["brand_id"]]["family_id"]
    if family in taxonomy.families:
        return [{"family_id": family, "source": "audience_criteria", "matched_term": None}]
    lines: dict[str, dict[str, Any]] = {}
    for text in (campaign.get("name"), campaign.get("subject")):
        for hit in taxonomy.match(text):
            lines.setdefault(hit.family_id, {"family_id": hit.family_id, "source": "name_or_subject",
                                             "matched_term": hit.term})
    return list(lines.values())

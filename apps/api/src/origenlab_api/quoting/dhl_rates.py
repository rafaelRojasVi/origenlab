"""DHL Express Chile 2026 import rate card (public published rates, USD).

The negotiated discount and the monthly fuel surcharge are cost parameters
(catalog.cost_parameter), never part of this file.
"""
from __future__ import annotations

import json
from bisect import bisect_right
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

_DEFAULT = Path(__file__).with_name("data") / "dhl_cl_import_2026.json"


def _round_up(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def chargeable_weight_kg(
    actual_kg: Decimal,
    length_cm: Decimal | None,
    width_cm: Decimal | None,
    height_cm: Decimal | None,
    divisor: Decimal = Decimal(5000),
) -> Decimal:
    """Greater of actual and volumetric weight, rounded up (0.5 kg to 30 kg, 1 kg above)."""
    weight = actual_kg
    if length_cm and width_cm and height_cm:
        weight = max(weight, (length_cm * width_cm * height_cm) / divisor)
    if weight <= 0:
        return Decimal("0.5")
    step = Decimal("0.5") if weight <= 30 else Decimal("1")
    return _round_up(weight, step)


@dataclass(frozen=True)
class RateCard:
    version: str
    country_zone: dict[str, int]
    brackets_kg: list[Decimal]
    import_usd: dict[int, list[Decimal]]
    increments: list[dict]
    surcharges_usd: dict[str, Decimal]

    @classmethod
    def load(cls, path: Path | None = None) -> "RateCard":
        raw = json.loads((path or _DEFAULT).read_text(encoding="utf-8"))
        return cls(
            version=raw["version"],
            country_zone={k.upper(): int(v) for k, v in raw["country_zone"].items()},
            brackets_kg=[Decimal(x) for x in raw["brackets_kg"]],
            import_usd={int(z): [Decimal(p) for p in prices] for z, prices in raw["import_usd"].items()},
            increments=[
                {
                    "from_kg": Decimal(i["from_kg"]),
                    "to_kg": Decimal(i["to_kg"]),
                    "step_kg": Decimal(i["step_kg"]),
                    "usd_per_step": {int(z): Decimal(v) for z, v in i["usd_per_step"].items()},
                }
                for i in raw["increments"]
            ],
            surcharges_usd={k: Decimal(v) for k, v in raw["surcharges_usd"].items()},
        )

    def zone_for(self, country: str) -> int:
        return self.country_zone[country.upper()]

    def surcharge_usd(self, code: str) -> Decimal:
        return self.surcharges_usd[code]

    def published_import_usd(self, zone: int, chargeable_kg: Decimal) -> Decimal:
        """Published price: the largest published bracket at or below the weight,
        plus the per-step increment of every band the remainder crosses."""
        idx = bisect_right(self.brackets_kg, chargeable_kg) - 1
        if idx < 0:
            return self.import_usd[zone][0]
        base_kg = self.brackets_kg[idx]
        price = self.import_usd[zone][idx]
        for band in self.increments:
            overlap = min(chargeable_kg, band["to_kg"]) - max(base_kg, band["from_kg"])
            if overlap <= 0:
                continue
            steps = (overlap / band["step_kg"]).to_integral_value(rounding=ROUND_CEILING)
            price += steps * band["usd_per_step"][zone]
        return price

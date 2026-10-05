from decimal import Decimal as D

import pytest

from origenlab_api.quoting.dhl_rates import RateCard, chargeable_weight_kg


@pytest.fixture(scope="module")
def card() -> RateCard:
    return RateCard.load()


def test_germany_and_spain_are_zone_4(card):
    assert card.zone_for("DE") == 4 and card.zone_for("ES") == 4


def test_unknown_country_is_refused(card):
    with pytest.raises(KeyError):
        card.zone_for("XX")


def test_every_published_country_is_present(card):
    assert len(card.country_zone) >= 225
    assert card.zone_for("us") == 3 and card.zone_for("CN") == 5 and card.zone_for("AR") == 1


@pytest.mark.parametrize("kg,usd", [("10", "486.18"), ("25", "1043.48"), ("70", "2754.98")])
def test_published_zone_4_points(card, kg, usd):
    assert card.published_import_usd(4, D(kg)) == D(usd)


def test_above_70_kg_adds_per_kg(card):
    assert card.published_import_usd(4, D("72")) == D("2754.98") + 2 * D("39.22")


def test_between_published_brackets_adds_increments(card):
    # 30 kg is published; 32 kg adds two 1 kg steps; 10.5 kg adds one 0.5 kg step
    base30 = card.published_import_usd(4, D("30"))
    assert card.published_import_usd(4, D("32")) == base30 + 2 * D("38.24")
    assert card.published_import_usd(4, D("10.5")) == D("486.18") + D("18.77")


def test_increment_bands_agree_with_published_40_kg(card):
    assert card.published_import_usd(4, D("40")) == card.published_import_usd(4, D("30")) + 10 * D("38.24")


def test_chargeable_weight_uses_volumetric_when_larger():
    # 60x40x40 cm = 96000 cm3 / 5000 = 19.2 kg -> rounds up to 19.5 (<= 30 kg: 0.5 steps)
    assert chargeable_weight_kg(D("8"), D("60"), D("40"), D("40")) == D("19.5")


def test_chargeable_weight_rounds_up_to_whole_kg_above_30():
    assert chargeable_weight_kg(D("31.2"), None, None, None) == D("32")


def test_chargeable_weight_without_dimensions_uses_actual():
    assert chargeable_weight_kg(D("12.1"), None, None, None) == D("12.5")


def test_surcharge_lookup(card):
    assert card.surcharge_usd("dg_fully_regulated") == D("133.00")


def test_data_file_ships_in_the_package_and_has_no_private_parameters():
    import importlib.resources as r
    text = (r.files("origenlab_api.quoting") / "data" / "dhl_cl_import_2026.json").read_text(encoding="utf-8")
    assert "fuel" not in text.lower() and "discount" not in text.lower()

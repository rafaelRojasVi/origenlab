from decimal import Decimal as D

import pytest

from origenlab_api.quoting.dhl_rates import RateCard
from origenlab_api.quoting.engine import EngineError, peso
from origenlab_api.quoting.expenses import ShipmentItem, dhl_expenses, expenses_for, profile_expenses

PARAMS = {"profile_freight_clp": D(400000), "profile_agent_clp": D(200000), "profile_customs_clp": D(100000),
          "profile_transport_clp": D(90000), "dhl_import_discount_pct": D("0.5"), "dhl_fuel_surcharge_pct": D("0.2"),
          "dhl_customs_clearance_fee_usd": D(20), "courier_threshold_usd_fob": D(3000), "customs_agent_fee_clp": D(150000),
          "duty_rate_general": D("0.06"), "duty_rate_fta": D(0), "insurance_pct_of_fob": D(0), "domestic_transport_clp": D(50000)}


def ship(n=1, kg="10", fob="1000", country="DE", supplier="s1", dangerous=False, qty=1):
    return ShipmentItem(n, D(qty), None if kg is None else D(kg), None, None, None, country, D(fob), dangerous, supplier)


def test_profile_gives_four_clp_lines():
    lines, warnings = profile_expenses(PARAMS)
    assert [l.amount for l in lines] == [D(400000), D(200000), D(100000), D(90000)]
    assert [l.label for l in lines] == ["Flete", "Agente de aduana", "Derechos y aduana", "Transporte local"]
    assert all(l.currency == "CLP" and l.method == "profile" for l in lines) and warnings == []


def test_profile_missing_parameter_is_a_warning():
    lines, warnings = profile_expenses({k: v for k, v in PARAMS.items() if k != "profile_agent_clp"})
    assert len(lines) == 3 and warnings == ["profile_parameter_missing:profile_agent_clp"]


def test_dhl_under_threshold_uses_the_dhl_clearance_fee_and_no_agent():
    card = RateCard.load()
    lines, _ = dhl_expenses([ship(fob="1000")], PARAMS, D(900), card)
    labels = [l.label for l in lines]
    assert any(l.startswith("Flete DHL") for l in labels) and "Despacho DHL" in labels
    assert "Agente de aduana" not in labels and "Derechos" not in labels      # DE = FTA origin, duty 0
    freight = next(l for l in lines if l.label.startswith("Flete DHL"))
    zone = card.zone_for("DE")
    base = card.published_import_usd(zone, D(10))
    assert freight.amount == peso(base * D("0.5") * D("1.2") * 900)
    assert freight.calc_inputs["chargeable_kg"] == "10"
    assert freight.currency == "CLP" and freight.method == "dhl_calc"
    despacho = next(l for l in lines if l.label == "Despacho DHL")
    assert despacho.amount == peso(D(20) * 900)
    assert labels[-1] == "Transporte local" and lines[-1].amount == D(50000)
    assert [l.line_no for l in lines] == list(range(1, len(lines) + 1))


def test_dhl_over_threshold_uses_the_agent():
    lines, _ = dhl_expenses([ship(fob="5000")], PARAMS, D(900), RateCard.load())
    labels = [l.label for l in lines]
    assert "Agente de aduana" in labels and "Despacho DHL" not in labels
    assert next(l for l in lines if l.label == "Agente de aduana").amount == D(150000)


def test_general_duty_outside_fta_origins():
    card = RateCard.load()
    lines, _ = dhl_expenses([ship(fob="5000", country="CN")], PARAMS, D(900), card)
    freight = next(l for l in lines if l.label.startswith("Flete DHL"))
    freight_usd = card.published_import_usd(card.zone_for("CN"), D(10)) * D("0.5") * D("1.2")
    duty = next(l for l in lines if l.label == "Derechos")
    assert duty.amount == peso(D("0.06") * (D(5000) + freight_usd) * 900) and duty.amount > 0
    # IVA on import is information only: never an amount, always a string in calc_inputs
    assert isinstance(freight.calc_inputs["cash_advance_iva_clp"], str)
    assert freight.amount == peso(freight_usd * 900)
    assert D(freight.calc_inputs["cash_advance_iva_clp"]) == peso(
        D("0.19") * ((D(5000) + freight_usd) * 900 + duty.amount))


def test_one_group_per_supplier_and_transport_once():
    lines, _ = dhl_expenses([ship(1, supplier="a"), ship(2, supplier="b", country="CN")], PARAMS, D(900), RateCard.load())
    assert sum(l.label.startswith("Flete DHL") for l in lines) == 2
    assert [l.label for l in lines].count("Transporte local") == 1
    assert "Flete DHL (a)" in [l.label for l in lines] and "Flete DHL (b)" in [l.label for l in lines]


def test_quantity_multiplies_chargeable_weight_and_fob():
    card = RateCard.load()
    lines, _ = dhl_expenses([ship(kg="10", fob="1000", qty=3)], PARAMS, D(900), card)
    freight = next(l for l in lines if l.label.startswith("Flete DHL"))
    assert freight.calc_inputs["chargeable_kg"] == "30"
    assert freight.calc_inputs["fob_usd"] == "3000"
    assert "Despacho DHL" in [l.label for l in lines]          # 3000 is not above the threshold


def test_dangerous_goods_adds_the_card_surcharge_once_per_group():
    card = RateCard.load()
    plain, _ = dhl_expenses([ship()], PARAMS, D(900), card)
    dg, _ = dhl_expenses([ship(1, dangerous=True), ship(2, dangerous=True)], PARAMS, D(900), card)
    f_plain = next(l for l in plain if l.label.startswith("Flete DHL"))
    f_dg = next(l for l in dg if l.label.startswith("Flete DHL"))
    # two 10 kg pieces -> 20 kg chargeable
    base = card.published_import_usd(card.zone_for("DE"), D(20))
    assert f_dg.amount == peso((base * D("0.5") * D("1.2") + card.surcharge_usd("dg_fully_regulated")) * 900)
    assert f_dg.amount > f_plain.amount


def test_missing_weight_falls_back_to_profile():
    lines, warnings = expenses_for("dhl_calc", [ship(kg=None)], PARAMS, D(900), RateCard.load())
    assert all(l.method == "profile" for l in lines) and warnings[0] == "dhl_fallback:dhl_weight_missing"


def test_dhl_raises_on_missing_weight():
    with pytest.raises(EngineError) as exc:
        dhl_expenses([ship(kg=None)], PARAMS, D(900), RateCard.load())
    assert exc.value.code == "dhl_weight_missing"


def test_unknown_origin_country_falls_back():
    lines, warnings = expenses_for("dhl_calc", [ship(country="ZZ")], PARAMS, D(900), RateCard.load())
    assert warnings[0].startswith("dhl_fallback:") and all(l.method == "profile" for l in lines)


def test_profile_method_and_dhl_success_have_expected_methods():
    assert all(l.method == "profile" for l in expenses_for("profile", [ship()], PARAMS, D(900), RateCard.load())[0])
    lines, warnings = expenses_for("dhl_calc", [ship()], PARAMS, D(900), RateCard.load())
    assert all(l.method == "dhl_calc" for l in lines) and warnings == []


def test_manual_generates_nothing():
    assert expenses_for("manual", [ship()], PARAMS, D(900), RateCard.load()) == ([], [])


def test_dhl_needs_its_parameters():
    with pytest.raises(EngineError) as exc:
        dhl_expenses([ship()], {}, D(900), RateCard.load())
    assert exc.value.code.startswith("dhl_parameter_missing:")

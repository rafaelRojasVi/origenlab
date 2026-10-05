"""Pricing FX: fakes only, no network and no database."""
from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.errors import register_exception_handlers
from origenlab_api.v2.catalog.fx import BdeProvider, FxUnavailable, MindicadorProvider, PricingFx, quiet_http_logs
from origenlab_api.v2.catalog.routes import catalog_read_router
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup


class _Reads:
    def __init__(self, stored):
        self.stored = stored

    def stored_fx(self, currency, on):
        days = [d for (c, d) in self.stored if c == currency and d <= on]
        if not days:
            return None
        d = max(days)
        return {"rate_date": d.isoformat(), "clp_per_unit": str(self.stored[(currency, d)]),
                "source": "bcentral", "provider": "bde"}


class _Writer:
    def __init__(self):
        self.rows = []

    def __call__(self, rows):
        self.rows.extend(rows)


class _Provider:
    def __init__(self, days, name="bde"):
        self.days, self.name, self.calls = days, name, 0

    def observed(self, currency, start, end):
        self.calls += 1
        return self.days


class _Broken:
    name = "bde"

    def observed(self, currency, start, end):
        raise httpx.ConnectError("down")


def _today():
    return date(2026, 10, 5)


def test_weekend_resolves_to_previous_business_day():
    fx = PricingFx(_Reads({}), _Writer(), [_Provider({date(2026, 10, 2): D("1104.57")})], _today)
    q = fx.rate("EUR", date(2026, 10, 4))
    assert q["clp_per_unit"] == D("1104.57") and q["as_of"] == date(2026, 10, 2)


def test_stored_rate_wins_and_no_provider_call():
    p = _Provider({})
    fx = PricingFx(_Reads({("USD", date(2026, 10, 1)): D("950")}), _Writer(), [p], _today)
    assert fx.rate("USD", date(2026, 10, 1))["clp_per_unit"] == D("950") and p.calls == 0


def test_stale_stored_rate_outside_window_is_refetched():
    p = _Provider({date(2026, 10, 5): D("7")})
    fx = PricingFx(_Reads({("USD", date(2026, 9, 1)): D("950")}), _Writer(), [p], _today)
    assert fx.rate("USD", date(2026, 10, 5))["clp_per_unit"] == D("7") and p.calls == 1


def test_fetched_days_are_stored_with_provider():
    w = _Writer()
    PricingFx(_Reads({}), w, [_Provider({date(2026, 10, 1): D("1"), date(2026, 10, 2): D("2")},
                                        name="mindicador")], _today).rate("USD", date(2026, 10, 2))
    assert {r["provider"] for r in w.rows} == {"mindicador"} and len(w.rows) == 2


def test_falls_back_to_second_provider():
    fx = PricingFx(_Reads({}), _Writer(), [_Broken(), _Provider({date(2026, 10, 1): D("9")}, "mindicador")], _today)
    q = fx.rate("USD", date(2026, 10, 1))
    assert q["provider"] == "mindicador" and q["clp_per_unit"] == D("9")


def test_declining_provider_falls_through():
    fx = PricingFx(_Reads({}), _Writer(), [_Provider({}), _Provider({date(2026, 10, 1): D("9")}, "mindicador")], _today)
    assert fx.rate("EUR", date(2026, 10, 1))["provider"] == "mindicador"


def test_provider_outage_and_nothing_stored_raises():
    with pytest.raises(FxUnavailable):
        PricingFx(_Reads({}), _Writer(), [_Broken()], _today).rate("USD", date(2026, 10, 1))


def test_never_returns_a_rate_dated_after_the_request():
    fx = PricingFx(_Reads({}), _Writer(), [_Provider({date(2026, 10, 3): D("5"), date(2026, 10, 1): D("4")})], _today)
    assert fx.rate("USD", date(2026, 10, 2))["as_of"] == date(2026, 10, 1)


def test_future_date_is_clamped_to_today():
    fx = PricingFx(_Reads({}), _Writer(), [_Provider({date(2026, 10, 5): D("3"), date(2026, 10, 9): D("8")})], _today)
    assert fx.rate("USD", date(2026, 12, 1))["as_of"] == date(2026, 10, 5)


def test_writer_failure_does_not_fail_the_read():
    def boom(rows):
        raise RuntimeError("db down")

    fx = PricingFx(_Reads({}), boom, [_Provider({date(2026, 10, 1): D("4")})], _today)
    assert fx.rate("USD", date(2026, 10, 1))["clp_per_unit"] == D("4")


def test_parse_mindicador_payload():
    payload = {"serie": [{"fecha": "2026-10-02T03:00:00.000Z", "valor": 1104.57}]}
    assert MindicadorProvider.parse(payload) == {date(2026, 10, 2): D("1104.57")}


def test_parse_bde_payload():
    payload = {"Series": {"Obs": [{"indexDateString": "02-10-2026", "value": "950.25", "statusCode": "OK"},
                                  {"indexDateString": "03-10-2026", "value": "NaN"}]}}
    assert BdeProvider.parse(payload) == {date(2026, 10, 2): D("950.25")}


def test_bde_declines_without_credentials_or_series():
    class _NoNet:
        def get(self, *a, **k):
            raise AssertionError("no network")

    assert BdeProvider(None, None, _NoNet(), {"USD": "X"}).observed("USD", date(2026, 10, 1), date(2026, 10, 2)) == {}
    assert BdeProvider("u", "p", _NoNet(), {"USD": "X", "EUR": ""}).observed("EUR", date(2026, 10, 1), date(2026, 10, 2)) == {}


def test_mindicador_provider_uses_year_endpoint_and_timeout():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"serie": [{"fecha": "2026-10-02T03:00:00.000Z", "valor": 1.5},
                                                   {"fecha": "2026-01-02T03:00:00.000Z", "valor": 2}]})

    p = MindicadorProvider(httpx.Client(transport=httpx.MockTransport(handler)))
    assert p.observed("USD", date(2026, 9, 28), date(2026, 10, 5)) == {date(2026, 10, 2): D("1.5")}
    assert seen == ["https://mindicador.cl/api/dolar/2026"]


# ── route ──
LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"


class _Lookup(OperatorLookup):
    def __init__(self, op):
        self._op = op

    def by_email(self, email_norm):
        return self._op


def _client(fx, role="viewer"):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(catalog_read_router)
    app.state.catalog_fx = fx
    op = OperatorIdentity(operator_id="00000000-0000-4000-8000-000000000001", email_norm="op@example.test",
                          display_name="Op", role=role, status="active")
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(op))
    return TestClient(app)


H = {OPERATOR_EMAIL_HEADER: "op@example.test"}


@pytest.mark.parametrize("role", ["viewer", "sales", "admin"])
def test_route_readable_by_every_role(role):
    fx = PricingFx(_Reads({("USD", date(2026, 10, 1)): D("950.5")}), _Writer(), [], _today)
    r = _client(fx, role).get("/v2/catalog/fx?currency=USD&date=2026-10-01", headers=H)
    assert r.status_code == 200
    assert r.json()["clp_per_unit"] == "950.5" and r.json()["as_of"] == "2026-10-01"


def test_route_503_envelope_when_unavailable():
    fx = PricingFx(_Reads({}), _Writer(), [_Broken()], _today)
    r = _client(fx).get("/v2/catalog/fx?currency=USD&date=2026-10-01", headers=H)
    assert r.status_code == 503 and "fx_unavailable" in r.text


@pytest.mark.parametrize("q", ["currency=CLP", "currency=USD&date=nope", "date=2026-10-01"])
def test_route_422(q):
    fx = PricingFx(_Reads({}), _Writer(), [], _today)
    assert _client(fx).get(f"/v2/catalog/fx?{q}", headers=H).status_code == 422


def test_bde_non_dict_series_is_empty():
    assert BdeProvider.parse({"Series": None}) == {} and BdeProvider.parse({"Series": []}) == {}
    assert BdeProvider.parse(None) == {}


@pytest.mark.parametrize("body", [{"Series": None}, {"Series": []}])
def test_bde_malformed_body_falls_through_and_never_500(body):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    bde = BdeProvider("u", "p", client, {"USD": "S"})
    mind = _Provider({date(2026, 10, 1): D("9")}, "mindicador")
    assert PricingFx(_Reads({}), _Writer(), [bde, mind], _today).rate("USD", date(2026, 10, 1))["provider"] == "mindicador"
    r = _client(PricingFx(_Reads({}), _Writer(), [bde], _today)).get(
        "/v2/catalog/fx?currency=USD&date=2026-10-01", headers=H)
    assert r.status_code == 503


def test_cache_fill_failure_is_logged_without_values(caplog):
    def boom(rows):
        raise RuntimeError("secret-detail")

    with caplog.at_level("WARNING"):
        PricingFx(_Reads({}), boom, [_Provider({date(2026, 10, 1): D("4.321")}, "mindicador")], _today).rate(
            "USD", date(2026, 10, 1))
    text = caplog.text
    assert "USD" in text and "mindicador" in text and "2026-10-01" in text
    assert "4.321" not in text and "secret-detail" not in text


def test_http_error_path_never_logs_the_password(caplog):
    quiet_http_logs()  # what main does where the provider is built
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    bde = BdeProvider("someuser", "hunter2-pass", client, {"USD": "S"})
    with caplog.at_level("DEBUG"):
        with pytest.raises(FxUnavailable):
            PricingFx(_Reads({}), _Writer(), [bde], _today).rate("USD", date(2026, 10, 1))
    assert "hunter2-pass" not in caplog.text and "someuser" not in caplog.text and "pass=" not in caplog.text


def test_route_default_date_uses_service_clock():
    fx = PricingFx(_Reads({("USD", date(2026, 10, 5)): D("1")}), _Writer(), [], _today)
    assert _client(fx).get("/v2/catalog/fx?currency=USD", headers=H).json()["as_of"] == "2026-10-05"

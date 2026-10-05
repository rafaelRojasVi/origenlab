"""Pricing exchange rates: stored Banco Central observed rates, fetched on a miss.

Unlike `v2/fx_rates.py` (display only), these rates may price a quote. A miss asks the providers
(BDE when configured, then mindicador.cl) over a short-timeout HTTP call that holds no database
connection, then fills the cache in its own short write transaction (`on conflict do nothing`, no
domain event, no operator). Never returns a rate dated after the requested day.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import httpx

FX_TIMEOUT_SECONDS = 5.0
FX_WINDOW_DAYS = 7
CURRENCIES = ("USD", "EUR")
BDE_URL = "https://si3.bcentral.cl/SieteRestWS/SieteRestWS.ashx"
MINDICADOR_URL = "https://mindicador.cl/api"
_MINDICADOR_KEY = {"USD": "dolar", "EUR": "euro"}


class FxUnavailable(RuntimeError):
    """No stored rate and no provider could supply one."""


class FxProvider(Protocol):
    name: str

    def observed(self, currency: str, start: dt.date, end: dt.date) -> dict[dt.date, Decimal]: ...


def _positive(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        return None
    return d if d.is_finite() and d > 0 else None


class MindicadorProvider:
    name = "mindicador"

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    @staticmethod
    def parse(payload: Any) -> dict[dt.date, Decimal]:
        out: dict[dt.date, Decimal] = {}
        serie = payload.get("serie") if isinstance(payload, dict) else None
        for item in serie if isinstance(serie, list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("fecha"), str):
                continue
            try:
                day = dt.date.fromisoformat(item["fecha"][:10])
            except ValueError:
                continue
            value = _positive(item.get("valor"))
            if value is not None:
                out[day] = value
        return out

    def observed(self, currency: str, start: dt.date, end: dt.date) -> dict[dt.date, Decimal]:
        key = _MINDICADOR_KEY[currency]
        out: dict[dt.date, Decimal] = {}
        for year in range(start.year, end.year + 1):
            resp = self._client.get(f"{MINDICADOR_URL}/{key}/{year}", timeout=FX_TIMEOUT_SECONDS)
            resp.raise_for_status()
            out.update(self.parse(resp.json()))
        return {d: v for d, v in out.items() if start <= d <= end}


class BdeProvider:
    name = "bde"

    def __init__(self, user: str | None, password: str | None, client: httpx.Client,
                 series: dict[str, str]) -> None:
        self._user, self._password, self._client = user, password, client
        self._series = {c: s for c, s in series.items() if s}

    @staticmethod
    def parse(payload: Any) -> dict[dt.date, Decimal]:
        out: dict[dt.date, Decimal] = {}
        obs = (payload or {}).get("Series", {}).get("Obs") if isinstance(payload, dict) else None
        for item in obs if isinstance(obs, list) else []:
            try:
                day = dt.datetime.strptime(item["indexDateString"], "%d-%m-%Y").date()
            except (KeyError, TypeError, ValueError):
                continue
            value = _positive(item.get("value"))
            if value is not None:
                out[day] = value
        return out

    def observed(self, currency: str, start: dt.date, end: dt.date) -> dict[dt.date, Decimal]:
        series = self._series.get(currency)
        if not (self._user and self._password and series):
            return {}  # declines: the next provider answers
        resp = self._client.get(BDE_URL, timeout=FX_TIMEOUT_SECONDS, params={
            "user": self._user, "pass": self._password, "function": "GetSeries", "timeseries": series,
            "firstdate": start.isoformat(), "lastdate": end.isoformat()})
        resp.raise_for_status()
        return self.parse(resp.json())


class PricingFx:
    def __init__(self, reads: Any, writer: Callable[[dict], None], providers: list[FxProvider],
                 today: Callable[[], dt.date]) -> None:
        self._reads, self._writer, self._providers, self._today = reads, writer, providers, today

    def _stored(self, currency: str, on: dt.date) -> dict | None:
        row = self._reads.stored_fx(currency, on)
        if not row:
            return None
        day = row["rate_date"]
        day = dt.date.fromisoformat(day) if isinstance(day, str) else day
        if day > on or (on - day).days > FX_WINDOW_DAYS:
            return None
        return {"currency": currency, "clp_per_unit": Decimal(str(row["clp_per_unit"])), "as_of": day,
                "source": row["source"], "provider": row["provider"]}

    def rate(self, currency: str, on: dt.date) -> dict:
        on = min(on, self._today())
        stored = self._stored(currency, on)
        if stored:
            return stored
        start = on - dt.timedelta(days=FX_WINDOW_DAYS)
        for provider in self._providers:
            try:
                days = provider.observed(currency, start, on)
            except (httpx.HTTPError, ValueError, KeyError):
                continue
            days = {d: v for d, v in days.items() if start <= d <= on}
            if not days:
                continue
            try:
                for d in sorted(days):
                    self._writer({"rate_date": d, "currency": currency, "clp_per_unit": days[d],
                                  "provider": provider.name})
            except Exception:  # a cache fill must not fail the read
                pass
            latest = max(days)
            return {"currency": currency, "clp_per_unit": days[latest], "as_of": latest,
                    "source": "bcentral", "provider": provider.name}
        raise FxUnavailable(f"no {currency} rate available for {on.isoformat()}")


class FxCacheWriter:
    """Its own short write transaction per fetch; the API role may insert catalog.fx_rate."""

    def __init__(self, connect: Any, dsn: str, statement_timeout_ms: int = 30_000) -> None:
        self._connect, self._dsn, self._timeout = connect, dsn, statement_timeout_ms

    def __call__(self, row: dict) -> None:
        with self._connect(self._dsn, autocommit=False) as conn:
            with conn.cursor() as cur:
                cur.execute(f"set local statement_timeout = {int(self._timeout)}")
                cur.execute(
                    "insert into catalog.fx_rate (rate_date, currency, clp_per_unit, source, provider) "
                    "values (%(rate_date)s, %(currency)s, %(clp_per_unit)s, 'bcentral', %(provider)s) "
                    "on conflict (rate_date, currency, provider) where source = 'bcentral' do nothing", row)
            conn.commit()

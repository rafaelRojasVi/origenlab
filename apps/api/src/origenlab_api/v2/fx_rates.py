"""Exchange rates for the dashboard — the dólar observado, the euro and the UF, in pesos.

The figures are the Banco Central de Chile's, read from mindicador.cl, which republishes them
daily with no account; when it fails, from findic.cl, which republishes the same figures in the
same shape. Each failure is logged with its source. **Display only:** nothing here may price a
quote. A quote line records its own `fx_rate`, `fx_as_of` and `fx_source` (`docs/WORKFLOWS.md`),
and pricing reads `catalog.fx_rate` through `v2/catalog/fx.py`, never through this module.

One copy per API process, refreshed at most once an hour. A failed refresh keeps the last
figures, marked `stale`. With none to keep (a fresh process), it answers the newest stored
Banco Central observation in `catalog.fx_rate`, also `stale`; with none stored either, the answer
is "unavailable". No new attempt is made for a minute after a failure, so down sources cannot
make every page request wait on them.

A successful mindicador.cl fetch also writes its USD and EUR through to `catalog.fx_rate`
(`source='bcentral'`, `provider='mindicador'`, `on conflict do nothing`), which is what keeps that
fallback current. findic.cl's figures are shown but never stored: the table's providers are the
reviewed ones (`bde`, `mindicador`, `operator`), and a row is never filed under another
provider's name. Reading and writing happen outside the cache lock and never during an HTTP call;
a database failure is logged and changes nothing the operator sees beyond "unavailable".
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from collections.abc import Callable
from collections.abc import Sequence
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from origenlab_api.v2.campaign_planning import PLANNING_TIME_ZONE

MINDICADOR_URL = "https://mindicador.cl/api"
FINDIC_URL = "https://findic.cl/api/"
FX_FETCH_TIMEOUT_SECONDS = 5
FX_MAX_BODY_BYTES = 256 * 1024
FX_CACHE_SECONDS = 3600
FX_RETRY_AFTER_FAILURE_SECONDS = 60

logger = logging.getLogger(__name__)

#: mindicador key → (code shown, label shown), in display order.
_SERIES = (("dolar", "USD", "Dólar observado"), ("euro", "EUR", "Euro"), ("uf", "UF", "UF"))

#: Source name → `catalog.fx_rate.provider`, for the sources whose figures are written through.
_STORED_PROVIDER = {"mindicador.cl": "mindicador"}
#: The currencies `catalog.fx_rate` holds (no UF).
_STORED_CURRENCIES = ("USD", "EUR")

#: `(currency, on) -> row | None`: the effective stored rate for `currency` on or before `on`.
StoreReader = Callable[[str, date], "dict[str, Any] | None"]
#: Writes `[{rate_date, currency, clp_per_unit, provider}]` as `bcentral` rows; conflicts ignored.
StoreWriter = Callable[[list[dict[str, Any]]], None]


class FxUnavailable(RuntimeError):
    """No usable exchange rate: the source failed and there is no earlier figure to show."""


def _fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=FX_FETCH_TIMEOUT_SECONDS) as resp:
        raw = resp.read(FX_MAX_BODY_BYTES + 1)
    if len(raw) > FX_MAX_BODY_BYTES:
        raise FxUnavailable("exchange-rate answer too large")
    return json.loads(raw)


def _fetch_mindicador() -> Any:
    return _fetch_json(MINDICADOR_URL)


def _fetch_findic() -> Any:
    return _fetch_json(FINDIC_URL)


#: (source name, fetch), in the order they are tried. The name is also the source's host.
FxSources = Sequence[tuple[str, Callable[[], Any]]]
DEFAULT_SOURCES: FxSources = (("mindicador.cl", _fetch_mindicador), ("findic.cl", _fetch_findic))


def _chile_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if len(value) == 10:
        # findic.cl writes the Chile day itself.
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(ZoneInfo(PLANNING_TIME_ZONE)).date().isoformat()


def parse_mindicador(body: Any) -> list[dict[str, Any]]:
    """The usable series of a mindicador-shaped answer (findic.cl's too), in display order;
    raises :class:`FxUnavailable` when none is."""
    if not isinstance(body, dict):
        raise FxUnavailable("exchange-rate answer is not an object")
    rates = []
    for key, code, label in _SERIES:
        item = body.get(key)
        if not isinstance(item, dict):
            continue
        value = item.get("valor")
        # bool is an int in Python; a True "rate" is not a rate.
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            continue
        as_of = _chile_date(item.get("fecha"))
        if as_of is None:
            continue
        rates.append({"code": code, "label": label, "clp": float(value), "as_of": as_of})
    if not rates:
        raise FxUnavailable("no usable exchange rate in the answer")
    return rates


class FxRates:
    """The current figures, cached for an hour. Thread-safe; one per API process."""

    def __init__(
        self,
        fetch: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        sources: FxSources | None = None,
        store_reader: StoreReader | None = None,
        store_writer: StoreWriter | None = None,
    ) -> None:
        # `fetch` alone stands in for mindicador.cl as the only source (tests).
        if sources is None:
            sources = DEFAULT_SOURCES if fetch is None else (("mindicador.cl", fetch),)
        self._sources = tuple(sources)
        self._clock = clock
        self._wall_clock = wall_clock
        self._lock = threading.Lock()
        self._figures: dict[str, Any] | None = None
        self._fetched_at = 0.0
        self._failed_at: float | None = None
        self._store_reader = store_reader
        self._store_writer = store_writer
        # (clock time, answer or None): the last stored-rate read, reused for the retry window so
        # an outage costs one database read (and at most one WARNING) a minute, not one a request.
        self._stored_cache: tuple[float, dict[str, Any] | None] | None = None
        self._write_thread: threading.Thread | None = None
        # One refresh at a time, outside the lock: while it runs, others get the last figures.
        self._refreshing = False

    def current(self) -> dict[str, Any]:
        fetched: tuple[str, list[dict[str, Any]]] | None = None
        with self._lock:
            now = self._clock()
            if self._figures is not None and now - self._fetched_at < FX_CACHE_SECONDS:
                return {**self._figures, "stale": False}
            if self._refreshing and self._figures is not None:
                # Another request is asking the outside service: never wait on it.
                return {**self._figures, "stale": True}
            in_backoff = self._failed_at is not None and now - self._failed_at < FX_RETRY_AFTER_FAILURE_SECONDS
            if not in_backoff:
                self._refreshing = True
        answer: dict[str, Any] | None = None
        if in_backoff:
            with self._lock:
                answer = self._in_memory()
        else:
            try:
                # The outside call runs without the lock: only this request waits on it.
                for name, fetch in self._sources:
                    try:
                        rates = parse_mindicador(fetch())
                    except Exception as exc:  # noqa: BLE001 — any failure of a source means "try the next"
                        logger.warning("fx source %s failed: %s: %s", name, type(exc).__name__, str(exc)[:200])
                        continue
                    fetched = (name, rates)
                    break
            finally:
                with self._lock:
                    self._refreshing = False
                    if fetched is not None:
                        name, rates = fetched
                        self._failed_at = None
                        self._fetched_at = now
                        self._figures = {
                            "source": name,
                            "source_label": f"Banco Central de Chile, vía {name}",
                            "source_url": f"https://{name}",
                            "rates": rates,
                            "fetched_at": datetime.fromtimestamp(self._wall_clock(), tz=timezone.utc).isoformat(),
                        }
                        answer = {**self._figures, "stale": False}
                    else:
                        self._failed_at = now
                        answer = self._in_memory()
        with self._lock:
            if answer is None and self._stored_cache is not None:
                read_at, cached = self._stored_cache
                if now - read_at < FX_RETRY_AFTER_FAILURE_SECONDS:
                    if cached is None:
                        raise FxUnavailable("exchange rates unavailable")
                    return cached
        # Outside the lock: the database never holds up another request, and no HTTP call is open.
        if fetched is not None:
            self._start_write_through(*fetched)
        if answer is not None:
            return answer
        stored = self._stored()
        with self._lock:
            self._stored_cache = (now, stored)
        if stored is None:
            raise FxUnavailable("exchange rates unavailable")
        return stored

    def _start_write_through(self, source: str, rates: list[dict[str, Any]]) -> None:
        """Write the fetched USD and EUR in a short daemon thread: the request that triggered the
        hourly refresh answers without waiting on the database."""
        if self._store_writer is None or source not in _STORED_PROVIDER:
            return
        thread = threading.Thread(target=self._write_through, args=(source, rates),
                                  name="fx-write-through", daemon=True)
        self._write_thread = thread
        thread.start()

    def wait_for_write_through(self, timeout: float) -> bool:
        """Block until the last write-through has finished (tests); True when none is running."""
        thread = self._write_thread
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True

    def _in_memory(self) -> dict[str, Any] | None:
        return None if self._figures is None else {**self._figures, "stale": True}

    def _write_through(self, source: str, rates: list[dict[str, Any]]) -> None:
        if self._store_writer is None:
            return
        try:
            rows = _rows_to_store(source, rates)
            if rows:
                self._store_writer(rows)
        except Exception as exc:  # noqa: BLE001 — a failed write never changes the answer
            # The type only: a driver's message may name the database host.
            logger.warning("fx write-through to catalog.fx_rate failed: %s", type(exc).__name__)

    def _stored(self) -> dict[str, Any] | None:
        """The newest stored observation per currency as of today in Chile, or None."""
        if self._store_reader is None:
            return None
        today = datetime.fromtimestamp(self._wall_clock(), tz=ZoneInfo(PLANNING_TIME_ZONE)).date()
        labels = {code: label for _, code, label in _SERIES}
        rates: list[dict[str, Any]] = []
        newest: datetime | None = None
        try:
            for currency in _STORED_CURRENCIES:
                row = self._store_reader(currency, today)
                if not row:
                    continue
                day = _as_date(row["rate_date"])
                if day > today:
                    continue
                rates.append({"code": currency, "label": labels[currency],
                              "clp": float(row["clp_per_unit"]), "as_of": day.isoformat()})
                created = _as_utc(row["created_at"])
                newest = created if newest is None or created > newest else newest
        except Exception as exc:  # noqa: BLE001 — a failed read is "unavailable", never a 500
            logger.warning("fx stored-rate read from catalog.fx_rate failed: %s", type(exc).__name__)
            return None
        if not rates or newest is None:
            return None
        return {
            "source": "catalog.fx_rate",
            "source_label": "Banco Central de Chile (último valor guardado)",
            "source_url": "https://si3.bcentral.cl",
            "rates": rates,
            "fetched_at": newest.isoformat(),
            "stale": True,
        }


def _rows_to_store(source: str, rates: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    provider = _STORED_PROVIDER.get(source)
    if provider is None:
        return None
    return [
        {"rate_date": date.fromisoformat(r["as_of"]), "currency": r["code"],
         "clp_per_unit": Decimal(str(r["clp"])), "provider": provider}
        for r in rates
        if r["code"] in _STORED_CURRENCIES
    ] or None


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _as_utc(value: Any) -> datetime:
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)

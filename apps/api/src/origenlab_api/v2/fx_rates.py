"""Exchange rates for the dashboard — the dólar observado, the euro and the UF, in pesos.

The figures are the Banco Central de Chile's, read from mindicador.cl, which republishes them
daily with no account; when it fails, from findic.cl, which republishes the same figures in the
same shape. Each failure is logged with its source. **Display only:** nothing here is stored, and nothing here may price a
quote. A quote line records its own `fx_rate`, `fx_as_of` and `fx_source` (`docs/WORKFLOWS.md`),
and quotation generation will read the Banco Central's own API for that.

One copy per API process, refreshed at most once an hour. A failed refresh keeps the last
figures, marked `stale`; with none to keep, the answer is "unavailable", and no new attempt is
made for a minute, so down sources cannot make every page request wait on them.
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

    def current(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            if self._figures is not None and now - self._fetched_at < FX_CACHE_SECONDS:
                return {**self._figures, "stale": False}
            if self._failed_at is not None and now - self._failed_at < FX_RETRY_AFTER_FAILURE_SECONDS:
                return self._fallback()
            for name, fetch in self._sources:
                try:
                    rates = parse_mindicador(fetch())
                except Exception as exc:  # noqa: BLE001 — any failure of a source means "try the next"
                    logger.warning("fx source %s failed: %s: %s", name, type(exc).__name__, str(exc)[:200])
                    continue
                self._failed_at = None
                self._fetched_at = now
                self._figures = {
                    "source": name,
                    "source_label": f"Banco Central de Chile, vía {name}",
                    "source_url": f"https://{name}",
                    "rates": rates,
                    "fetched_at": datetime.fromtimestamp(self._wall_clock(), tz=timezone.utc).isoformat(),
                }
                return {**self._figures, "stale": False}
            self._failed_at = now
            return self._fallback()

    def _fallback(self) -> dict[str, Any]:
        if self._figures is None:
            raise FxUnavailable("exchange rates unavailable")
        return {**self._figures, "stale": True}

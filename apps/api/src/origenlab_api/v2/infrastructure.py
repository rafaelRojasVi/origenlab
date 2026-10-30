"""«Costos e infraestructura»: Supabase and Render usage vs. the plan limits.

Read-only. No secret is returned; Render figures are fetched from the Render API when the key
is set, cached in-process for 10 minutes. Supabase figures come from SQL over the API's own
read connection. Both are best-effort: a single failed sub-read returns null with a reason and
never 500s the whole response.

Plans are a Python constant, checked-in 2026-10-30, listed as nominal prices to review:
the owner can correct the values here if the plan changes.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, date, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Plan table — checked-in 2026-10-30; review when the plan changes.
# ---------------------------------------------------------------------------

#: Nominal prices (USD/month) for the plan we are on; ``None`` = «por uso» (billed by usage).
PLANS: dict[str, Any] = {
    "_as_of": "2026-10-30",
    "_note": "Precios nominales de lista; revisar si cambia el plan.",
    "render": {
        "workspace": {
            "plan": "Hobby",
            "monthly_usd": 0.0,
            "outbound_bandwidth_gb_included": 5.0,
            "outbound_overage_usd_per_100_gb": 15.0,
        },
        "service_plans": {
            "starter_web": {"plan": "starter", "monthly_usd": 7.0},
            "starter_worker": {"plan": "starter", "monthly_usd": 7.0},
            "static_site": {"plan": "static_site", "monthly_usd": 0.0},
            "cron": {"plan": "cron", "monthly_usd": None},  # billed by usage
        },
    },
    "supabase": {
        "plan": "Pro",
        "monthly_usd": 25.0,
        "database_gb_included": 8.0,
        "file_storage_gb_included": 100.0,
        "egress_gb_included": 250.0,
    },
}

#: Supabase Pro monthly cost in USD (from PLANS above).
_SUPABASE_MONTHLY_USD: float = float(PLANS["supabase"]["monthly_usd"])

#: App schemas whose tables appear in «Tablas más grandes». Supabase internal schemas
#: (pg_catalog, pg_toast, information_schema, _supabase, extensions, storage, auth,
#: graphql, graphql_public, realtime, _realtime, pgbouncer, pgsodium, vault) are excluded.
APP_SCHEMAS = (
    "crm", "comms", "evidence", "outbound", "catalog", "procurement", "platform", "procrastinate"
)

_APP_SCHEMA_LIST = ", ".join(f"'{s}'" for s in APP_SCHEMAS)

LARGEST_TABLES_SQL = f"""
select nspname || '.' || relname as table_name,
       pg_total_relation_size(c.oid) as total_bytes
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
 where c.relkind = 'r'
   and n.nspname in ({_APP_SCHEMA_LIST})
 order by total_bytes desc
 limit 8
"""

DATABASE_SIZE_SQL = "select pg_database_size(current_database())"

MAIL_BYTES_SQL = """
select coalesce(sum(size_bytes), 0)::bigint as total_bytes,
       count(*)::int as total_count,
       coalesce(sum(case when created_at >= date_trunc('month', now()) then size_bytes else 0 end), 0)::bigint as month_bytes
  from comms.message
"""

# ---------------------------------------------------------------------------
# Render HTTP client
# ---------------------------------------------------------------------------

RENDER_API_BASE = "https://api.render.com/v1"
RENDER_TIMEOUT_SECONDS = 10

#: Render service types as reported by the API, mapped to a human-readable kind.
_RENDER_TYPE_LABEL: dict[str, str] = {
    "web_service": "web",
    "background_worker": "worker",
    "static_site": "static",
    "cron_job": "cron",
    "private_service": "private",
}


def _plan_price_usd(service_type: str, plan_name: str | None) -> float | None:
    """Return the known monthly USD price for a Render service, or None for «por uso»."""
    pt = service_type or ""
    pn = (plan_name or "").lower()
    if pt == "web_service" and "starter" in pn:
        return float(PLANS["render"]["service_plans"]["starter_web"]["monthly_usd"])
    if pt == "background_worker" and "starter" in pn:
        return float(PLANS["render"]["service_plans"]["starter_worker"]["monthly_usd"])
    if pt == "static_site":
        return float(PLANS["render"]["service_plans"]["static_site"]["monthly_usd"])
    if pt == "cron_job":
        return None  # billed by usage
    # Unknown plan: return None rather than 0 to signal «unknown».
    return None


class RenderClient:
    """Thin HTTP wrapper around the Render API; injectable for tests."""

    def __init__(self, api_key: str, fetch: Any = None, timeout: float = RENDER_TIMEOUT_SECONDS) -> None:
        self._api_key = api_key
        self._fetch = fetch
        self._timeout = timeout

    def _get(self, path: str) -> Any:
        """GET from the Render API and return the parsed JSON body."""
        import urllib.request

        url = RENDER_API_BASE + path
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310 - own URL
            import json
            return json.loads(resp.read().decode())

    def list_services(self) -> list[dict[str, Any]]:
        """Return up to 50 Render services as dicts (name, serviceDetails, etc.)."""
        data = self._get("/services?limit=50")
        # The response is a list of {"service": {...}, "cursor": ...}
        if isinstance(data, list):
            return [item.get("service", item) for item in data]
        # Some response shapes wrap it differently — be tolerant.
        if isinstance(data, dict) and "services" in data:
            return data["services"]
        return []

    def bandwidth_bytes(self, service_id: str, start_iso: str, end_iso: str) -> int:
        """Return total outbound bandwidth bytes for ``service_id`` in the given window.

        The Render metrics endpoint returns a time-series response:
        ``{"data": [{"timestamp": "...", "value": ...}, ...]}``
        or a list of ``{"datapoints": [...]}`` objects, or similar. We parse
        tolerantly: sum every numeric ``value`` we find in the response.
        """
        path = f"/metrics/bandwidth?resource={service_id}&startTime={start_iso}&endTime={end_iso}"
        try:
            data = self._get(path)
        except Exception:  # noqa: BLE001
            return 0
        return _sum_metric_values(data)


def _sum_metric_values(data: Any) -> int:
    """Tolerantly extract numeric values from a Render metrics response and sum them."""
    total = 0
    if isinstance(data, dict):
        # {"data": [...]} or {"datapoints": [...]}
        for key in ("data", "datapoints", "values", "metrics"):
            if isinstance(data.get(key), list):
                total += _sum_metric_values(data[key])
        if "value" in data:
            try:
                total += int(float(data["value"]))
            except (TypeError, ValueError):
                pass
    elif isinstance(data, list):
        for item in data:
            total += _sum_metric_values(item)
    return total


# ---------------------------------------------------------------------------
# In-process cache for the Render section (10 minutes).
# ---------------------------------------------------------------------------

_RENDER_CACHE_TTL = 600.0  # seconds


class RenderCache:
    """Thread-safe in-process cache for the Render section. TTL is 10 minutes."""

    def __init__(self, ttl: float = _RENDER_CACHE_TTL, clock: Any = None) -> None:
        self._ttl = ttl
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._value: Any = None
        self._at: float | None = None

    def get(self, load: Any) -> Any:
        with self._lock:
            now = self._clock()
            if self._at is None or now - self._at >= self._ttl:
                self._value, self._at = load(), now
            return self._value

    def clear(self) -> None:
        with self._lock:
            self._value, self._at = None, None


# ---------------------------------------------------------------------------
# The read itself
# ---------------------------------------------------------------------------


def _month_window() -> tuple[str, str]:
    """Return (start, end) ISO 8601 strings for the current calendar month."""
    now = datetime.now(UTC)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat(), now.isoformat()


def read_supabase(cur: Any) -> dict[str, Any]:
    """Read Supabase usage over the read cursor; returns null sub-values on partial failure."""
    result: dict[str, Any] = {}
    # Database size.
    try:
        cur.execute(DATABASE_SIZE_SQL)
        result["database_bytes"] = int(cur.fetchone()[0])
    except Exception as exc:  # noqa: BLE001
        result["database_bytes"] = None
        result["database_bytes_error"] = type(exc).__name__  # the kind only: operators read this
    # Largest tables.
    try:
        cur.execute(LARGEST_TABLES_SQL)
        result["largest_tables"] = [
            {"table": row[0], "bytes": int(row[1])}
            for row in cur.fetchall()
        ]
    except Exception as exc:  # noqa: BLE001
        result["largest_tables"] = None
        result["largest_tables_error"] = type(exc).__name__  # the kind only: operators read this
    # Mail bytes from comms.message.
    try:
        cur.execute(MAIL_BYTES_SQL)
        row = cur.fetchone()
        result["mail_bytes"] = int(row[0]) if row else 0
        result["mail_count"] = int(row[1]) if row else 0
        result["mail_bytes_this_month"] = int(row[2]) if row else 0
    except Exception as exc:  # noqa: BLE001
        result["mail_bytes"] = None
        result["mail_count"] = None
        result["mail_bytes_this_month"] = None
        result["mail_bytes_error"] = type(exc).__name__  # the kind only: operators read this
    # Plan limits.
    sup = PLANS["supabase"]
    result["plan"] = sup["plan"]
    result["plan_monthly_usd"] = float(sup["monthly_usd"])
    result["database_gb_included"] = float(sup["database_gb_included"])
    result["file_storage_gb_included"] = float(sup["file_storage_gb_included"])
    return result


def fetch_render(client: RenderClient, cache: RenderCache) -> dict[str, Any]:
    """Fetch Render services and their bandwidth, cached for 10 minutes."""
    def _load() -> dict[str, Any]:
        start_iso, end_iso = _month_window()
        try:
            services_raw = client.list_services()
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "reason": f"Error al leer los servicios de Render ({type(exc).__name__})"}
        services = []
        total_bw_bytes = 0
        for svc in services_raw:
            svc_id = svc.get("id", "")
            name = svc.get("name", "")
            svc_type = svc.get("type", "")
            suspended = svc.get("suspended", "not_suspended") != "not_suspended"
            details = svc.get("serviceDetails") or {}
            plan_name = details.get("plan", "")
            price_usd = _plan_price_usd(svc_type, plan_name)
            bw_bytes = 0
            if svc_id and not suspended:
                try:
                    bw_bytes = client.bandwidth_bytes(svc_id, start_iso, end_iso)
                except Exception:  # noqa: BLE001
                    bw_bytes = 0
                total_bw_bytes += bw_bytes
            services.append({
                "id": svc_id,
                "name": name,
                "type": _RENDER_TYPE_LABEL.get(svc_type, svc_type),
                "plan": plan_name,
                "suspended": suspended,
                "price_usd": price_usd,
                "bandwidth_bytes_this_month": bw_bytes,
            })
        workspace = PLANS["render"]["workspace"]
        return {
            "available": True,
            "services": services,
            "workspace_plan": workspace["plan"],
            "workspace_monthly_usd": float(workspace["monthly_usd"]),
            "bandwidth_bytes_this_month": total_bw_bytes,
            "bandwidth_gb_included": float(workspace["outbound_bandwidth_gb_included"]),
            "bandwidth_overage_usd_per_100_gb": float(workspace["outbound_overage_usd_per_100_gb"]),
        }

    return cache.get(_load)


def compute_monthly_cost(render: dict[str, Any], supabase: dict[str, Any]) -> float:
    """Estimate total known monthly cost (USD): Supabase + non-suspended Render services."""
    total = float(supabase.get("plan_monthly_usd") or 0.0)
    if render.get("available"):
        for svc in render.get("services") or []:
            if not svc.get("suspended") and svc.get("price_usd") is not None:
                total += float(svc["price_usd"])
    return total


def read_infrastructure(cur: Any, render_client: RenderClient | None, render_cache: RenderCache) -> dict[str, Any]:
    """Build the full infrastructure response: supabase, render and computed totals."""
    supabase = read_supabase(cur)

    if render_client is not None:
        render = fetch_render(render_client, render_cache)
    else:
        render = {
            "available": False,
            "reason": "Falta la clave de la API de Render en el servicio",
        }

    monthly_cost = compute_monthly_cost(render, supabase)

    return {
        "supabase": supabase,
        "render": render,
        "monthly_cost_usd": monthly_cost,
        "plans_as_of": PLANS["_as_of"],
    }

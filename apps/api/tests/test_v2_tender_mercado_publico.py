import json
import logging
from urllib.error import HTTPError

from origenlab_api.v2.tenders.mercado_publico import fetch_tender_detail, resolve_ticket

CODE = "1234567-12-LE26"


def _payload(rut="61.111.111-8"):
    return {
        "Listado": [
            {
                "CodigoExterno": CODE,
                "Nombre": "Balanzas analíticas",
                "Estado": "Publicada",
                "FechaCierre": "2026-11-05T15:00:00",
                "Comprador": {"NombreOrganismo": "Hospital Ejemplo", "CodigoOrganismo": "7001", "RutUnidad": rut},
                "Items": {"Listado": [{"Correlativo": 1, "NombreProducto": "Balanza", "Cantidad": 2}]},
            }
        ]
    }


class _Resp:
    status = 200

    def __init__(self, body):
        self._b = body

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _open(payload):
    def f(request, timeout=None):
        return _Resp(json.dumps(payload).encode())

    return f


def test_detail_maps_buyer():
    d = fetch_tender_detail(CODE, ticket="t", urlopen_fn=_open(_payload()))
    assert (d.buyer_name, d.buyer_code, d.title) == ("Hospital Ejemplo", "7001", "Balanzas analíticas")
    assert d.buyer_rut == "61111111-8"
    assert d.items and d.head["Nombre"] == "Balanzas analíticas"


def test_invalid_rut_dropped():
    d = fetch_tender_detail(CODE, ticket="t", urlopen_fn=_open(_payload("61.111.111-1")))
    assert d.buyer_rut is None


def test_no_ticket_returns_none():
    assert fetch_tender_detail(CODE, ticket=None) is None


def test_empty_listado_returns_none():
    assert fetch_tender_detail(CODE, ticket="t", urlopen_fn=_open({"Listado": []})) is None


def test_http_error_returns_none_and_ticket_not_in_log(caplog):
    def failing(request, timeout=None):
        raise HTTPError(request.full_url, 500, "boom", {}, None)

    with caplog.at_level(logging.DEBUG):
        assert fetch_tender_detail(CODE, ticket="sekret-ticket", urlopen_fn=failing) is None
    assert "sekret-ticket" not in caplog.text


def test_429_retries_then_succeeds():
    calls = []

    def flaky(request, timeout=None):
        calls.append(1)
        if len(calls) < 3:
            raise HTTPError(request.full_url, 429, "slow", {}, None)
        return _Resp(json.dumps(_payload()).encode())

    d = fetch_tender_detail(CODE, ticket="t", urlopen_fn=flaky, sleep=lambda s: None)
    assert d is not None and len(calls) == 3


def test_resolve_ticket_env_fallback(monkeypatch):
    monkeypatch.setenv("CHILECOMPRA_API_TICKET", "envt")
    assert resolve_ticket(None) == "envt"
    assert resolve_ticket("cfg") == "cfg"
    monkeypatch.delenv("CHILECOMPRA_API_TICKET")
    assert resolve_ticket("  ") is None


def test_the_api_service_declares_the_ticket_as_a_render_secret() -> None:
    """The ticket is set on Render (`sync: false`), declared under the API service, never a value in the file."""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[3] / "render.yaml").read_text(encoding="utf-8")
    start = text.index("  - type: web\n    name: origenlab-api\n")
    end = text.find("\n  - type:", start + 1)
    service = text[start:end]
    assert "      - key: ORIGENLAB_V2_CHILECOMPRA_TICKET\n        sync: false\n" in service
    assert text.count("ORIGENLAB_V2_CHILECOMPRA_TICKET") == 1

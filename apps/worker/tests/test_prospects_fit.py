"""`call_fit`: the worker's own key, no SDK retries, and the usage recorded before the answer is
parsed. No network: the SDK module is a fake; no database: the connection records its statements."""

from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from origenlab_worker.prospects.fit import DEFAULT_MODEL, call_fit


class _Cursor:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, params=None):
        self.log.append(sql.split()[0].lower() + (" model_usage" if "model_usage" in sql else ""))

    def fetchone(self):
        return (0,)


class _Conn:
    def __init__(self):
        self.log = []

    @contextmanager
    def transaction(self):
        yield

    @contextmanager
    def cursor(self):
        yield _Cursor(self.log)


class _Unparsable:
    model = DEFAULT_MODEL
    usage = SimpleNamespace(input_tokens=500, output_tokens=50)

    @property
    def content(self):
        raise RuntimeError("the answer could not be read")


def _fake_sdk(monkeypatch, built, answer):
    fake = types.ModuleType("anthropic")

    def anthropic(**kw):
        built.append(kw)
        return SimpleNamespace(messages=SimpleNamespace(create=lambda **req: answer))

    fake.Anthropic = anthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake)


def test_the_call_uses_the_worker_key_without_sdk_retries_and_records_before_parsing(monkeypatch) -> None:
    built: list[dict] = []
    _fake_sdk(monkeypatch, built, _Unparsable())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ORIGENLAB_WORKER_ANTHROPIC_API_KEY", "worker-key-for-tests")
    conn = _Conn()
    with pytest.raises(RuntimeError, match="could not be read"):
        call_fit(conn, "Proyecto: ficticio", DEFAULT_MODEL, None)
    assert built == [{"api_key": "worker-key-for-tests", "max_retries": 0}]
    assert "insert model_usage" in conn.log  # paid, so recorded, although the answer never parsed


def test_an_explicit_key_wins_and_no_key_makes_no_call(monkeypatch) -> None:
    built: list[dict] = []
    answer = SimpleNamespace(model=DEFAULT_MODEL, usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                             content=[SimpleNamespace(text='{"fit": 80, "why_now": "x", "equipment": []}')])
    _fake_sdk(monkeypatch, built, answer)
    monkeypatch.delenv("ORIGENLAB_WORKER_ANTHROPIC_API_KEY", raising=False)
    assert call_fit(_Conn(), "x", DEFAULT_MODEL, None, api_key="explicit").fit == 80
    assert built[-1]["api_key"] == "explicit"
    with pytest.raises(RuntimeError, match="anthropic_api_key_missing"):
        call_fit(_Conn(), "x", DEFAULT_MODEL, None)
    assert len(built) == 1

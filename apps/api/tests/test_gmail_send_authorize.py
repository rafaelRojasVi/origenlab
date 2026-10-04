import importlib.util
import pathlib

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "gmail_send_authorize.py"
spec = importlib.util.spec_from_file_location("gmail_send_authorize", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_the_token_is_written_only_for_contacto() -> None:
    out = mod.token_payload("cid", "sec", "rt", "Contacto@OrigenLab.cl")
    assert out == {"client_id": "cid", "client_secret": "sec", "refresh_token": "rt", "address": "contacto@origenlab.cl"}
    with pytest.raises(SystemExit):
        mod.token_payload("cid", "sec", "rt", "otra@example.invalid")
    with pytest.raises(SystemExit):
        mod.token_payload("cid", "sec", None, "contacto@origenlab.cl")

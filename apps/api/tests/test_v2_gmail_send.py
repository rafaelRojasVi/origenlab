"""Gmail send client — no network: every transport below is a stub. Invented addresses only."""

from __future__ import annotations

import base64
import email
import json
from pathlib import Path

import pytest

from origenlab_api.v2.gmail_send import (
    SENDER_ADDRESS,
    GmailSendError,
    GmailSender,
    GmailSendToken,
    build_test_message,
    load_send_token,
    valid_test_address,
)

TOKEN = GmailSendToken(client_id="cid", client_secret="secret", refresh_token="rt", address=SENDER_ADDRESS)


def _write(tmp_path: Path, **over) -> str:
    data = {"client_id": "cid", "client_secret": "secret", "refresh_token": "rt", "address": SENDER_ADDRESS, **over}
    p = tmp_path / "token.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def test_token_file_loads(tmp_path: Path) -> None:
    assert load_send_token(_write(tmp_path)) == TOKEN


@pytest.mark.parametrize("over", [{"address": "otra@example.invalid"}, {"refresh_token": ""}, {"client_id": None}])
def test_token_file_is_refused_when_incomplete_or_for_another_account(tmp_path: Path, over: dict) -> None:
    with pytest.raises(ValueError):
        load_send_token(_write(tmp_path, **over))


def test_missing_token_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        load_send_token(str(tmp_path / "nope.json"))


@pytest.mark.parametrize("ok", ["ana@example.invalid", "Ana.Perez+test@sub.example.invalid"])
def test_a_plain_address_is_accepted(ok: str) -> None:
    assert valid_test_address(f"  {ok} ") == ok


@pytest.mark.parametrize(
    "bad",
    ['"Ana" <ana@example.invalid>', "a@example.invalid, b@example.invalid", "a@example.invalid\r\nBcc: z@example.invalid",
     "a@example.invalid;b@example.invalid", "sin-arroba", "a@b", "a @example.invalid", "x" * 250 + "@example.invalid", ""],
)
def test_anything_but_one_plain_address_is_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        valid_test_address(bad)


def test_the_message_is_the_stored_html_with_the_test_subject_and_headers() -> None:
    html = '<p>Hola — ñandú</p><img src="https://origenlab.cl/a.png">'
    msg = email.message_from_bytes(build_test_message(to="ana@example.invalid", subject="Cyber OrigenLab", html=html))
    assert msg["From"] == f"OrigenLab <{SENDER_ADDRESS}>"
    assert msg["To"] == "ana@example.invalid"
    assert str(email.header.make_header(email.header.decode_header(msg["Subject"]))) == "[PRUEBA] Cyber OrigenLab"
    assert msg["List-Unsubscribe"] == f"<mailto:{SENDER_ADDRESS}?subject=REMOVER>"
    assert msg["X-OrigenLab-Test-Send"] == "1"
    part = next(p for p in msg.walk() if p.get_content_type() == "text/html")
    assert part.get_payload(decode=True).decode(part.get_content_charset()).rstrip() == html


class _Transport:
    def __init__(self, *answers: tuple[int, dict]) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, str, dict, bytes]] = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body))
        status, payload = self.answers.pop(0)
        return status, json.dumps(payload).encode()


class _Clock:
    now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_send_refreshes_once_then_reuses_the_token() -> None:
    t = _Transport((200, {"access_token": "at1", "expires_in": 3600}), (200, {"id": "m1"}), (200, {"id": "m2"}))
    sender = GmailSender(TOKEN, transport=t, clock=_Clock())
    assert sender.send(b"raw1") == "m1"
    assert sender.send(b"raw2") == "m2"
    assert [c[1] for c in t.calls] == [
        "https://oauth2.googleapis.com/token",
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
    ]
    sent = json.loads(t.calls[1][3])
    assert base64.urlsafe_b64decode(sent["raw"]) == b"raw1"
    assert t.calls[1][2]["Authorization"] == "Bearer at1"


def test_send_refreshes_again_a_minute_before_expiry() -> None:
    clock = _Clock()
    t = _Transport((200, {"access_token": "at1", "expires_in": 120}), (200, {"id": "m1"}),
                   (200, {"access_token": "at2", "expires_in": 3600}), (200, {"id": "m2"}))
    sender = GmailSender(TOKEN, transport=t, clock=clock)
    sender.send(b"a")
    clock.now += 61
    sender.send(b"b")
    assert t.calls[3][2]["Authorization"] == "Bearer at2"


def test_a_revoked_permission_is_a_token_refresh_failure() -> None:
    t = _Transport((400, {"error": "invalid_grant"}))
    with pytest.raises(GmailSendError) as exc:
        GmailSender(TOKEN, transport=t, clock=_Clock()).send(b"x")
    assert exc.value.kind == "token_refresh_failed"


def test_gmail_refusing_the_message_is_a_rejection() -> None:
    t = _Transport((200, {"access_token": "at1", "expires_in": 3600}), (403, {"error": {"message": "quota"}}))
    with pytest.raises(GmailSendError) as exc:
        GmailSender(TOKEN, transport=t, clock=_Clock()).send(b"x")
    assert exc.value.kind == "gmail_rejected"


def test_a_network_failure_is_named_as_such() -> None:
    def broken(*_a, **_k):
        raise OSError("down")

    with pytest.raises(GmailSendError) as exc:
        GmailSender(TOKEN, transport=broken, clock=_Clock()).send(b"x")
    assert exc.value.kind == "network"

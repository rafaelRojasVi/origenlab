"""gmail_api_send_message says whether Gmail clearly refused. Never touches the network."""

from __future__ import annotations

import io
import urllib.error
from unittest.mock import patch

import pytest

from origenlab_email_pipeline.gmail_send import GmailSendError, gmail_api_send_message


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        code, "error", {}, io.BytesIO(b'{"error": "x"}'),
    )


@pytest.mark.parametrize(("code", "refused"), [(400, True), (403, True), (429, True), (500, False), (503, False)])
def test_http_error_carries_status(code: int, refused: bool) -> None:
    with patch("urllib.request.urlopen", side_effect=_http_error(code)):
        with pytest.raises(GmailSendError) as info:
            gmail_api_send_message(access_token="tok", raw_message_bytes=b"x")
    assert info.value.status == code
    assert info.value.definitely_not_sent is refused


@pytest.mark.parametrize(
    "error",
    [TimeoutError("The read operation timed out"), urllib.error.URLError("connection refused"),
     ConnectionResetError("reset")],
)
def test_no_answer_has_no_status(error: Exception) -> None:
    with patch("urllib.request.urlopen", side_effect=error):
        with pytest.raises(GmailSendError) as info:
            gmail_api_send_message(access_token="tok", raw_message_bytes=b"x")
    assert info.value.status is None
    assert info.value.definitely_not_sent is False

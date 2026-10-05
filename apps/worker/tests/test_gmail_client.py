"""Gmail read client — no network: every transport is a script of canned answers. Invented values."""

from __future__ import annotations

import base64
import http.client
import json

import pytest

from origenlab_worker import gmail_client
from origenlab_worker.gmail_client import (
    READONLY_SCOPE,
    GmailAuthError,
    GmailCredentials,
    GmailNotFound,
    GmailReader,
    GmailUnavailable,
    HistoryExpired,
)

CREDS = GmailCredentials(client_id="cid", client_secret="sec", refresh_token="rt")
FULL_SCOPE = "https://mail.google.com/"
TOKEN = "oauth2.googleapis.com/token"


class _Script:
    """Answers in order; each step names a fragment its URL must contain."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body))
        fragment, status, payload = self.steps.pop(0)
        assert fragment in url, f"expected {fragment!r} in {url!r}"
        return status, payload if isinstance(payload, bytes) else json.dumps(payload).encode()


def token(access="at1", scope=READONLY_SCOPE):
    return (TOKEN, 200, {"access_token": access, "expires_in": 3600, "scope": scope})


def reader(script) -> GmailReader:
    return GmailReader(CREDS, transport=script, clock=lambda: 1000.0, sleep=lambda s: None)


def page(*ids, next_token=None, history_id="105"):
    body = {"history": [{"messagesAdded": [{"message": {"id": i}} for i in ids]}], "historyId": history_id}
    if next_token:
        body["nextPageToken"] = next_token
    return body


def test_one_refresh_serves_every_request_and_the_profile_is_folded() -> None:
    s = _Script(token(), ("/profile", 200, {"emailAddress": "Contacto@OrigenLab.cl", "historyId": "4321"}),
                ("/profile", 200, {"emailAddress": "contacto@origenlab.cl", "historyId": "4322"}))
    r = reader(s)
    assert (r.profile().email_address, r.profile().history_id) == ("contacto@origenlab.cl", "4322")
    assert [c[1].split("?")[0].rsplit("/", 1)[-1] for c in s.calls] == ["token", "profile", "profile"]
    assert s.calls[1][2]["Authorization"] == "Bearer at1" and r.granted_scopes == (READONLY_SCOPE,)


@pytest.mark.parametrize("scope", [FULL_SCOPE, f"{READONLY_SCOPE} {FULL_SCOPE}", "", "https://www.googleapis.com/auth/gmail.send"])
def test_a_token_that_is_not_exactly_read_only_is_refused_before_any_request(scope) -> None:
    s = _Script(token(scope=scope))
    with pytest.raises(GmailAuthError) as exc:
        reader(s).profile()
    assert exc.value.kind == "scope_not_readonly" and len(s.calls) == 1


def test_a_revoked_consent_is_invalid_grant() -> None:
    with pytest.raises(GmailAuthError) as exc:
        reader(_Script((TOKEN, 400, {"error": "invalid_grant"}))).profile()
    assert exc.value.kind == "invalid_grant"


def test_history_reads_every_page_in_order_and_names_each_id_once() -> None:
    s = _Script(token(), ("startHistoryId=100", 200, page("a1", "a2", next_token="p2")),
                ("pageToken=p2", 200, page("a2", "a3", history_id="107")))
    window = reader(s).history("100")
    assert (window.message_ids, window.history_id) == (("a1", "a2", "a3"), "107")
    assert "historyTypes=messageAdded" in s.calls[1][1]


def test_history_with_nothing_new_still_returns_the_current_id() -> None:
    window = reader(_Script(token(), ("history", 200, {"historyId": "100"}))).history("100")
    assert (window.message_ids, window.history_id) == ((), "100")


@pytest.mark.parametrize("pages_before", [0, 1])
def test_an_expired_history_id_is_history_expired_even_on_a_later_page(pages_before) -> None:
    steps = [token()] + [("history", 200, page("a1", next_token="p2"))] * pages_before
    steps.append(("history", 404, {"error": {"code": 404}}))
    with pytest.raises(HistoryExpired):
        reader(_Script(*steps)).history("100")


def test_consent_revoked_between_pages_is_invalid_grant_after_one_refresh() -> None:
    """Review Focus 5, client half: the 401 earns exactly one refresh, which Google refuses."""
    s = _Script(token(), ("history", 200, page("a1", next_token="p2")), ("pageToken=p2", 401, {}),
                (TOKEN, 400, {"error": "invalid_grant"}))
    with pytest.raises(GmailAuthError) as exc:
        reader(s).history("100")
    assert exc.value.kind == "invalid_grant" and not s.steps


def test_an_expired_access_token_is_refreshed_and_the_request_repeated() -> None:
    s = _Script(token(access="at1"), ("profile", 401, {}), token(access="at2"),
                ("profile", 200, {"emailAddress": "contacto@origenlab.cl", "historyId": "1"}))
    reader(s).profile()
    assert s.calls[-1][2]["Authorization"] == "Bearer at2"


def test_a_second_401_after_a_fresh_token_is_unauthorized() -> None:
    s = _Script(token(), ("profile", 401, {}), token(access="at2"), ("profile", 401, {}))
    with pytest.raises(GmailAuthError) as exc:
        reader(s).profile()
    assert exc.value.kind == "unauthorized"


def test_a_busy_gmail_is_retried_then_reported() -> None:
    ok = _Script(token(), ("profile", 503, {}), ("profile", 429, {}),
                 ("profile", 200, {"emailAddress": "x@example.invalid", "historyId": "1"}))
    assert reader(ok).profile().history_id == "1"
    with pytest.raises(GmailUnavailable) as exc:
        reader(_Script(token(), *[("profile", 503, {})] * 4)).profile()
    assert exc.value.kind == "http_503"


def test_a_network_failure_is_named_as_such() -> None:
    def broken(*_args):
        raise OSError("down")

    with pytest.raises(GmailUnavailable) as exc:
        GmailReader(CREDS, transport=broken, sleep=lambda s: None).profile()
    assert exc.value.kind == "network"


def test_a_resync_lists_every_page_oldest_first() -> None:
    s = _Script(token(), ("q=after%3A1791201600", 200, {"messages": [{"id": "n3"}, {"id": "n2"}], "nextPageToken": "p2"}),
                ("pageToken=p2", 200, {"messages": [{"id": "n1"}]}))
    assert reader(s).messages_after(1791201600) == ["n1", "n2", "n3"]


def test_metadata_carries_labels_size_date_and_folded_header_names() -> None:
    s = _Script(token(), ("format=metadata", 200, {
        "id": "m1", "threadId": "t1", "labelIds": ["INBOX"], "sizeEstimate": 2048, "internalDate": "1791810000000",
        "payload": {"headers": [{"name": "Subject", "value": "Hola"}, {"name": "From", "value": "Ana <ana@cliente.invalid>"}]},
    }))
    meta = reader(s).metadata("m1")
    assert (meta.thread_id, meta.label_ids, meta.size_estimate, meta.internal_date_ms) == ("t1", ("INBOX",), 2048, 1791810000000)
    assert meta.headers == {"subject": "Hola", "from": "Ana <ana@cliente.invalid>"}
    assert "metadataHeaders=Message-ID" in s.calls[1][1]


def test_raw_is_decoded_from_unpadded_base64url() -> None:
    data = b"Subject: x\r\n\r\n\xff\xfe cuerpo"
    encoded = base64.urlsafe_b64encode(data).decode().rstrip("=")
    s = _Script(token(), ("format=raw", 200, {"id": "m1", "threadId": "t1", "labelIds": ["SENT"], "internalDate": "1", "raw": encoded}))
    assert reader(s).raw("m1").raw == data


def test_a_missing_message_is_not_found() -> None:
    with pytest.raises(GmailNotFound):
        reader(_Script(token(), ("messages/m9", 404, {}))).metadata("m9")


def test_labels_map_ids_to_names() -> None:
    s = _Script(token(), ("labels", 200, {"labels": [{"id": "INBOX", "name": "INBOX"}, {"id": "Label_7", "name": "Borradores 2026"}]}))
    assert reader(s).labels() == {"INBOX": "INBOX", "Label_7": "Borradores 2026"}


def _invalid(script, call) -> None:
    with pytest.raises(GmailUnavailable) as exc:
        call(reader(script))
    assert exc.value.kind == "invalid_json"
    assert "oauth" not in str(exc.value) and "abc" not in str(exc.value)


@pytest.mark.parametrize("body", [b"not json", b"[1]", b'"x"'])
def test_a_token_200_that_is_not_a_json_object_is_invalid_json(body) -> None:
    _invalid(_Script((TOKEN, 200, body)), lambda r: r.profile())


def test_a_non_numeric_expires_in_is_invalid_json() -> None:
    _invalid(_Script((TOKEN, 200, {"access_token": "at1", "expires_in": "abc", "scope": READONLY_SCOPE})),
             lambda r: r.profile())


@pytest.mark.parametrize("body", [b"not json", b"[1]"])
def test_a_malformed_top_level_body_is_invalid_json(body) -> None:
    _invalid(_Script(token(), ("profile", 200, body)), lambda r: r.profile())


@pytest.mark.parametrize("history", ["abc", [{"messagesAdded": "abc"}], [{"messagesAdded": ["abc"]}], ["abc"]])
def test_a_malformed_history_record_is_invalid_json(history) -> None:
    _invalid(_Script(token(), ("history", 200, {"history": history})), lambda r: r.history("100"))


@pytest.mark.parametrize("body", [{"messages": "abc"}, {"messages": ["abc"]}])
def test_a_malformed_list_is_invalid_json(body) -> None:
    _invalid(_Script(token(), ("messages", 200, body)), lambda r: r.messages_after(1))


@pytest.mark.parametrize("body", [
    {"id": "m1", "sizeEstimate": "abc"}, {"id": "m1", "internalDate": "abc"},
    {"id": "m1", "payload": "abc"}, {"id": "m1", "payload": {"headers": ["abc"]}},
    {"id": "m1", "labelIds": 5}, {"id": "m1", "labelIds": "INBOX"},
])
def test_a_malformed_metadata_is_invalid_json(body) -> None:
    _invalid(_Script(token(), ("format=metadata", 200, body)), lambda r: r.metadata("m1"))


@pytest.mark.parametrize("body", [{"id": "m1", "raw": "a"}, {"id": "m1", "internalDate": "abc", "raw": ""},
                                  {"id": "m1", "labelIds": 5}])
def test_a_malformed_raw_is_invalid_json(body) -> None:
    _invalid(_Script(token(), ("format=raw", 200, body)), lambda r: r.raw("m1"))


def test_a_malformed_label_list_is_invalid_json() -> None:
    _invalid(_Script(token(), ("labels", 200, {"labels": ["abc"]})), lambda r: r.labels())


@pytest.mark.parametrize("status", [429, 500, 503])
def test_a_token_endpoint_outage_is_unavailable_not_an_auth_error(status) -> None:
    with pytest.raises(GmailUnavailable) as exc:
        reader(_Script((TOKEN, status, {"error": "backend_error"}))).profile()
    assert exc.value.kind == f"http_{status}"


def test_a_token_400_that_is_not_invalid_grant_is_token_refresh_failed() -> None:
    with pytest.raises(GmailAuthError) as exc:
        reader(_Script((TOKEN, 400, {"error": "invalid_client"}))).profile()
    assert exc.value.kind == "token_refresh_failed"


def test_a_token_response_without_a_scope_is_refused() -> None:
    with pytest.raises(GmailAuthError) as exc:
        reader(_Script((TOKEN, 200, {"access_token": "at1", "expires_in": 3600}))).profile()
    assert exc.value.kind == "scope_not_readonly"


def test_a_401_on_the_last_attempt_still_earns_its_one_refresh() -> None:
    s = _Script(token(), ("profile", 503, {}), ("profile", 503, {}), ("profile", 503, {}), ("profile", 401, {}),
                token(access="at2"), ("profile", 200, {"emailAddress": "x@example.invalid", "historyId": "1"}))
    assert reader(s).profile().history_id == "1" and not s.steps
    assert s.calls[-1][2]["Authorization"] == "Bearer at2"


@pytest.mark.parametrize("error", [http.client.IncompleteRead(b""), TimeoutError("slow"), http.client.BadStatusLine("x")])
def test_http_exceptions_and_timeouts_are_network(error) -> None:
    def broken(*_args):
        raise error

    with pytest.raises(GmailUnavailable) as exc:
        GmailReader(CREDS, transport=broken, sleep=lambda s: None).profile()
    assert exc.value.kind == "network"


def test_a_page_token_that_never_ends_is_a_typed_error(monkeypatch) -> None:
    monkeypatch.setattr(gmail_client, "MAX_PAGES", 3)
    forever = [("history", 200, page("a1", next_token="p"))] * 5
    with pytest.raises(GmailUnavailable) as exc:
        reader(_Script(token(), *forever)).history("100")
    assert exc.value.kind == "too_many_pages"
    loop = [("messages", 200, {"messages": [{"id": "n"}], "nextPageToken": "p"})] * 5
    with pytest.raises(GmailUnavailable) as exc:
        reader(_Script(token(), *loop)).messages_after(1)
    assert exc.value.kind == "too_many_pages"

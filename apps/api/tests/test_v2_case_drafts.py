"""«Borrador en Gmail» (owner 2026-11-16): reply drafts waiting in contacto@ on a case's threads.
No network: the Gmail transport is a stub. Every id here is invented."""

from __future__ import annotations

import json

from origenlab_api.v2.case_drafts import DraftThreads, case_drafts, read_case_drafts
from origenlab_api.v2.gmail_send import SENDER_ADDRESS, GmailSendError, GmailSender, GmailSendToken

TOKEN = GmailSendToken(client_id="cid", client_secret="secret", refresh_token="rt", address=SENDER_ADDRESS)


def _sender(pages: list[dict], calls: list[str] | None = None) -> GmailSender:
    answers = iter(pages)

    def transport(method, url, headers, body, timeout):
        if "/token" in url:
            return 200, json.dumps({"access_token": "at", "expires_in": 3600}).encode()
        assert method == "GET" and "/drafts?" in url
        if calls is not None:
            calls.append(url)
        return 200, json.dumps(next(answers)).encode()

    return GmailSender(TOKEN, transport=transport, clock=lambda: 0.0)


def test_drafts_are_read_by_thread_across_pages() -> None:
    calls: list[str] = []
    sender = _sender([
        {"drafts": [{"id": "d1", "message": {"id": "m1", "threadId": "t1"}},
                    {"id": "d2", "message": {"id": "m2", "threadId": "t2"}}], "nextPageToken": "p2"},
        {"drafts": [{"id": "d3", "message": {"id": "m3", "threadId": "t1"}}]},
    ], calls)
    assert sender.list_draft_threads() == {"t1": ["m1", "m3"], "t2": ["m2"]}
    assert "pageToken=p2" in calls[1]


def test_an_empty_mailbox_has_no_drafts() -> None:
    assert _sender([{"resultSizeEstimate": 0}]).list_draft_threads() == {}


def test_a_rejection_is_an_error() -> None:
    def transport(method, url, headers, body, timeout):
        if "/token" in url:
            return 200, json.dumps({"access_token": "at", "expires_in": 3600}).encode()
        return 403, b'{"error": {"status": "PERMISSION_DENIED"}}'

    try:
        GmailSender(TOKEN, transport=transport, clock=lambda: 0.0).list_draft_threads()
    except GmailSendError as exc:
        assert exc.kind == "draft_list_failed"
    else:  # pragma: no cover
        raise AssertionError("expected GmailSendError")


class _Lister:
    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.calls = 0

    def list_draft_threads(self):
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_the_list_is_cached_and_a_failure_keeps_the_last_one() -> None:
    now = [0.0]
    lister = _Lister({"t1": ["m1"]}, GmailSendError("network", "down"), {"t2": ["m2"]})
    cache = DraftThreads(lister, clock=lambda: now[0], ttl=60)
    assert cache.get() == {"t1": ["m1"]}
    now[0] = 30
    assert cache.get() == {"t1": ["m1"]} and lister.calls == 1
    now[0] = 61
    assert cache.get() == {"t1": ["m1"]} and lister.calls == 2  # Gmail failed: the last list stands
    now[0] = 90
    assert cache.get() == {"t1": ["m1"]} and lister.calls == 2  # no retry before a full period
    now[0] = 122
    assert cache.get() == {"t2": ["m2"]}


def test_never_read_is_unavailable() -> None:
    cache = DraftThreads(_Lister(GmailSendError("network", "down")), clock=lambda: 0.0)
    assert cache.get() is None
    assert read_case_drafts(cache, None, "", 1000) == {"available": False, "items": {}}
    assert read_case_drafts(None, None, "", 1000) == {"available": False, "items": {}}


def test_drafts_count_per_case_on_any_of_its_threads() -> None:
    threads = {"t1": ["m1"], "t2": ["m2", "m3"], "t9": ["m9"]}
    rows = [{"opportunity_id": "c-1", "thread_id": "t1"}, {"opportunity_id": "c-1", "thread_id": "t2"},
            {"opportunity_id": "c-2", "thread_id": "t5"}]
    assert case_drafts(threads, rows) == {
        "c-1": {"drafts": 3, "url": "https://mail.google.com/mail/u/0/#all/t1"}}

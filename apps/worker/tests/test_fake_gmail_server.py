"""The fake Gmail server speaks the reader's six calls over real HTTP (no database needed)."""

from __future__ import annotations

import pytest

from fake_gmail_server import FakeGmailServer
from mailfixtures import INTERNAL_MS, make_raw
from origenlab_worker.gmail_client import GmailCredentials, GmailReader, HistoryExpired


def test_the_reader_pages_lists_and_decodes_against_the_fake() -> None:
    with FakeGmailServer() as server:
        state = server.state
        for n in range(1, 6):
            state.add(f"m{n}", make_raw(message_id=f"<m{n}@cliente.invalid>"), labels=("INBOX",),
                      internal_ms=INTERNAL_MS + n)
        state.list_again("m1")
        reader = GmailReader(GmailCredentials("cid", "sec", "rt"), api_base=server.api_base,
                             token_url=server.token_url, sleep=lambda _s: None)
        assert reader.profile().history_id == "106"
        window = reader.history("100")
        assert (window.message_ids, window.history_id) == (("m1", "m2", "m3", "m4", "m5"), "106")
        assert reader.messages_after(INTERNAL_MS // 1000 - 10) == ["m1", "m2", "m3", "m4", "m5"]
        meta = reader.metadata("m3")
        assert (meta.headers["message-id"], meta.label_ids) == ("<m3@cliente.invalid>", ("INBOX",))
        assert reader.raw("m3").raw == state.messages["m3"]["raw"]
        assert reader.labels()["INBOX"] == "INBOX"
        state.expired_below = 1000
        with pytest.raises(HistoryExpired):
            reader.history("100")

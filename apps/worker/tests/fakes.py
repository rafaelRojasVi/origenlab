"""In-memory stand-ins for the database, Gmail and Storage, for the run logic's tests.

Each one keeps the real component's contract — `record` is idempotent on the Gmail id, evidence on
its dedupe key, `put_if_absent` never replaces — so a test of the run is a test of the run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from mailfixtures import INTERNAL_MS, MAILBOX
from origenlab_worker.database import Mailbox, RecordOutcome
from origenlab_worker.gmail_client import (
    GmailAuthError,
    GmailNotFound,
    GmailUnavailable,
    HistoryExpired,
    HistoryWindow,
    MessageMeta,
    Profile,
    RawMessage,
)
from origenlab_worker.storage import StorageConflict, StorageTooLarge

LAST_SYNC = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


class FakeDb:
    def __init__(self, *, state: str = "authorized", history_id: str | None = "100",
                 last_synced_at: datetime | None = LAST_SYNC, locked_by_other: bool = False, no_mailbox: bool = False) -> None:
        self.box = Mailbox("mb-1", MAILBOX, state, history_id, last_synced_at)
        self.locked_by_other = locked_by_other
        self.no_mailbox = no_mailbox
        self.messages: dict[str, object] = {}
        self.evidence: set[str] = set()
        self.cursor: list[str] = []
        self.revoked = False
        self.authorized: list[tuple[str, list[str]]] = []
        self.fail_on_record: str | None = None

    def try_lock(self) -> bool:
        return not self.locked_by_other

    def mailbox(self, address: str) -> Mailbox | None:
        return self.box if address == self.box.address and not self.no_mailbox else None

    def authorize(self, mailbox_id, *, baseline_history_id, scopes):
        self.authorized.append((baseline_history_id, scopes))
        return "baseline" if self.box.history_id is None else "resumed"

    def mark_revoked(self, mailbox_id):
        self.revoked = True

    def advance_cursor(self, mailbox_id, history_id):
        self.cursor.append(history_id)

    def message_exists(self, mailbox_id, provider_message_id):
        return provider_message_id in self.messages

    def record(self, capture, *, mailbox_id, eml_path, eml_sha256, size_bytes):
        if self.fail_on_record == capture.provider_message_id:
            raise RuntimeError("ana@cliente.invalid broke the insert")
        if capture.provider_message_id in self.messages:
            return RecordOutcome(False, False)
        self.messages[capture.provider_message_id] = ("parsed", capture, eml_path, eml_sha256)
        key = f"gmail_message:{capture.provider_message_id}"
        created = not capture.is_bulk_send and key not in self.evidence
        if created:
            self.evidence.add(key)
        return RecordOutcome(True, created)

    def record_unparsed(self, row, *, mailbox_id, eml_path, eml_sha256, size_bytes):
        if row.provider_message_id in self.messages:
            return False
        self.messages[row.provider_message_id] = ("parse_failed", row, eml_path, eml_sha256)
        return True


@dataclass
class FakeMessage:
    raw: bytes
    labels: tuple[str, ...] = ("INBOX",)
    thread_id: str = "t-1"
    internal_ms: int = INTERNAL_MS
    size: int | None = None


@dataclass
class FakeGmail:
    email: str = MAILBOX
    current: str = "200"
    pages: list[list[str]] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    messages: dict[str, FakeMessage] = field(default_factory=dict)
    expire_on_page: int | None = None
    revoke_on_page: int | None = None
    revoke_on_metadata: str | None = None
    revoke_on_profile: bool = False
    auth_kind: str = "invalid_grant"
    after_error: bool = False
    calls: list[str] = field(default_factory=list)
    unavailable_raw: str | None = None
    after_calls: list[int] = field(default_factory=list)
    raw_calls: list[str] = field(default_factory=list)
    label_calls: int = 0
    window_id: str | None = None  # the historyId history() answers with, when it differs from `current`

    def profile(self) -> Profile:
        self.calls.append("profile")
        if self.revoke_on_profile:
            raise GmailAuthError(self.auth_kind)
        return Profile(self.email, self.current)

    def history(self, start_history_id: str) -> HistoryWindow:
        self.calls.append("history")
        ids: dict[str, None] = {}
        for number, page in enumerate(self.pages):
            if number == self.expire_on_page:
                raise HistoryExpired("history_expired")
            if number == self.revoke_on_page:
                raise GmailAuthError(self.auth_kind)
            ids.update(dict.fromkeys(page))
        return HistoryWindow(tuple(ids), self.window_id or self.current)

    def messages_after(self, epoch_seconds: int) -> list[str]:
        self.after_calls.append(epoch_seconds)
        if self.after_error:
            raise GmailUnavailable("http_503")
        self.current = "999"  # mail arriving while the list runs moves Gmail's history id on
        return list(self.after)

    def metadata(self, message_id: str) -> MessageMeta:
        if message_id == self.revoke_on_metadata:
            raise GmailAuthError(self.auth_kind)
        m = self.messages.get(message_id)
        if m is None:
            raise GmailNotFound("not_found")
        return MessageMeta(message_id, m.thread_id, m.labels, m.size or len(m.raw), m.internal_ms,
                           {"from": "Ana <ana@cliente.invalid>", "subject": "x"})

    def raw(self, message_id: str) -> RawMessage:
        self.raw_calls.append(message_id)
        if message_id == self.unavailable_raw:
            raise GmailUnavailable("http_503")
        m = self.messages[message_id]
        return RawMessage(message_id, m.thread_id, m.labels, m.internal_ms, m.raw)

    def labels(self) -> dict[str, str]:
        self.label_calls += 1
        return {"INBOX": "INBOX", "SENT": "SENT", "Label_7": "Borradores 2026"}


class FakeStore:
    def __init__(self, too_large: set[str] | None = None) -> None:
        self.objects: dict[str, bytes] = {}
        self.too_large = too_large or set()
        self.checked = 0

    def check(self) -> None:
        self.checked += 1

    def put_if_absent(self, key: str, raw: bytes) -> str:
        if any(key.endswith(f"/{gmail_id}.eml") for gmail_id in self.too_large):
            raise StorageTooLarge("storage_object_too_large")
        if key in self.objects:
            if self.objects[key] != raw:
                raise StorageConflict("storage_object_conflict")
            return "present"
        self.objects[key] = raw
        return "stored"

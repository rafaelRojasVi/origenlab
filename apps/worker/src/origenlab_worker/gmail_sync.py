"""One Gmail capture run (spec 2026-10-04 §4): lock → mailbox → history window (or resync) → each
message → cursor.

Idempotent by construction: every write is `ON CONFLICT DO NOTHING` on the table's own unique key,
each message commits alone, the `.eml` is uploaded before the row that names it, and the cursor
moves only after every message of the window committed. A crash re-reads the same window on the
next run, and what already committed is counted as a duplicate.

`RunReport.mode` is one of: `locked`, `no_mailbox`, `paused`, `not_authorized`, `wrong_account`,
`history`, `resync`, `init_baseline`, `init_resumed`, `already_initialized`, plus a `_dry_run`
suffix on `history`, `resync` and `init` (`init_dry_run`). A run that fails before its mode is
known reports where it stopped: `start` (lock or mailbox read), `profile` (Gmail's account check)
or `init` (the baseline).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

import psycopg

from origenlab_worker.capture import (
    Capture,
    ParseFailed,
    UnparsedMessage,
    build_capture,
    intake_skip_reason,
    unparsed_message,
)
from origenlab_worker.database import LOCK_ACQUIRED, LOCK_STUCK, Mailbox, RecordOutcome
from origenlab_worker.gmail_client import (
    READONLY_SCOPE,
    GmailAuthError,
    GmailError,
    GmailNotFound,
    HistoryExpired,
    HistoryWindow,
    MessageMeta,
    Profile,
    RawMessage,
)
from origenlab_worker.storage import BUCKET, StorageError, StorageTooLarge, eml_key

MAILBOX_ADDRESS = "contacto@origenlab.cl"
RESYNC_OVERLAP = timedelta(days=1)
#: Gmail accepts incoming messages up to 50 MB; nothing genuine is refused, a pathological size is.
MAX_RAW_BYTES = 50 * 1024 * 1024

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_AUTH = 2
EXIT_CONFIG = 3


class SyncDb(Protocol):
    def try_lock(self) -> str: ...  # LOCK_ACQUIRED, LOCK_HELD or LOCK_STUCK
    def mailbox(self, address: str) -> Mailbox | None: ...
    def authorize(self, mailbox_id: str, *, baseline_history_id: str, scopes: list[str]) -> str: ...
    def mark_revoked(self, mailbox_id: str) -> None: ...
    def advance_cursor(self, mailbox_id: str, history_id: str) -> None: ...
    def message_exists(self, mailbox_id: str, provider_message_id: str) -> bool: ...
    def record(
        self, capture: Capture, *, mailbox_id: str, eml_path: str, eml_sha256: str, size_bytes: int
    ) -> RecordOutcome: ...
    def record_unparsed(
        self, row: UnparsedMessage, *, mailbox_id: str, eml_path: str | None,
        eml_sha256: str | None, size_bytes: int | None,
    ) -> bool: ...


class GmailSource(Protocol):
    def profile(self) -> Profile: ...
    def history(self, start_history_id: str) -> HistoryWindow: ...
    def messages_after(self, epoch_seconds: int) -> list[str]: ...
    def metadata(self, message_id: str) -> MessageMeta: ...
    def raw(self, message_id: str) -> RawMessage: ...
    def labels(self) -> dict[str, str]: ...


class EmlStore(Protocol):
    def check(self) -> None: ...
    def put_if_absent(self, key: str, raw: bytes) -> str: ...


@dataclass
class RunCounts:
    """`bulk_sends` is a subset of `stored`. A `too_large` message is kept as `parse_failed` in the
    database but counted only in `too_large`, not in `parse_failed`. In a dry run `stored` means
    "would store" and `evidence` stays 0."""

    seen: int = 0
    stored: int = 0
    evidence: int = 0
    bulk_sends: int = 0
    duplicates: int = 0
    skipped_draft: int = 0
    skipped_spam: int = 0
    skipped_trash: int = 0
    gone: int = 0
    too_large: int = 0
    parse_failed: int = 0


@dataclass(frozen=True)
class RunReport:
    mode: str
    exit_code: int
    counts: RunCounts
    error: str | None = None

    def as_log(self) -> dict[str, object]:
        """Counts, mode and an error *class* — never a subject, body, address or Gmail id."""
        return {"event": "gmail_sync", "mode": self.mode, "exit": self.exit_code,
                "error": self.error, **asdict(self.counts)}


def failure_code(exc: BaseException) -> str:
    """What the log line says about a failed run — never `str(exc)`, which may carry an address.

    `StorageError.code` and `GmailError.kind` are fixed identifiers by construction. A database
    error is its class plus the SQLSTATE (`UniqueViolation:23505`), a five-character code Postgres
    defines. Anything else is its class name only.
    """
    if isinstance(exc, StorageError):
        return exc.code
    if isinstance(exc, GmailError):
        return exc.kind
    if isinstance(exc, psycopg.Error):
        state = exc.sqlstate
        return f"{type(exc).__name__}:{state}" if state else type(exc).__name__
    return type(exc).__name__


def _label(mode: str, dry_run: bool) -> str:
    return f"{mode}_dry_run" if dry_run and mode in ("history", "resync", "init") else mode


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _LabelNames:
    """`users.labels.list`, at most once per run and only when a message is kept."""

    def __init__(self, gmail: GmailSource) -> None:
        self._gmail = gmail
        self._names: dict[str, str] | None = None

    def get(self) -> dict[str, str]:
        if self._names is None:
            self._names = self._gmail.labels()
        return self._names


def run_gmail_sync(
    *,
    db: SyncDb,
    gmail: GmailSource,
    store: EmlStore,
    enabled: bool,
    init: bool = False,
    dry_run: bool = False,
    now: Callable[[], datetime] = _utcnow,
) -> RunReport:
    counts = RunCounts()
    mode = "start"
    mailbox: Mailbox | None = None
    try:
        lock = db.try_lock()
        if lock == LOCK_STUCK:  # a holder that will never let go: fail loudly, not `locked` forever
            return RunReport("locked", EXIT_FAILED, counts, error="locked_by_stuck_session")
        if lock != LOCK_ACQUIRED:
            return RunReport("locked", EXIT_OK, counts)
        mailbox = db.mailbox(MAILBOX_ADDRESS)
        if mailbox is None:
            return RunReport("no_mailbox", EXIT_CONFIG, counts)
        if init:
            mode = "init"
            return _init(db, gmail, store, mailbox, dry_run, counts)
        if not enabled:
            return RunReport("paused", EXIT_OK, counts)
        if mailbox.authorization_state != "authorized":
            return RunReport("not_authorized", EXIT_OK, counts)
        mode = "profile"
        profile = gmail.profile()
        if profile.email_address != mailbox.address:
            return RunReport("wrong_account", EXIT_AUTH, counts)
        message_ids: list[str] | None = None
        # The new cursor is the profile read *before* any list, in history mode too: mail added while
        # the history pages are read is replayed next run and refused as a duplicate, never skipped.
        cursor = profile.history_id
        if mailbox.history_id is not None:
            mode = "history"
            try:
                window = gmail.history(mailbox.history_id)
                message_ids = list(window.message_ids)
            except HistoryExpired:
                pass
        if message_ids is None:
            mode = "resync"
            if mailbox.last_synced_at is None:
                # Resyncing from "now" would skip older mail silently; stopping is the safe answer.
                return RunReport(_label(mode, dry_run), EXIT_CONFIG, counts, error="no_resync_baseline")
            since = mailbox.last_synced_at - RESYNC_OVERLAP
            # The new baseline is the profile read *before* the list: mail that arrives while the
            # list runs is replayed from it next run and refused as a duplicate — never skipped.
            message_ids = gmail.messages_after(int(since.timestamp()))
        if not cursor:
            return RunReport(_label(mode, dry_run), EXIT_FAILED, counts, error="empty_history_id")
        names = _LabelNames(gmail)
        for message_id in message_ids:
            _capture_one(message_id, db=db, gmail=gmail, store=store, mailbox=mailbox,
                         names=names, dry_run=dry_run, counts=counts)
        if dry_run:
            return RunReport(_label(mode, dry_run), EXIT_OK, counts)
        db.advance_cursor(mailbox.id, cursor)
        return RunReport(mode, EXIT_OK, counts)
    except GmailAuthError as exc:
        error = exc.kind
        # Only consent that was live can be revoked; a bad token at `--init` revokes nothing.
        if (exc.kind == "invalid_grant" and mailbox is not None and not dry_run
                and mailbox.authorization_state == "authorized"):
            try:
                db.mark_revoked(mailbox.id)
            except Exception as db_exc:  # noqa: BLE001 — the class only, as below
                error = f"{exc.kind}+{type(db_exc).__name__}"
        return RunReport(_label(mode, dry_run), EXIT_AUTH, counts, error=error)
    except Exception as exc:  # noqa: BLE001 — a code or a class, never the text (it may carry an address)
        return RunReport(_label(mode, dry_run), EXIT_FAILED, counts, error=failure_code(exc))


def _init(db: SyncDb, gmail: GmailSource, store: EmlStore, mailbox: Mailbox, dry_run: bool,
          counts: RunCounts) -> RunReport:
    """Owner-run, once (spec §4): the baseline, nothing else. Refuses a mailbox already authorized,
    so a forgotten `--init` in the cron command fails every run loudly instead of syncing nothing."""
    if mailbox.authorization_state == "authorized":
        return RunReport("already_initialized", EXIT_CONFIG, counts)
    profile = gmail.profile()
    if profile.email_address != mailbox.address:
        return RunReport("wrong_account", EXIT_AUTH, counts)
    store.check()
    if dry_run:
        return RunReport("init_dry_run", EXIT_OK, counts)
    outcome = db.authorize(mailbox.id, baseline_history_id=profile.history_id, scopes=[READONLY_SCOPE])
    return RunReport(f"init_{outcome}", EXIT_OK, counts)


def _count_unparsed(counts: RunCounts, reason: str) -> None:
    if reason == "too_large":
        counts.too_large += 1
    else:
        counts.parse_failed += 1


def _capture_one(message_id: str, *, db: SyncDb, gmail: GmailSource, store: EmlStore,
                 mailbox: Mailbox, names: _LabelNames, dry_run: bool, counts: RunCounts) -> None:
    counts.seen += 1
    if db.message_exists(mailbox.id, message_id):
        counts.duplicates += 1
        return
    try:
        meta = gmail.metadata(message_id)
    except GmailNotFound:  # a draft saved over, or a message deleted before this run reached it
        counts.gone += 1
        return
    skip = intake_skip_reason(meta.label_ids)
    if skip is not None:
        setattr(counts, f"skipped_{skip}", getattr(counts, f"skipped_{skip}") + 1)
        return

    def unparsed(reason: str, eml_path: str | None, digest: str | None, size: int) -> None:
        """Kept as `parse_failed` with its reason, `.eml` when there is one, and no evidence."""
        if not dry_run:
            row = unparsed_message(
                gmail_message_id=message_id, gmail_thread_id=meta.thread_id,
                label_ids=meta.label_ids, label_names=names.get(),
                internal_date_ms=meta.internal_date_ms, headers=meta.headers,
                mailbox_address=mailbox.address, reason=reason,
            )
            if not db.record_unparsed(row, mailbox_id=mailbox.id, eml_path=eml_path,
                                      eml_sha256=digest, size_bytes=size):
                counts.duplicates += 1
                return
        _count_unparsed(counts, reason)

    if meta.size_estimate > MAX_RAW_BYTES:
        unparsed("too_large", None, None, meta.size_estimate)
        return
    try:
        raw = gmail.raw(message_id)
    except GmailNotFound:
        counts.gone += 1
        return
    if len(raw.raw) > MAX_RAW_BYTES:
        # The cap is enforced on the real bytes: Gmail's estimate can be low. Kept without a body.
        unparsed("too_large", None, None, len(raw.raw))
        return
    digest = hashlib.sha256(raw.raw).hexdigest()
    label_ids = raw.label_ids or meta.label_ids
    try:
        capture: Capture | None = build_capture(
            raw.raw, gmail_message_id=message_id, gmail_thread_id=raw.thread_id or meta.thread_id,
            label_ids=label_ids, label_names=names.get(),
            internal_date_ms=raw.internal_date_ms or meta.internal_date_ms,
            mailbox_address=mailbox.address,
        )
        reason = ""
    except ParseFailed as exc:
        capture, reason = None, exc.code
    if dry_run:
        if capture is None:
            _count_unparsed(counts, reason)
        else:
            counts.stored += 1
            counts.bulk_sends += int(capture.is_bulk_send)
        return
    key = eml_key(mailbox.address, meta.internal_date_ms, message_id)
    try:
        store.put_if_absent(key, raw.raw)
    except StorageTooLarge:
        # The message is at or under the cap (checked on its real bytes above), so Storage's limit is wrong:
        # a bucket limit set too low must fail loudly and hold the cursor, not strip evidence.
        raise StorageError("storage_too_large_below_cap") from None
    eml_path = f"{BUCKET}/{key}"
    if capture is None:
        unparsed(reason, eml_path, digest, len(raw.raw))
        return
    outcome = db.record(capture, mailbox_id=mailbox.id, eml_path=eml_path, eml_sha256=digest,
                        size_bytes=len(raw.raw))
    if not outcome.new_message:
        counts.duplicates += 1
        return
    counts.stored += 1
    counts.bulk_sends += int(capture.is_bulk_send)
    counts.evidence += int(outcome.evidence_created)

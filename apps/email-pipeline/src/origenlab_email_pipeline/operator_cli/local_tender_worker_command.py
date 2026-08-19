"""CLI adapter for the OriginLab local tender-processing worker."""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path

from origenlab_email_pipeline.operator_cli.local_tender_worker import (
    DEFAULT_STATE_DIR,
    LocalTenderWorkerError,
    LocalTenderWorkerHttpError,
    find_pending_job,
    load_or_build_outbox,
    mark_job_done,
    post_structured_local_import,
    resolve_api_base_url,
    resolve_api_token,
    zip_is_settled,
)

MAX_RETRY_BACKOFF_SECONDS = 60.0


@dataclass(frozen=True)
class LocalTenderWorkerOptions:
    downloads_dir: Path
    state_dir: Path
    once: bool = False
    watch: bool = False
    apply: bool = False
    poll_seconds: float = 5.0
    settle_seconds: float = 2.0


def parse_local_tender_worker_args(
    argv: list[str],
) -> LocalTenderWorkerOptions:
    parser = argparse.ArgumentParser(
        prog="local-tender-worker",
        description=(
            "Watch locally downloaded Mercado Público tender ZIPs, run "
            "extraction/OCR/T1 on this workstation, and publish only the "
            "structured result to OriginLab."
        ),
    )

    parser.add_argument(
        "--downloads-dir",
        type=Path,
        required=True,
        help="Directory containing OriginLab tender tickets and Licitaciones_*.zip",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=DEFAULT_STATE_DIR,
        help="Local outbox/done state directory",
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--once",
        action="store_true",
        help="Evaluate at most one pending job, then exit",
    )
    mode.add_argument(
        "--watch",
        action="store_true",
        help="Keep watching for tender tickets and downloaded ZIPs",
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Actually process the ZIP locally and publish structured JSON. "
            "Without this flag the command is read-only."
        ),
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=5.0,
        help="Watcher polling interval",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=2.0,
        help="Seconds a ZIP must remain unchanged before processing",
    )

    ns = parser.parse_args(argv)

    if ns.poll_seconds <= 0:
        parser.error("--poll-seconds must be > 0")
    if ns.settle_seconds < 0:
        parser.error("--settle-seconds must be >= 0")

    return LocalTenderWorkerOptions(
        downloads_dir=ns.downloads_dir.expanduser(),
        state_dir=ns.state_dir.expanduser(),
        once=ns.once,
        watch=ns.watch,
        apply=ns.apply,
        poll_seconds=ns.poll_seconds,
        settle_seconds=ns.settle_seconds,
    )


def _run_one(options: LocalTenderWorkerOptions) -> int:
    job = find_pending_job(
        options.downloads_dir,
        options.state_dir,
    )

    if job is None:
        print("local-tender-worker: no pending job")
        return 0

    print(
        "local-tender-worker: "
        f"ticket={job.ticket.ticket_id} "
        f"tender={job.ticket.tender_code} "
        f"zip={job.zip_path.name}"
    )

    if not zip_is_settled(
        job.zip_path,
        settle_seconds=options.settle_seconds,
    ):
        print("local-tender-worker: ZIP is not settled yet")
        return 0

    if not options.apply:
        print("local-tender-worker: dry-run only; use --apply to process and publish")
        return 0

    # Fail before expensive extraction/OCR/T1 if the outbound API
    # configuration is not usable.
    api_base_url = resolve_api_base_url()
    api_token = resolve_api_token()

    # This is the expensive workstation-only section. If an outbox file
    # already exists, load_or_build_outbox returns it instead of rerunning
    # extraction/OCR/T1.
    payload = load_or_build_outbox(
        job,
        options.state_dir,
    )

    print(
        "local-tender-worker: local processing complete; publishing structured result"
    )

    result = post_structured_local_import(
        api_base_url=api_base_url,
        api_token=api_token,
        tender_code=job.ticket.tender_code,
        payload=payload,
    )

    mark_job_done(
        job,
        options.state_dir,
        payload,
    )

    print(
        "local-tender-worker: complete "
        f"tender={job.ticket.tender_code} "
        f"persisted={result.get('persisted') is True}"
    )
    return 0


def run_local_tender_worker(
    options: LocalTenderWorkerOptions,
) -> int:
    if options.once:
        try:
            return _run_one(options)
        except LocalTenderWorkerError as exc:
            print(f"local-tender-worker: ERROR: {exc}")
            return 2

    print(
        "local-tender-worker: watching "
        f"{options.downloads_dir} "
        f"every {options.poll_seconds:g}s"
    )

    retry_backoff_seconds = min(
        options.poll_seconds,
        MAX_RETRY_BACKOFF_SECONDS,
    )

    while True:
        sleep_seconds = options.poll_seconds

        try:
            _run_one(options)

            # A healthy iteration resets any prior transient-HTTP backoff.
            retry_backoff_seconds = min(
                options.poll_seconds,
                MAX_RETRY_BACKOFF_SECONDS,
            )
        except KeyboardInterrupt:
            print("local-tender-worker: stopped")
            return 0
        except LocalTenderWorkerHttpError as exc:
            # The structured outbox is deliberately retained. Only transient
            # HTTP/network failures are retried; they therefore reuse the
            # existing outbox instead of rerunning extraction/OCR/T1.
            if not exc.retryable:
                print(f"local-tender-worker: API error (non-retryable): {exc}")
                return 2

            print(f"local-tender-worker: API error (retryable): {exc}")
            sleep_seconds = retry_backoff_seconds
            retry_backoff_seconds = min(
                retry_backoff_seconds * 2,
                MAX_RETRY_BACKOFF_SECONDS,
            )
        except LocalTenderWorkerError as exc:
            # Pairing ambiguity, configuration errors, ZIP failures, and
            # deterministic local extraction/OCR/T1 failures require operator
            # intervention. Retrying them automatically could repeat expensive
            # work forever or associate the wrong dossier.
            print(f"local-tender-worker: ERROR: {exc}")
            return 2

        try:
            time.sleep(sleep_seconds)
        except KeyboardInterrupt:
            print("local-tender-worker: stopped")
            return 0


def print_local_tender_worker_help() -> None:
    print(
        "local-tender-worker — process Mercado Público tender dossiers locally\n\n"
        "Example:\n"
        "  uv run origenlab local-tender-worker "
        "--downloads-dir /mnt/c/Users/Rafael/Downloads "
        "--watch --apply\n\n"
        "The worker watches for an OriginLab tender ticket followed by a "
        "Licitaciones_*.zip download. ZIP validation, extraction, OCR, and T1 "
        "run on this workstation. Only structured JSON is sent to the "
        "OriginLab API; the raw ZIP is never uploaded by this workflow."
    )


__all__ = [
    "LocalTenderWorkerOptions",
    "parse_local_tender_worker_args",
    "print_local_tender_worker_help",
    "run_local_tender_worker",
]

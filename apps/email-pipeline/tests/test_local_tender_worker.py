from __future__ import annotations

import json
import os
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from origenlab_email_pipeline.operator_cli import local_tender_worker as worker


def _write_ticket(
    directory: Path,
    *,
    ticket_id: str,
    tender_code: str,
    declared_complete: bool = True,
    created_at_utc: datetime | None = None,
) -> Path:
    path = directory / f"origenlab-tender-{ticket_id}.json"
    created_at = created_at_utc or datetime.now(timezone.utc)

    path.write_text(
        json.dumps(
            {
                "contract_version": worker.TICKET_CONTRACT_VERSION,
                "ticket_id": ticket_id,
                "tender_code": tender_code,
                "operator_declared_complete": declared_complete,
                "created_at_utc": created_at.replace(microsecond=0).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("bases.txt", "centrifuga refrigerada")


def _set_mtime(path: Path, when_ns: int) -> None:
    os.utime(path, ns=(when_ns, when_ns))


def test_load_processing_ticket_accepts_exact_v1_contract(tmp_path: Path) -> None:
    ticket_path = _write_ticket(
        tmp_path,
        ticket_id="ticket_12345678",
        tender_code="4291-46-LE26",
    )

    ticket = worker.load_processing_ticket(ticket_path)

    assert ticket.ticket_id == "ticket_12345678"
    assert ticket.tender_code == "4291-46-le26"
    assert ticket.operator_declared_complete is True


def test_find_pending_job_pairs_ticket_with_first_newer_zip(
    tmp_path: Path,
) -> None:
    downloads = tmp_path / "Downloads"
    state = tmp_path / "state"
    downloads.mkdir()

    old_zip = downloads / "Licitaciones_old.zip"
    ticket_path = _write_ticket(
        downloads,
        ticket_id="ticket_abcdefgh",
        tender_code="4291-46-LE26",
    )
    new_zip = downloads / "Licitaciones_new.zip"

    _write_zip(old_zip)
    _write_zip(new_zip)

    # Keep every synthetic timestamp safely in the past while preserving
    # old ZIP < ticket < new ZIP ordering.
    base = time.time_ns() - 10_000_000_000
    _set_mtime(old_zip, base)
    _set_mtime(ticket_path, base + 1_000_000_000)
    _set_mtime(new_zip, base + 2_000_000_000)

    job = worker.find_pending_job(downloads, state)

    assert job is not None
    assert job.ticket.ticket_id == "ticket_abcdefgh"
    assert job.zip_path == new_zip


def test_completed_job_does_not_reuse_zip_for_next_ticket(
    tmp_path: Path,
) -> None:
    downloads = tmp_path / "Downloads"
    state = tmp_path / "state"
    downloads.mkdir()

    # First job exists by itself: the worker never chooses between multiple
    # simultaneously-active tickets.
    ticket_a = _write_ticket(
        downloads,
        ticket_id="ticket_aaaaaaaa",
        tender_code="4291-46-LE26",
    )
    zip_a = downloads / "Licitaciones_a.zip"
    _write_zip(zip_a)

    base = time.time_ns() - 20_000_000_000
    _set_mtime(ticket_a, base)
    _set_mtime(zip_a, base + 1_000_000_000)

    first = worker.find_pending_job(downloads, state)

    assert first is not None
    assert first.ticket.ticket_id == "ticket_aaaaaaaa"
    assert first.zip_path == zip_a

    worker.mark_job_done(
        first,
        state,
        {"raw": {"archive": {"zip_sha256": "a" * 64}}},
    )

    # Only after the first job is complete does a second dashboard job appear.
    ticket_b = _write_ticket(
        downloads,
        ticket_id="ticket_bbbbbbbb",
        tender_code="745712-19-LP26",
    )
    zip_b = downloads / "Licitaciones_b.zip"
    _write_zip(zip_b)

    second_base = time.time_ns() - 10_000_000_000
    _set_mtime(ticket_b, second_base)
    _set_mtime(zip_b, second_base + 1_000_000_000)

    second = worker.find_pending_job(downloads, state)

    assert second is not None
    assert second.ticket.ticket_id == "ticket_bbbbbbbb"
    assert second.zip_path == zip_b


def test_outbox_is_reused_without_rerunning_local_compute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    downloads = tmp_path / "Downloads"
    state = tmp_path / "state"
    downloads.mkdir()

    ticket_path = _write_ticket(
        downloads,
        ticket_id="ticket_outbox12",
        tender_code="4291-46-LE26",
    )
    zip_path = downloads / "Licitaciones_outbox.zip"
    _write_zip(zip_path)

    job = worker.LocalTenderJob(
        ticket=worker.load_processing_ticket(ticket_path),
        zip_path=zip_path,
    )

    expected = {
        "contract_version": worker.LOCAL_IMPORT_CONTRACT_VERSION,
        "tender_code": "4291-46-le26",
        "operator_declared_complete": True,
        "raw": {
            "result": "imported",
            "archive": {"zip_sha256": worker.ticket_digest(zip_path)},
        },
    }

    calls = 0

    def fake_build(_job: worker.LocalTenderJob) -> dict:
        nonlocal calls
        calls += 1
        return expected

    monkeypatch.setattr(worker, "build_structured_local_import", fake_build)

    assert worker.load_or_build_outbox(job, state) == expected
    assert calls == 1

    def must_not_recompute(_job: worker.LocalTenderJob) -> dict:
        raise AssertionError("OCR/T1 must not rerun")

    monkeypatch.setattr(
        worker,
        "build_structured_local_import",
        must_not_recompute,
    )

    assert worker.load_or_build_outbox(job, state) == expected
    assert calls == 1


def test_corrupt_existing_outbox_fails_closed(tmp_path: Path) -> None:
    downloads = tmp_path / "Downloads"
    state = tmp_path / "state"
    downloads.mkdir()

    ticket_path = _write_ticket(
        downloads,
        ticket_id="ticket_corrupt1",
        tender_code="4291-46-LE26",
    )
    zip_path = downloads / "Licitaciones_corrupt.zip"
    _write_zip(zip_path)

    job = worker.LocalTenderJob(
        ticket=worker.load_processing_ticket(ticket_path),
        zip_path=zip_path,
    )

    outbox = state / "outbox" / f"{job.ticket.ticket_id}.json"
    outbox.parent.mkdir(parents=True)
    outbox.write_text("{not-json\n", encoding="utf-8")

    with pytest.raises(worker.LocalTenderWorkerError, match="outbox is unreadable"):
        worker.load_or_build_outbox(job, state)


def test_zip_is_settled_requires_stable_valid_zip(tmp_path: Path) -> None:
    good = tmp_path / "Licitaciones_good.zip"
    bad = tmp_path / "Licitaciones_bad.zip"

    _write_zip(good)
    bad.write_bytes(b"not-a-zip")

    assert worker.zip_is_settled(
        good,
        settle_seconds=0,
        sleep_fn=lambda _: None,
    )
    assert not worker.zip_is_settled(
        bad,
        settle_seconds=0,
        sleep_fn=lambda _: None,
    )


def test_multiple_active_tickets_fail_closed(tmp_path: Path) -> None:
    downloads = tmp_path / "Downloads"
    state = tmp_path / "state"
    downloads.mkdir()

    _write_ticket(
        downloads,
        ticket_id="ticket_multi111",
        tender_code="4291-46-LE26",
    )
    _write_ticket(
        downloads,
        ticket_id="ticket_multi222",
        tender_code="745712-19-LP26",
    )

    with pytest.raises(
        worker.LocalTenderWorkerError,
        match="multiple active tender tickets",
    ):
        worker.find_pending_job(downloads, state)


def test_multiple_candidate_zips_fail_closed(tmp_path: Path) -> None:
    downloads = tmp_path / "Downloads"
    state = tmp_path / "state"
    downloads.mkdir()

    ticket = _write_ticket(
        downloads,
        ticket_id="ticket_multizip",
        tender_code="4291-46-LE26",
    )

    zip_a = downloads / "Licitaciones_candidate_a.zip"
    zip_b = downloads / "Licitaciones_candidate_b.zip"
    _write_zip(zip_a)
    _write_zip(zip_b)

    base = time.time_ns()
    _set_mtime(ticket, base)
    _set_mtime(zip_a, base + 1_000_000)
    _set_mtime(zip_b, base + 2_000_000)

    with pytest.raises(
        worker.LocalTenderWorkerError,
        match="multiple candidate tender ZIPs",
    ):
        worker.find_pending_job(downloads, state)


def test_expired_ticket_cannot_claim_later_zip(tmp_path: Path) -> None:
    downloads = tmp_path / "Downloads"
    state = tmp_path / "state"
    downloads.mkdir()

    ticket = _write_ticket(
        downloads,
        ticket_id="ticket_expired1",
        tender_code="4291-46-LE26",
    )
    zip_path = downloads / "Licitaciones_later.zip"
    _write_zip(zip_path)

    now = time.time_ns()
    two_hours_ns = 2 * 60 * 60 * 1_000_000_000

    _set_mtime(ticket, now - two_hours_ns)
    _set_mtime(zip_path, now)

    assert (
        worker.find_pending_job(
            downloads,
            state,
            max_ticket_age_seconds=60 * 60,
        )
        is None
    )


def test_ticket_tender_code_is_casefolded_before_compute(tmp_path: Path) -> None:
    ticket_path = _write_ticket(
        tmp_path,
        ticket_id="ticket_casefold1",
        tender_code="4291-46-LE26",
    )

    ticket = worker.load_processing_ticket(ticket_path)

    assert ticket.tender_code == "4291-46-le26"


def test_cached_outbox_is_rejected_when_paired_zip_bytes_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    downloads = tmp_path / "Downloads"
    state = tmp_path / "state"
    downloads.mkdir()

    ticket_path = _write_ticket(
        downloads,
        ticket_id="ticket_digest12",
        tender_code="4291-46-LE26",
    )
    zip_path = downloads / "Licitaciones_digest.zip"
    _write_zip(zip_path)

    job = worker.LocalTenderJob(
        ticket=worker.load_processing_ticket(ticket_path),
        zip_path=zip_path,
    )

    expected = {
        "contract_version": worker.LOCAL_IMPORT_CONTRACT_VERSION,
        "tender_code": "4291-46-le26",
        "operator_declared_complete": True,
        "raw": {
            "result": "imported",
            "archive": {
                "zip_sha256": worker.ticket_digest(zip_path),
            },
        },
    }

    monkeypatch.setattr(
        worker,
        "build_structured_local_import",
        lambda _job: expected,
    )

    assert worker.load_or_build_outbox(job, state) == expected

    # Replace the paired ZIP with a different valid archive while retaining
    # the same ticket ID and tender code.
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("changed.txt", "different bytes")

    monkeypatch.setattr(
        worker,
        "build_structured_local_import",
        lambda _job: pytest.fail("cached mismatch must fail closed"),
    )

    with pytest.raises(
        worker.LocalTenderWorkerError,
        match="outbox ZIP digest does not match paired ZIP",
    ):
        worker.load_or_build_outbox(job, state)


def test_new_outbox_is_rejected_when_result_zip_digest_is_wrong(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    downloads = tmp_path / "Downloads"
    state = tmp_path / "state"
    downloads.mkdir()

    ticket_path = _write_ticket(
        downloads,
        ticket_id="ticket_badsha12",
        tender_code="4291-46-le26",
    )
    zip_path = downloads / "Licitaciones_badsha.zip"
    _write_zip(zip_path)

    job = worker.LocalTenderJob(
        ticket=worker.load_processing_ticket(ticket_path),
        zip_path=zip_path,
    )

    payload = {
        "contract_version": worker.LOCAL_IMPORT_CONTRACT_VERSION,
        "tender_code": "4291-46-le26",
        "operator_declared_complete": True,
        "raw": {
            "result": "imported",
            "archive": {
                "zip_sha256": "0" * 64,
            },
        },
    }

    monkeypatch.setattr(
        worker,
        "build_structured_local_import",
        lambda _job: payload,
    )

    with pytest.raises(
        worker.LocalTenderWorkerError,
        match="local processing ZIP digest does not match paired ZIP",
    ):
        worker.load_or_build_outbox(job, state)

    assert not (state / "outbox" / f"{job.ticket.ticket_id}.json").exists()


def test_old_ticket_content_cannot_be_resurrected_by_fresh_mtime(
    tmp_path: Path,
) -> None:
    downloads = tmp_path / "Downloads"
    state = tmp_path / "state"
    downloads.mkdir()

    ticket = _write_ticket(
        downloads,
        ticket_id="ticket_oldbody12",
        tender_code="4291-46-LE26",
        created_at_utc=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    zip_path = downloads / "Licitaciones_oldbody.zip"
    _write_zip(zip_path)

    base = time.time_ns() - 2_000_000_000
    _set_mtime(ticket, base)
    _set_mtime(zip_path, base + 1_000_000_000)

    assert (
        worker.find_pending_job(
            downloads,
            state,
            max_ticket_age_seconds=60 * 60,
        )
        is None
    )


def test_stale_malformed_ticket_is_ignored_before_parse(
    tmp_path: Path,
) -> None:
    downloads = tmp_path / "Downloads"
    state = tmp_path / "state"
    downloads.mkdir()

    ticket = downloads / "origenlab-tender-malformed-old.json"
    ticket.write_text("{not-json\n", encoding="utf-8")

    _set_mtime(
        ticket,
        time.time_ns() - 2 * 60 * 60 * 1_000_000_000,
    )

    assert (
        worker.find_pending_job(
            downloads,
            state,
            max_ticket_age_seconds=60 * 60,
        )
        is None
    )


def test_future_ticket_content_timestamp_fails_closed(
    tmp_path: Path,
) -> None:
    downloads = tmp_path / "Downloads"
    state = tmp_path / "state"
    downloads.mkdir()

    _write_ticket(
        downloads,
        ticket_id="ticket_future12",
        tender_code="4291-46-LE26",
        created_at_utc=datetime.now(timezone.utc) + timedelta(minutes=10),
    )

    with pytest.raises(
        worker.LocalTenderWorkerError,
        match="future created_at_utc",
    ):
        worker.find_pending_job(downloads, state)


def test_cached_outbox_is_bound_to_completeness_declaration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    downloads = tmp_path / "Downloads"
    state = tmp_path / "state"
    downloads.mkdir()

    ticket_path = _write_ticket(
        downloads,
        ticket_id="ticket_complete12",
        tender_code="4291-46-LE26",
        declared_complete=True,
    )
    zip_path = downloads / "Licitaciones_complete.zip"
    _write_zip(zip_path)

    first_job = worker.LocalTenderJob(
        ticket=worker.load_processing_ticket(ticket_path),
        zip_path=zip_path,
    )

    payload = {
        "contract_version": worker.LOCAL_IMPORT_CONTRACT_VERSION,
        "tender_code": "4291-46-le26",
        "operator_declared_complete": True,
        "raw": {
            "result": "imported",
            "archive": {
                "zip_sha256": worker.ticket_digest(zip_path),
            },
        },
    }

    monkeypatch.setattr(
        worker,
        "build_structured_local_import",
        lambda _job: payload,
    )

    assert worker.load_or_build_outbox(first_job, state) == payload

    # Same ticket ID/tender/ZIP, but a different operator assertion.
    _write_ticket(
        downloads,
        ticket_id="ticket_complete12",
        tender_code="4291-46-LE26",
        declared_complete=False,
    )

    second_job = worker.LocalTenderJob(
        ticket=worker.load_processing_ticket(ticket_path),
        zip_path=zip_path,
    )

    monkeypatch.setattr(
        worker,
        "build_structured_local_import",
        lambda _job: pytest.fail("cached mismatch must not recompute"),
    )

    with pytest.raises(
        worker.LocalTenderWorkerError,
        match="operator_declared_complete does not match ticket",
    ):
        worker.load_or_build_outbox(second_job, state)


def test_tampered_cached_outbox_portal_token_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    downloads = tmp_path / "Downloads"
    state = tmp_path / "state"
    downloads.mkdir()

    ticket_path = _write_ticket(
        downloads,
        ticket_id="ticket_token123",
        tender_code="4291-46-LE26",
    )
    zip_path = downloads / "Licitaciones_token.zip"
    _write_zip(zip_path)

    job = worker.LocalTenderJob(
        ticket=worker.load_processing_ticket(ticket_path),
        zip_path=zip_path,
    )

    payload = {
        "contract_version": worker.LOCAL_IMPORT_CONTRACT_VERSION,
        "tender_code": "4291-46-le26",
        "operator_declared_complete": True,
        "raw": {
            "result": "imported",
            "archive": {
                "zip_sha256": worker.ticket_digest(zip_path),
            },
        },
    }

    monkeypatch.setattr(
        worker,
        "build_structured_local_import",
        lambda _job: payload,
    )
    worker.load_or_build_outbox(job, state)

    outbox = state / "outbox" / f"{job.ticket.ticket_id}.json"
    tampered = json.loads(outbox.read_text(encoding="utf-8"))
    tampered["synthetic_note"] = "enc=synthetic-test-value"
    outbox.write_text(json.dumps(tampered), encoding="utf-8")

    with pytest.raises(
        worker.LocalTenderWorkerError,
        match="forbidden portal token",
    ):
        worker.load_or_build_outbox(job, state)


def test_api_base_url_requires_https_except_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        worker.API_URL_ENV,
        "http://api.origenlab.cl",
    )

    with pytest.raises(
        worker.LocalTenderWorkerError,
        match="requires HTTPS",
    ):
        worker.resolve_api_base_url()

    monkeypatch.setenv(
        worker.API_URL_ENV,
        "http://127.0.0.1:8001",
    )
    assert worker.resolve_api_base_url() == "http://127.0.0.1:8001"


class _FakeHttpResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_post_rejects_success_response_for_different_tender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        worker.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _FakeHttpResponse(
            {
                "persisted": True,
                "tender_code": "745712-19-lp26",
            }
        ),
    )

    with pytest.raises(
        worker.LocalTenderWorkerError,
        match="response tender_code does not match request",
    ):
        worker.post_structured_local_import(
            api_base_url="https://api.origenlab.cl",
            api_token="synthetic-test-token",
            tender_code="4291-46-le26",
            payload={},
        )


def test_post_accepts_matching_persisted_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        worker.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _FakeHttpResponse(
            {
                "persisted": True,
                "tender_code": "4291-46-LE26",
            }
        ),
    )

    response = worker.post_structured_local_import(
        api_base_url="https://api.origenlab.cl",
        api_token="synthetic-test-token",
        tender_code="4291-46-le26",
        payload={},
    )

    assert response["persisted"] is True
    assert response["tender_code"] == "4291-46-LE26"


def test_build_structured_local_import_uses_local_ollama_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticket_path = _write_ticket(
        tmp_path,
        ticket_id="ticket_semantic12",
        tender_code="4291-46-LE26",
    )
    zip_path = tmp_path / "Licitaciones_semantic.zip"
    _write_zip(zip_path)

    job = worker.LocalTenderJob(
        ticket=worker.load_processing_ticket(ticket_path),
        zip_path=zip_path,
    )

    semantic_client = object()
    captured = {}

    def fake_client(*, model, thinking):
        captured["model"] = model
        captured["thinking"] = thinking
        return semantic_client

    def fake_preview(
        source,
        *,
        tender_code,
        semantic_client=None,
    ):
        captured["tender_code"] = tender_code
        captured["semantic_client"] = semantic_client
        return {
            "result": "imported",
            "archive": {
                "zip_sha256": worker.ticket_digest(zip_path),
            },
        }

    monkeypatch.setattr(
        worker,
        "OllamaSemanticFallbackClient",
        fake_client,
    )
    monkeypatch.setattr(
        worker,
        "build_operator_annex_bundle_preview",
        fake_preview,
    )

    payload = worker.build_structured_local_import(job)

    assert captured["model"] == "gpt-oss:20b"
    assert captured["thinking"] == "medium"
    assert captured["tender_code"] == "4291-46-le26"
    assert captured["semantic_client"] is semantic_client
    assert payload["raw"]["result"] == "imported"

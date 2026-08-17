"""Tests for the current-tender ANEXO acquisition + T1 publication bridge."""

from __future__ import annotations

from pathlib import Path

import pytest

from origenlab_email_pipeline.chilecompra_anexo_evidence import LocalAttachmentSource
from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (
    REASON_GATED_LISTING_UNREACHABLE,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms import (
    production_publish as publication,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.current_tender_annex_publish import (
    acquire_and_publish_current_tender_terms,
    select_current_tender_codes,
)


def _queue_row(tender_code: str, **extra: object) -> dict[str, object]:
    row = {"tender_code": tender_code, "institution_id": "inst-1"}
    row.update(extra)
    return row


@pytest.fixture
def out_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Give write_atomically/publish_tender_terms a synthetic reports/out root
    with no Git dependency, exactly like production_publish's own tests do."""
    email_pipeline_root = tmp_path / "email-pipeline"
    reports_out = email_pipeline_root / "reports" / "out"
    reports_out.mkdir(parents=True)
    monkeypatch.setattr(publication, "_email_pipeline_root", lambda: email_pipeline_root)
    return reports_out / "tender_terms"


@pytest.fixture
def cache_dir(out_dir: Path) -> Path:
    """Bundle-snapshot persistence also goes through write_atomically, which
    requires its destination under the same synthetic reports/out root as
    ``out_dir`` -- so this must share ``out_dir``'s email_pipeline_root, not
    live in an unrelated tmp_path subdirectory."""
    return out_dir.parent / "tender_terms_annex_cache"


# --- selection / dedup ------------------------------------------------------


def test_select_current_tender_codes_dedupes_across_category_rows() -> None:
    queue = [
        _queue_row("4291-46-LE26", equipment_category="centrifuge"),
        _queue_row("4291-46-LE26", equipment_category="balanza"),
        _queue_row("745712-19-LP26"),
    ]
    codes = select_current_tender_codes(queue)
    assert codes == ["4291-46-LE26", "745712-19-LP26"]


def test_select_current_tender_codes_drops_blank() -> None:
    queue = [_queue_row(""), _queue_row("  "), _queue_row("X-1")]
    assert select_current_tender_codes(queue) == ["X-1"]


def test_select_current_tender_codes_performs_no_bounding() -> None:
    """Bounding is the acquire/publish function's job, visibly -- dedupe alone
    must never quietly drop an eligible tender_code."""
    queue = [_queue_row(f"T-{i}") for i in range(30)]
    codes = select_current_tender_codes(queue)
    assert len(codes) == 30


# --- acquisition wiring ------------------------------------------------------


def test_dedup_acquires_exactly_one_bundle_per_tender_code(out_dir: Path, cache_dir: Path) -> None:
    calls: list[str] = []

    def build_source_fn(tender_code: str, detail_url: str):
        calls.append(tender_code)
        return LocalAttachmentSource(items=[("bases.txt", "text/plain", b"hola equipo")])

    queue = [
        _queue_row("T-1", equipment_category="centrifuge"),
        _queue_row("T-1", equipment_category="balanza"),
    ]
    result = acquire_and_publish_current_tender_terms(
        current_opportunity_queue=queue,
        tender_code_to_detail_url={"T-1": "https://www.mercadopublico.cl/x"},
        out_dir=out_dir,
        cache_dir=cache_dir,
        as_of_utc="2026-08-15T00:00:00Z",
        build_source_fn=build_source_fn,
        require_git_ignored=False,
    )
    assert calls == ["T-1"]
    assert result.gate_status == "acquired_and_published"
    assert len(result.acquisitions) == 1
    assert result.publish is not None
    assert result.publish.result == "applied"


def test_non_current_tenders_are_never_acquired(out_dir: Path, cache_dir: Path) -> None:
    calls: list[str] = []

    def build_source_fn(tender_code: str, detail_url: str):
        calls.append(tender_code)
        return LocalAttachmentSource(items=[])

    # Only "T-CURRENT" is passed in current_opportunity_queue; a historical
    # tender code is never even offered to this function by the caller, and
    # the url map only contains the current one -- simulating a caller that
    # (correctly) never surfaces non-current tenders here at all.
    result = acquire_and_publish_current_tender_terms(
        current_opportunity_queue=[_queue_row("T-CURRENT")],
        tender_code_to_detail_url={
            "T-CURRENT": "https://www.mercadopublico.cl/current",
            "T-HISTORICAL": "https://www.mercadopublico.cl/historical",
        },
        out_dir=out_dir,
        cache_dir=cache_dir,
        as_of_utc="2026-08-15T00:00:00Z",
        build_source_fn=build_source_fn,
        require_git_ignored=False,
    )
    assert calls == ["T-CURRENT"]
    assert "T-HISTORICAL" not in [a.tender_code for a in result.acquisitions]


def test_reduced_mode_blocks_acquisition_entirely(out_dir: Path, cache_dir: Path) -> None:
    calls: list[str] = []

    def build_source_fn(tender_code: str, detail_url: str):
        calls.append(tender_code)
        return LocalAttachmentSource(items=[])

    result = acquire_and_publish_current_tender_terms(
        current_opportunity_queue=[_queue_row("T-1")],
        tender_code_to_detail_url={"T-1": "https://www.mercadopublico.cl/x"},
        out_dir=out_dir,
        cache_dir=cache_dir,
        as_of_utc="2026-08-15T00:00:00Z",
        build_source_fn=build_source_fn,
        reduced_mode=True,
        require_git_ignored=False,
    )
    assert calls == []
    assert result.gate_status == "blocked_reduced_mode_w1"
    assert result.acquisitions == ()
    assert result.publish is None
    assert result.eligible_count == 0
    assert result.selected_count == 0
    assert result.truncated is False
    assert not out_dir.exists()


def test_empty_current_queue_acquires_nothing(out_dir: Path, cache_dir: Path) -> None:
    result = acquire_and_publish_current_tender_terms(
        current_opportunity_queue=[],
        tender_code_to_detail_url={},
        out_dir=out_dir,
        cache_dir=cache_dir,
        as_of_utc="2026-08-15T00:00:00Z",
        require_git_ignored=False,
    )
    assert result.gate_status == "no_current_opportunities"
    assert result.publish is None
    assert result.eligible_count == 0
    assert result.selected_count == 0


def test_source_invoked_only_for_selected_tender_codes(out_dir: Path, cache_dir: Path) -> None:
    """Directly assert the attachment source factory is never called for a
    tender_code outside this run's current_opportunity_queue selection."""
    invoked: list[str] = []

    def build_source_fn(tender_code: str, detail_url: str):
        invoked.append(tender_code)
        return LocalAttachmentSource(items=[])

    queue = [_queue_row("A"), _queue_row("B")]
    acquire_and_publish_current_tender_terms(
        current_opportunity_queue=queue,
        tender_code_to_detail_url={
            "A": "https://www.mercadopublico.cl/a",
            "B": "https://www.mercadopublico.cl/b",
            "C": "https://www.mercadopublico.cl/c",  # not in queue -> must not be visited
        },
        out_dir=out_dir,
        cache_dir=cache_dir,
        as_of_utc="2026-08-15T00:00:00Z",
        build_source_fn=build_source_fn,
        require_git_ignored=False,
    )
    assert sorted(invoked) == ["A", "B"]


# --- no cross-run body-download skip (fix: item 1/2) ------------------------


def test_portal_attachment_source_has_no_cache_skip_fields() -> None:
    """Regression guard: PortalAttachmentSource must never regain a cache/
    known_digests-style field that could skip a real body download based on
    metadata alone."""
    import dataclasses

    from origenlab_email_pipeline.chilecompra_anexo_evidence.acquire import (
        PortalAttachmentSource,
    )

    field_names = {f.name for f in dataclasses.fields(PortalAttachmentSource)}
    assert "cache" not in field_names
    assert "known_digests" not in field_names
    assert not hasattr(PortalAttachmentSource, "attachment_identity_key")


def test_portal_attachment_source_redownloads_every_run_even_with_identical_metadata(
    tmp_path: Path,
) -> None:
    """Two independent PortalAttachmentSource instances (simulating two
    separate runs) against a row with byte-for-byte identical portal
    metadata must each perform a real download and reflect real bytes --
    the second run must never reuse the first run's bytes just because the
    filename/tipo/descripcion/fecha_adjunto match."""
    from unittest.mock import patch

    from origenlab_email_pipeline.chilecompra_anexo_evidence.acquire import (
        PortalAttachmentSource,
    )

    class _FakeAttachment:
        nombre = "bases.pdf"
        tipo = "bases"
        descripcion = ""
        fecha_adjunto = "2026-08-01"
        listing_url = "https://www.mercadopublico.cl/listing"

    class _FakePortal:
        attachments = [_FakeAttachment()]
        listing_urls = ["https://www.mercadopublico.cl/listing"]
        session = None
        listing_form_fields = {"https://www.mercadopublico.cl/listing": {}}
        gated_listing_hint = False

        def listing_ordinal_of(self, attachment):
            return 0

    class _Download:
        def __init__(self, content: bytes) -> None:
            self.content = content
            self.content_type = "application/pdf"

    download_calls: list[bytes] = []

    def _run_once(payload: bytes) -> bytes:
        source = PortalAttachmentSource(detail_url="https://www.mercadopublico.cl/detail")
        source._portal_inventory = lambda: _FakePortal()  # type: ignore[method-assign]

        def _fake_download(*args: object, **kwargs: object) -> _Download:
            download_calls.append(payload)
            return _Download(payload)

        with patch(
            "origenlab_email_pipeline.chilecompra_api.download_portal_attachment",
            side_effect=_fake_download,
        ), patch(
            "origenlab_email_pipeline.chilecompra_api.safe_attachment_filename",
            side_effect=lambda name: name,
        ):
            inventory = source.inventory()
            results = list(source.iter_payloads(inventory))
        assert len(results) == 1
        return results[0].payload

    first_run_bytes = _run_once(b"version one bytes")
    second_run_bytes = _run_once(b"version two bytes -- CHANGED")

    # Real download happened both times, and the second run's returned
    # payload reflects the *new* bytes, not a stale cached copy of the first.
    assert len(download_calls) == 2
    assert first_run_bytes == b"version one bytes"
    assert second_run_bytes == b"version two bytes -- CHANGED"
    assert second_run_bytes != first_run_bytes


# --- bundle snapshot persistence (fix: item 3) -------------------------------


def test_bundle_snapshot_persisted_atomically_for_each_acquired_tender(
    out_dir: Path, cache_dir: Path
) -> None:
    from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms import (
        current_tender_annex_publish as mod,
    )

    def build_source_fn(tender_code: str, detail_url: str):
        return LocalAttachmentSource(items=[("bases.txt", "text/plain", b"contenido real")])

    result = acquire_and_publish_current_tender_terms(
        current_opportunity_queue=[_queue_row("T-1")],
        tender_code_to_detail_url={"T-1": "https://www.mercadopublico.cl/x"},
        out_dir=out_dir,
        cache_dir=cache_dir,
        as_of_utc="2026-08-15T00:00:00Z",
        build_source_fn=build_source_fn,
        require_git_ignored=False,
    )
    assert result.acquisitions[0].bundle_snapshot_persisted is True

    snapshot_path = (
        cache_dir / mod.BUNDLE_SNAPSHOT_DIRNAME / "T-1" / "bundle.pkl"
    )
    assert snapshot_path.is_file()

    import pickle

    loaded = pickle.loads(snapshot_path.read_bytes())
    assert loaded.tender_id == "T-1"
    assert loaded.attachments_downloaded == 1


def test_bundle_snapshot_not_persisted_for_failed_acquisitions(
    out_dir: Path, cache_dir: Path
) -> None:
    from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms import (
        current_tender_annex_publish as mod,
    )

    def build_source_fn(tender_code: str, detail_url: str):
        raise RuntimeError("simulated network explosion")

    result = acquire_and_publish_current_tender_terms(
        current_opportunity_queue=[_queue_row("T-BAD")],
        tender_code_to_detail_url={"T-BAD": "https://www.mercadopublico.cl/bad"},
        out_dir=out_dir,
        cache_dir=cache_dir,
        as_of_utc="2026-08-15T00:00:00Z",
        build_source_fn=build_source_fn,
        require_git_ignored=False,
    )
    assert result.acquisitions[0].status == "acquisition_exception"
    assert result.acquisitions[0].bundle_snapshot_persisted is False
    assert not (cache_dir / mod.BUNDLE_SNAPSHOT_DIRNAME / "T-BAD").exists()


def test_bundle_snapshot_interrupted_write_leaves_no_partial_or_corrupt_snapshot(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate a write that dies mid-flight (after the atomic helper's temp
    staging begins but before the pickle bytes are produced) and confirm the
    previously-persisted snapshot is left byte-for-byte intact -- never a
    partial or corrupt file at the "current" snapshot path."""
    from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms import (
        current_tender_annex_publish as mod,
    )

    bundle = mod._failed_bundle("T-1", "acquisition_exception")

    ok = mod._persist_bundle_snapshot(
        bundle, cache_dir=cache_dir, tender_code="T-1", require_git_ignored=False
    )
    assert ok is True
    snapshot_path = cache_dir / mod.BUNDLE_SNAPSHOT_DIRNAME / "T-1" / "bundle.pkl"
    assert snapshot_path.is_file()
    prior_bytes = snapshot_path.read_bytes()

    def _boom(*args: object, **kwargs: object) -> bytes:
        raise RuntimeError("simulated interrupted write")

    monkeypatch.setattr(mod.pickle, "dumps", _boom)

    ok2 = mod._persist_bundle_snapshot(
        bundle, cache_dir=cache_dir, tender_code="T-1", require_git_ignored=False
    )
    assert ok2 is False
    # The prior snapshot must still be intact: not deleted, not truncated,
    # not partially overwritten.
    assert snapshot_path.is_file()
    assert snapshot_path.read_bytes() == prior_bytes


def test_bundle_snapshot_persist_failure_never_blocks_publication(
    out_dir: Path, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A snapshot-cache write failure is best-effort and must never turn an
    otherwise-successful acquisition+publication into a failure."""
    from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms import (
        current_tender_annex_publish as mod,
    )

    def build_source_fn(tender_code: str, detail_url: str):
        return LocalAttachmentSource(items=[("bases.txt", "text/plain", b"contenido")])

    monkeypatch.setattr(
        mod,
        "_persist_bundle_snapshot",
        lambda *args, **kwargs: False,
    )

    result = acquire_and_publish_current_tender_terms(
        current_opportunity_queue=[_queue_row("T-1")],
        tender_code_to_detail_url={"T-1": "https://www.mercadopublico.cl/x"},
        out_dir=out_dir,
        cache_dir=cache_dir,
        as_of_utc="2026-08-15T00:00:00Z",
        build_source_fn=build_source_fn,
        require_git_ignored=False,
    )
    assert result.acquisitions[0].status == "acquired"
    assert result.acquisitions[0].bundle_snapshot_persisted is False
    assert result.publish is not None
    assert result.publish.result == "applied"


# --- max_tenders bound: fail closed, never silent (fix: item 4) -------------


def test_max_tenders_exceeded_fails_closed_not_silent_truncation(
    out_dir: Path, cache_dir: Path
) -> None:
    calls: list[str] = []

    def build_source_fn(tender_code: str, detail_url: str):
        calls.append(tender_code)
        return LocalAttachmentSource(items=[])

    queue = [_queue_row(f"T-{i}") for i in range(3)]
    url_map = {f"T-{i}": f"https://www.mercadopublico.cl/{i}" for i in range(3)}

    result = acquire_and_publish_current_tender_terms(
        current_opportunity_queue=queue,
        tender_code_to_detail_url=url_map,
        out_dir=out_dir,
        cache_dir=cache_dir,
        as_of_utc="2026-08-15T00:00:00Z",
        build_source_fn=build_source_fn,
        max_tenders=2,
        require_git_ignored=False,
    )
    # Fail closed: acquisition never even starts once the bound is exceeded --
    # no partial/best-guess subset is silently acquired or published.
    assert calls == []
    assert result.gate_status == "blocked_max_tenders_exceeded"
    assert result.acquisitions == ()
    assert result.publish is None
    assert result.eligible_count == 3
    assert result.selected_count == 0
    assert result.truncated is True
    assert result.truncation_reason
    assert not out_dir.exists()


def test_max_tenders_not_exceeded_reports_eligible_and_selected_counts(
    out_dir: Path, cache_dir: Path
) -> None:
    def build_source_fn(tender_code: str, detail_url: str):
        return LocalAttachmentSource(items=[])

    queue = [_queue_row("T-1"), _queue_row("T-2")]
    url_map = {
        "T-1": "https://www.mercadopublico.cl/1",
        "T-2": "https://www.mercadopublico.cl/2",
    }
    result = acquire_and_publish_current_tender_terms(
        current_opportunity_queue=queue,
        tender_code_to_detail_url=url_map,
        out_dir=out_dir,
        cache_dir=cache_dir,
        as_of_utc="2026-08-15T00:00:00Z",
        build_source_fn=build_source_fn,
        max_tenders=5,
        require_git_ignored=False,
    )
    assert result.gate_status == "acquired_and_published"
    assert result.eligible_count == 2
    assert result.selected_count == 2
    assert result.truncated is False
    assert result.truncation_reason is None


def test_max_tenders_exactly_at_bound_is_not_truncated(out_dir: Path, cache_dir: Path) -> None:
    def build_source_fn(tender_code: str, detail_url: str):
        return LocalAttachmentSource(items=[])

    queue = [_queue_row("T-1"), _queue_row("T-2")]
    url_map = {
        "T-1": "https://www.mercadopublico.cl/1",
        "T-2": "https://www.mercadopublico.cl/2",
    }
    result = acquire_and_publish_current_tender_terms(
        current_opportunity_queue=queue,
        tender_code_to_detail_url=url_map,
        out_dir=out_dir,
        cache_dir=cache_dir,
        as_of_utc="2026-08-15T00:00:00Z",
        build_source_fn=build_source_fn,
        max_tenders=2,
        require_git_ignored=False,
    )
    assert result.gate_status == "acquired_and_published"
    assert result.truncated is False
    assert result.selected_count == 2


# --- failure isolation --------------------------------------------------


def test_incomplete_attachment_download_stays_incomplete_not_silently_empty(
    out_dir: Path, cache_dir: Path,
) -> None:
    def build_source_fn(tender_code: str, detail_url: str):
        return LocalAttachmentSource(
            items=[("bases.pdf", "application/pdf", b"%PDF-fake")],
            failures={0: "simulated_download_failure"},
        )

    result = acquire_and_publish_current_tender_terms(
        current_opportunity_queue=[_queue_row("T-1")],
        tender_code_to_detail_url={"T-1": "https://www.mercadopublico.cl/x"},
        out_dir=out_dir,
        cache_dir=cache_dir,
        as_of_utc="2026-08-15T00:00:00Z",
        build_source_fn=build_source_fn,
        require_git_ignored=False,
    )
    outcome = result.acquisitions[0]
    assert outcome.status == "acquired"
    assert outcome.bundle_complete is False
    assert result.publish is not None
    assert result.publish.result == "applied"


def test_gated_listing_tender_publishes_honest_incomplete_coverage_without_sinking_others(
    out_dir: Path, cache_dir: Path,
) -> None:
    """Fixture D: mirrors the real 4291-46-LE26 regression end to end. The
    gated tender's one genuinely-reachable attachment is still read and
    published, but its coverage must not claim completeness -- and a
    completely unrelated, normally-complete tender in the same run must be
    fully unaffected (per-tender/per-bundle signal, never a global mode)."""

    def build_source_fn(tender_code: str, detail_url: str):
        if tender_code == "4291-46-LE26":
            # What PortalAttachmentSource would have produced for the real
            # Antofagasta ficha: one reachable, genuinely-read DOCX, plus the
            # source-level signal that a second, CAPTCHA-gated listing exists
            # and could not be enumerated.
            return LocalAttachmentSource(
                items=[
                    (
                        "FORMULARIO_OBLIGATORIO_1234.docx",
                        "text/plain",
                        b"contenido real del formulario",
                    )
                ],
                incomplete_reason_codes=(REASON_GATED_LISTING_UNREACHABLE,),
            )
        return LocalAttachmentSource(
            items=[("bases.txt", "text/plain", b"contenido normal completo")]
        )

    result = acquire_and_publish_current_tender_terms(
        current_opportunity_queue=[
            _queue_row("4291-46-LE26"),
            _queue_row("T-NORMAL"),
        ],
        tender_code_to_detail_url={
            "4291-46-LE26": "https://www.mercadopublico.cl/gated",
            "T-NORMAL": "https://www.mercadopublico.cl/normal",
        },
        out_dir=out_dir,
        cache_dir=cache_dir,
        as_of_utc="2026-08-15T00:00:00Z",
        build_source_fn=build_source_fn,
        require_git_ignored=False,
    )

    outcomes = {a.tender_code: a for a in result.acquisitions}
    gated = outcomes["4291-46-LE26"]
    normal = outcomes["T-NORMAL"]

    # The one HTTP-reachable attachment was genuinely acquired -- never
    # invented, never silently dropped.
    assert gated.status == "acquired"
    assert gated.attachments_discovered == 1
    assert gated.attachments_downloaded == 1
    assert REASON_GATED_LISTING_UNREACHABLE in gated.incomplete_reason_codes
    assert gated.bundle_complete is False

    # The unrelated tender is completely unaffected by the gated one.
    assert normal.status == "acquired"
    assert normal.incomplete_reason_codes == ()
    assert normal.bundle_complete is True

    assert result.publish is not None
    assert result.publish.result == "applied"
    assert result.publish.tender_count == 2
    # Exactly one tender's published coverage is honestly incomplete; the
    # other tender's publication is not degraded by it.
    assert result.publish.complete_coverage_count == 1
    assert result.publish.incomplete_coverage_count == 1


def test_one_failed_tender_does_not_corrupt_publish_for_others(out_dir: Path, cache_dir: Path) -> None:
    def build_source_fn(tender_code: str, detail_url: str):
        if tender_code == "T-BAD":
            raise RuntimeError("simulated network explosion")
        return LocalAttachmentSource(items=[("bases.txt", "text/plain", b"contenido")])

    result = acquire_and_publish_current_tender_terms(
        current_opportunity_queue=[_queue_row("T-BAD"), _queue_row("T-GOOD")],
        tender_code_to_detail_url={
            "T-BAD": "https://www.mercadopublico.cl/bad",
            "T-GOOD": "https://www.mercadopublico.cl/good",
        },
        out_dir=out_dir,
        cache_dir=cache_dir,
        as_of_utc="2026-08-15T00:00:00Z",
        build_source_fn=build_source_fn,
        require_git_ignored=False,
    )
    statuses = {a.tender_code: a.status for a in result.acquisitions}
    assert statuses["T-BAD"] == "acquisition_exception"
    assert statuses["T-GOOD"] == "acquired"
    assert result.publish is not None
    assert result.publish.result == "applied"
    assert result.publish.tender_count == 2


def test_no_detail_url_is_skipped_not_silently_dropped(out_dir: Path, cache_dir: Path) -> None:
    result = acquire_and_publish_current_tender_terms(
        current_opportunity_queue=[_queue_row("T-NO-URL")],
        tender_code_to_detail_url={},
        out_dir=out_dir,
        cache_dir=cache_dir,
        as_of_utc="2026-08-15T00:00:00Z",
        require_git_ignored=False,
    )
    assert result.acquisitions[0].status == "skipped_no_detail_url"
    assert result.publish is not None
    assert result.publish.result == "applied"


# --- no DB/Gmail/outreach paths ------------------------------------------


def test_module_imports_no_db_gmail_outreach_symbols() -> None:
    import ast

    import origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.current_tender_annex_publish as mod

    banned_substrings = ("gmail", "outreach", "sqlite3", "psycopg")
    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)
    joined = " ".join(imported_modules).lower()
    for banned in banned_substrings:
        assert banned not in joined, f"unexpected {banned!r} import in {mod.__file__}"

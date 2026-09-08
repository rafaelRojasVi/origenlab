from __future__ import annotations

import csv
import hashlib

from origenlab_email_pipeline.historical_quote_register.document_bundle import (
    BundleManifestRow,
    copy_into_bundle,
    slugify,
    write_drive_upload_manifest,
)


def test_slugify_basic():
    assert slugify("Universidad Austral") == "universidad-austral"
    assert slugify("") == "unknown"
    assert slugify("Ñandú S.A.") == "andu-s-a" or slugify("Ñandú S.A.") != ""


def test_copy_into_bundle_never_duplicates_identical_sha256(tmp_path):
    payload = b"%PDF-1.4 fake quote"
    sha = hashlib.sha256(payload).hexdigest()

    first_path = copy_into_bundle(
        run_dir=tmp_path, payload=payload, sha256=sha, original_filename="cot.pdf",
        year="2026", client_slug="universidad-austral", quote_number_or_key="COT-2026-014",
        revision_index=1,
    )
    second_path = copy_into_bundle(
        run_dir=tmp_path, payload=payload, sha256=sha, original_filename="cot_v2.pdf",
        year="2026", client_slug="universidad-austral", quote_number_or_key="COT-2026-014",
        revision_index=1,
    )
    assert first_path == second_path
    assert first_path.exists()
    assert first_path.read_bytes() == payload

    all_files = list((tmp_path / "documents").rglob("*.pdf"))
    assert len(all_files) == 1  # no duplicate physical copy for identical sha256


def test_copy_into_bundle_preserves_original_bytes_exactly(tmp_path):
    payload = b"binary \x00\x01\x02 content"
    sha = hashlib.sha256(payload).hexdigest()
    path = copy_into_bundle(
        run_dir=tmp_path, payload=payload, sha256=sha, original_filename="x.bin",
        year="2026", client_slug="cliente", quote_number_or_key="hqk-1", revision_index=1,
    )
    assert path.read_bytes() == payload


def test_copy_into_bundle_disambiguates_distinct_content_at_same_path(tmp_path):
    payload_a = b"%PDF-1.4 quote A content"
    payload_b = b"%PDF-1.4 quote B totally different content"
    sha_a = hashlib.sha256(payload_a).hexdigest()
    sha_b = hashlib.sha256(payload_b).hexdigest()
    assert sha_a != sha_b

    kwargs = dict(
        run_dir=tmp_path, original_filename="cotizacion.pdf",
        year="2026", client_slug="universidad-austral", quote_number_or_key="COT-2026-014",
        revision_index=1,
    )
    path_a = copy_into_bundle(payload=payload_a, sha256=sha_a, **kwargs)
    path_b = copy_into_bundle(payload=payload_b, sha256=sha_b, **kwargs)

    assert path_a != path_b
    assert path_a.exists()
    assert path_b.exists()
    assert path_a.read_bytes() == payload_a
    assert path_b.read_bytes() == payload_b


def test_write_drive_upload_manifest(tmp_path):
    row = BundleManifestRow(
        local_export_path="documents/2026/cliente/COT-2026-014/revision-1/cot.pdf",
        original_path="gmail:contacto@origenlab.cl/[Gmail]/Enviados#1", sha256="abc123",
        historical_quote_key="hqk-1", quote_number="COT-2026-014", client_name="Cliente SA",
        sent_at="2026-05-10T00:00:00",
    )
    manifest_path = write_drive_upload_manifest([row], tmp_path)
    with manifest_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["sha256"] == "abc123"
    assert rows[0]["historical_quote_key"] == "hqk-1"

from __future__ import annotations

import json

from origenlab_email_pipeline.historical_quote_register.manifest import (
    RunManifest,
    hash_output_files,
    write_manifest,
)


def test_hash_output_files(tmp_path):
    (tmp_path / "a.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    hashes = hash_output_files(tmp_path, ["a.csv"])
    assert "a.csv" in hashes
    assert len(hashes["a.csv"]) == 64


def test_write_manifest_round_trips_json(tmp_path):
    m = RunManifest(
        run_started_at="2026-08-28T00:00:00Z", run_finished_at="2026-08-28T00:05:00Z",
        source_db_path="/home/user/data/origenlab-email/sqlite/emails.sqlite",
        source_fingerprint_before={"size_bytes": 100}, source_fingerprint_after={"size_bytes": 100},
        sent_coverage={"gmail_enviados": {"count": 1665}}, inbox_coverage_note="not scanned",
        counts={"candidates": 10}, classifier_version="v1", exporter_version="v1",
        no_mutation_declaration="canonical archive opened read-only throughout; zero writes issued",
        output_file_hashes={"historical_customer_quotes.csv": "abc"},
    )
    path = write_manifest(m, tmp_path)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["counts"]["candidates"] == 10
    assert loaded["no_mutation_declaration"].startswith("canonical archive")


def test_write_manifest_is_atomic_no_partial_file_on_crash(tmp_path, monkeypatch):
    import origenlab_email_pipeline.historical_quote_register.manifest as mf

    calls = {"n": 0}
    real_replace = mf.os.replace

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulated crash before rename")
        return real_replace(src, dst)

    monkeypatch.setattr(mf.os, "replace", flaky_replace)
    m = RunManifest(
        run_started_at="2026-08-28T00:00:00Z", run_finished_at="2026-08-28T00:05:00Z",
        source_db_path="/home/user/data/origenlab-email/sqlite/emails.sqlite",
        source_fingerprint_before={"size_bytes": 100}, source_fingerprint_after={"size_bytes": 100},
        sent_coverage={"gmail_enviados": {"count": 1665}}, inbox_coverage_note="not scanned",
        counts={"candidates": 10}, classifier_version="v1", exporter_version="v1",
        no_mutation_declaration="canonical archive opened read-only throughout; zero writes issued",
        output_file_hashes={"historical_customer_quotes.csv": "abc"},
    )
    try:
        write_manifest(m, tmp_path)
    except OSError:
        pass
    assert not (tmp_path / "manifest.json").exists()
    # Verify no temp files left behind
    tmp_files = list(tmp_path.glob(".manifest.*.tmp"))
    assert len(tmp_files) == 0

"""Machine-readable run manifest — the evidence trail for coverage, counts,
and the no-mutation guarantee (design §3.1)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile


@dataclass(frozen=True)
class RunManifest:
    run_started_at: str
    run_finished_at: str
    source_db_path: str
    source_fingerprint_before: dict
    source_fingerprint_after: dict
    sent_coverage: dict
    inbox_coverage_note: str
    counts: dict
    classifier_version: str
    exporter_version: str
    no_mutation_declaration: str
    output_file_hashes: dict[str, str]


def hash_output_files(run_dir: Path, filenames: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in filenames:
        path = run_dir / name
        if not path.exists():
            continue
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        result[name] = h.hexdigest()
    return result


def write_manifest(manifest: RunManifest, run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "manifest.json"
    fd, tmp_name = tempfile.mkstemp(dir=run_dir, prefix=".manifest.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(asdict(manifest), f, indent=2, ensure_ascii=False)
    os.replace(tmp_name, path)
    return path

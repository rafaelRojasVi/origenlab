"""Tests for sync_tender_terms_to_cloud.sh and T1 cloud-sync wiring.

Mirrors tests/test_sync_institution_prospects_to_cloud.py's structure/patterns
for the W1 counterpart. No real SSH/SCP is executed against Render (no
credentials in this environment) — behavioral tests stub scp/ssh/uv and
assert on local-only preflight/promotion logic.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SYNC_SCRIPT = _REPO / "scripts/ops/sync_tender_terms_to_cloud.sh"

# Minimum required T1 files, per load_published_tender_terms /
# production_publish.SUMMARY_FILENAME / TENDER_TERMS_FILENAME.
_REQUIRED_T1_FILES = (
    "summary.json",
    "tender_terms.jsonl",
)

_MINIMAL_SUMMARY = """\
{
  "complete_coverage_count": 0,
  "contact_authorization": false,
  "contract_version": "tender_terms_publication_v1",
  "fact_state_counts": {},
  "incomplete_coverage_count": 0,
  "outreach_authorization": false,
  "persisted": false,
  "production_queue_mutated": false,
  "publication_digest": "0" ,
  "source_digest": "0",
  "as_of_utc": "2026-08-15T17:30:14Z",
  "tender_count": 0,
  "terms_version": "v1"
}
"""


def _script_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _make_minimal_bundle(dest: Path) -> None:
    """Populate dest with the minimum required T1 files (contract shape only;
    not necessarily reconciling — sufficient for scp-gating/structure tests
    that stub out the Python validation step)."""
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "summary.json").write_text(_MINIMAL_SUMMARY)
    (dest / "tender_terms.jsonl").write_text("")


def _make_archive_and_sha(src: Path, archive_path: Path) -> str:
    subprocess.run(
        ["tar", "-czf", str(archive_path), "-C", str(src), "."],
        check=True,
        timeout=10,
    )
    result = subprocess.run(
        ["sha256sum", str(archive_path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return result.stdout.split()[0]


def _stub_bin(tmp_path: Path, *, scp_called: Path) -> Path:
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    scp_stub = stub_bin / "scp"
    scp_stub.write_text(
        textwrap.dedent(f'#!/usr/bin/env bash\ntouch "{scp_called}"\nexit 0\n')
    )
    scp_stub.chmod(0o755)
    (stub_bin / "ssh").write_text("#!/usr/bin/env bash\nexit 0\n")
    (stub_bin / "ssh").chmod(0o755)
    (stub_bin / "uv").write_text("#!/usr/bin/env bash\nexit 0\n")
    (stub_bin / "uv").chmod(0o755)
    return stub_bin


def _fake_ssh_key(tmp_path: Path) -> Path:
    fake_key = tmp_path / "fake_key"
    fake_key.write_text("FAKE")
    fake_key.chmod(0o600)
    return fake_key


def _base_env(tmp_path: Path, stub_bin: Path, fake_key: Path, local_dir: Path) -> dict:
    return {
        **os.environ,
        "PATH": f"{stub_bin}:{os.environ.get('PATH', '')}",
        "ORIGENLAB_RENDER_SSH_HOST": "ssh.oregon.render.com",
        "ORIGENLAB_RENDER_SSH_USER": "srv-test",
        "ORIGENLAB_RENDER_SSH_KEY": str(fake_key),
        "ORIGENLAB_TENDER_TERMS_LOCAL_DIR": str(local_dir),
    }


# ---------------------------------------------------------------------------
# Sync script static checks
# ---------------------------------------------------------------------------


def test_sync_script_exists_and_is_executable() -> None:
    assert _SYNC_SCRIPT.exists()
    assert os.access(_SYNC_SCRIPT, os.X_OK)


def test_sync_script_syntax_check() -> None:
    cp = subprocess.run(
        ["bash", "-n", str(_SYNC_SCRIPT)],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert cp.returncode == 0, cp.stderr


def test_sync_script_uses_set_euo_pipefail() -> None:
    assert "set -euo pipefail" in _script_text(_SYNC_SCRIPT)


def test_sync_script_never_echoes_ssh_key_contents() -> None:
    text = _script_text(_SYNC_SCRIPT)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "SSH_KEY" in stripped:
            assert "cat " not in stripped or "SSH_KEY" not in stripped
            assert 'echo "$SSH_KEY"' not in stripped
            assert "echo ${SSH_KEY}" not in stripped


def test_sync_script_requires_ssh_env_vars() -> None:
    text = _script_text(_SYNC_SCRIPT)
    assert "ORIGENLAB_RENDER_SSH_HOST" in text
    assert "ORIGENLAB_RENDER_SSH_USER" in text
    assert "ORIGENLAB_RENDER_SSH_KEY" in text
    assert ":?" in text


def test_sync_script_never_invokes_acquisition_as_a_runtime_command() -> None:
    """--publish-tender-terms on auto-refresh-chilecompra-equipment is a real,
    opt-in flag (see operator_cli/chilecompra_auto_refresh.py). The sync
    script may accurately mention it in operator-facing prose (comments or
    echoed guidance) as the way to produce fresh local T1 output before
    syncing, but it must never shell out to run auto-refresh or any
    acquisition entrypoint itself -- it only ships whatever is already
    published locally."""
    text = _script_text(_SYNC_SCRIPT)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("echo "):
            continue
        assert "origenlab auto-refresh-chilecompra-equipment" not in stripped, (
            f"sync script must not invoke auto-refresh as a runtime command: {line!r}"
        )
        assert "auto-refresh-chilecompra-equipment --once" not in stripped, (
            f"sync script must not invoke auto-refresh as a runtime command: {line!r}"
        )


def test_sync_script_mentions_real_publish_tender_terms_flag_accurately() -> None:
    """--publish-tender-terms now exists (opt-in, requires
    --publish-institution-prospects). If the sync script mentions it, that
    is now correct operator guidance, not a stale/false claim."""
    text = _script_text(_SYNC_SCRIPT)
    assert "--publish-tender-terms" in text
    assert "--publish-institution-prospects" in text


def test_sync_script_still_documents_manual_replay_path() -> None:
    """The manual replay/recovery path (publish_tender_terms_from_cached_bundles.py)
    remains available and must still be referenced as an alternate path, not
    the primary production path."""
    text = _script_text(_SYNC_SCRIPT)
    assert "publish_tender_terms_from_cached_bundles.py" in text
    assert "--bundles-dir" in text
    referenced = _REPO / "scripts/commercial/publish_tender_terms_from_cached_bundles.py"
    assert referenced.exists()


def test_sync_script_error_message_points_at_real_manual_publisher() -> None:
    """When the local T1 dir is missing, the script must point at the actual
    manual publisher script that exists, with correct invocation."""
    text = _script_text(_SYNC_SCRIPT)
    assert "publish_tender_terms_from_cached_bundles.py" in text
    assert "--bundles-dir" in text
    # The referenced script must actually exist at that relative path.
    referenced = _REPO / "scripts/commercial/publish_tender_terms_from_cached_bundles.py"
    assert referenced.exists()


def test_sync_script_validates_local_source_bundle_before_network() -> None:
    text = _script_text(_SYNC_SCRIPT)
    local_validation_pos = text.find("local_source_validation_ok")
    scp_pos = text.find("\nscp ")
    assert local_validation_pos != -1
    assert scp_pos != -1
    assert local_validation_pos < scp_pos


def test_sync_script_validates_extracted_archive_before_network() -> None:
    text = _script_text(_SYNC_SCRIPT)
    extract_validation_pos = text.find("local_extracted_validation_ok")
    scp_pos = text.find("\nscp ")
    assert extract_validation_pos != -1
    assert extract_validation_pos < scp_pos


def test_sync_script_sha256_computed_before_scp() -> None:
    text = _script_text(_SYNC_SCRIPT)
    sha_pos = text.find("ARCHIVE_SHA256")
    scp_pos = text.find("\nscp ")
    assert sha_pos != -1
    assert sha_pos < scp_pos


def test_sync_script_uses_atomic_symlink_promotion() -> None:
    text = _script_text(_SYNC_SCRIPT)
    assert "ln -s" in text
    assert "mv -T" in text
    assert "ln -sfn" not in text


def test_sync_script_keep_snapshots_validated_before_network() -> None:
    text = _script_text(_SYNC_SCRIPT)
    validation_pos = text.find("TT_KEEP_SNAPSHOTS")
    scp_pos = text.find("\nscp ")
    assert validation_pos < scp_pos
    assert "exit 2" in text[validation_pos:scp_pos]


def test_sync_script_uses_load_published_tender_terms_for_local_validation() -> None:
    """The sync script's local-validate step must call the T1 module's own
    fail-closed loader, not reimplement validation."""
    text = _script_text(_SYNC_SCRIPT)
    assert "load_published_tender_terms" in text
    assert "production_publish" in text


def test_sync_script_remote_has_no_python_or_uv() -> None:
    body = _remote_body(_script_text(_SYNC_SCRIPT))
    for forbidden in ("python", "python3", "uv "):
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert forbidden not in stripped, f"Remote script must not invoke {forbidden!r}: {line!r}"


def _remote_body(script_text: str) -> str:
    opening = "<<'REMOTE_SCRIPT'"
    open_pos = script_text.find(opening)
    assert open_pos != -1
    body_start = open_pos + len(opening)
    close_pos = script_text.find("\nREMOTE_SCRIPT", body_start)
    assert close_pos != -1
    return script_text[body_start:close_pos]


def test_sync_script_remote_checks_required_files() -> None:
    body = _remote_body(_script_text(_SYNC_SCRIPT))
    for fname in _REQUIRED_T1_FILES:
        assert fname in body


def test_sync_script_guards_canonical_real_directory() -> None:
    text = _script_text(_SYNC_SCRIPT)
    assert "-d " in text
    assert "! -L " in text


def test_sync_script_does_not_invoke_sends_or_outreach_or_db_writes() -> None:
    _FORBIDDEN = (
        "send_inline_html",
        "mark_sent_batch",
        "mark_outreach_state",
        "refresh_outbound_safety_memory",
        "promote_deal_from_preview",
        "build_business_mart",
        "build_commercial_intel",
        "psycopg",
        "sqlite3",
    )
    runtime_lines = [
        ln
        for ln in _script_text(_SYNC_SCRIPT).splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    runtime = "\n".join(runtime_lines)
    for forbidden in _FORBIDDEN:
        assert forbidden not in runtime, f"sync script must not call {forbidden}"


def test_sync_script_never_calls_chilecompra_acquisition() -> None:
    """The sync script may reference auto-refresh-chilecompra-equipment only
    in prose (comments or echoed operator-facing messages), never invoke it
    or any acquisition function as an actual command."""
    text = _script_text(_SYNC_SCRIPT)
    for forbidden in (
        "build_equipment_queue_from_chilecompra_api",
        "build_tender_bundle",
    ):
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert forbidden not in stripped, (
                f"sync script must not invoke acquisition entrypoint {forbidden!r}: {line!r}"
            )
    # auto-refresh-chilecompra-equipment must never appear as an actual
    # invocation (e.g. `uv run origenlab auto-refresh-chilecompra-equipment`
    # or `origenlab auto-refresh-chilecompra-equipment` as a bare command).
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "auto-refresh-chilecompra-equipment" not in stripped:
            continue
        assert "origenlab auto-refresh-chilecompra-equipment" not in stripped, (
            f"sync script must not invoke auto-refresh-chilecompra-equipment: {line!r}"
        )


# ---------------------------------------------------------------------------
# Behavioral: preflight failures stop before SCP
# ---------------------------------------------------------------------------


def test_missing_local_publication_fails_before_scp(tmp_path: Path) -> None:
    scp_called = tmp_path / "scp_called"
    stub_bin = _stub_bin(tmp_path, scp_called=scp_called)
    fake_key = _fake_ssh_key(tmp_path)

    cp = subprocess.run(
        ["bash", str(_SYNC_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
        env=_base_env(tmp_path, stub_bin, fake_key, tmp_path / "no_such_dir"),
    )

    assert cp.returncode != 0
    assert not scp_called.exists(), "scp must NOT be called when local publication is missing"
    assert "not found" in cp.stderr.lower() or "ERROR" in cp.stderr


def test_missing_summary_json_fails_before_scp(tmp_path: Path) -> None:
    scp_called = tmp_path / "scp_called"
    stub_bin = _stub_bin(tmp_path, scp_called=scp_called)
    fake_key = _fake_ssh_key(tmp_path)

    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "tender_terms.jsonl").write_text("")

    cp = subprocess.run(
        ["bash", str(_SYNC_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
        env=_base_env(tmp_path, stub_bin, fake_key, bundle_dir),
    )

    assert cp.returncode != 0
    assert not scp_called.exists()
    assert "summary.json" in cp.stderr


def test_malformed_publication_rejected_before_scp(tmp_path: Path) -> None:
    """Local validation must actually invoke load_published_tender_terms and
    fail closed on a contract-version mismatch, not just check file presence."""
    scp_called = tmp_path / "scp_called"
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    scp_stub = stub_bin / "scp"
    scp_stub.write_text(textwrap.dedent(f'#!/usr/bin/env bash\ntouch "{scp_called}"\nexit 0\n'))
    scp_stub.chmod(0o755)
    (stub_bin / "ssh").write_text("#!/usr/bin/env bash\nexit 0\n")
    (stub_bin / "ssh").chmod(0o755)
    # Real uv/python: let the real validation run against a malformed publication.
    fake_key = _fake_ssh_key(tmp_path)

    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "summary.json").write_text('{"contract_version": "wrong_version"}')
    (bundle_dir / "tender_terms.jsonl").write_text("")

    env = {
        **os.environ,
        "PATH": f"{stub_bin}:{os.environ.get('PATH', '')}",
        "ORIGENLAB_RENDER_SSH_HOST": "ssh.oregon.render.com",
        "ORIGENLAB_RENDER_SSH_USER": "srv-test",
        "ORIGENLAB_RENDER_SSH_KEY": str(fake_key),
        "ORIGENLAB_TENDER_TERMS_LOCAL_DIR": str(bundle_dir),
    }
    cp = subprocess.run(
        ["bash", str(_SYNC_SCRIPT)],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )

    assert cp.returncode != 0
    assert not scp_called.exists(), "scp must NOT be called for a malformed publication"
    assert "failed validation" in cp.stderr.lower() or "unsupported publication contract" in (
        cp.stdout + cp.stderr
    )


def test_local_extracted_archive_validation_failure_prevents_scp(tmp_path: Path) -> None:
    scp_called = tmp_path / "scp_called"
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    scp_stub = stub_bin / "scp"
    scp_stub.write_text(textwrap.dedent(f'#!/usr/bin/env bash\ntouch "{scp_called}"\nexit 0\n'))
    scp_stub.chmod(0o755)
    (stub_bin / "ssh").write_text("#!/usr/bin/env bash\nexit 0\n")
    (stub_bin / "ssh").chmod(0o755)

    call_counter = tmp_path / "uv_run_count"
    call_counter.write_text("0")
    uv_stub = stub_bin / "uv"
    uv_stub.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            if [[ "$1" == "sync" ]]; then exit 0; fi
            if [[ "$1" == "run" && "$2" == "python" ]]; then
              count=$(cat "{call_counter}")
              count=$((count + 1))
              echo "$count" > "{call_counter}"
              if [[ "$count" -ge 2 ]]; then
                echo "ERROR: simulated extraction validation failure" >&2
                exit 1
              fi
              echo "local_source_validation_ok"
              exit 0
            fi
            exit 0
            """
        )
    )
    uv_stub.chmod(0o755)

    fake_key = _fake_ssh_key(tmp_path)
    bundle_dir = tmp_path / "bundle"
    _make_minimal_bundle(bundle_dir)

    cp = subprocess.run(
        ["bash", str(_SYNC_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
        env=_base_env(tmp_path, stub_bin, fake_key, bundle_dir),
    )

    assert cp.returncode != 0
    assert not scp_called.exists()


def test_missing_ssh_key_file_fails_before_scp(tmp_path: Path) -> None:
    scp_called = tmp_path / "scp_called"
    stub_bin = _stub_bin(tmp_path, scp_called=scp_called)

    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "summary.json").write_text("{}")

    cp = subprocess.run(
        ["bash", str(_SYNC_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "PATH": f"{stub_bin}:{os.environ.get('PATH', '')}",
            "ORIGENLAB_RENDER_SSH_HOST": "ssh.oregon.render.com",
            "ORIGENLAB_RENDER_SSH_USER": "srv-test",
            "ORIGENLAB_RENDER_SSH_KEY": str(tmp_path / "no_such_key"),
            "ORIGENLAB_TENDER_TERMS_LOCAL_DIR": str(bundle_dir),
        },
    )

    assert cp.returncode != 0
    assert not scp_called.exists()


def test_missing_required_ssh_env_var_fails(tmp_path: Path) -> None:
    cp = subprocess.run(
        ["bash", str(_SYNC_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=15,
        env={
            **os.environ,
            "ORIGENLAB_RENDER_SSH_USER": "srv-test",
            "ORIGENLAB_RENDER_SSH_KEY": str(tmp_path / "fake_key"),
        },
    )
    assert cp.returncode != 0


def test_keep_snapshots_zero_fails_before_network(tmp_path: Path) -> None:
    scp_called = tmp_path / "scp_called"
    stub_bin = _stub_bin(tmp_path, scp_called=scp_called)
    fake_key = _fake_ssh_key(tmp_path)

    env = _base_env(tmp_path, stub_bin, fake_key, tmp_path / "no_such_dir")
    env["ORIGENLAB_TENDER_TERMS_KEEP_SNAPSHOTS"] = "0"

    cp = subprocess.run(
        ["bash", str(_SYNC_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    assert cp.returncode != 0
    assert not scp_called.exists()
    assert "KEEP_SNAPSHOTS" in cp.stderr


def test_keep_snapshots_non_integer_fails_before_network(tmp_path: Path) -> None:
    scp_called = tmp_path / "scp_called"
    stub_bin = _stub_bin(tmp_path, scp_called=scp_called)
    fake_key = _fake_ssh_key(tmp_path)

    env = _base_env(tmp_path, stub_bin, fake_key, tmp_path / "no_such_dir")
    env["ORIGENLAB_TENDER_TERMS_KEEP_SNAPSHOTS"] = "two"

    cp = subprocess.run(
        ["bash", str(_SYNC_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    assert cp.returncode != 0
    assert not scp_called.exists()
    assert "KEEP_SNAPSHOTS" in cp.stderr


# ---------------------------------------------------------------------------
# Promotion lifecycle regression — runs the remote promotion logic directly.
# _PROMOTION_SCRIPT mirrors the actual REMOTE_SCRIPT heredoc, including
# SHA-256 verification, required-file check, and atomic promotion. No real
# SSH/scp involved. Archive files are real tar.gz so SHA works.
# ---------------------------------------------------------------------------

_PROMOTION_SCRIPT = """\
#!/usr/bin/env bash
set -euo pipefail

STAGING="$1"
ARCHIVE="$2"
CANONICAL="$3"
REMOTE_DATA="$4"
KEEP="${5:-2}"
EXPECTED_SHA="${6:?SHA-256 of archive expected as $6}"
NEXT_LINK="${REMOTE_DATA}/tender_terms.next"

ACTUAL_SHA="$(sha256sum "${ARCHIVE}" | awk '{print $1}')"
if [[ "${ACTUAL_SHA}" != "${EXPECTED_SHA}" ]]; then
  echo "ERROR: Archive SHA-256 mismatch." >&2
  echo "  expected: ${EXPECTED_SHA}" >&2
  echo "  actual:   ${ACTUAL_SHA}" >&2
  rm -f "${ARCHIVE}"
  exit 2
fi
echo "SHA-256 verified: ${ACTUAL_SHA}"

if [[ -d "${CANONICAL}" && ! -L "${CANONICAL}" ]]; then
  echo "ERROR: ${CANONICAL} is a real directory, not a symlink." >&2
  rm -f "${ARCHIVE}"
  exit 2
fi

mkdir -p "${STAGING}"
echo "Extracting to ${STAGING} ..."
tar -xzf "${ARCHIVE}" -C "${STAGING}"
rm -f "${ARCHIVE}"

_REQUIRED=(
  "summary.json"
  "tender_terms.jsonl"
)
for _f in "${_REQUIRED[@]}"; do
  if [[ ! -f "${STAGING}/${_f}" ]]; then
    echo "ERROR: Required T1 file missing from extracted publication: ${_f}" >&2
    rm -rf "${STAGING}"
    exit 2
  fi
done

rm -f "${NEXT_LINK}"
ln -s "${STAGING}" "${NEXT_LINK}"
mv -T "${NEXT_LINK}" "${CANONICAL}"
echo "Canonical promoted: ${CANONICAL} -> ${STAGING}"

OLD_SNAPS="$(
  find "${REMOTE_DATA}" -maxdepth 1 -name 'tender_terms.*.staging' \\
    -not -path "${STAGING}" 2>/dev/null \\
    | sort \\
    | head -n "-${KEEP}" 2>/dev/null \\
    || true
)"
if [[ -n "${OLD_SNAPS}" ]]; then
  while IFS= read -r snap; do
    [[ -d "${snap}" ]] || continue
    rm -rf "${snap}"
    echo "Removed old snapshot: ${snap}"
  done <<< "${OLD_SNAPS}"
fi
"""


def _run_promotion(
    remote_data: Path,
    staging: Path,
    *,
    keep: int = 2,
    sha_override: str | None = None,
    missing_file: str | None = None,
) -> subprocess.CompletedProcess:
    bundle_src = remote_data / f"_src_{staging.name}"
    _make_minimal_bundle(bundle_src)
    if missing_file is not None:
        f = bundle_src / missing_file
        if f.exists():
            f.unlink()

    archive = remote_data / f"t1_upload_{staging.name}.tar.gz"
    actual_sha = _make_archive_and_sha(bundle_src, archive)
    expected_sha = sha_override if sha_override is not None else actual_sha

    canonical = remote_data / "tender_terms"
    script = remote_data / "_promote.sh"
    script.write_text(_PROMOTION_SCRIPT)
    script.chmod(0o755)

    return subprocess.run(
        ["bash", str(script), str(staging), str(archive), str(canonical), str(remote_data), str(keep), expected_sha],
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_remote_sha_mismatch_exits_before_extract(tmp_path: Path) -> None:
    remote_data = tmp_path / "remote"
    remote_data.mkdir()
    staging = remote_data / "tender_terms.20260101T000000Z.staging"
    canonical = remote_data / "tender_terms"

    cp = _run_promotion(remote_data, staging, sha_override="deadbeef" * 8)

    assert cp.returncode != 0
    assert "mismatch" in cp.stderr.lower()
    assert not staging.exists()
    assert not canonical.exists()


def test_remote_sha_mismatch_preserves_existing_canonical(tmp_path: Path) -> None:
    remote_data = tmp_path / "remote"
    remote_data.mkdir()
    snap1 = remote_data / "tender_terms.20260101T000000Z.staging"

    cp1 = _run_promotion(remote_data, snap1, keep=2)
    assert cp1.returncode == 0

    canonical = remote_data / "tender_terms"
    assert canonical.resolve() == snap1.resolve()

    snap2 = remote_data / "tender_terms.20260102T000000Z.staging"
    cp2 = _run_promotion(remote_data, snap2, sha_override="cafebabe" * 8)

    assert cp2.returncode != 0
    assert canonical.resolve() == snap1.resolve()
    assert not snap2.exists()


def test_remote_sha_match_can_promote(tmp_path: Path) -> None:
    remote_data = tmp_path / "remote"
    remote_data.mkdir()
    snap = remote_data / "tender_terms.20260101T000000Z.staging"
    canonical = remote_data / "tender_terms"

    cp = _run_promotion(remote_data, snap, keep=2)

    assert cp.returncode == 0, cp.stderr
    assert canonical.is_symlink()
    assert canonical.resolve() == snap.resolve()


def test_remote_missing_required_file_prevents_promotion(tmp_path: Path) -> None:
    remote_data = tmp_path / "remote"
    remote_data.mkdir()
    staging = remote_data / "tender_terms.20260101T000000Z.staging"
    canonical = remote_data / "tender_terms"

    cp = _run_promotion(remote_data, staging, missing_file="tender_terms.jsonl")

    assert cp.returncode != 0
    assert "missing" in cp.stderr.lower() or "Required" in cp.stderr
    assert not canonical.exists()
    assert not staging.exists()


def test_two_consecutive_promotions_advance_canonical(tmp_path: Path) -> None:
    remote_data = tmp_path / "remote"
    remote_data.mkdir()

    snap1 = remote_data / "tender_terms.20260101T000000Z.staging"
    snap2 = remote_data / "tender_terms.20260102T000000Z.staging"
    canonical = remote_data / "tender_terms"

    cp1 = _run_promotion(remote_data, snap1, keep=2)
    assert cp1.returncode == 0, cp1.stderr
    assert canonical.resolve() == snap1.resolve()

    cp2 = _run_promotion(remote_data, snap2, keep=2)
    assert cp2.returncode == 0, cp2.stderr
    assert canonical.resolve() == snap2.resolve()

    assert snap1.exists()
    next_inside_snap1 = snap1 / "tender_terms.next"
    assert not next_inside_snap1.exists()


def test_retention_never_deletes_current_canonical_target(tmp_path: Path) -> None:
    remote_data = tmp_path / "remote"
    remote_data.mkdir()
    canonical = remote_data / "tender_terms"
    keep = 1

    snaps = [remote_data / f"tender_terms.2026010{i}T000000Z.staging" for i in range(1, 6)]

    for i, snap in enumerate(snaps):
        cp = _run_promotion(remote_data, snap, keep=keep)
        assert cp.returncode == 0, f"promotion #{i + 1} failed:\n{cp.stderr}"
        assert canonical.resolve() == snap.resolve()
        assert canonical.exists()

    assert snaps[4].exists()
    assert snaps[3].exists()
    assert not snaps[0].exists()
    assert not snaps[1].exists()
    assert not snaps[2].exists()
    assert canonical.resolve() == snaps[4].resolve()


def test_promotion_guard_rejects_plain_directory_as_canonical(tmp_path: Path) -> None:
    remote_data = tmp_path / "remote"
    remote_data.mkdir()
    canonical = remote_data / "tender_terms"
    canonical.mkdir()

    snap = remote_data / "tender_terms.20260101T000000Z.staging"
    cp = _run_promotion(remote_data, snap, keep=2)

    assert cp.returncode != 0
    assert "real directory" in cp.stderr
    assert canonical.is_dir() and not canonical.is_symlink()

"""Tests for sync_institution_prospects_to_cloud.sh and W1 cloud-sync wiring."""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SYNC_SCRIPT = _REPO / "scripts/ops/sync_institution_prospects_to_cloud.sh"
_REFRESH = _REPO / "scripts/ops/refresh_render_dashboard_once.sh"
_RENDER_YAML = _REPO.parent.parent / "render.yaml"

# Minimum required W1 files as declared by load_published_read_model / OPERATOR_QUEUE_NAMES.
_REQUIRED_W1_FILES = (
    "institution_prospect_packet.json",
    "current_opportunity_queue.csv",
    "historical_prospect_queue.csv",
    "institution_match_review_queue.csv",
    "contact_gap_queue.csv",
    "line_evidence_review_queue.csv",
    "retender_review_queue.csv",
)


def _script_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Minimal bundle helpers used across behavioral tests
# ---------------------------------------------------------------------------


def _make_minimal_bundle(dest: Path) -> None:
    """Populate dest with the minimum required W1 files (no real planner data needed)."""
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "institution_prospect_packet.json").write_text(
        '{"contract_version": "institution_prospect_contract_v4", "profiles": []}'
    )
    for fname in _REQUIRED_W1_FILES[1:]:  # CSV files
        (dest / fname).write_text("col1\n")


def _make_archive_and_sha(src: Path, archive_path: Path) -> str:
    """Create tar.gz from src dir, return SHA-256 hex string."""
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


# ---------------------------------------------------------------------------
# render.yaml configuration (static text checks — no yaml dep required)
# ---------------------------------------------------------------------------


def test_render_yaml_api_service_has_persistent_disk() -> None:
    text = _RENDER_YAML.read_text(encoding="utf-8")
    assert "mountPath: /var/data" in text
    assert "sizeGB: 1" in text or "sizeGB:" in text


def test_render_yaml_api_service_has_institution_prospect_dir_env() -> None:
    text = _RENDER_YAML.read_text(encoding="utf-8")
    assert "ORIGENLAB_INSTITUTION_PROSPECT_DIR" in text
    assert "/var/data/institution_prospects" in text


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
    text = _script_text(_SYNC_SCRIPT)
    assert "set -euo pipefail" in text


def test_sync_script_never_echoes_ssh_key_contents() -> None:
    """SSH key path may be logged but its *contents* must never be cat/printed."""
    text = _script_text(_SYNC_SCRIPT)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "SSH_KEY" in stripped:
            assert "cat " not in stripped or "SSH_KEY" not in stripped, (
                f"Possible key content echo on line: {line}"
            )
            assert 'echo "$SSH_KEY"' not in stripped
            assert "echo ${SSH_KEY}" not in stripped


def test_sync_script_requires_ssh_env_vars() -> None:
    text = _script_text(_SYNC_SCRIPT)
    assert "ORIGENLAB_RENDER_SSH_HOST" in text
    assert "ORIGENLAB_RENDER_SSH_USER" in text
    assert "ORIGENLAB_RENDER_SSH_KEY" in text
    assert ":?" in text


def test_sync_script_validates_local_source_bundle_before_network() -> None:
    text = _script_text(_SYNC_SCRIPT)
    # local source validation (first Python call) must appear before scp
    local_validation_pos = text.find("local_source_validation_ok")
    scp_pos = text.find("\nscp ")
    assert local_validation_pos != -1, "local_source_validation_ok marker not found"
    assert scp_pos != -1, "scp not found"
    assert local_validation_pos < scp_pos, (
        "local source validation must precede SCP upload"
    )


def test_sync_script_validates_extracted_archive_before_network() -> None:
    """The extracted local archive must be validated before SCP."""
    text = _script_text(_SYNC_SCRIPT)
    extract_validation_pos = text.find("local_extracted_validation_ok")
    scp_pos = text.find("\nscp ")
    assert extract_validation_pos != -1, "local_extracted_validation_ok marker not found"
    assert extract_validation_pos < scp_pos, (
        "local extracted archive validation must precede SCP upload"
    )


def test_sync_script_sha256_computed_before_scp() -> None:
    text = _script_text(_SYNC_SCRIPT)
    sha_pos = text.find("ARCHIVE_SHA256")
    scp_pos = text.find("\nscp ")
    assert sha_pos != -1
    assert sha_pos < scp_pos, "SHA-256 must be computed before SCP upload"


def test_sync_script_passes_sha256_to_remote() -> None:
    text = _script_text(_SYNC_SCRIPT)
    # SHA must appear in the ssh invocation line (passed as positional arg)
    ssh_invocation_pos = text.find('ssh "${SSH_OPTS[@]}"')
    assert ssh_invocation_pos != -1
    # ARCHIVE_SHA256 must appear between the ssh invocation and REMOTE_SCRIPT marker
    remote_marker_pos = text.find("<<'REMOTE_SCRIPT'")
    ssh_to_remote = text[ssh_invocation_pos:remote_marker_pos]
    assert "ARCHIVE_SHA256" in ssh_to_remote, (
        "ARCHIVE_SHA256 must be passed as argument to remote bash -s"
    )


def test_sync_script_uses_atomic_symlink_promotion() -> None:
    text = _script_text(_SYNC_SCRIPT)
    assert "ln -s" in text
    # -T is required: without it GNU mv follows an existing canonical
    # symlink-to-directory and moves NEXT_LINK *inside* the old snapshot.
    assert "mv -T" in text
    # ln -sfn would be non-atomic; must not be used.
    assert "ln -sfn" not in text


def test_sync_script_keep_snapshots_validated_before_network() -> None:
    text = _script_text(_SYNC_SCRIPT)
    validation_pos = text.find("W1_KEEP_SNAPSHOTS")
    scp_pos = text.find("\nscp ")
    assert validation_pos < scp_pos
    assert "exit 2" in text[validation_pos:scp_pos]


def test_sync_script_ssh_opts_is_array() -> None:
    text = _script_text(_SYNC_SCRIPT)
    assert "SSH_OPTS=(" in text
    assert '"${SSH_OPTS[@]}"' in text


def test_sync_script_cleans_archive_and_extract_dir_on_exit() -> None:
    text = _script_text(_SYNC_SCRIPT)
    assert "trap" in text
    assert "LOCAL_ARCHIVE" in text
    assert "LOCAL_EXTRACT_DIR" in text
    # Both must be in the same trap
    trap_pos = text.find("trap ")
    trap_line_end = text.find("\n", trap_pos + 1)
    trap_line = text[trap_pos:trap_line_end]
    assert "LOCAL_ARCHIVE" in trap_line and "LOCAL_EXTRACT_DIR" in trap_line, (
        "trap must clean both LOCAL_ARCHIVE and LOCAL_EXTRACT_DIR"
    )


def test_sync_script_extract_dir_uses_mktemp() -> None:
    text = _script_text(_SYNC_SCRIPT)
    assert "mktemp -d" in text
    assert "LOCAL_EXTRACT_DIR" in text


def test_sync_script_remote_has_no_python_or_uv() -> None:
    """Remote script heredoc must not invoke python, python3, or uv."""
    body = _remote_body(_script_text(_SYNC_SCRIPT))
    for forbidden in ("python", "python3", "uv "):
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert forbidden not in stripped, (
                f"Remote script must not invoke {forbidden!r}: {line!r}"
            )


def test_sync_script_no_hardcoded_docker_or_render_paths() -> None:
    """No /app/apps/api or /opt/render/project in runtime portions of the script."""
    text = _script_text(_SYNC_SCRIPT)
    for forbidden in ("/app/apps/api", "/opt/render/project"):
        for line in text.splitlines():
            if line.strip().startswith("#"):
                continue
            assert forbidden not in line, (
                f"Hardcoded path {forbidden!r} found in non-comment line: {line!r}"
            )


def _remote_body(script_text: str) -> str:
    """Extract the body of the REMOTE_SCRIPT heredoc (between the markers)."""
    opening = "<<'REMOTE_SCRIPT'"
    open_pos = script_text.find(opening)
    assert open_pos != -1, "<<'REMOTE_SCRIPT' marker not found"
    body_start = open_pos + len(opening)
    close_pos = script_text.find("\nREMOTE_SCRIPT", body_start)
    assert close_pos != -1, "closing REMOTE_SCRIPT marker not found"
    return script_text[body_start:close_pos]


def test_sync_script_remote_requires_sha256_arg() -> None:
    """Remote script must use :? to fail-fast if SHA-256 arg is missing."""
    body = _remote_body(_script_text(_SYNC_SCRIPT))
    assert "EXPECTED_SHA" in body
    assert ":?" in body


def test_sync_script_remote_checks_required_files() -> None:
    body = _remote_body(_script_text(_SYNC_SCRIPT))
    for fname in _REQUIRED_W1_FILES:
        assert fname in body, (
            f"Remote script must check for required file: {fname}"
        )


def test_sync_script_remote_extracts_to_staging_not_canonical() -> None:
    """The remote script must extract to a versioned staging dir, not directly to canonical."""
    text = _script_text(_SYNC_SCRIPT)
    assert "staging" in text
    assert "STAGING" in text
    remote_section_start = text.find("REMOTE_SCRIPT")
    assert remote_section_start != -1
    remote_section = text[text.find("bash -s"):remote_section_start]
    assert "STAGING" in remote_section


def test_sync_script_guards_canonical_real_directory() -> None:
    """Guard: if canonical path is a real directory (not symlink), script must abort."""
    text = _script_text(_SYNC_SCRIPT)
    assert "-d " in text
    assert "! -L " in text


def test_sync_script_does_not_invoke_sends_or_outreach() -> None:
    _FORBIDDEN = (
        "send_inline_html",
        "mark_sent_batch",
        "mark_outreach_state",
        "refresh_outbound_safety_memory",
        "promote_deal_from_preview",
        "build_business_mart",
        "build_commercial_intel",
    )
    runtime_lines = [
        ln
        for ln in _script_text(_SYNC_SCRIPT).splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    runtime = "\n".join(runtime_lines)
    for forbidden in _FORBIDDEN:
        assert forbidden not in runtime, f"sync script must not call {forbidden}"


# ---------------------------------------------------------------------------
# refresh_render_dashboard_once.sh wiring
# ---------------------------------------------------------------------------


def test_refresh_w1_cloud_sync_defaults_off() -> None:
    text = _script_text(_REFRESH)
    assert 'RUN_W1_CLOUD_SYNC="${RUN_W1_CLOUD_SYNC:-0}"' in text


def test_refresh_w1_cloud_sync_is_gated() -> None:
    text = _script_text(_REFRESH)
    assert 'RUN_W1_CLOUD_SYNC" == "1"' in text


def test_refresh_w1_cloud_sync_calls_sync_script() -> None:
    text = _script_text(_REFRESH)
    assert "sync_institution_prospects_to_cloud.sh" in text


def test_refresh_w1_cloud_sync_failure_does_not_exit() -> None:
    """W1 sync failure must be non-fatal: no `exit 1` inside the W1 block."""
    text = _script_text(_REFRESH)
    w1_block_start = text.find("sync_institution_prospects_to_cloud.sh")
    assert w1_block_start != -1
    before = text[:w1_block_start]
    w1_if_start = before.rfind('if [[ "$RUN_W1_CLOUD_SYNC"')
    assert w1_if_start != -1
    block_text = text[w1_if_start:]
    depth = 0
    fi_end = None
    for i, _ in enumerate(block_text):
        segment = block_text[i:]
        if segment.startswith("if ") or segment.startswith("if\n") or segment.startswith("if [["):
            depth += 1
        elif segment.startswith("fi\n") or segment.startswith("fi\r") or segment == "fi":
            depth -= 1
            if depth == 0:
                fi_end = i + 3
                break
    assert fi_end is not None
    w1_block = block_text[:fi_end]
    assert "exit 1" not in w1_block, "W1 cloud sync block must not exit 1 on failure"
    assert "exit 2" not in w1_block, "W1 cloud sync block must not exit 2 on failure"


def test_refresh_summary_includes_w1_cloud_sync_status() -> None:
    text = _script_text(_REFRESH)
    assert "W1_CLOUD_SYNC_LABEL" in text
    assert "W1_CLOUD_SYNC_STATUS" in text
    assert "W1 institution-prospect" in text


def test_refresh_w1_sync_comment_explains_non_fatal_rationale() -> None:
    text = _script_text(_REFRESH)
    assert "non-fatal" in text.lower() or "Non-fatal" in text


# ---------------------------------------------------------------------------
# Behavioral: preflight failures stop before SCP
# ---------------------------------------------------------------------------


def test_missing_local_bundle_fails_before_scp(tmp_path: Path) -> None:
    """If the local W1 directory doesn't exist, script must exit 2 before any scp call."""
    scp_called = tmp_path / "scp_called"
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

    fake_key = tmp_path / "fake_key"
    fake_key.write_text("FAKE")
    fake_key.chmod(0o600)

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
            "ORIGENLAB_RENDER_SSH_KEY": str(fake_key),
            "ORIGENLAB_W1_LOCAL_DIR": str(tmp_path / "no_such_dir"),
        },
    )

    assert cp.returncode != 0
    assert not scp_called.exists(), "scp must NOT be called when local bundle is missing"
    assert "not found" in cp.stderr.lower() or "ERROR" in cp.stderr


def test_missing_packet_json_fails_before_scp(tmp_path: Path) -> None:
    """Local dir exists but institution_prospect_packet.json is absent → exit before scp."""
    scp_called = tmp_path / "scp_called"
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

    fake_key = tmp_path / "fake_key"
    fake_key.write_text("FAKE")
    fake_key.chmod(0o600)

    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

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
            "ORIGENLAB_RENDER_SSH_KEY": str(fake_key),
            "ORIGENLAB_W1_LOCAL_DIR": str(bundle_dir),
        },
    )

    assert cp.returncode != 0
    assert not scp_called.exists(), "scp must NOT be called when packet JSON is missing"


def test_local_extracted_archive_validation_failure_prevents_scp(tmp_path: Path) -> None:
    """
    If the local extracted archive fails read-model validation, SCP must not be called.

    Simulated by stubbing uv to:
      - succeed on first call (source bundle validation)
      - fail on second call (extracted archive validation)
    """
    scp_called = tmp_path / "scp_called"
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()

    scp_stub = stub_bin / "scp"
    scp_stub.write_text(
        textwrap.dedent(f'#!/usr/bin/env bash\ntouch "{scp_called}"\nexit 0\n')
    )
    scp_stub.chmod(0o755)
    (stub_bin / "ssh").write_text("#!/usr/bin/env bash\nexit 0\n")
    (stub_bin / "ssh").chmod(0o755)

    # uv stub: first 'run python' call succeeds (source validation),
    # second 'run python' call fails (extracted archive validation).
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

    fake_key = tmp_path / "fake_key"
    fake_key.write_text("FAKE")
    fake_key.chmod(0o600)

    bundle_dir = tmp_path / "bundle"
    _make_minimal_bundle(bundle_dir)

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
            "ORIGENLAB_RENDER_SSH_KEY": str(fake_key),
            "ORIGENLAB_W1_LOCAL_DIR": str(bundle_dir),
        },
    )

    assert cp.returncode != 0
    assert not scp_called.exists(), "scp must NOT be called when extracted archive validation fails"


def test_missing_ssh_key_file_fails_before_scp(tmp_path: Path) -> None:
    """SSH key path set but file does not exist → exit before any scp."""
    scp_called = tmp_path / "scp_called"
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

    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "institution_prospect_packet.json").write_text("{}")

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
            "ORIGENLAB_W1_LOCAL_DIR": str(bundle_dir),
        },
    )

    assert cp.returncode != 0
    assert not scp_called.exists(), "scp must NOT be called when SSH key file is missing"


def test_missing_required_ssh_env_var_fails(tmp_path: Path) -> None:
    """Missing ORIGENLAB_RENDER_SSH_HOST → script must exit non-zero immediately."""
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
    """ORIGENLAB_W1_KEEP_SNAPSHOTS=0 must be rejected before any scp call."""
    scp_called = tmp_path / "scp_called"
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
    fake_key = tmp_path / "fake_key"
    fake_key.write_text("FAKE")
    fake_key.chmod(0o600)

    cp = subprocess.run(
        ["bash", str(_SYNC_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=15,
        env={
            **os.environ,
            "PATH": f"{stub_bin}:{os.environ.get('PATH', '')}",
            "ORIGENLAB_RENDER_SSH_HOST": "ssh.oregon.render.com",
            "ORIGENLAB_RENDER_SSH_USER": "srv-test",
            "ORIGENLAB_RENDER_SSH_KEY": str(fake_key),
            "ORIGENLAB_W1_LOCAL_DIR": str(tmp_path / "no_such_dir"),
            "ORIGENLAB_W1_KEEP_SNAPSHOTS": "0",
        },
    )
    assert cp.returncode != 0
    assert not scp_called.exists(), "scp must NOT be called when KEEP_SNAPSHOTS < 1"
    assert "KEEP_SNAPSHOTS" in cp.stderr


def test_keep_snapshots_non_integer_fails_before_network(tmp_path: Path) -> None:
    scp_called = tmp_path / "scp_called"
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
    fake_key = tmp_path / "fake_key"
    fake_key.write_text("FAKE")
    fake_key.chmod(0o600)

    cp = subprocess.run(
        ["bash", str(_SYNC_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=15,
        env={
            **os.environ,
            "PATH": f"{stub_bin}:{os.environ.get('PATH', '')}",
            "ORIGENLAB_RENDER_SSH_HOST": "ssh.oregon.render.com",
            "ORIGENLAB_RENDER_SSH_USER": "srv-test",
            "ORIGENLAB_RENDER_SSH_KEY": str(fake_key),
            "ORIGENLAB_W1_LOCAL_DIR": str(tmp_path / "no_such_dir"),
            "ORIGENLAB_W1_KEEP_SNAPSHOTS": "two",
        },
    )
    assert cp.returncode != 0
    assert not scp_called.exists()
    assert "KEEP_SNAPSHOTS" in cp.stderr


# ---------------------------------------------------------------------------
# Promotion lifecycle regression — runs the remote promotion logic directly.
#
# _PROMOTION_SCRIPT mirrors the actual REMOTE_SCRIPT heredoc exactly,
# including SHA-256 verification, required-file check, and atomic promotion.
# No real SSH or scp is involved. Archive files are real tar.gz so SHA works.
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
NEXT_LINK="${REMOTE_DATA}/institution_prospects.next"

# Verify SHA-256 of the received archive.
ACTUAL_SHA="$(sha256sum "${ARCHIVE}" | awk '{print $1}')"
if [[ "${ACTUAL_SHA}" != "${EXPECTED_SHA}" ]]; then
  echo "ERROR: Archive SHA-256 mismatch." >&2
  echo "  expected: ${EXPECTED_SHA}" >&2
  echo "  actual:   ${ACTUAL_SHA}" >&2
  rm -f "${ARCHIVE}"
  exit 2
fi
echo "SHA-256 verified: ${ACTUAL_SHA}"

# Guard: canonical must not be a plain directory.
if [[ -d "${CANONICAL}" && ! -L "${CANONICAL}" ]]; then
  echo "ERROR: ${CANONICAL} is a real directory, not a symlink." >&2
  rm -f "${ARCHIVE}"
  exit 2
fi

# Extract to the versioned staging directory.
mkdir -p "${STAGING}"
echo "Extracting to ${STAGING} ..."
tar -xzf "${ARCHIVE}" -C "${STAGING}"
rm -f "${ARCHIVE}"

# Required-file sanity check.
_REQUIRED=(
  "institution_prospect_packet.json"
  "current_opportunity_queue.csv"
  "historical_prospect_queue.csv"
  "institution_match_review_queue.csv"
  "contact_gap_queue.csv"
  "line_evidence_review_queue.csv"
  "retender_review_queue.csv"
)
for _f in "${_REQUIRED[@]}"; do
  if [[ ! -f "${STAGING}/${_f}" ]]; then
    echo "ERROR: Required W1 file missing from extracted bundle: ${_f}" >&2
    rm -rf "${STAGING}"
    exit 2
  fi
done

# Atomic symlink promotion.
rm -f "${NEXT_LINK}"
ln -s "${STAGING}" "${NEXT_LINK}"
mv -T "${NEXT_LINK}" "${CANONICAL}"
echo "Canonical promoted: ${CANONICAL} -> ${STAGING}"

# Clean up old snapshots.
OLD_SNAPS="$(
  find "${REMOTE_DATA}" -maxdepth 1 -name 'institution_prospects.*.staging' \\
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
    """Run one remote promotion cycle using real tar.gz + sha256sum."""
    bundle_src = remote_data / f"_src_{staging.name}"
    _make_minimal_bundle(bundle_src)
    if missing_file is not None:
        f = bundle_src / missing_file
        if f.exists():
            f.unlink()

    archive = remote_data / f"w1_upload_{staging.name}.tar.gz"
    actual_sha = _make_archive_and_sha(bundle_src, archive)
    expected_sha = sha_override if sha_override is not None else actual_sha

    canonical = remote_data / "institution_prospects"
    script = remote_data / "_promote.sh"
    script.write_text(_PROMOTION_SCRIPT)
    script.chmod(0o755)

    return subprocess.run(
        [
            "bash",
            str(script),
            str(staging),
            str(archive),
            str(canonical),
            str(remote_data),
            str(keep),
            expected_sha,
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_remote_sha_mismatch_exits_before_extract(tmp_path: Path) -> None:
    """
    Remote SHA-256 mismatch must:
    - exit nonzero
    - not create the staging directory
    - not touch canonical
    """
    remote_data = tmp_path / "remote"
    remote_data.mkdir()
    staging = remote_data / "institution_prospects.20260101T000000Z.staging"
    canonical = remote_data / "institution_prospects"

    cp = _run_promotion(remote_data, staging, sha_override="deadbeef" * 8)

    assert cp.returncode != 0
    assert "mismatch" in cp.stderr.lower()
    assert not staging.exists(), "staging must not be created on SHA mismatch"
    assert not canonical.exists(), "canonical must not be touched on SHA mismatch"


def test_remote_sha_mismatch_preserves_existing_canonical(tmp_path: Path) -> None:
    """If canonical already exists, a SHA mismatch must leave it untouched."""
    remote_data = tmp_path / "remote"
    remote_data.mkdir()
    snap1 = remote_data / "institution_prospects.20260101T000000Z.staging"

    # Establish a good canonical via promotion #1
    cp1 = _run_promotion(remote_data, snap1, keep=2)
    assert cp1.returncode == 0

    canonical = remote_data / "institution_prospects"
    assert canonical.resolve() == snap1.resolve()

    # Attempt promotion #2 with corrupt SHA
    snap2 = remote_data / "institution_prospects.20260102T000000Z.staging"
    cp2 = _run_promotion(remote_data, snap2, sha_override="cafebabe" * 8)

    assert cp2.returncode != 0
    assert canonical.resolve() == snap1.resolve(), (
        "canonical must still point at snap1 after SHA mismatch on promotion #2"
    )
    assert not snap2.exists(), "staging must not have been created on SHA mismatch"


def test_remote_sha_match_can_promote(tmp_path: Path) -> None:
    """Correct SHA allows extraction and promotion."""
    remote_data = tmp_path / "remote"
    remote_data.mkdir()
    snap = remote_data / "institution_prospects.20260101T000000Z.staging"
    canonical = remote_data / "institution_prospects"

    cp = _run_promotion(remote_data, snap, keep=2)

    assert cp.returncode == 0, cp.stderr
    assert canonical.is_symlink()
    assert canonical.resolve() == snap.resolve()


def test_remote_missing_required_file_prevents_promotion(tmp_path: Path) -> None:
    """
    If a required W1 file is missing from the extracted bundle:
    - promotion must not occur
    - staging dir must be cleaned up
    - canonical must not be created
    """
    remote_data = tmp_path / "remote"
    remote_data.mkdir()
    staging = remote_data / "institution_prospects.20260101T000000Z.staging"
    canonical = remote_data / "institution_prospects"

    # Remove a required queue CSV before archiving
    cp = _run_promotion(
        remote_data,
        staging,
        missing_file="current_opportunity_queue.csv",
    )

    assert cp.returncode != 0
    assert "missing" in cp.stderr.lower() or "Required" in cp.stderr
    assert not canonical.exists(), "canonical must not be created when required file is missing"
    assert not staging.exists(), "staging must be cleaned when required file is missing"


def test_two_consecutive_promotions_advance_canonical(tmp_path: Path) -> None:
    """
    Regression for the mv -T bug.

    Without -T, GNU mv on promotion #2 would move NEXT_LINK *inside* snapshot1
    (because CANONICAL is a symlink-to-dir) instead of replacing it.
    With -T, canonical advances: canonical -> snap1, then canonical -> snap2.
    """
    remote_data = tmp_path / "remote"
    remote_data.mkdir()

    snap1 = remote_data / "institution_prospects.20260101T000000Z.staging"
    snap2 = remote_data / "institution_prospects.20260102T000000Z.staging"
    canonical = remote_data / "institution_prospects"

    cp1 = _run_promotion(remote_data, snap1, keep=2)
    assert cp1.returncode == 0, f"promotion #1 failed:\n{cp1.stderr}"
    assert canonical.is_symlink()
    assert canonical.resolve() == snap1.resolve(), "canonical must point at snap1 after promotion #1"

    cp2 = _run_promotion(remote_data, snap2, keep=2)
    assert cp2.returncode == 0, f"promotion #2 failed:\n{cp2.stderr}"
    assert canonical.is_symlink()
    assert canonical.resolve() == snap2.resolve(), (
        "canonical must point at snap2 after promotion #2 — if snap1, mv -T is missing"
    )

    assert snap1.exists(), "snap1 must still exist within retention window (keep=2)"
    next_inside_snap1 = snap1 / "institution_prospects.next"
    assert not next_inside_snap1.exists(), (
        "NEXT_LINK must not have been moved inside snap1 — mv -T is required"
    )


def test_retention_never_deletes_current_canonical_target(tmp_path: Path) -> None:
    """
    After many consecutive promotions (keep=1), the current canonical target
    must never be deleted, canonical must not dangle, and only the intended
    old snapshots are removed.
    """
    remote_data = tmp_path / "remote"
    remote_data.mkdir()
    canonical = remote_data / "institution_prospects"
    keep = 1

    snaps = [
        remote_data / f"institution_prospects.2026010{i}T000000Z.staging"
        for i in range(1, 6)
    ]

    for i, snap in enumerate(snaps):
        cp = _run_promotion(remote_data, snap, keep=keep)
        assert cp.returncode == 0, f"promotion #{i + 1} failed:\n{cp.stderr}"
        assert canonical.is_symlink(), f"canonical not a symlink after promotion #{i + 1}"
        assert canonical.resolve() == snap.resolve(), (
            f"canonical must point at {snap.name} after promotion #{i + 1}"
        )
        assert canonical.exists(), f"canonical is dangling after promotion #{i + 1}"

    assert snaps[4].exists(), "current canonical target (snap5) must exist"
    assert snaps[3].exists(), "one retained old snapshot (snap4) must exist (keep=1)"
    assert not snaps[0].exists(), "snap1 must have been cleaned up"
    assert not snaps[1].exists(), "snap2 must have been cleaned up"
    assert not snaps[2].exists(), "snap3 must have been cleaned up"
    assert canonical.resolve() == snaps[4].resolve()


def test_promotion_guard_rejects_plain_directory_as_canonical(tmp_path: Path) -> None:
    """If canonical is a real (non-symlink) directory, promotion must abort."""
    remote_data = tmp_path / "remote"
    remote_data.mkdir()
    canonical = remote_data / "institution_prospects"
    canonical.mkdir()

    snap = remote_data / "institution_prospects.20260101T000000Z.staging"
    cp = _run_promotion(remote_data, snap, keep=2)

    assert cp.returncode != 0
    assert "real directory" in cp.stderr
    assert canonical.is_dir() and not canonical.is_symlink()

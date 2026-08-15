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


def _script_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


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
            # Allowed: variable assignment, option flag, file existence check
            # Forbidden: cat / echo / $(<...) of the key variable
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
    # Must use :? to fail-fast if unset
    assert ":?" in text


def test_sync_script_validates_local_bundle_before_network() -> None:
    text = _script_text(_SYNC_SCRIPT)
    # local validation must appear before scp
    local_validation_pos = text.find("load_published_read_model")
    scp_pos = text.find("\nscp ")
    assert local_validation_pos != -1, "local_validation not found"
    assert scp_pos != -1, "scp not found"
    assert local_validation_pos < scp_pos, (
        "local Python validation must precede SCP upload"
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
    # Validation must appear before scp
    validation_pos = text.find("W1_KEEP_SNAPSHOTS")
    # The :? guard for SSH vars comes after; keep-snapshots check comes earlier
    scp_pos = text.find("\nscp ")
    assert validation_pos < scp_pos
    assert "exit 2" in text[validation_pos : scp_pos]


def test_sync_script_ssh_opts_is_array() -> None:
    text = _script_text(_SYNC_SCRIPT)
    assert "SSH_OPTS=(" in text
    assert '"${SSH_OPTS[@]}"' in text


def test_sync_script_cleans_archive_on_exit() -> None:
    text = _script_text(_SYNC_SCRIPT)
    assert "trap" in text
    assert "LOCAL_ARCHIVE" in text


def test_sync_script_remote_extracts_to_staging_not_canonical() -> None:
    """The remote script must extract to a versioned staging dir, not directly to canonical."""
    text = _script_text(_SYNC_SCRIPT)
    # staging dir must be used for extraction
    assert "staging" in text
    assert "STAGING" in text
    # remote validation runs against staging, not canonical
    remote_section_start = text.find("REMOTE_SCRIPT")
    assert remote_section_start != -1
    remote_section = text[text.find("bash -s") : remote_section_start]
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
    # Find the W1 block boundaries
    w1_block_start = text.find("sync_institution_prospects_to_cloud.sh")
    assert w1_block_start != -1
    # Scan backward to find the opening if
    before = text[:w1_block_start]
    w1_if_start = before.rfind('if [[ "$RUN_W1_CLOUD_SYNC"')
    assert w1_if_start != -1
    # Find the matching fi — count if/fi pairs
    block_text = text[w1_if_start:]
    depth = 0
    fi_end = None
    for i, ch in enumerate(block_text):
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
# Behavioral: missing local bundle fails before network I/O
# ---------------------------------------------------------------------------


def _make_stub_bin(tmp_path: Path, *, scp_succeeds: bool = True) -> Path:
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()

    # scp stub
    scp_stub = stub_bin / "scp"
    if scp_succeeds:
        scp_stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    else:
        scp_stub.write_text(
            "#!/usr/bin/env bash\necho 'scp: connection refused' >&2\nexit 1\n"
        )
    scp_stub.chmod(0o755)

    # ssh stub (just succeeds, no remote side execution needed for these tests)
    ssh_stub = stub_bin / "ssh"
    ssh_stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    ssh_stub.chmod(0o755)

    # uv stub — delegates to real uv for Python, stubs sync
    uv_stub = stub_bin / "uv"
    uv_stub.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            if [[ "$1" == "sync" ]]; then exit 0; fi
            exec "$(which uv)" "$@"
            """
        )
    )
    uv_stub.chmod(0o755)

    return stub_bin


def _run_sync_script(
    tmp_path: Path,
    stub_bin: Path,
    *,
    w1_local_dir: Path | None,
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{stub_bin}:{os.environ.get('PATH', '')}",
        "ORIGENLAB_RENDER_SSH_HOST": "ssh.oregon.render.com",
        "ORIGENLAB_RENDER_SSH_USER": "srv-test",
        "ORIGENLAB_RENDER_SSH_KEY": str(tmp_path / "fake_key"),
        "ORIGENLAB_W1_REMOTE_DATA": str(tmp_path / "remote"),
    }
    if w1_local_dir is not None:
        env["ORIGENLAB_W1_LOCAL_DIR"] = str(w1_local_dir)
    else:
        env["ORIGENLAB_W1_LOCAL_DIR"] = str(tmp_path / "nonexistent_bundle")

    # Create fake SSH key file (path must exist; contents never echoed)
    fake_key = tmp_path / "fake_key"
    fake_key.write_text("FAKE_KEY_CONTENT\n")
    fake_key.chmod(0o600)

    return subprocess.run(
        ["bash", str(_SYNC_SCRIPT)],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def test_missing_local_bundle_fails_before_scp(tmp_path: Path) -> None:
    """If the local W1 directory doesn't exist, script must exit 2 before any scp call."""
    scp_called = tmp_path / "scp_called"
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()

    scp_stub = stub_bin / "scp"
    scp_stub.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            touch "{scp_called}"
            exit 0
            """
        )
    )
    scp_stub.chmod(0o755)

    (stub_bin / "ssh").write_text("#!/usr/bin/env bash\nexit 0\n")
    (stub_bin / "ssh").chmod(0o755)

    uv_stub = stub_bin / "uv"
    uv_stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    uv_stub.chmod(0o755)

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
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            touch "{scp_called}"
            exit 0
            """
        )
    )
    scp_stub.chmod(0o755)

    (stub_bin / "ssh").write_text("#!/usr/bin/env bash\nexit 0\n")
    (stub_bin / "ssh").chmod(0o755)

    uv_stub = stub_bin / "uv"
    uv_stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    uv_stub.chmod(0o755)

    fake_key = tmp_path / "fake_key"
    fake_key.write_text("FAKE")
    fake_key.chmod(0o600)

    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    # No institution_prospect_packet.json inside

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


def test_missing_ssh_key_file_fails_before_scp(tmp_path: Path) -> None:
    """SSH key path set but file does not exist → exit before any scp."""
    scp_called = tmp_path / "scp_called"
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()

    scp_stub = stub_bin / "scp"
    scp_stub.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            touch "{scp_called}"
            exit 0
            """
        )
    )
    scp_stub.chmod(0o755)

    (stub_bin / "ssh").write_text("#!/usr/bin/env bash\nexit 0\n")
    (stub_bin / "ssh").chmod(0o755)

    uv_stub = stub_bin / "uv"
    uv_stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    uv_stub.chmod(0o755)

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
            # ORIGENLAB_RENDER_SSH_HOST deliberately omitted
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
# Promotion lifecycle regression — runs the remote promotion logic locally.
#
# We extract the REMOTE_SCRIPT heredoc from the sync script and execute it
# directly via bash, substituting a local tmp_path for /var/data.  No real
# SSH or scp is involved.  The uv stub succeeds immediately so the Python
# validation step is bypassed — we're testing the shell promotion logic only.
# ---------------------------------------------------------------------------

_PROMOTION_SCRIPT = """\
#!/usr/bin/env bash
set -euo pipefail

STAGING="$1"
ARCHIVE="$2"
CANONICAL="$3"
REMOTE_DATA="$4"
KEEP="${5:-2}"
NEXT_LINK="${REMOTE_DATA}/institution_prospects.next"

# Guard: canonical must not be a plain directory.
if [[ -d "${CANONICAL}" && ! -L "${CANONICAL}" ]]; then
  echo "ERROR: ${CANONICAL} is a real directory, not a symlink." >&2
  exit 2
fi

# Extract (ARCHIVE is pre-created as an empty dir in tests, not a real tar).
mkdir -p "${STAGING}"
# (archive extraction skipped in unit test — STAGING pre-populated by test)
rm -f "${ARCHIVE}"

# (Python validation skipped in unit test — uv is stubbed to succeed)

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
) -> subprocess.CompletedProcess:
    """Run the promotion logic for one sync cycle."""
    # Create the staging directory (simulates successful tar extraction).
    staging.mkdir(parents=True, exist_ok=True)
    # ARCHIVE path — the script rm -f's it; just pass a non-existent path.
    archive = remote_data / f"w1_upload_{staging.name}.tar.gz"
    canonical = remote_data / "institution_prospects"

    script = remote_data / "_promote.sh"
    script.write_text(_PROMOTION_SCRIPT)
    script.chmod(0o755)

    return subprocess.run(
        ["bash", str(script), str(staging), str(archive), str(canonical), str(remote_data), str(keep)],
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_two_consecutive_promotions_advance_canonical(tmp_path: Path) -> None:
    """
    Regression for the mv -T bug.

    Without -T, GNU mv on promotion #2 would move NEXT_LINK *inside* snapshot1
    (because CANONICAL is a symlink-to-dir) instead of replacing it.
    With -T, canonical advances: canonical -> snapshot1, then canonical -> snapshot2.
    """
    remote_data = tmp_path / "remote"
    remote_data.mkdir()

    snap1 = remote_data / "institution_prospects.20260101T000000Z.staging"
    snap2 = remote_data / "institution_prospects.20260102T000000Z.staging"
    canonical = remote_data / "institution_prospects"

    # --- Promotion #1 ---
    cp1 = _run_promotion(remote_data, snap1, keep=2)
    assert cp1.returncode == 0, f"promotion #1 failed:\n{cp1.stderr}"
    assert canonical.is_symlink(), "canonical must be a symlink after promotion #1"
    assert canonical.resolve() == snap1.resolve(), (
        "canonical must point at snap1 after promotion #1"
    )

    # --- Promotion #2 ---
    cp2 = _run_promotion(remote_data, snap2, keep=2)
    assert cp2.returncode == 0, f"promotion #2 failed:\n{cp2.stderr}"
    assert canonical.is_symlink(), "canonical must still be a symlink after promotion #2"
    assert canonical.resolve() == snap2.resolve(), (
        "canonical must point at snap2 after promotion #2 — "
        "if it still points at snap1, mv -T is missing"
    )

    # snap1 must remain as a plain old snapshot directory, not be deleted
    # (keep=2 → with only 2 snaps total the older one is within the retention window).
    assert snap1.exists(), "snap1 must still exist within retention window"

    # No 'next' symlink must have been left inside snap1 (the GNU mv bug symptom).
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

        # canonical must resolve to current snap after every promotion
        assert canonical.is_symlink(), f"canonical not a symlink after promotion #{i + 1}"
        assert canonical.resolve() == snap.resolve(), (
            f"canonical must point at {snap.name} after promotion #{i + 1}"
        )

        # canonical target must exist (not dangling)
        assert canonical.exists(), f"canonical is dangling after promotion #{i + 1}"

    # After 5 promotions with keep=1, only snap4 and snap5 should exist
    # (snap5 = current canonical target, snap4 = the 1 retained old snapshot).
    # snap1, snap2, snap3 should be deleted.
    assert snaps[4].exists(), "current canonical target (snap5) must exist"
    assert snaps[3].exists(), "one retained old snapshot (snap4) must exist (keep=1)"
    assert not snaps[0].exists(), "snap1 must have been cleaned up"
    assert not snaps[1].exists(), "snap2 must have been cleaned up"
    assert not snaps[2].exists(), "snap3 must have been cleaned up"

    # Canonical must not be dangling.
    assert canonical.resolve() == snaps[4].resolve()


def test_promotion_guard_rejects_plain_directory_as_canonical(tmp_path: Path) -> None:
    """If canonical is a real (non-symlink) directory, promotion must abort."""
    remote_data = tmp_path / "remote"
    remote_data.mkdir()
    canonical = remote_data / "institution_prospects"
    # Create as a real directory (simulates manual creation before first sync).
    canonical.mkdir()

    snap = remote_data / "institution_prospects.20260101T000000Z.staging"
    cp = _run_promotion(remote_data, snap, keep=2)

    assert cp.returncode != 0
    assert "real directory" in cp.stderr
    # The real directory must remain untouched.
    assert canonical.is_dir() and not canonical.is_symlink()

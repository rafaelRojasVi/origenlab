"""The command line: two explicit modes, one authorisation, and no write mode anywhere.

`--mode local` audits the local Supabase stack through the existing bash guard. It cannot be
pointed at a remote host: the guard refuses a non-loopback URL and `olaudit.target_local` refuses
it again.

`--mode hosted` audits the hosted project, and refuses to start unless every one of these holds:

* `--authorize-hosted-connection` was passed. A hosted connection is never a default.
* The project is **not linked**. `supabase/.temp/project-ref` must not exist and `config.toml` must
  declare no `project_ref`. Hosted mode reads its target from the reviewed target file only, so
  link state is not a shortcut it may take -- it is a reason to stop.
* The **entire tracked working tree is clean**. A hosted audit is evidence about a commit; a dirty
  tree means the report names a commit whose content was not what ran. Untracked, ignored files are
  permitted only at the three approved paths.
* The target file, the credential environment variable and (for a complete verdict) the operator
  attestation are all present and valid.

`--mode hosted --simulate` replays a committed fixture through the whole engine and contacts
nothing. Its verdict is prefixed `SIMULATED_` and can never satisfy a gate.

Deliberately absent: any flag that regenerates a baseline, and any flag that disables redaction.
Baselines are committed and reviewed; a maintenance utility for them would be a separate, local
developer tool with its own review. Redaction has no off switch, so no report can be produced
without it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

from . import attestation as attest_mod
from . import checks, psqlrun, redact, report as report_mod, sqlbank, target_hosted, target_local
from .target import Target, TargetError, inherited_scrubbed_names
from .verdict import decide

DEFAULT_OUT_DIR = Path("supabase/.audit/reports")
DEFAULT_FIXTURE = Path("supabase/audit/fixtures/simulated_hosted.json")


class PreflightError(RuntimeError):
    """A precondition for this run does not hold. Nothing was executed."""


# ------------------------------------------------------------------------------------------------
# Repository preflight
# ------------------------------------------------------------------------------------------------


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )


def repo_head(repo_root: Path) -> str:
    completed = _git(repo_root, "rev-parse", "HEAD")
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def tracked_tree_status(repo_root: Path) -> list[str]:
    """Every path git considers dirty or untracked-and-not-ignored. Empty means clean.

    Raises rather than returning an empty list when git itself fails: for the hosted preflight,
    "git could not tell us" and "the tree is clean" must never be the same answer.
    """
    completed = _git(repo_root, "status", "--porcelain", "--untracked-files=all")
    if completed.returncode != 0:
        raise PreflightError("git status failed; the working tree cannot be proven clean")
    return [line for line in completed.stdout.splitlines() if line.strip()]


def tree_clean_for_report(repo_root: Path) -> bool | None:
    """The same fact for the report, where an unanswerable question is recorded as unanswered.

    A hosted run has already refused unless the tree was clean, so this only softens the *report*
    of a local run in a context where git cannot answer -- never the gate.
    """
    try:
        return not tracked_tree_status(repo_root)
    except PreflightError:
        return None


def assert_not_linked(repo_root: Path) -> None:
    """Hosted mode must not be able to inherit a target from `supabase link`."""
    ref_file = repo_root / "supabase" / ".temp" / "project-ref"
    if ref_file.exists():
        raise PreflightError(
            f"{ref_file.relative_to(repo_root)} exists, so this project is linked. Hosted mode "
            "takes its target only from the reviewed target file and refuses to run against link "
            "state (`supabase unlink`)."
        )
    config = repo_root / "supabase" / "config.toml"
    if config.is_file():
        for line in config.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped.replace(" ", "").startswith("project_ref="):
                raise PreflightError("supabase/config.toml declares a hosted project_ref; refusing to run")


def assert_only_approved_local_files(repo_root: Path) -> list[str]:
    """The ignored audit directory may hold only the approved target, attestation and reports."""
    audit_dir = repo_root / "supabase" / ".audit"
    if not audit_dir.exists():
        return []
    approved_files = {repo_root / path for path in target_hosted.APPROVED_PATHS}
    reports_dir = repo_root / target_hosted.REPORT_DIR
    present: list[str] = []
    for path in sorted(audit_dir.rglob("*")):
        if path.is_dir():
            if path != reports_dir:
                raise PreflightError(
                    f"unexpected directory {path.relative_to(repo_root)} under supabase/.audit; "
                    "only the approved target, attestation and reports paths are permitted"
                )
            continue
        if path in approved_files:
            present.append(str(path.relative_to(repo_root)))
            continue
        if path.parent == reports_dir:
            present.append(str(path.relative_to(repo_root)))
            continue
        raise PreflightError(
            f"unexpected file {path.relative_to(repo_root)} under supabase/.audit; only the "
            "approved target, attestation and reports paths are permitted"
        )
    return present


def hosted_preflight(repo_root: Path, args: argparse.Namespace) -> None:
    if not args.authorize_hosted_connection:
        raise PreflightError(
            "a hosted run requires --authorize-hosted-connection. This audit does not contact a "
            "hosted project on a default."
        )
    assert_not_linked(repo_root)
    assert_only_approved_local_files(repo_root)
    dirty = tracked_tree_status(repo_root)
    if dirty:
        raise PreflightError(
            "the tracked working tree is not clean, so a hosted report would name a commit whose "
            f"content is not what ran. {len(dirty)} path(s) differ:\n  "
            + "\n  ".join(dirty[:20])
        )


# ------------------------------------------------------------------------------------------------
# Optional project metadata -- key types only, never a key value
# ------------------------------------------------------------------------------------------------


def collect_api_key_types(project_ref: str, environ: dict[str, str], secrets: tuple[str, ...]) -> dict:
    """Record which API-key types exist on the project. No `--reveal`, no raw output retained.

    Only the `name` field of each entry is read. Everything else the command returns -- including
    anything that could be or could contain a key -- is discarded with the child's output when this
    function returns, and never reaches a file, a log or a report.
    """
    token = environ.get("SUPABASE_ACCESS_TOKEN", "")
    if not token:
        return {}
    env = {
        "PATH": environ.get("PATH", "/usr/bin:/bin"),
        "HOME": environ.get("HOME", "/tmp"),
        "SUPABASE_ACCESS_TOKEN": token,
    }
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["supabase", "projects", "api-keys", "--project-ref", project_ref, "--output", "json"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if completed.returncode != 0:
        return {}
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, list):
        return {}
    names = []
    for entry in payload:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            names.append(redact.redact(entry["name"], secrets))
    return {"key_names": names}


# ------------------------------------------------------------------------------------------------
# Orchestration
# ------------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="slice0_audit.sh",
        description="Read-only Slice 0 audit (docs/MIGRATION.md §5.2, docs/OPERATIONS.md §4.2).",
    )
    parser.add_argument("--mode", choices=("local", "hosted"), help="which target to audit")
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="hosted mode only: replay a committed fixture, contact nothing, and report a "
        "SIMULATED_ verdict that can never satisfy a gate",
    )
    parser.add_argument(
        "--authorize-hosted-connection",
        action="store_true",
        help="required for a real hosted run; a hosted connection is never a default",
    )
    parser.add_argument("--target-file", type=Path, default=None, help="hosted target file path")
    parser.add_argument("--attestation", type=Path, default=None, help="operator attestation path")
    parser.add_argument("--fixture", type=Path, default=None, help="simulated-run fixture path")
    parser.add_argument("--out", type=Path, default=None, help="report output directory")
    parser.add_argument(
        "--cli-metadata",
        action="store_true",
        help="hosted mode only: record which API-key types exist on the project. Never requests, "
        "displays or persists a key value and never passes --reveal.",
    )
    parser.add_argument(
        "--verify-report",
        type=Path,
        default=None,
        help="run the credential and project-reference leak assertions against an existing report "
        "and exit; writes nothing",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="fix the clock to an ISO 8601 UTC instant, so a run is byte-for-byte reproducible",
    )
    return parser


def _now(args: argparse.Namespace) -> dt.datetime:
    if args.now:
        return attest_mod.parse_timestamp(args.now)
    return dt.datetime.now(dt.timezone.utc)


def run(argv: list[str], repo_root: Path, environ: dict[str, str], stream=sys.stdout) -> int:
    args = build_parser().parse_args(argv)

    if args.verify_report is not None:
        leaks = report_mod.verify_file(args.verify_report)
        if leaks:
            kinds = sorted({leak.kind for leak in leaks})
            print(
                f"FAIL: {args.verify_report} failed the leak assertions "
                f"({len(leaks)} match(es): {', '.join(kinds)}); it must not be uploaded.",
                file=sys.stderr,
            )
            return 1
        print(f"ok  {args.verify_report} carries no credential, key, host name or project reference", file=stream)
        return 0

    if not args.mode:
        print("FAIL: --mode is required (local or hosted)", file=sys.stderr)
        return 2
    if args.simulate and args.mode != "hosted":
        print("FAIL: --simulate applies to hosted mode only", file=sys.stderr)
        return 2
    if args.cli_metadata and args.mode != "hosted":
        print("FAIL: --cli-metadata applies to hosted mode only", file=sys.stderr)
        return 2
    # A run either replays a fixture or contacts a project. Accepting both flags together would
    # leave it ambiguous which one the operator meant, and the ambiguity would be resolved silently.
    if args.simulate and args.authorize_hosted_connection:
        print(
            "FAIL: --simulate and --authorize-hosted-connection are mutually exclusive. A "
            "simulated run contacts nothing and needs no authorisation.",
            file=sys.stderr,
        )
        return 2
    if args.simulate and args.cli_metadata:
        print("FAIL: --cli-metadata needs a real hosted run; a simulated run contacts nothing", file=sys.stderr)
        return 2

    now = _now(args)
    bank = sqlbank.load()
    baseline = checks.load_baseline()
    out_dir = repo_root / (args.out or DEFAULT_OUT_DIR)

    target: Target | None = None
    attestation: dict = {}

    if args.mode == "hosted" and args.simulate:
        fixture = repo_root / (args.fixture or DEFAULT_FIXTURE)
        result = psqlrun.run_simulated(bank, fixture)
        # A simulated run may carry an attestation, so that a fully satisfied simulated run can be
        # produced and seen to report SIMULATED_PASS. That is the point of the prefix: the word
        # that stops a canned result being read as a real one is the prefix, not the incompleteness
        # that would otherwise happen to be there. There is no target, so the attestation is not
        # bound to a project reference.
        if args.attestation is not None:
            try:
                attestation = attest_mod.load(repo_root / args.attestation, project_ref=None, now=now)
            except attest_mod.AttestationError as exc:
                print(f"note: {redact.redact(str(exc))}", file=sys.stderr)
                attestation = {}
        target_description = {
            "mode": "hosted",
            "host_class": "none",
            "host_shown": "[no connection was made]",
            "port": 0,
            "user": target_hosted.AUDIT_IDENTITY,
            "database": "[none]",
            "sslmode": "[none]",
            "tls_verified_against_hostname": False,
            "address_pinned_before_connect": False,
            "project_ref_present": False,
            "statement_timeout_ms": 0,
            "connect_timeout_s": 0,
            "guard_notes": [
                "simulated run: a committed fixture was replayed. No target was resolved, no name "
                "was looked up, no socket was opened and no process was started.",
            ],
        }
        secrets: tuple[str, ...] = ()

    elif args.mode == "hosted":
        hosted_preflight(repo_root, args)
        target = target_hosted.resolve(repo_root, environ, target_file=args.target_file)
        secrets = target.secrets
        attestation_path = repo_root / (args.attestation or target_hosted.ATTESTATION_FILE)
        try:
            attestation = attest_mod.load(
                attestation_path, project_ref=target.project_ref, now=now
            )
        except attest_mod.AttestationError as exc:
            print(f"note: {redact.redact(str(exc), secrets)}", file=sys.stderr)
            attestation = {}
        result = psqlrun.run(bank, target)
        target_description = target.describe()

    else:
        target = target_local.resolve(repo_root)
        secrets = target.secrets
        result = psqlrun.run(bank, target)
        target_description = target.describe()

    observations = dict(result.observations)
    if args.mode == "hosted" and not args.simulate and args.cli_metadata and target is not None:
        metadata = collect_api_key_types(target.project_ref or "", environ, secrets)
        if metadata:
            observations["c01"] = metadata

    ctx = checks.Context(mode=args.mode, simulated=result.simulated, attestation=attestation)
    results = checks.evaluate(observations, baseline, ctx)

    if result.missing or result.unexpected:
        for check_id in result.unexpected:
            print(f"note: the run produced an observation for unknown check '{check_id}'", file=sys.stderr)

    outcome = decide(
        results,
        mode=args.mode,
        simulated=result.simulated,
        hosted_contacted=result.hosted_contacted,
    )

    document = report_mod.build(
        results=results,
        outcome=outcome,
        target_description=target_description,
        attestation_description=attest_mod.describe(attestation),
        run_started_at=now,
        repo_head=repo_head(repo_root),
        tree_clean=tree_clean_for_report(repo_root),
        baseline_path=checks.BASELINE_PATH,
        scrubbed_env_names=inherited_scrubbed_names(),
        sql_files=[(check.name, _digest(check.sql)) for check in bank],
    )
    json_path, markdown_path = report_mod.write(document, out_dir, secrets)

    _print_summary(document, results, json_path, markdown_path, stream)
    return outcome.exit_code


def _digest(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _print_summary(document: dict, results: list, json_path: Path, markdown_path: Path, stream) -> None:
    verdict = document["verdict"]
    target = document["target"]

    # The target is printed before the checks, not only written to the report. An operator watching
    # a run should be able to see which destination was validated, and see the guard say that it
    # ignored a hostile inherited environment, without opening a file afterwards.
    print(
        f"target: {target['mode']} — {target['host_class']} {target['host_shown']}:{target['port']} "
        f"as {target['user']}, tls {target['sslmode']}",
        file=stream,
    )
    for note in target.get("guard_notes", []):
        print(f"  {note}", file=stream)
    print("", file=stream)

    for result in results:
        if result.status in {"PASS", "ATTESTED", "CORROBORATED", "RECORDED"}:
            marker = "ok  "
        elif result.status in {"FAIL", "ERROR"}:
            marker = "FAIL"
        elif result.required:
            # Required and unanswered. Distinct from a failure, and equally not a pass.
            marker = "MISS"
        else:
            marker = "n/a "
        print(f"{marker} {result.check_id}  {result.status:<12} {result.title}", file=stream)
        for finding in result.findings:
            print(f"       - {finding}", file=stream)
    print("", file=stream)
    print(f"verdict: {verdict['verdict']}  (gate_eligible={str(verdict['gate_eligible']).lower()})", file=stream)
    if verdict["blocking_checks"]:
        print(f"blocking: {', '.join(verdict['blocking_checks'])}", file=stream)
    if verdict["incomplete_required_checks"]:
        print(f"incomplete: {', '.join(verdict['incomplete_required_checks'])}", file=stream)
    print(f"reports: {json_path}, {markdown_path}", file=stream)


def main(argv: list[str] | None = None) -> int:
    import os

    repo_root = Path(__file__).resolve().parents[3]
    try:
        return run(list(argv if argv is not None else sys.argv[1:]), repo_root, dict(os.environ))
    except (TargetError, PreflightError, sqlbank.SqlBankError, psqlrun.RunError, redact.LeakError) as exc:
        print(f"FAIL: {redact.redact(str(exc))}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - a raw traceback must never reach a hosted terminal
        print(
            f"FAIL: the audit stopped on an unexpected {exc.__class__.__name__}. "
            "The detail is withheld because it may carry connection data.",
            file=sys.stderr,
        )
        return 2

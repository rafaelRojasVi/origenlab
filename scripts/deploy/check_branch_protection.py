#!/usr/bin/env python3
"""Fail closed unless main is protected against unreviewed production code merges.

This must be checked BEFORE installing production secrets, not only at deploy-time.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

REPOSITORY = "rafaelRojasVi/origenlab"
RULESET_ID = 17174991
# Only require a universally running PR check. Heavy API/worker/dashboard/Supabase
# workflows remain path-filtered on PRs and are required for every MAIN release.
# Requiring them on all PRs would deadlock unrelated/docs-only PRs.
REQUIRED_CHECKS = frozenset({"gitleaks"})


class Unprotected(RuntimeError):
    pass


def validate(rule: dict) -> None:
    if rule.get("id") != RULESET_ID or rule.get("enforcement") != "active":
        raise Unprotected("Production main ruleset not active")
    refs = rule.get("conditions", {}).get("ref_name", {})
    if "~DEFAULT_BRANCH" not in refs.get("include", []) or refs.get("exclude"):
        raise Unprotected("Production ruleset does not cover the unexcluded default branch")
    if rule.get("bypass_actors"):
        raise Unprotected("Production ruleset has bypass actors")
    rules = {r.get("type"): r.get("parameters", {}) for r in rule.get("rules", [])}
    pull = rules.get("pull_request")
    if pull is None or pull.get("required_approving_review_count", 0) < 1 \
            or not pull.get("dismiss_stale_reviews_on_push"):
        raise Unprotected("Require one independent approval and dismiss stale approvals")
    checks = rules.get("required_status_checks")
    if checks is None or not checks.get("strict_required_status_checks"):
        raise Unprotected("Require strict branch-up-to-date status checks")
    present = {x.get("context") for x in checks.get("required_status_checks", [])}
    missing = REQUIRED_CHECKS - present
    if missing:
        raise Unprotected("Missing mandatory status checks: " + ", ".join(sorted(missing)))


def fetch_rule() -> dict:
    endpoint = f"https://api.github.com/repos/{REPOSITORY}/rulesets/{RULESET_ID}"
    if sys.argv[1:] == ["--local"]:
        result = subprocess.run(["gh", "api", f"repos/{REPOSITORY}/rulesets/{RULESET_ID}"],
                                capture_output=True, text=True, check=False, timeout=30)
        if result.returncode:
            raise Unprotected("GitHub CLI cannot read the production ruleset")
        return json.loads(result.stdout)
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise Unprotected("Read-only GitHub token missing")
    req = urllib.request.Request(endpoint,
          headers={"Authorization": "Bearer " + token,
                   "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except (urllib.error.URLError, ValueError) as exc:
        raise Unprotected("Could not verify protected main ruleset") from exc


if __name__ == "__main__":
    try:
        validate(fetch_rule())
        print("Main protection verified: reviewed PR, up-to-date CI checks, no bypass")
    except (Unprotected, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        print(f"Configure {REPOSITORY} ruleset 'Protect main' before production release.", file=sys.stderr)
        sys.exit(1)

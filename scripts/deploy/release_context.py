#!/usr/bin/env python3
"""Authenticate event provenance before production steps; missing flags are OFF."""
import json
import os
import re
import sys

REPOSITORY = "rafaelRojasVi/origenlab"


def validate(event_name, event, ref, sha, mode, release_enabled, backup_enabled):
    if ref != "refs/heads/main" or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Production runs require main and an exact SHA")
    if event.get("repository", {}).get("full_name") != REPOSITORY:
        raise ValueError("Unexpected event repository")
    if event_name == "workflow_run":
        run = event.get("workflow_run", {})
        if (run.get("event"), run.get("head_branch"), run.get("head_sha"), run.get("conclusion"),
                run.get("head_repository", {}).get("full_name")) != (
                "push", "main", sha, "success", REPOSITORY):
            raise ValueError("Untrusted workflow_run provenance")
        if mode != "release":
            raise ValueError("workflow_run must use release mode")
    elif event_name == "schedule":
        if mode != "backup_only" or backup_enabled != "true":
            raise ValueError("Scheduled backups remain on hold until explicitly enabled")
    elif event_name != "workflow_dispatch":
        raise ValueError("Unsupported production event")
    if mode not in {"plan", "backup_only", "release"}:
        raise ValueError("Unexpected execution mode")
    if mode == "release" and release_enabled != "true":
        raise ValueError("First-release hold active; prove restoration before activation")


if __name__ == "__main__":
    try:
        with open(os.environ["GITHUB_EVENT_PATH"]) as stream:
            event = json.load(stream)
        validate(os.environ["GITHUB_EVENT_NAME"], event, os.environ["GITHUB_REF"],
                 os.environ["EXPECTED_SHA"], os.environ["EXECUTION_MODE"],
                 os.environ.get("RELEASE_ENABLED", ""), os.environ.get("BACKUP_ENABLED", ""))
    except (ValueError, KeyError, OSError) as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        sys.exit(1)

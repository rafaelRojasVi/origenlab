#!/usr/bin/env python3
"""One-time explicit Render auto-deploy disable. No deploys, stops or service restarts.

Use only after release environment secrets and CI have been prepared. Scope is the
four existing OrigenLab services. Refuses unknown owners/repositories/branches.
"""
from __future__ import annotations

import os
import sys
from render_release import OWNER, REPOSITORY, SERVICES, Refused, request


def check_identity(service: dict, expected_id: str) -> None:
    if service.get("id") != expected_id or service.get("ownerId") != OWNER \
            or service.get("repo", "").rstrip("/").removesuffix(".git") != REPOSITORY \
            or service.get("branch") != "main":
        raise Refused("Refusing to modify a non-OrigenLab main service")


def configure(token: str) -> None:
    if not token:
        raise Refused("Missing local Render API key")
    # All identities must be verified before the first mutation.
    verified = []
    for name, ident in SERVICES:
        service = request("GET", f"/services/{ident}", token)
        check_identity(service, ident)
        verified.append((name, ident, service.get("autoDeployTrigger")))
    for name, ident, trigger in verified:
        if trigger == "off":
            print(f"ALREADY OFF: {name}")
            continue
        if trigger not in {"commit", "checksPass"}:
            raise Refused(f"Unexpected auto-deploy state for {name}")
        request("PATCH", f"/services/{ident}", token, {"autoDeployTrigger": "off"})
        after = request("GET", f"/services/{ident}", token)
        check_identity(after, ident)
        if after.get("autoDeployTrigger") != "off":
            raise Refused(f"Render did not confirm auto-deploy disabled for {name}")
        print(f"DISABLED independent auto-deploy: {name}")
    print("All four Render services are now gated for CI-controlled releases.")
    print("No deploys were triggered and no services were stopped.")


if __name__ == "__main__":
    try:
        if sys.argv[1:] != ["--disable-autodeploy"]:
            raise Refused("Explicit --disable-autodeploy argument required")
        configure(os.environ.get("RENDER_API_KEY", ""))
    except (OSError, Refused) as exc:
        print("REFUSED: " + str(exc), file=sys.stderr)
        sys.exit(1)

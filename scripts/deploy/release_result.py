#!/usr/bin/env python3
"""Never label a partial or skipped release successful.

Preflight, deploy and verify must succeed on every release. Backup and migrate must succeed,
except in one case: the preflight's migration plan reported exactly "0" pending migrations, and
then both must be "skipped" (the deliberate skip) or "success". A failure, a cancellation, an
unknown result or an unknown pending count is never read as a skip.
"""
from __future__ import annotations

import os
import sys


class ReleaseIncomplete(RuntimeError):
    pass


def outcome(preflight: str, pending: str, backup: str, migrate: str, deploy: str, verify: str) -> str:
    for job, result in (("preflight", preflight), ("deploy", deploy), ("verify", verify)):
        if result != "success":
            raise ReleaseIncomplete(f"{job} did not succeed ({result or 'missing'})")
    if pending == "0":
        for job, result in (("backup", backup), ("migrate", migrate)):
            if result not in {"skipped", "success"}:
                raise ReleaseIncomplete(f"{job} did not succeed or skip deliberately ({result or 'missing'})")
        if backup == "skipped" and migrate == "success":
            raise ReleaseIncomplete("migrate ran without its backup")
        if (backup, migrate) == ("skipped", "skipped"):
            return "Release complete: no pending migration (backup and migrate skipped), deployment and postflight passed"
    else:
        for job, result in (("backup", backup), ("migrate", migrate)):
            if result != "success":
                raise ReleaseIncomplete(
                    f"{job} did not succeed ({result or 'missing'}) with pending migrations {pending or 'unknown'}")
    return "Release complete: backup, migration, deployment and postflight passed"


if __name__ == "__main__":
    try:
        print(outcome(*(os.environ.get(k, "") for k in (
            "PREFLIGHT_RESULT", "PENDING_MIGRATIONS", "BACKUP_RESULT", "MIGRATE_RESULT",
            "DEPLOY_RESULT", "VERIFY_RESULT"))))
    except ReleaseIncomplete as exc:
        print("RELEASE INCOMPLETE: " + str(exc), file=sys.stderr)
        sys.exit(1)

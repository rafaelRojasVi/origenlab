"""OrigenLab V2 — Slice 0 audit engine.

A read-only auditor for the Slice 0 foundation (docs/MIGRATION.md §5.2), able to run against the
local Supabase stack or, under an explicit authorisation, against the hosted project.

Standard library only, by design: the audit must be runnable from a clean checkout with nothing
installed beyond python3 and psql, and it must not pull a dependency tree into the one tool that is
allowed to touch the hosted project.

The engine has no write mode. It issues no INSERT, UPDATE, DELETE, DDL, sequence change or any
other intentional mutation, in either mode; see olaudit.sqlbank for the static guarantee and
olaudit.psqlrun for the session boundary.
"""

__all__ = [
    "attestation",
    "checks",
    "cli",
    "psqlrun",
    "redact",
    "report",
    "sqlbank",
    "target_hosted",
    "target_local",
    "verdict",
]

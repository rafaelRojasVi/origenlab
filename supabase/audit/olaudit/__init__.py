"""OrigenLab V2 — Slice 0 audit engine.

A read-only auditor for the Slice 0 foundation (docs/MIGRATION.md §5.2), able to run against the
local Supabase stack or, under an explicit authorisation, against the hosted project.

Standard library only, by design: the audit must be runnable from a clean checkout with nothing
installed beyond python3 and psql, and it must not pull a dependency tree into the one tool that is
allowed to touch the hosted project.

The engine has no write mode. It issues no INSERT, UPDATE, DELETE, DDL, sequence change or any
other intentional mutation, in either mode; see olaudit.sqlbank for the static guarantee and
olaudit.psqlrun for the session boundary.

`olaudit.bootstrap` and `olaudit.bootstrap_cli` are the hosted role bootstrap. They are the one
part of this package concerned with a file that *writes* -- and they too open no connection in any
mode: the bootstrap tool statically proves supabase/hosted_roles.sql is nothing but role management
over four names and prints it, and an operator applies it separately. Its static analyser shares no
code with olaudit.sqlbank, so a relaxation in either cannot widen the other.
"""

__all__ = [
    "attestation",
    "bootstrap",
    "bootstrap_cli",
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

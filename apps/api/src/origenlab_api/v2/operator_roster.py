"""Approve named dashboard operators from a local roster file.

Signing in with Google proves an address; it grants nothing. Access to the dashboard is the
`platform.operator` row for that address (`auth_routes.google_callback`, re-checked on every
request by `GoogleSessionIdentity`). This module is the one way this repository creates or
changes those rows for real people:

* The roster is a local JSON file **outside the repository** — the repository is public and
  must never carry a real address. Each entry names one person: `email`, `display_name`,
  `role` (`admin` | `sales` | `viewer`) and optionally `status` (`active` | `disabled`).
* Every address must be exactly `@<workspace_domain>`. Belonging to the domain is a
  precondition checked here and again at sign-in; it is never the grant.
* `plan` reads only. `apply` runs the same plan in one transaction, and only when the caller
  states how many changes it expects, so a roster edited since the plan was reviewed cannot
  slip extra changes through.
* Rows not in the roster are reported and left alone: this never disables or deletes anyone.
* Output masks every address (`r***@origenlab.cl`), so a plan can be pasted into a review.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROLES = ("admin", "sales", "viewer")
STATUSES = ("active", "disabled")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ALLOWED_KEYS = {"email", "display_name", "role", "status"}


class RosterRefused(ValueError):
    """The roster is unsafe or malformed; nothing was read from or written to the database."""


@dataclass(frozen=True)
class RosterEntry:
    email_norm: str
    display_name: str
    role: str
    status: str


@dataclass(frozen=True)
class Change:
    action: str  # insert | update | unchanged
    entry: RosterEntry
    operator_id: str | None
    fields: tuple[str, ...]


@dataclass(frozen=True)
class Plan:
    changes: tuple[Change, ...]
    left_alone: int

    @property
    def pending(self) -> tuple[Change, ...]:
        return tuple(c for c in self.changes if c.action != "unchanged")


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


def refuse_path_inside(path: Path, repo_root: Path) -> None:
    resolved, root = path.resolve(), repo_root.resolve()
    if resolved == root or root in resolved.parents:
        raise RosterRefused(
            "the roster must live outside the repository (it is public); keep it under ~/data"
        )


def parse_roster(raw: Any, *, workspace_domain: str) -> tuple[RosterEntry, ...]:
    domain = workspace_domain.strip().lower()
    if not domain or "@" in domain:
        raise RosterRefused("a workspace domain is required")
    if not isinstance(raw, list) or not raw:
        raise RosterRefused("the roster must be a non-empty JSON list of people")
    entries: list[RosterEntry] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        where = f"entry {i + 1}"
        if not isinstance(item, dict):
            raise RosterRefused(f"{where}: must be an object")
        unknown = set(item) - _ALLOWED_KEYS
        if unknown:
            raise RosterRefused(f"{where}: unknown field(s) {sorted(unknown)}")
        email = str(item.get("email") or "").strip().lower()
        if not _EMAIL.match(email):
            raise RosterRefused(f"{where}: not an email address")
        if email.rpartition("@")[2] != domain:
            raise RosterRefused(f"{where}: {mask_email(email)} is not an @{domain} account")
        if email in seen:
            raise RosterRefused(f"{where}: {mask_email(email)} appears twice")
        seen.add(email)
        name = str(item.get("display_name") or "").strip()
        if not name:
            raise RosterRefused(f"{where}: display_name is required")
        role = item.get("role")
        if role not in ROLES:
            raise RosterRefused(f"{where}: role must be one of {', '.join(ROLES)}")
        status = item.get("status", "active")
        if status not in STATUSES:
            raise RosterRefused(f"{where}: status must be one of {', '.join(STATUSES)}")
        entries.append(RosterEntry(email, name, str(role), str(status)))
    return tuple(entries)


def load_roster(path: Path, *, workspace_domain: str, repo_root: Path) -> tuple[RosterEntry, ...]:
    refuse_path_inside(path, repo_root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RosterRefused(f"cannot read the roster: {exc.__class__.__name__}") from exc
    return parse_roster(raw, workspace_domain=workspace_domain)


def _existing(cur: Any) -> dict[str, dict[str, Any]]:
    cur.execute(
        "select id::text, email_norm, display_name, role, status, version from platform.operator"
    )
    cols = ("id", "email_norm", "display_name", "role", "status", "version")
    return {row[1]: dict(zip(cols, row)) for row in cur.fetchall()}


def compute_plan(entries: Iterable[RosterEntry], existing: dict[str, dict[str, Any]]) -> Plan:
    changes: list[Change] = []
    named: set[str] = set()
    for e in entries:
        named.add(e.email_norm)
        row = existing.get(e.email_norm)
        if row is None:
            changes.append(Change("insert", e, None, ("display_name", "role", "status")))
            continue
        fields = tuple(
            f for f in ("display_name", "role", "status") if row[f] != getattr(e, f)
        )
        changes.append(Change("update" if fields else "unchanged", e, row["id"], fields))
    return Plan(tuple(changes), left_alone=len(set(existing) - named))


def plan(conn: Any, entries: Iterable[RosterEntry]) -> Plan:
    """Read-only: compare the roster with `platform.operator`."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("set transaction read only")
        return compute_plan(entries, _existing(cur))


def apply(conn: Any, entries: Iterable[RosterEntry], *, expected_changes: int) -> Plan:
    """Apply the plan in one transaction; refuse if it no longer has `expected_changes`."""
    entries = tuple(entries)
    with conn.transaction(), conn.cursor() as cur:
        result = compute_plan(entries, _existing(cur))
        if len(result.pending) != expected_changes:
            raise RosterRefused(
                f"the plan has {len(result.pending)} change(s), not the {expected_changes} "
                "you confirmed; re-run the plan and review it again"
            )
        for c in result.pending:
            e = c.entry
            if c.action == "insert":
                cur.execute(
                    "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                    "values (gen_random_uuid(), %s, %s, %s, %s)",
                    (e.email_norm, e.display_name, e.role, e.status),
                )
            else:
                cur.execute(
                    "update platform.operator set display_name = %s, role = %s, status = %s, "
                    "version = version + 1, updated_at = now() where id = %s",
                    (e.display_name, e.role, e.status, c.operator_id),
                )
    return result


def describe(result: Plan) -> list[str]:
    lines = []
    for c in result.changes:
        what = c.action if c.action != "update" else f"update {', '.join(c.fields)}"
        lines.append(
            f"{what:<28} {mask_email(c.entry.email_norm):<28} role={c.entry.role:<7} "
            f"status={c.entry.status}"
        )
    lines.append(f"pending changes: {len(result.pending)}")
    lines.append(
        f"to apply this plan: --apply --confirm-changes {len(result.pending)}"
        if result.pending
        else "nothing to apply"
    )
    lines.append(f"operators not in the roster (left unchanged): {result.left_alone}")
    return lines

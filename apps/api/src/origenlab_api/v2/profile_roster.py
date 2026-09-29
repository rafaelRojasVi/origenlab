"""Provision a shared Google sign-in and its operator profiles from a local roster file.

`docs/ARCHITECTURE.md` §5.1, `docs/DOMAIN.md` §7.3; procedure in
`apps/api/docs/PRODUCTION_AUTH.md` ("Shared Workspace login with operator profiles").

The roster is a JSON file **outside the repository** (the repository is public):

    {
      "principal": {"email": "cuenta@<workspace domain>", "status": "active"},
      "profiles": [
        {"key": "ana", "display_name": "Ana", "role": "admin", "sort_order": 1},
        {"key": "luis", "display_name": "Luis", "role": "sales", "status": "active"}
      ]
    }

* The principal address must belong to the workspace domain. It must not be an operator's
  own address (the database refuses that too).
* `provider_subject` is the Google account's stable subject (the ID token's `sub`; the Admin
  SDK's user `id`). **Production refuses a principal without one**, and refuses a token whose
  subject differs from it even for the same address — a deleted and recreated account keeps
  the address and gets a new subject. It is stored with the canonical Google issuer. Omitting
  it leaves a pinned subject unchanged; this tool never unpins one. Changing it is a re-pin,
  shown as such in the plan, and ends every session of the principal (its version moves).
* A profile is identified by its `key` within the principal. `status` (default `active`)
  applies to the profile and its operator together.
* PINs are **never** in the roster and never on the command line. They come from a hidden
  terminal prompt, or from a separate PIN file outside the repository that only its owner can
  read (`0600`). A new profile needs a PIN; an existing one gets a new PIN only when one is
  given. Every PIN must pass `profile_pin.validate_new_pin` before anything is planned.
* `plan` reads only. `apply` recomputes the plan in one transaction and refuses unless the
  caller states the exact number of changes and the exact database name — a roster, PIN file
  or database that changed since the plan was reviewed cannot slip anything through.
* Profiles of the principal that the roster does not name are reported and left alone: this
  never disables or deletes anyone. Disable a person by setting `"status": "disabled"`.
* Output masks every address and never shows a PIN or a hash.

Writes run as `origenlab_owner` (`set local role`), because the runtime API role can neither
write a principal, link a profile nor store a PIN hash. The login used must be one that may
assume the owner — the migrator — and never a runtime login.
"""

from __future__ import annotations

import json
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from origenlab_api.v2.google_oidc import GOOGLE_CANONICAL_ISSUER
from origenlab_api.v2.operator_roster import mask_email, refuse_path_inside
from origenlab_api.v2.profile_pin import PinHasher, PinPolicyRefused, validate_new_pin

ROLES = ("admin", "sales", "viewer")
STATUSES = ("active", "disabled")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_KEY = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")
_PRINCIPAL_KEYS = {"email", "status", "provider_subject"}
_PROFILE_KEYS = {"key", "display_name", "role", "status", "sort_order"}


class ProfileRosterRefused(ValueError):
    """The roster, PIN input or target is unsafe or malformed; nothing was written."""


@dataclass(frozen=True)
class PrincipalEntry:
    email_norm: str
    status: str
    provider_subject: str | None


@dataclass(frozen=True)
class ProfileEntry:
    slug: str
    display_name: str
    role: str
    status: str
    sort_order: int


@dataclass(frozen=True)
class Roster:
    principal: PrincipalEntry
    profiles: tuple[ProfileEntry, ...]


@dataclass(frozen=True)
class Change:
    subject: str          # "principal" or "profile <key>"
    action: str           # insert | update | set_pin
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    database: str
    principal_email: str
    changes: tuple[Change, ...]
    unchanged: tuple[str, ...]
    left_alone: int
    #: True when, after this plan, the principal still has no pinned Google account.
    unpinned: bool = False

    @property
    def pending(self) -> int:
        return len(self.changes)


# ------------------------------------------------------------------------------ input


def parse_roster(raw: Any, *, workspace_domain: str) -> Roster:
    domain = workspace_domain.strip().lower()
    if not domain or "@" in domain:
        raise ProfileRosterRefused("a workspace domain is required")
    if not isinstance(raw, dict) or set(raw) != {"principal", "profiles"}:
        raise ProfileRosterRefused('the roster is an object with exactly "principal" and "profiles"')
    p = raw["principal"]
    if not isinstance(p, dict) or set(p) - _PRINCIPAL_KEYS:
        raise ProfileRosterRefused(f"principal: allowed fields are {sorted(_PRINCIPAL_KEYS)}")
    email = str(p.get("email") or "").strip().lower()
    if not _EMAIL.match(email):
        raise ProfileRosterRefused("principal: not an email address")
    if email.rpartition("@")[2] != domain:
        raise ProfileRosterRefused(f"principal: {mask_email(email)} is not an @{domain} account")
    status = p.get("status", "active")
    if status not in STATUSES:
        raise ProfileRosterRefused(f"principal: status must be one of {', '.join(STATUSES)}")
    subject = p.get("provider_subject")
    if subject is not None and (not isinstance(subject, str) or not re.match(r"^[A-Za-z0-9._-]{1,255}$", subject)):
        raise ProfileRosterRefused("principal: provider_subject must be a Google subject id")
    principal = PrincipalEntry(email, str(status), subject)

    items = raw["profiles"]
    if not isinstance(items, list) or not items:
        raise ProfileRosterRefused("profiles must be a non-empty list")
    profiles: list[ProfileEntry] = []
    seen: set[str] = set()
    for i, item in enumerate(items):
        where = f"profile {i + 1}"
        if not isinstance(item, dict):
            raise ProfileRosterRefused(f"{where}: must be an object")
        unknown = set(item) - _PROFILE_KEYS
        if unknown:
            # A "pin" field is refused by name: PINs never live in the roster.
            raise ProfileRosterRefused(f"{where}: unknown field(s) {sorted(unknown)}")
        slug = item.get("key")
        if not isinstance(slug, str) or not _KEY.match(slug):
            raise ProfileRosterRefused(f"{where}: key must be a lowercase slug (a-z, 0-9, _ or -)")
        if slug in seen:
            raise ProfileRosterRefused(f"{where}: key {slug!r} appears twice")
        seen.add(slug)
        name = str(item.get("display_name") or "").strip()
        if not name or len(name) > 80:
            raise ProfileRosterRefused(f"{where}: display_name is required (at most 80 characters)")
        role = item.get("role")
        if role not in ROLES:
            raise ProfileRosterRefused(f"{where}: role must be one of {', '.join(ROLES)}")
        pstatus = item.get("status", "active")
        if pstatus not in STATUSES:
            raise ProfileRosterRefused(f"{where}: status must be one of {', '.join(STATUSES)}")
        order = item.get("sort_order", i + 1)
        if isinstance(order, bool) or not isinstance(order, int) or not 0 <= order <= 1000:
            raise ProfileRosterRefused(f"{where}: sort_order must be an integer from 0 to 1000")
        profiles.append(ProfileEntry(slug, name, str(role), str(pstatus), order))
    return Roster(principal, tuple(profiles))


def load_roster(path: Path, *, workspace_domain: str, repo_root: Path) -> Roster:
    _refuse_inside(path, repo_root, "roster")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileRosterRefused(f"cannot read the roster: {exc.__class__.__name__}") from exc
    return parse_roster(raw, workspace_domain=workspace_domain)


def _refuse_inside(path: Path, repo_root: Path, what: str) -> None:
    try:
        refuse_path_inside(path, repo_root)
    except ValueError as exc:
        raise ProfileRosterRefused(
            f"the {what} must live outside the repository (it is public); keep it under ~/data"
        ) from exc


def load_pin_file(path: Path, *, repo_root: Path) -> dict[str, str]:
    """PINs keyed by profile key, from a protected file outside the repository.

    Refused unless the file is a regular file owned by the caller with no group or other
    permission bits. Every PIN is checked against the policy; an error names the key and the
    rule, never the PIN.
    """
    _refuse_inside(path, repo_root, "PIN file")
    try:
        info = path.lstat()
    except OSError as exc:
        raise ProfileRosterRefused(f"cannot read the PIN file: {exc.__class__.__name__}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ProfileRosterRefused("the PIN file must be a regular file (not a link)")
    if info.st_mode & 0o077:
        raise ProfileRosterRefused("the PIN file must be readable by its owner only (chmod 600)")
    import os

    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise ProfileRosterRefused("the PIN file must be owned by the user running this command")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileRosterRefused(f"cannot read the PIN file: {exc.__class__.__name__}") from None
    if not isinstance(raw, dict) or not raw:
        raise ProfileRosterRefused('the PIN file is an object {"<profile key>": "<PIN>", ...}')
    return check_pins(raw)


def check_pins(pins: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for slug, pin in pins.items():
        if not isinstance(slug, str) or not _KEY.match(slug):
            raise ProfileRosterRefused("the PIN file names a key that is not a profile key")
        try:
            out[slug] = validate_new_pin(pin if isinstance(pin, str) else "")
        except PinPolicyRefused as exc:
            rule = exc.args[0] if exc.args else "policy"
            raise ProfileRosterRefused(f"PIN for profile {slug!r} refused: {rule}") from None
    return out


# ------------------------------------------------------------------------------ plan


def _read_state(cur: Any, roster: Roster) -> tuple[str, dict[str, Any] | None, dict[str, dict[str, Any]], int]:
    cur.execute("select current_database()")
    database = cur.fetchone()[0]
    cur.execute(
        "select id::text, status, provider_subject, provider_issuer from platform.auth_principal "
        "where provider = 'google' and email_norm = %s",
        (roster.principal.email_norm,),
    )
    row = cur.fetchone()
    principal = ({"id": row[0], "status": row[1], "provider_subject": row[2], "provider_issuer": row[3]}
                 if row else None)
    profiles: dict[str, dict[str, Any]] = {}
    total = 0
    if principal is not None:
        cur.execute(
            """
            select p.profile_key, p.operator_id::text, o.display_name, o.role, o.status,
                   p.status, p.sort_order
              from platform.operator_profile p join platform.operator o on o.id = p.operator_id
             where p.principal_id = %s::uuid
            """,
            (principal["id"],),
        )
        for slug, oid, name, role, ostatus, pstatus, order in cur.fetchall():
            profiles[slug] = {"operator_id": oid, "display_name": name, "role": role,
                             "operator_status": ostatus, "profile_status": pstatus,
                             "sort_order": order}
            total += 1
    return database, principal, profiles, total


def compute_plan(
    roster: Roster,
    pins: Mapping[str, str],
    *,
    database: str,
    principal_row: dict[str, Any] | None,
    profile_rows: dict[str, dict[str, Any]],
) -> Plan:
    unknown = set(pins) - {p.slug for p in roster.profiles}
    if unknown:
        raise ProfileRosterRefused(f"PINs were given for keys the roster does not name: {sorted(unknown)}")
    changes: list[Change] = []
    unchanged: list[str] = []
    pe = roster.principal
    if principal_row is None:
        changes.append(Change("principal", "insert", ("email", "status", "provider_subject")
                              if pe.provider_subject is not None else ("email", "status")))
        unpinned = pe.provider_subject is None
    else:
        fields: tuple[str, ...] = ("status",) if principal_row["status"] != pe.status else ()
        if pe.provider_subject is not None and (
                principal_row["provider_subject"] != pe.provider_subject
                or principal_row["provider_issuer"] != GOOGLE_CANONICAL_ISSUER):
            fields += ("provider_subject (re-pin)" if principal_row["provider_subject"] is not None
                       else "provider_subject",)
        if fields:
            changes.append(Change("principal", "update", fields))
        else:
            unchanged.append("principal")
        unpinned = pe.provider_subject is None and principal_row["provider_subject"] is None
    for entry in roster.profiles:
        subject = f"profile {entry.slug}"
        row = profile_rows.get(entry.slug)
        if row is None:
            if entry.slug not in pins:
                raise ProfileRosterRefused(f"profile {entry.slug!r} is new and needs a PIN")
            changes.append(Change(subject, "insert", ("display_name", "role", "status", "sort_order", "pin")))
            continue
        fields = tuple(f for f, new in (
            ("display_name", entry.display_name), ("role", entry.role),
            ("status", entry.status), ("sort_order", entry.sort_order),
        ) if (row["operator_status"] if f == "status" else row[f]) != new
            or (f == "status" and row["profile_status"] != new))
        if fields:
            changes.append(Change(subject, "update", fields))
        if entry.slug in pins:
            changes.append(Change(subject, "set_pin"))
        if not fields and entry.slug not in pins:
            unchanged.append(subject)
    named = {p.slug for p in roster.profiles}
    return Plan(database, pe.email_norm, tuple(changes), tuple(unchanged),
                left_alone=len(set(profile_rows) - named), unpinned=unpinned)


def plan(conn: Any, roster: Roster, pins: Mapping[str, str]) -> Plan:
    """Read-only: compare the roster with the database."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("set transaction read only")
        cur.execute("set local role origenlab_owner")
        database, principal, profiles, _ = _read_state(cur, roster)
        return compute_plan(roster, pins, database=database, principal_row=principal,
                            profile_rows=profiles)


def apply(
    conn: Any,
    roster: Roster,
    pins: Mapping[str, str],
    *,
    hasher: PinHasher,
    expected_changes: int,
    expected_database: str,
) -> Plan:
    """Apply the plan in one transaction, as the owner; refuse if it is not the one reviewed."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("set local role origenlab_owner")
        cur.execute("select current_user")
        if cur.fetchone()[0] != "origenlab_owner":  # pragma: no cover - set role would have failed
            raise ProfileRosterRefused("could not assume origenlab_owner")
        database, principal, profiles, _ = _read_state(cur, roster)
        result = compute_plan(roster, pins, database=database, principal_row=principal,
                              profile_rows=profiles)
        if database != expected_database:
            raise ProfileRosterRefused(
                f"connected to database {database!r}, not the {expected_database!r} you confirmed"
            )
        if result.pending != expected_changes:
            raise ProfileRosterRefused(
                f"the plan has {result.pending} change(s), not the {expected_changes} you "
                "confirmed; re-run the plan and review it again"
            )
        pe = roster.principal
        issuer = GOOGLE_CANONICAL_ISSUER if pe.provider_subject is not None else None
        if principal is None:
            cur.execute(
                "insert into platform.auth_principal (email_norm, status, provider_subject, provider_issuer) "
                "values (%s, %s, %s, %s) returning id::text",
                (pe.email_norm, pe.status, pe.provider_subject, issuer),
            )
            principal_id = cur.fetchone()[0]
        else:
            principal_id = principal["id"]
            # An omitted subject keeps the pinned one: coalesce, never null.
            cur.execute(
                "update platform.auth_principal set status = %s, "
                "provider_subject = coalesce(%s::text, provider_subject), "
                "provider_issuer = coalesce(%s::text, provider_issuer), updated_at = now() "
                "where id = %s::uuid and (status, provider_subject, provider_issuer) is distinct from "
                "(%s::text, coalesce(%s::text, provider_subject), coalesce(%s::text, provider_issuer))",
                (pe.status, pe.provider_subject, issuer, principal_id,
                 pe.status, pe.provider_subject, issuer),
            )
        for entry in roster.profiles:
            row = profiles.get(entry.slug)
            pin = pins.get(entry.slug)
            pin_hash = hasher.hash(pin) if pin is not None else None
            if row is None:
                cur.execute(
                    "insert into platform.operator (auth_user_id, display_name, role, status, sign_in_kind) "
                    "values (gen_random_uuid(), %s, %s, %s, 'shared_profile') returning id::text",
                    (entry.display_name, entry.role, entry.status),
                )
                operator_id = cur.fetchone()[0]
                cur.execute(
                    "insert into platform.operator_profile "
                    "(operator_id, principal_id, profile_key, pin_hash, status, sort_order) "
                    "values (%s::uuid, %s::uuid, %s, %s, %s, %s)",
                    (operator_id, principal_id, entry.slug, pin_hash, entry.status, entry.sort_order),
                )
                continue
            cur.execute(
                "update platform.operator set display_name = %s, role = %s, status = %s, "
                "updated_at = now() where id = %s::uuid and (display_name, role, status) "
                "is distinct from (%s::text, %s::text, %s::text)",
                (entry.display_name, entry.role, entry.status, row["operator_id"],
                 entry.display_name, entry.role, entry.status),
            )
            cur.execute(
                "update platform.operator_profile set status = %s, sort_order = %s, "
                "pin_hash = coalesce(%s::text, pin_hash), updated_at = now() "
                "where operator_id = %s::uuid and ((status, sort_order) is distinct from (%s::text, %s::smallint) "
                "or %s::text is not null)",
                (entry.status, entry.sort_order, pin_hash, row["operator_id"],
                 entry.status, entry.sort_order, pin_hash),
            )
    return result


def describe(result: Plan) -> list[str]:
    lines = [
        f"database: {result.database}",
        f"principal: {mask_email(result.principal_email)}",
    ]
    for c in result.changes:
        what = c.action + (f" ({', '.join(c.fields)})" if c.fields else "")
        lines.append(f"  {c.subject:<24} {what}")
    for subject in result.unchanged:
        lines.append(f"  {subject:<24} unchanged")
    lines.append(f"pending changes: {result.pending}")
    lines.append(
        f"to apply this plan: --apply --confirm-changes {result.pending} --confirm-database {result.database}"
        if result.pending else "nothing to apply"
    )
    lines.append(f"profiles of this principal not in the roster (left unchanged): {result.left_alone}")
    if result.unpinned:
        lines.append("WARNING: no pinned Google account (provider_subject): production refuses this "
                     "principal's sign-in until one is pinned")
    return lines

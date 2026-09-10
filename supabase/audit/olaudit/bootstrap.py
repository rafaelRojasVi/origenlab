"""The hosted role bootstrap, and the static proof that it can only touch four roles.

`supabase/hosted_roles.sql` is the one file this slice can put in front of an operator. This module
is the guarantee that what the file *says* it does is what it *can* do -- checked by parsing the
file, before it is printed, piped or shown, and independently of any database.

It is the write-side counterpart of `olaudit.sqlbank`. That module proves a check file is a single
read; this one proves a bootstrap file is nothing but role management over a closed set of names.
The two share no code, because a relaxation in one must not be able to widen the other.

What is statically proven, in order:

* **No named dollar quoting.** A bare ``$$`` body is permitted and is analysed *as code*, so the
  statements inside a ``do`` block are held to every rule below rather than hidden by it. A named
  ``$tag$`` is refused outright: no part of this bootstrap needs one, and a parser that has to get
  tags right is a parser that can be wrong.
* **No psql meta-command.** A backslash surviving outside a string literal refuses the file, so the
  bytes are safe to pipe to ``psql``.
* **No password, ever.** The token ``password`` -- and ``encrypted``, and ``valid until`` -- may not
  appear in the file's *code*. Assigning the migrator's password is a separate operator action with
  a hidden secret input; it is deliberately not expressible here. (A prose mention of the word in a
  stripped comment is not code and is not a credential; `assert_no_credential` covers the file's raw
  bytes against the shared leak assertions separately.)
* **No forbidden verb.** Every DML verb, every DDL noun except ``role``, ``alter system``, and the
  handful of read-shaped functions that reach the filesystem or another server.
* **Closed statement shapes.** Exactly four shapes are recognised -- ``create role``, ``alter role``,
  ``grant <role> to <role>`` and ``revoke <role> from <role>``. Every occurrence of the words
  ``create``, ``alter``, ``grant`` and ``revoke`` in the code must be accounted for by a matched
  shape. This is the rule that makes the analysis complete rather than merely suggestive: a
  statement the parser does not understand is not ignored, it refuses the file.
* **A closed set of role names.** Every name named by any of those shapes must be one of
  ``origenlab_owner``, ``origenlab_migrator``, ``origenlab_api`` and ``origenlab_worker``. A
  Supabase-managed role -- ``postgres``, ``service_role``, ``anon``, ``authenticated``,
  ``supabase_admin`` and the rest -- can therefore never be created, altered, granted a membership
  or granted anything, and the bootstrap cannot be made to depend on altering one.
* **The intended membership graph, exactly.** One membership is granted and it is
  ``origenlab_owner`` to ``origenlab_migrator`` with ``inherit false, set true, admin false``. Any
  second grant, or that grant with different options, refuses the file.
* **The intended attribute model, exactly.** All four roles ``nosuperuser``, ``nobypassrls``,
  ``noreplication``; the owner ``nologin``; the other three ``login``; the migrator ``noinherit``.

The environment classification is handled here too, and deliberately never reaches the SQL. See
`validate_environment`: the run requires an explicit, approved, non-secret classification, and the
emitted SQL is byte-identical whichever one is given.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

from . import redact

BOOTSTRAP_FILE = Path("supabase/hosted_roles.sql")

# The closed set. Nothing else may be created, altered, granted or revoked by the bootstrap.
MANAGED_ROLES: tuple[str, ...] = (
    "origenlab_owner",
    "origenlab_migrator",
    "origenlab_api",
    "origenlab_worker",
)

# Named so a refusal can say *why* a name is refused, rather than only that it is not in the set.
# This list is documentation, not the boundary -- the boundary is MANAGED_ROLES, an allowlist.
PLATFORM_ROLES: frozenset[str] = frozenset(
    {
        "postgres", "anon", "authenticated", "service_role", "authenticator",
        "supabase_admin", "supabase_auth_admin", "supabase_storage_admin",
        "supabase_realtime_admin", "supabase_read_only_user", "supabase_replication_admin",
        "dashboard_user", "pgbouncer", "pgsodium_keyholder", "pgsodium_keyiduser",
        "pg_read_all_data", "pg_write_all_data", "pg_monitor", "pg_signal_backend",
        "pg_read_server_files", "pg_write_server_files", "pg_execute_server_program",
        "pg_checkpoint", "pg_maintain", "pg_use_reserved_connections", "pg_create_subscription",
        "public",
    }
)

# The one membership the hosted model permits, and the options it must carry (docs/ARCHITECTURE.md
# §6: the migrator may assume the owner explicitly and never inherits it).
REQUIRED_MEMBERSHIP = ("origenlab_owner", "origenlab_migrator")
REQUIRED_GRANT_OPTIONS = ("inherit false", "set true", "admin false")

# Attributes every managed role must carry, and the per-role attributes of docs/ARCHITECTURE.md §6.
REQUIRED_ATTRIBUTES_ALL = ("nosuperuser", "nobypassrls", "noreplication")
REQUIRED_ATTRIBUTES = {
    "origenlab_owner": ("nologin", "inherit"),
    "origenlab_migrator": ("login", "noinherit"),
    "origenlab_api": ("login", "inherit"),
    "origenlab_worker": ("login", "inherit"),
}

# Attributes that come in a positive/negative pair. A role that is declared both ways across its
# `create` and its `alter` is not merely redundant: which one wins depends on statement order, so
# the file would no longer be a deterministic statement of the model. Both forms present refuses it.
NEGATABLE_ATTRIBUTES = ("login", "inherit", "createdb", "createrole", "superuser", "bypassrls",
                        "replication")

FORBIDDEN_TOKENS: frozenset[str] = frozenset(
    {
        # credentials -- the prohibition that matters most in this file
        "password", "encrypted", "unencrypted", "valid", "until",
        # data modification
        "insert", "update", "delete", "merge", "truncate", "copy",
        # DDL nouns other than `role`: nothing but roles may be created or changed here
        "table", "schema", "database", "function", "procedure", "view", "index", "sequence",
        "trigger", "policy", "publication", "subscription", "extension", "tablespace", "server",
        "system", "type", "domain", "aggregate", "operator", "collation", "statistics",
        "rename", "owner", "reassign", "owned",
        # `drop` is never legitimate here: this bootstrap converges, it never removes a role
        "drop",
        # privilege attributes in their positive form. `nosuperuser`, `nobypassrls` and
        # `noreplication` tokenise as single words and are therefore not matched by these.
        "superuser", "bypassrls", "replication", "createdb", "createrole", "createuser",
        # dynamic execution and anything that reaches outside this database
        "execute", "prepare", "deallocate", "dblink", "dblink_exec", "load", "checkpoint",
        "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file", "pg_sleep",
        "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf", "set_config",
        "lo_import", "lo_export", "copy_from", "copy_to",
    }
)

_NAMED_DOLLAR_QUOTE = re.compile(r"\$[A-Za-z0-9_]+\$")
_BARE_DOLLAR_QUOTE = re.compile(r"\$\$")
_LINE_COMMENT = re.compile(r"--[^\n]*")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_IDENT = r"([a-z_][a-z0-9_]*)"
_CREATE_ROLE = re.compile(rf"\bcreate\s+role\s+{_IDENT}", re.IGNORECASE)
_ALTER_ROLE = re.compile(rf"\balter\s+role\s+{_IDENT}", re.IGNORECASE)
_GRANT = re.compile(rf"\bgrant\s+{_IDENT}\s+to\s+{_IDENT}", re.IGNORECASE)
_REVOKE = re.compile(rf"\brevoke\s+{_IDENT}\s+from\s+{_IDENT}", re.IGNORECASE)


class BootstrapError(ValueError):
    """The bootstrap file is not provably a closed role bootstrap. Nothing is emitted."""


class RoleStatement(NamedTuple):
    """One recognised role-management statement, as the analyser understood it."""

    verb: str          # create | alter | grant | revoke
    roles: tuple[str, ...]
    attributes: tuple[str, ...]


class RoleModel(NamedTuple):
    """The role and membership matrix, derived from the file rather than restated beside it."""

    attributes: dict[str, tuple[str, ...]]
    memberships: tuple[tuple[str, str], ...]   # (role granted, member)
    revocations: tuple[tuple[str, str], ...]   # (role revoked, member)
    statements: tuple[RoleStatement, ...]


# ------------------------------------------------------------------------------------------------
# Lexing
# ------------------------------------------------------------------------------------------------


def _strip_block_comments(text: str) -> str:
    """Remove block comments, honouring PostgreSQL's nesting. Newlines are preserved."""
    out: list[str] = []
    depth = 0
    index = 0
    while index < len(text):
        if text.startswith("/*", index):
            depth += 1
            index += 2
            continue
        if depth and text.startswith("*/", index):
            depth -= 1
            index += 2
            continue
        if depth == 0:
            out.append(text[index])
        elif text[index] == "\n":
            out.append("\n")
        index += 1
    if depth:
        raise BootstrapError("unterminated block comment")
    return "".join(out)


def to_code(text: str) -> str:
    """Return `text` reduced to its executable shape: no comments, no literals, no dollar markers.

    A bare ``$$`` marker is replaced by whitespace rather than swallowing what it delimits, so the
    body of a ``do`` block is analysed as code by every rule. A named ``$tag$`` is refused first.
    """
    text = _strip_block_comments(text)
    text = _LINE_COMMENT.sub("", text)
    if _NAMED_DOLLAR_QUOTE.search(text):
        raise BootstrapError("named dollar quoting is not permitted in the bootstrap file")
    text = _BARE_DOLLAR_QUOTE.sub("  ", text)

    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "'":
            index += 1
            closed = False
            while index < length:
                if text[index] == "'":
                    if index + 1 < length and text[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    closed = True
                    break
                if text[index] == "\n":
                    out.append("\n")
                index += 1
            if not closed:
                raise BootstrapError("unterminated string literal")
            out.append("''")
            continue
        out.append(char)
        index += 1
    return "".join(out)


# ------------------------------------------------------------------------------------------------
# Static validation
# ------------------------------------------------------------------------------------------------


def _attributes_after(code: str, start: int) -> tuple[str, ...]:
    """The attribute words of a `create role` / `alter role` statement, up to its terminator."""
    end = code.find(";", start)
    tail = code[start:] if end == -1 else code[start:end]
    words = [word.lower() for word in _WORD.findall(tail)]
    # words[0:3] are the verb, the noun `role` and the role name itself.
    return tuple(words[3:]) if len(words) > 3 else ()


def _check_shape_completeness(code: str, found: dict[str, int]) -> None:
    """Every `create`/`alter`/`grant`/`revoke` word must belong to a shape the analyser matched.

    Without this, an unrecognised statement would simply not be reported -- the analyser would be
    silent about exactly the thing it exists to catch. With it, anything the four regexes do not
    understand refuses the whole file.
    """
    words = [word.lower() for word in _WORD.findall(code)]
    for verb in ("create", "alter", "grant", "revoke"):
        occurrences = words.count(verb)
        matched = found[verb]
        if occurrences != matched:
            raise BootstrapError(
                f"{occurrences} occurrence(s) of '{verb}' but only {matched} recognised "
                f"'{verb}' statement(s); the bootstrap file contains a statement shape this "
                "analyser does not understand, so the file is refused"
            )


def analyse(text: str, *, name: str = "hosted_roles.sql") -> RoleModel:
    """Statically prove `text` is a closed role bootstrap and return the model it declares."""
    code = to_code(text)

    if "\\" in code:
        raise BootstrapError(f"{name}: a backslash outside a string literal is a psql meta-command")

    for word in _WORD.findall(code.lower()):
        if word in FORBIDDEN_TOKENS:
            raise BootstrapError(f"{name}: forbidden token '{word}' in the bootstrap file")

    attributes: dict[str, tuple[str, ...]] = {}
    statements: list[RoleStatement] = []
    memberships: list[tuple[str, str]] = []
    revocations: list[tuple[str, str]] = []
    created: list[str] = []
    altered: list[str] = []

    for match in _CREATE_ROLE.finditer(code):
        role = match.group(1).lower()
        attrs = _attributes_after(code, match.start())
        created.append(role)
        attributes.setdefault(role, ())
        attributes[role] = tuple(dict.fromkeys(attributes[role] + attrs))
        statements.append(RoleStatement("create", (role,), attrs))

    for match in _ALTER_ROLE.finditer(code):
        role = match.group(1).lower()
        attrs = _attributes_after(code, match.start())
        altered.append(role)
        attributes.setdefault(role, ())
        attributes[role] = tuple(dict.fromkeys(attributes[role] + attrs))
        statements.append(RoleStatement("alter", (role,), attrs))

    for match in _GRANT.finditer(code):
        pair = (match.group(1).lower(), match.group(2).lower())
        memberships.append(pair)
        end = code.find(";", match.start())
        options = code[match.start(): end if end != -1 else len(code)].lower()
        statements.append(RoleStatement("grant", pair, tuple(options.split())))

    for match in _REVOKE.finditer(code):
        pair = (match.group(1).lower(), match.group(2).lower())
        revocations.append(pair)
        statements.append(RoleStatement("revoke", pair, ()))

    _check_shape_completeness(
        code,
        {
            "create": len(created),
            "alter": len(altered),
            "grant": len(memberships),
            "revoke": len(revocations),
        },
    )

    named = set(created) | set(altered) | {r for pair in memberships + revocations for r in pair}
    for role in sorted(named):
        if role not in MANAGED_ROLES:
            why = (
                "it is a Supabase-managed or PostgreSQL predefined role, which this bootstrap may "
                "never create, alter or grant"
                if role in PLATFORM_ROLES
                else "it is outside the closed OrigenLab role set"
            )
            raise BootstrapError(f"{name}: the bootstrap names role '{role}' -- {why}")

    missing_created = [role for role in MANAGED_ROLES if role not in created]
    if missing_created:
        raise BootstrapError(f"{name}: no create statement for {', '.join(missing_created)}")
    missing_altered = [role for role in MANAGED_ROLES if role not in altered]
    if missing_altered:
        raise BootstrapError(f"{name}: no convergence (alter) statement for {', '.join(missing_altered)}")

    for role in MANAGED_ROLES:
        held = attributes[role]
        for attribute in NEGATABLE_ATTRIBUTES:
            if attribute in held and f"no{attribute}" in held:
                raise BootstrapError(
                    f"{name}: {role} is declared both {attribute.upper()} and NO{attribute.upper()}; "
                    "the create and the convergence statements disagree, so the file does not state "
                    "one deterministic role model"
                )
        for required in REQUIRED_ATTRIBUTES_ALL + REQUIRED_ATTRIBUTES[role]:
            if required not in held:
                raise BootstrapError(
                    f"{name}: {role} is not declared {required.upper()}; the hosted role model of "
                    "docs/ARCHITECTURE.md §6 is not satisfied"
                )

    if tuple(memberships) != (REQUIRED_MEMBERSHIP,):
        raise BootstrapError(
            f"{name}: the bootstrap must grant exactly one membership -- "
            f"{REQUIRED_MEMBERSHIP[0]} to {REQUIRED_MEMBERSHIP[1]} -- but declares "
            f"{len(memberships)}: {memberships}"
        )
    grant_statement = next(s for s in statements if s.verb == "grant")
    flattened = " ".join(grant_statement.attributes)
    for option in REQUIRED_GRANT_OPTIONS:
        if option not in flattened:
            raise BootstrapError(
                f"{name}: the owner membership must be granted WITH "
                f"{', '.join(REQUIRED_GRANT_OPTIONS).upper()}; '{option.upper()}' is missing"
            )

    for granted, member in revocations:
        if granted == REQUIRED_MEMBERSHIP[0] and member == REQUIRED_MEMBERSHIP[1]:
            raise BootstrapError(f"{name}: the file both grants and revokes the owner membership")

    return RoleModel(
        attributes=dict(sorted(attributes.items())),
        memberships=tuple(memberships),
        revocations=tuple(revocations),
        statements=tuple(statements),
    )


def assert_no_credential(text: str, *, what: str = "the bootstrap file") -> None:
    """Re-read the raw bytes through the shared leak assertions before anything is emitted."""
    redact.assert_clean(text, what=what)


def load(repo_root: Path, *, path: Path | None = None) -> tuple[str, RoleModel]:
    """Read, statically validate and leak-check the bootstrap file. Raises before any output."""
    target = repo_root / (path or BOOTSTRAP_FILE)
    if target.is_symlink() or not target.is_file():
        raise BootstrapError(f"{target} is missing or is not a regular file; refusing to proceed")
    text = target.read_text(encoding="utf-8")
    model = analyse(text, name=target.name)
    assert_no_credential(text)
    return text, model


# ------------------------------------------------------------------------------------------------
# The environment classification
# ------------------------------------------------------------------------------------------------
#
# Two requirements that only look contradictory. The run must name its target explicitly -- a
# bootstrap that defaults to somewhere is a bootstrap that will one day default to production. And
# the run must not disclose the target -- no project reference, no organisation identifier, no host
# name, no connection string.
#
# They are reconciled by making the required argument a *classification* rather than an address: a
# short, non-secret, closed-set word that identifies which environment's rules apply and identifies
# no endpoint at all. It is validated against the allowlist below, it is never interpolated into the
# emitted SQL, and `assert_environment_absent` proves that after the fact rather than assuming it.
#
# Nothing in this repository stores or derives the project reference, organisation identifier or
# host name of any hosted project. This tool never learns one, because it never connects.


class Durability(NamedTuple):
    """The backup and point-in-time-recovery posture decided for one environment."""

    decided: bool
    summary: str
    detail: str


# docs/OPERATIONS.md §4.3 owns the procedure; docs/MIGRATION.md §5.2 check 11 owns the obligation.
DURABILITY: dict[str, Durability] = {
    "staging": Durability(
        decided=True,
        summary="Pro plan daily backups, seven-day retention; PITR deliberately declined",
        detail=(
            "Staging carries no durable human commercial truth and is rebuildable from migrations, "
            "so daily backups at seven-day retention are sufficient and point-in-time recovery is "
            "declined on purpose rather than left undecided."
        ),
    ),
    "production": Durability(
        decided=False,
        summary="no RPO or PITR decision has been recorded",
        detail=(
            "Production holds durable human commercial truth, so its recovery-point objective and "
            "whether point-in-time recovery is required are an explicit decision that has not been "
            "made. Staging's posture is not a precedent for it, and a staging audit must never be "
            "made to pass by weakening what production requires. Record the decision in "
            "docs/OPERATIONS.md §4.3 in a reviewed change before bootstrapping production."
        ),
    ),
}

APPROVED_ENVIRONMENTS: tuple[str, ...] = tuple(sorted(DURABILITY))

_CLASSIFICATION = re.compile(r"^[a-z][a-z0-9]{1,15}$")


class Environment(NamedTuple):
    """A validated, non-secret environment classification."""

    classification: str
    durability: Durability


def validate_environment(value: str) -> Environment:
    """Validate the required classification. Raises rather than defaulting to anything."""
    if not value:
        raise BootstrapError(
            "an explicit environment classification is required; this tool has no default target. "
            f"Approved classifications: {', '.join(APPROVED_ENVIRONMENTS)}"
        )
    if not _CLASSIFICATION.match(value):
        # Deliberately does not echo the value: a caller who passed a host name, a connection
        # string or a project reference by mistake must not have it printed back or logged.
        raise BootstrapError(
            "the environment classification is not a short lowercase word. It must be a "
            "classification, never an address, a project reference or a connection string. "
            f"Approved classifications: {', '.join(APPROVED_ENVIRONMENTS)}"
        )
    if value not in DURABILITY:
        raise BootstrapError(
            f"'{value}' is not an approved environment classification; approved: "
            f"{', '.join(APPROVED_ENVIRONMENTS)}"
        )

    durability = DURABILITY[value]
    if not durability.decided:
        raise BootstrapError(
            f"the '{value}' bootstrap is blocked: {durability.summary}. {durability.detail}"
        )
    return Environment(classification=value, durability=durability)


def assert_environment_absent(sql: str, environment: Environment) -> None:
    """Prove the classification never reached the SQL, rather than trusting that it did not."""
    if environment.classification in sql.lower():
        raise BootstrapError(
            "the environment classification appears in the emitted SQL; the bootstrap SQL must be "
            "byte-identical whichever environment it is reviewed for"
        )


# ------------------------------------------------------------------------------------------------
# The dry run
# ------------------------------------------------------------------------------------------------


def role_matrix(model: RoleModel) -> list[str]:
    """The role and membership matrix, rendered from the analysed file."""
    lines = ["role                 login    inherit    superuser  bypassrls  replication"]
    for role in MANAGED_ROLES:
        held = model.attributes[role]
        login = "LOGIN" if "login" in held else "NOLOGIN"
        inherit = "NOINHERIT" if "noinherit" in held else "INHERIT"
        # Derived from the file, not restated beside it: `analyse` has already refused the file if
        # any of the three negative attributes is missing for any managed role.
        superuser = "NO" if "nosuperuser" in held else "YES"
        bypassrls = "NO" if "nobypassrls" in held else "YES"
        replication = "NO" if "noreplication" in held else "YES"
        lines.append(
            f"{role:<20} {login:<8} {inherit:<10} "
            f"{superuser:<10} {bypassrls:<10} {replication}"
        )
    lines.append("")
    lines.append("memberships granted:")
    options = ", ".join(option.upper() for option in REQUIRED_GRANT_OPTIONS)
    for granted, member in model.memberships:
        lines.append(f"  {member} -> {granted}  ({options})")
    lines.append("memberships converged to absent:")
    for granted, member in model.revocations:
        lines.append(f"  {member} -> {granted}  (revoked)")
    lines.append("")
    lines.append("Supabase-managed roles touched: none. No platform role is created, altered,")
    lines.append("granted a membership in an OrigenLab role, or made an owner of anything.")
    return lines


def dry_run(repo_root: Path, environment: Environment, *, path: Path | None = None) -> tuple[str, list[str]]:
    """Return the reviewed SQL to emit and the human-readable plan summary.

    The first element is the bootstrap file verbatim -- nothing generated, nothing interpolated, no
    banner and no comment added, so what an operator reviews is what is committed. The second is the
    summary, which the entry point writes to stderr so the SQL stream stays pipeable and clean.
    """
    sql, model = load(repo_root, path=path)
    assert_environment_absent(sql, environment)

    summary = [
        "OrigenLab V2 -- hosted role bootstrap, DRY RUN. Nothing was connected to or applied.",
        "",
        f"environment classification : {environment.classification}",
        f"durability posture         : {environment.durability.summary}",
        f"bootstrap file             : {(path or BOOTSTRAP_FILE).as_posix()}",
        f"statements analysed        : {len(model.statements)}",
        "",
        *role_matrix(model),
        "",
        "This tool opens no database connection in any mode. It reads one committed file, proves",
        "statically that the file is nothing but role management over four names, and prints it.",
        "No password is assigned here; the origenlab_migrator credential is a separate operator",
        "action with a hidden secret input (docs/OPERATIONS.md §4.3).",
    ]
    return sql, summary

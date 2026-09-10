"""The fixed SQL check bank, and the static rejection of anything that is not a read.

Every statement the audit can send is a committed, reviewed file under `supabase/audit/sql/`. There
is no operator-supplied SQL, no template, no string interpolation into a statement and no dynamic
SQL constructed at run time. This module is the guarantee that stays true even if a file is edited
carelessly: it parses each file before a connection is opened and refuses the whole run if any file
contains anything but a single read.

What "a single read" means here:

* comments and string literals are removed first, so a keyword inside a LIKE pattern is data and a
  keyword inside a comment is prose -- neither is mistaken for a statement;
* dollar quoting is refused outright rather than parsed, because no catalogue query needs it and a
  parser that must get `$tag$` right is a parser that can be wrong;
* psql meta-commands are refused: a backslash outside a string literal terminates the run;
* exactly one statement may remain, and it must begin `select`, `with` or `show`;
* no token from the forbidden list may appear anywhere in the remaining code -- every statement that
  writes, every DDL verb, every transaction-control and session-setting verb, and the handful of
  read-shaped functions that reach the filesystem, another server or the process table.

The forbidden list is matched on word boundaries against code with literals removed, so
`updated_at`, `rolcreatedb`, `pg_settings` and `relforcerowsecurity` are not false positives while
a bare `update`, `create` or `set` is caught.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# Verbs that write, change the session, control the transaction, or reach outside this database.
FORBIDDEN_TOKENS: frozenset[str] = frozenset(
    {
        # data modification
        "insert", "update", "delete", "merge", "truncate", "copy", "upsert",
        # DDL and ownership
        "create", "alter", "drop", "comment", "rename", "cluster", "reindex", "vacuum",
        "analyze", "refresh", "import", "security",
        # privileges
        "grant", "revoke",
        # session and transaction control
        "set", "reset", "begin", "start", "commit", "rollback", "savepoint", "release",
        "abort", "discard", "listen", "unlisten", "notify", "checkpoint", "load",
        # `end` is deliberately absent: it is transaction control only in statement-initial
        # position, which the select/with/show rule already forbids, and it is the ordinary
        # terminator of every CASE expression in this bank.
        # procedural execution
        "call", "do", "execute", "prepare", "deallocate", "declare", "fetch", "move", "close",
        # locking
        "lock",
        # functions that reach the filesystem, the process table, another server or the WAL
        "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file", "pg_sleep",
        "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf", "pg_rotate_logfile",
        "pg_promote", "pg_create_restore_point", "pg_switch_wal", "pg_logical_emit_message",
        "pg_replication_origin_create", "set_config", "dblink", "dblink_exec",
        "lo_import", "lo_export", "query_to_xml", "query_to_json", "xpath_exists",
        "pg_advisory_lock", "pg_advisory_xact_lock",
    }
)

ALLOWED_LEADING = ("select", "with", "show")

_LINE_COMMENT = re.compile(r"--[^\n]*")
_DOLLAR_QUOTE = re.compile(r"\$[A-Za-z0-9_]*\$")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DECLARED_ID = re.compile(r"'check'\s*,\s*'([a-z0-9_]+)'")


class SqlBankError(ValueError):
    """Raised when a check file is not a single read. The whole run is refused."""


class Check(NamedTuple):
    """One reviewed query file."""

    check_id: str
    path: Path
    sql: str

    @property
    def name(self) -> str:
        return self.path.name


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
        raise SqlBankError("unterminated block comment")
    return "".join(out)


def to_code(text: str) -> str:
    """Return `text` with comments removed and every string literal blanked.

    The result is the executable shape of the file: keywords that survive here are real keywords.
    Quoted identifiers are preserved, because a quoted identifier is code.
    """
    if _DOLLAR_QUOTE.search(text):
        raise SqlBankError("dollar quoting is not permitted in a check file")
    text = _strip_block_comments(text)
    text = _LINE_COMMENT.sub("", text)

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
                raise SqlBankError("unterminated string literal")
            out.append("''")
            continue
        out.append(char)
        index += 1
    return "".join(out)


def statements(code: str) -> list[str]:
    """Split blanked code into non-empty statements."""
    return [part.strip() for part in code.split(";") if part.strip()]


def validate(text: str, *, name: str) -> str:
    """Statically prove `text` is a single read. Returns the check id the file declares."""
    code = to_code(text)

    if "\\" in code:
        raise SqlBankError(f"{name}: a backslash outside a string literal is a psql meta-command")

    found = statements(code)
    if len(found) != 1:
        raise SqlBankError(f"{name}: expected exactly one statement, found {len(found)}")

    statement = found[0]
    lowered = statement.lower()
    if not lowered.startswith(ALLOWED_LEADING):
        leading = lowered.split(None, 1)[0] if lowered.split() else "<empty>"
        raise SqlBankError(
            f"{name}: statement begins with '{leading}', not one of {ALLOWED_LEADING}"
        )

    for word in _WORD.findall(lowered):
        if word in FORBIDDEN_TOKENS:
            raise SqlBankError(f"{name}: forbidden token '{word}' in a check file")

    declared = _DECLARED_ID.search(text)
    if not declared:
        raise SqlBankError(f"{name}: the file does not declare its check id as 'check', '<id>'")
    return declared.group(1)


def load(sql_dir: Path | None = None) -> list[Check]:
    """Load and validate every check file, in file-name order. Raises on the first bad file."""
    directory = Path(sql_dir) if sql_dir is not None else SQL_DIR
    paths = sorted(p for p in directory.glob("*.sql") if p.is_file())
    if not paths:
        raise SqlBankError(f"no check files found under {directory}")

    checks: list[Check] = []
    seen: dict[str, str] = {}
    for path in paths:
        text = path.read_text(encoding="utf-8")
        check_id = validate(text, name=path.name)
        if check_id in seen:
            raise SqlBankError(
                f"{path.name}: check id '{check_id}' is already declared by {seen[check_id]}"
            )
        seen[check_id] = path.name
        checks.append(Check(check_id, path, text))
    return checks

"""Email → cases, automatically: safe intake/link rules applied on a timer, with a stop switch.

Owner-approved 2026-10-06 (docs/DOMAIN.md §3.6.6). The rules engine is unchanged
(`mail_rules.py` plans, `mail_rules_repository.py` applies); this module only decides **when** it
applies, and **which** rules may apply without a person pressing «Aplicar»:

| Rule | Automatic | Why |
|---|---|---|
| R1 | yes | same Gmail thread as one open case; a new sent CN PDF may also record the quote when the requester is already resolved |
| R2 | yes | the email names a quote number held by exactly one open case: link only |
| R7 | yes | an explicit inbound RFQ opens a lead; free-mail never invents an institution |
| R3–R6 | no | recipient-domain case creation and terminal win/loss actions still wait for an admin |

**The switch.** An admin turns the automatic run on or off in Revisión → «Acciones automáticas»
(`POST /v2/commands/set-auto-mail-rules`). Each flip is one `platform.command_receipt`
(`set_auto_mail_rules`) carrying `enabled`, the admin and a note; the newest completed one is the
state. No receipt means **off**. Nothing else stores the switch, so it survives restarts and
redeploys and says who flipped it, when and why.

**On whose behalf.** An automatic action is applied exactly as «Aplicar» applies it — the same
receipt key (`mail-rule:<email>:<rule>`), the same system attribution (`actor_kind = 'worker'`)
— with the admin who switched it on as the operator the rows and the receipt name, and
`attribution.trigger = 'automatic'`. If that admin is no longer an active admin, the run stops
and says so until another admin switches it on again.

**The timer.** :class:`AutoMailRulesRunner` is a daemon thread started with the API (only where
the case commands are mounted). Every `interval_seconds` it calls :meth:`AutoMailRules.run_once`.
`ORIGENLAB_V2_AUTO_MAIL_RULES_INTERVAL_SECONDS=0` never starts it — the deploy-level stop, for
when the dashboard itself is unreachable. Two API processes may both run a pass: every action
takes the per-email advisory lock and its receipt, so an email is acted on once.

Every action stays undoable from the same tab, exactly like one an admin applied.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import UTC, datetime
from typing import Any

from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.mail_rules import AUTO

logger = logging.getLogger(__name__)

#: Safe intake/link rules the timer applies. Terminal actions (R5/R6) and recipient-domain
#: creation (R3/R4) still wait for an admin.
AUTOMATIC_RULES: frozenset[str] = frozenset({"R1", "R2", "R7"})
SWITCH_COMMAND = "set_auto_mail_rules"
DEFAULT_INTERVAL_SECONDS = 300
#: At most this many actions per pass; the next pass takes the rest.
MAX_ACTIONS_PER_PASS = 100
MAX_NOTE_CHARS = 2000


def _digest(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


_SWITCH_SQL = """
select r.operator_id::text, r.completed_at, r.response_body,
       o.email_norm, o.display_name, o.role, o.status, o.version
  from platform.command_receipt r
  join platform.operator o on o.id = r.operator_id
 where r.command_name = %s and r.status = 'completed'
 order by r.completed_at desc, r.created_at desc
 limit 1
"""


def read_switch(cur: Any) -> dict[str, Any]:
    """The newest completed switch receipt, or «off, never switched on»."""
    cur.execute(_SWITCH_SQL, (SWITCH_COMMAND,))
    row = cur.fetchone()
    if row is None:
        return {"enabled": False, "changed_at": None, "changed_by": None, "note": None,
                "operator": None}
    operator_id, completed_at, body, email_norm, display_name, role, status, version = row
    body = _json(body) or {}
    operator = OperatorIdentity(operator_id=operator_id, email_norm=email_norm,
                                display_name=display_name, role=role, status=status,
                                auth_method="automatic", version=int(version) if version is not None else None)
    return {
        "enabled": bool(body.get("enabled")),
        "changed_at": completed_at.isoformat() if completed_at else None,
        "changed_by": display_name,
        "note": body.get("note"),
        "operator": operator,
    }


class AutoMailRules:
    """The switch and one automatic pass, over a :class:`MailRulesRepository`."""

    def __init__(self, repo: Any, *, interval_seconds: int = DEFAULT_INTERVAL_SECONDS) -> None:
        self._repo = repo
        self.interval_seconds = int(interval_seconds)
        self._lock = threading.Lock()
        self._last_run: dict[str, Any] | None = None

    # ------------------------------------------------------------------ state

    @property
    def timer_running(self) -> bool:
        return self.interval_seconds > 0 and bool(getattr(self._repo, "commands_enabled", False))

    def last_run(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._last_run) if self._last_run else None

    def state(self) -> dict[str, Any]:
        """What the tab shows: the switch, who set it, the timer, this process's last pass."""
        with self._repo._read() as cur:
            switch = read_switch(cur)
        operator: OperatorIdentity | None = switch.pop("operator")
        blocked = None
        if switch["enabled"] and operator is not None and not _is_active_admin(operator):
            blocked = (f"{operator.display_name} ya no es un administrador activo: "
                       "otro administrador debe volver a activarlo")
        return {
            **switch,
            "rules": sorted(AUTOMATIC_RULES),
            "interval_seconds": self.interval_seconds,
            "timer_running": self.timer_running,
            "blocked": blocked,
            "last_run": self.last_run(),
        }

    # ------------------------------------------------------------------ the switch

    def set_enabled(self, operator: OperatorIdentity, enabled: bool, note: str,
                    idempotency_key: str) -> dict[str, Any]:
        """Flip the switch. Admin only (the route checks the role; this checks it again)."""
        if operator.role != "admin" or not operator.is_active:
            raise CommandRefused(403, "role_may_not_apply_mail_rules",
                                 "only an admin switches the automatic email rules")
        note = (note or "").strip()
        if not note:
            raise CommandRefused(422, "note_required", "say why the automatic run is switched")
        if len(note) > MAX_NOTE_CHARS:
            raise CommandRefused(422, "note_too_long", f"at most {MAX_NOTE_CHARS} characters")
        digest = _digest({"enabled": bool(enabled), "note": note})
        with self._repo._write() as cur:
            # One flip at a time, so two admins cannot each read "off" and both turn it on.
            cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (SWITCH_COMMAND,))
            receipt_id, replay = self._repo._claim_receipt(cur, operator, idempotency_key,
                                                           SWITCH_COMMAND, digest)
            if replay is not None:
                return replay
            current = read_switch(cur)
            if current["enabled"] == bool(enabled) and not (enabled and current["operator"] is not None
                                                            and not _is_active_admin(current["operator"])):
                state = "activadas" if enabled else "detenidas"
                raise CommandRefused(409, "auto_mail_rules_unchanged",
                                     f"las acciones automáticas ya están {state}")
            response = {"enabled": bool(enabled), "note": note,
                        "rules": sorted(AUTOMATIC_RULES), "command_receipt_id": receipt_id,
                        "idempotency_key": idempotency_key, "replayed": False}
            self._repo._complete_receipt(cur, receipt_id, response)
            return response

    # ------------------------------------------------------------------ one pass

    def run_once(self) -> dict[str, Any]:
        """Apply every safe automatic R1/R2/R7 action the rules plan now, if the switch is on."""
        started = datetime.now(UTC)
        with self._repo._read() as cur:
            switch = read_switch(cur)
        operator: OperatorIdentity | None = switch["operator"]
        result: dict[str, Any] = {"at": started.isoformat(), "applied": 0, "refused": 0, "pending": 0,
                                  "skipped": None, "refusals": []}
        if not switch["enabled"] or operator is None:
            result["skipped"] = "off"
        elif not _is_active_admin(operator):
            result["skipped"] = "operator_not_admin"
        else:
            actions, _display, _snapshot = self._repo.plan()
            eligible = [a for a in actions if a.mode == AUTO and a.rule_id in AUTOMATIC_RULES]
            result["pending"] = max(0, len(eligible) - MAX_ACTIONS_PER_PASS)
            for action in eligible[:MAX_ACTIONS_PER_PASS]:
                outcome = self._repo.apply_planned(operator, action, automatic=True)
                if outcome.get("code"):
                    result["refused"] += 1
                    if len(result["refusals"]) < 10:
                        result["refusals"].append(outcome)
                else:
                    result["applied"] += 1
        with self._lock:
            self._last_run = result
        return result


def _is_active_admin(operator: OperatorIdentity) -> bool:
    return operator.role == "admin" and operator.is_active


class AutoMailRulesRunner:
    """The timer: one daemon thread per API process, stopped with the app."""

    def __init__(self, auto: AutoMailRules) -> None:
        self._auto = auto
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self._auto.timer_running or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="auto-mail-rules", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _loop(self) -> None:
        # The first pass waits one interval, so a restart loop never hammers the database.
        while not self._stop.wait(self._auto.interval_seconds):
            try:
                result = self._auto.run_once()
            except Exception:  # the timer must outlive one bad pass
                logger.exception("auto mail rules: the pass failed")
                continue
            if result["applied"] or result["refused"]:
                logger.info("auto mail rules: applied=%d refused=%d pending=%d",
                            result["applied"], result["refused"], result["pending"])

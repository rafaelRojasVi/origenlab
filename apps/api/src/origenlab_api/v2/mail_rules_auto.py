"""Email → cases, automatically: safe intake/link rules applied on a timer, with a stop switch.

Owner-approved 2026-10-06 (docs/DOMAIN.md §3.6.6). The rules engine is unchanged
(`mail_rules.py` plans, `mail_rules_repository.py` applies); this module only decides **when** it
applies, and **which** rules may apply without a person pressing «Aplicar»:

| Rule | Automatic | Why |
|---|---|---|
| R1 | yes | same Gmail thread as one open case; a new sent CN PDF may also record the quote when the requester is already resolved |
| R2 | yes | the email names a quote number held by exactly one open case: link only |
| R7 | yes | an explicit inbound RFQ opens a lead; free-mail never invents an institution |
| R9 | yes | a sent CN PDF whose own name names one institution with exactly one open case: link + «Enviada» + quote (owner-approved 2026-10-13) |
| R3, R4, R11–R13 | yes | case creation from a sent quote, forwards, the context matcher (owner 2026-10-14/15) |
| R14 | yes | a sent quote on an email already on its one open case, under R1's gates (owner 2026-10-19) |
| R15 | yes | an institution a rule created «por confirmar», once a quote to it stood 3 days (owner 2026-10-19) |
| R16 | yes | contacto@ sent the PDF of a Cotizador quote already approved on that email's case: sent + «Enviada» (owner 2026-10-21) |
| R5, R6, R10 | no | terminal win/loss actions and a PDF-named new institution still wait for an admin |

**Kill flags.** R14 and R15 each have their own deploy setting
(`ORIGENLAB_V2_AUTO_RULE_R14_ENABLED`, `ORIGENLAB_V2_AUTO_RULE_R15_ENABLED`, default **false**):
until the owner turns one on, that rule stays out of the automatic pass; «Aplicar» can still apply it.

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

#: Safe intake/link rules the timer applies (R11, forwarded requests: owner 2026-10-14). Terminal actions (R5/R6) and recipient-domain
#: creation (R3/R4) still wait for an admin.
AUTOMATIC_RULES: frozenset[str] = frozenset({"R1", "R2", "R3", "R4", "R7", "R9", "R11", "R12", "R13", "R14", "R15",
                                             "R16", "R17"})
#: Rules with their own kill flag in settings (`v2_auto_rule_<id>_enabled`).
FLAGGED_RULES: tuple[str, ...] = ("R14", "R15")
SWITCH_COMMAND = "set_auto_mail_rules"
DEFAULT_INTERVAL_SECONDS = 300
#: At most this many actions per pass; the next pass takes the rest.
MAX_ACTIONS_PER_PASS = 100
MAX_NOTE_CHARS = 2000
#: At most this many people actions per pass (strong ones only); the next pass takes the rest.
MAX_PEOPLE_ACTIONS_PER_PASS = 50


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

    def __init__(self, repo: Any, *, interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
                 disabled_rules: frozenset[str] = frozenset(), people_repo: Any = None,
                 people_enabled: bool = False) -> None:
        self._repo = repo
        #: «Who wrote each email» (`people_rules_repository.py`): runs before the mail rules, only
        #: while `ORIGENLAB_V2_AUTO_PEOPLE_ENABLED` is on and the same switch is on.
        self._people_repo = people_repo
        self.people_enabled = bool(people_enabled)
        self.interval_seconds = int(interval_seconds)
        #: Rules a deploy flag keeps out of the automatic pass (`FLAGGED_RULES`).
        self.rules: frozenset[str] = AUTOMATIC_RULES - frozenset(disabled_rules)
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
            "rules": sorted(self.rules),
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
                        "rules": sorted(self.rules), "command_receipt_id": receipt_id,
                        "idempotency_key": idempotency_key, "replayed": False}
            self._repo._complete_receipt(cur, receipt_id, response)
            return response

    # ------------------------------------------------------------------ one pass

    def _people_pass(self, operator: OperatorIdentity, result: dict[str, Any]) -> None:
        """Apply the strong people actions first, so the mail rules see who wrote each email. A
        failed people pass never stops the mail rules."""
        people = result["people"]
        try:
            strong = [a for a in self._people_repo.plan() if a.tier == "strong"]
            for action in strong[:MAX_PEOPLE_ACTIONS_PER_PASS]:
                outcome = self._people_repo.apply_planned(operator, action, automatic=True)
                people["refused" if outcome.get("code") else "applied"] += 1
        except Exception:
            logger.exception("auto mail rules: the people pass failed")
            people["skipped"] = "error"

    def run_once(self) -> dict[str, Any]:
        """Apply every safe automatic R1/R2/R7 action the rules plan now, if the switch is on."""
        started = datetime.now(UTC)
        with self._repo._read() as cur:
            switch = read_switch(cur)
        operator: OperatorIdentity | None = switch["operator"]
        result: dict[str, Any] = {"at": started.isoformat(), "applied": 0, "refused": 0, "pending": 0,
                                  "skipped": None, "refusals": [],
                                  "people": {"applied": 0, "refused": 0, "skipped": None}}
        if not switch["enabled"] or operator is None:
            result["skipped"] = "off"
        elif not _is_active_admin(operator):
            result["skipped"] = "operator_not_admin"
        if result["skipped"] is not None:
            result["people"]["skipped"] = result["skipped"]
        elif self._people_repo is None or not self.people_enabled:
            result["people"]["skipped"] = "disabled"
        else:
            self._people_pass(operator, result)
        if result["skipped"] is None:
            actions, _display, _snapshot = self._repo.plan()
            eligible = [a for a in actions if a.mode == AUTO and a.rule_id in self.rules]
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
            people = result.get("people") or {}
            if result["applied"] or result["refused"] or people.get("applied") or people.get("refused"):
                logger.info("auto mail rules: applied=%d refused=%d pending=%d people_applied=%d people_refused=%d",
                            result["applied"], result["refused"], result["pending"],
                            people.get("applied", 0), people.get("refused", 0))

"""Maintenance boot-policy (PR-D): persistent systemd enablement suppression.

During SQLite cutover maintenance, ``origenlab-api.service`` and
``origenlab-api-health.timer`` must remain persistently *disabled* so a WSL /
user-manager restart cannot auto-start them and reopen production SQLite.

Uses ``systemctl --user disable`` / ``enable`` (not runtime masks). Enablement
classification is fail-closed: only exact ``enabled`` / ``disabled`` results
are accepted.
"""

from __future__ import annotations

from typing import Any

# Units this policy may inspect or mutate. Never touch any other unit.
BOOT_POLICY_API_UNIT = "origenlab-api.service"
BOOT_POLICY_TIMER_UNIT = "origenlab-api-health.timer"
BOOT_POLICY_UNITS: frozenset[str] = frozenset(
    {BOOT_POLICY_API_UNIT, BOOT_POLICY_TIMER_UNIT}
)

BOOT_POLICY_SUPPRESS_ACTION = "suppress"
BOOT_POLICY_RESTORE_ACTION = "restore"
BOOT_POLICY_ACTIONS: frozenset[str] = frozenset(
    {BOOT_POLICY_SUPPRESS_ACTION, BOOT_POLICY_RESTORE_ACTION}
)
BOOT_POLICY_SUPPRESS_PHASES: frozenset[str] = frozenset(
    {"capture", "disable_timer", "disable_api", "stop_units", "active"}
)
BOOT_POLICY_RESTORE_PHASES: frozenset[str] = frozenset(
    {"restore_timer", "restore_api", "verified"}
)


def _is_exact_bool(value: Any) -> bool:
    return type(value) is bool


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def classify_unit_enablement(
    *, returncode: int, is_enabled_text: str | None
) -> dict[str, Any]:
    """Classify ``systemctl is-enabled`` into enabled / disabled / ambiguous.

    Only the exact successful ``enabled`` pairing (rc=0 + text ``enabled``) and
    the exact ``disabled`` pairing (nonzero rc≠127 + text ``disabled``) are
    definite. Command failure (rc=127), empty/unknown text, ``static``,
    ``masked``, ``indirect``, ``generated``, ``enabled-runtime``, or any
    inconsistent return-code/text pair is ambiguous. Never treat "nonzero means
    disabled" as a binary rule.
    """
    text = (is_enabled_text or "").strip().lower()
    rc = int(returncode)
    if rc == 127:
        enabled = disabled = False
        ambiguous = True
    elif text == "enabled" and rc == 0:
        enabled, disabled, ambiguous = True, False, False
    elif text == "disabled" and rc != 0:
        enabled, disabled, ambiguous = False, True, False
    else:
        enabled = disabled = False
        ambiguous = True
    return {
        "state": (
            "enabled" if enabled else "disabled" if disabled else "ambiguous"
        ),
        "enabled": enabled,
        "disabled": disabled,
        "ambiguous": ambiguous,
        "is_enabled_text": text or None,
        "returncode": rc,
    }


def boot_policy_intent_problem(intent: Any, *, maintenance_id: str) -> str | None:
    """Validate a typed maintenance_boot_policy_intent object."""
    if not isinstance(intent, dict) or not intent:
        return "boot_policy_intent_empty_or_not_object"
    action = intent.get("action")
    if action not in BOOT_POLICY_ACTIONS:
        return "boot_policy_intent_action"
    if intent.get("maintenance_id") != maintenance_id:
        return "boot_policy_intent_mid"
    if not _is_nonempty_str(intent.get("started_at_utc")):
        return "boot_policy_intent_timestamp"
    if not _is_exact_bool(intent.get("api_was_enabled")):
        return "boot_policy_intent_api_was_enabled"
    if not _is_exact_bool(intent.get("timer_was_enabled")):
        return "boot_policy_intent_timer_was_enabled"
    phase = intent.get("phase")
    if action == BOOT_POLICY_SUPPRESS_ACTION:
        if phase not in BOOT_POLICY_SUPPRESS_PHASES:
            return "boot_policy_intent_suppress_phase"
    else:
        if phase not in BOOT_POLICY_RESTORE_PHASES:
            return "boot_policy_intent_restore_phase"
    return None


def boot_policy_journal_problem(
    *,
    maintenance_id: str,
    api_enabled: Any,
    timer_enabled: Any,
    intent: Any,
    active: Any,
    restored: Any,
) -> str | None:
    """Central validation of PR-D journal enablement / intent / truth fields."""
    if active is not None and not _is_exact_bool(active):
        return "boot_policy_active_non_bool"
    if restored is not None and not _is_exact_bool(restored):
        return "boot_policy_restored_non_bool"
    if api_enabled is not None and not _is_exact_bool(api_enabled):
        return "pre_maintenance_api_enabled_non_bool"
    if timer_enabled is not None and not _is_exact_bool(timer_enabled):
        return "pre_maintenance_timer_enabled_non_bool"
    if intent is not None:
        problem = boot_policy_intent_problem(intent, maintenance_id=maintenance_id)
        if problem is not None:
            return problem
        if _is_exact_bool(api_enabled) and intent.get("api_was_enabled") != api_enabled:
            return "boot_policy_intent_api_ne_journal"
        if (
            _is_exact_bool(timer_enabled)
            and intent.get("timer_was_enabled") != timer_enabled
        ):
            return "boot_policy_intent_timer_ne_journal"
    if active is True:
        if not _is_exact_bool(api_enabled) or not _is_exact_bool(timer_enabled):
            return "boot_policy_active_missing_pre_state"
        if intent is None:
            return "boot_policy_active_missing_intent"
    return None


def sanitized_boot_policy_status(
    *,
    api_enabled: Any,
    timer_enabled: Any,
    intent: Any,
    active: Any,
    restored: Any,
) -> dict[str, Any]:
    """Public status fragment — no absolute paths or raw command output."""
    phase = None
    action = None
    if isinstance(intent, dict):
        phase = intent.get("phase") if isinstance(intent.get("phase"), str) else None
        action = intent.get("action") if isinstance(intent.get("action"), str) else None
    return {
        "pre_maintenance_api_enabled": api_enabled if _is_exact_bool(api_enabled) else None,
        "pre_maintenance_health_timer_enabled": (
            timer_enabled if _is_exact_bool(timer_enabled) else None
        ),
        "maintenance_boot_policy_active": active if _is_exact_bool(active) else None,
        "maintenance_boot_policy_restored": (
            restored if _is_exact_bool(restored) else None
        ),
        "intent_action": action,
        "intent_phase": phase,
    }

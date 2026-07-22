"""Maintenance boot-policy (PR-D): persistent systemd enablement suppression.

During SQLite cutover maintenance, ``origenlab-api.service`` and
``origenlab-api-health.timer`` must remain persistently *disabled* so a WSL /
user-manager restart cannot auto-start them and reopen production SQLite.

Uses ``systemctl --user disable`` / ``enable`` (not runtime masks). Enablement
classification is fail-closed: only exact lowercase ``enabled`` / ``disabled``
results are accepted (no case normalization).
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
BOOT_POLICY_ALL_PHASES: frozenset[str] = (
    BOOT_POLICY_SUPPRESS_PHASES | BOOT_POLICY_RESTORE_PHASES
)

# Coherent lifecycle labels (central contract).
LIFECYCLE_PRISTINE = "pristine"
LIFECYCLE_SUPPRESSION_IN_PROGRESS = "suppression_in_progress"
LIFECYCLE_SUPPRESSION_ACTIVE = "suppression_active"
LIFECYCLE_RESTORATION_IN_PROGRESS = "restoration_in_progress"
LIFECYCLE_RESTORED = "restored"
BOOT_POLICY_COHERENT_LIFECYCLES: frozenset[str] = frozenset(
    {
        LIFECYCLE_PRISTINE,
        LIFECYCLE_SUPPRESSION_IN_PROGRESS,
        LIFECYCLE_SUPPRESSION_ACTIVE,
        LIFECYCLE_RESTORATION_IN_PROGRESS,
        LIFECYCLE_RESTORED,
    }
)


def _is_exact_bool(value: Any) -> bool:
    return type(value) is bool


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def classify_unit_enablement(
    *, returncode: int, is_enabled_text: str | None
) -> dict[str, Any]:
    """Classify ``systemctl is-enabled`` into enabled / disabled / ambiguous.

    Only the exact successful ``enabled`` pairing (rc=0 + exact lowercase text
    ``enabled``) and the exact ``disabled`` pairing (nonzero rc≠127 + exact
    lowercase text ``disabled``) are definite. Stdout is trimmed but **not**
    case-normalized: uppercase or otherwise altered text is ambiguous. Command
    failure (rc=127), empty/unknown text, ``static``, ``masked``, ``indirect``,
    ``generated``, ``enabled-runtime``, or any inconsistent return-code/text
    pair is ambiguous. Never treat "nonzero means disabled" as a binary rule.
    """
    text = (is_enabled_text or "").strip()
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
    """Central validation of PR-D journal enablement / intent / truth fields.

    Also rejects contradictory lifecycle combinations before any skip/mutation
    decision. Prefer :func:`boot_policy_lifecycle` when the caller needs the
    coherent state label.
    """
    lifecycle, problem = boot_policy_lifecycle(
        maintenance_id=maintenance_id,
        api_enabled=api_enabled,
        timer_enabled=timer_enabled,
        intent=intent,
        active=active,
        restored=restored,
    )
    if problem is not None:
        return problem
    if lifecycle not in BOOT_POLICY_COHERENT_LIFECYCLES:
        return "boot_policy_lifecycle_unknown"
    return None


def boot_policy_lifecycle(
    *,
    maintenance_id: str,
    api_enabled: Any,
    timer_enabled: Any,
    intent: Any,
    active: Any,
    restored: Any,
) -> tuple[str | None, str | None]:
    """Classify PR-D journal fields into a coherent lifecycle or a problem.

    Returns ``(lifecycle_label, None)`` on success or ``(None, problem)`` when
    the combination is malformed / contradictory.

    Coherent states:

    - ``pristine``: no intent, no captured booleans, active=False, restored=False
    - ``suppression_in_progress``: typed suppress intent, exact captured
      booleans, active=False, restored=False
    - ``suppression_active``: typed suppress intent at phase ``active``, exact
      captured booleans, active=True, restored=False
    - ``restoration_in_progress``: typed restore intent, exact captured
      booleans, active=True, restored=False
    - ``restored``: typed restore intent at phase ``verified``, exact captured
      booleans, active=False, restored=True
    """
    # Exact-boolean shape for truth fields (defaults are always bool; reject
    # non-bool / None masquerading as missing when other evidence exists).
    if not _is_exact_bool(active):
        return None, "boot_policy_active_non_bool"
    if not _is_exact_bool(restored):
        return None, "boot_policy_restored_non_bool"

    # Contradictions that must never be skipped as "nothing to restore".
    if active is True and restored is True:
        return None, "boot_policy_active_and_restored"
    if restored is True and isinstance(intent, dict):
        if intent.get("action") == BOOT_POLICY_SUPPRESS_ACTION:
            return None, "boot_policy_restored_with_suppress_intent"
    if (
        isinstance(intent, dict)
        and intent.get("action") == BOOT_POLICY_RESTORE_ACTION
        and intent.get("phase") == "verified"
        and restored is not True
    ):
        return None, "boot_policy_verified_phase_not_restored"

    has_api = api_enabled is not None
    has_timer = timer_enabled is not None
    if has_api and not _is_exact_bool(api_enabled):
        return None, "pre_maintenance_api_enabled_non_bool"
    if has_timer and not _is_exact_bool(timer_enabled):
        return None, "pre_maintenance_timer_enabled_non_bool"

    intent_present = intent is not None
    if intent_present:
        ip = boot_policy_intent_problem(intent, maintenance_id=maintenance_id)
        if ip is not None:
            return None, ip
        assert isinstance(intent, dict)
        if _is_exact_bool(api_enabled) and intent.get("api_was_enabled") != api_enabled:
            return None, "boot_policy_intent_api_ne_journal"
        if (
            _is_exact_bool(timer_enabled)
            and intent.get("timer_was_enabled") != timer_enabled
        ):
            return None, "boot_policy_intent_timer_ne_journal"

    # Evidence of any policy work: intent and/or captured enablement bools.
    has_capture = has_api or has_timer
    if has_capture and not (has_api and has_timer and _is_exact_bool(api_enabled)
                            and _is_exact_bool(timer_enabled)):
        return None, "boot_policy_partial_pre_enablement"
    if (has_capture or intent_present or active is True or restored is True) and (
        intent_present is False
        and not has_capture
        and active is False
        and restored is False
    ):
        # Unreachable guard — kept for clarity.
        pass

    # Pristine: no intent, no captures, both truth flags false.
    if (
        not intent_present
        and not has_capture
        and active is False
        and restored is False
    ):
        return LIFECYCLE_PRISTINE, None

    # Any non-pristine evidence requires a typed intent + both captures.
    if not intent_present:
        return None, "boot_policy_missing_intent"
    if not (
        _is_exact_bool(api_enabled) and _is_exact_bool(timer_enabled)
    ):
        return None, "boot_policy_missing_pre_enablement"
    assert isinstance(intent, dict)
    action = intent.get("action")
    phase = intent.get("phase")

    if restored is True:
        if action != BOOT_POLICY_RESTORE_ACTION or phase != "verified":
            return None, "boot_policy_restored_intent_phase"
        if active is not False:
            return None, "boot_policy_restored_active_not_false"
        return LIFECYCLE_RESTORED, None

    if active is True:
        if restored is not False:
            return None, "boot_policy_active_restored_not_false"
        if action == BOOT_POLICY_SUPPRESS_ACTION:
            if phase != "active":
                return None, "boot_policy_active_suppress_phase"
            return LIFECYCLE_SUPPRESSION_ACTIVE, None
        if action == BOOT_POLICY_RESTORE_ACTION:
            if phase not in {"restore_timer", "restore_api"}:
                # verified+active handled above as contradiction.
                return None, "boot_policy_restore_active_phase"
            return LIFECYCLE_RESTORATION_IN_PROGRESS, None
        return None, "boot_policy_active_unknown_action"

    # active=False, restored=False, intent present → suppression in progress
    # (or a restore intent that has not yet flipped active — invalid).
    if action == BOOT_POLICY_SUPPRESS_ACTION:
        if phase == "active":
            # Intent says active but truth flag is false → inconsistent.
            return None, "boot_policy_suppress_active_phase_flag_false"
        if phase not in BOOT_POLICY_SUPPRESS_PHASES:
            return None, "boot_policy_intent_suppress_phase"
        return LIFECYCLE_SUPPRESSION_IN_PROGRESS, None
    if action == BOOT_POLICY_RESTORE_ACTION:
        # Restore with active=False and restored=False is not a coherent
        # intermediate (we keep active=True until verified success).
        return None, "boot_policy_restore_without_active_or_restored"
    return None, "boot_policy_lifecycle_incoherent"


def boot_policy_requires_restoration(lifecycle: str | None) -> bool:
    """True when a coherent non-pristine lifecycle must restore (or verify)."""
    return lifecycle in {
        LIFECYCLE_SUPPRESSION_IN_PROGRESS,
        LIFECYCLE_SUPPRESSION_ACTIVE,
        LIFECYCLE_RESTORATION_IN_PROGRESS,
        LIFECYCLE_RESTORED,
    }


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
        raw_action = intent.get("action")
        raw_phase = intent.get("phase")
        if raw_action in BOOT_POLICY_ACTIONS:
            action = raw_action
        if raw_phase in BOOT_POLICY_ALL_PHASES:
            phase = raw_phase
        # Malformed / non-allowlisted strings (paths, command output) → None.
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

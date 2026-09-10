"""The verdict algebra.

Three rules, and the second and third exist because a reassuring word in a report is the easiest
thing in this system to misread.

**A missing check is not a passing check.** A required check that produced no observation, or an
unusable one, makes the run `INCOMPLETE`. Incompleteness is never absorbed into a pass, and the
report always names what was missing.

**A run that did not contact the hosted project cannot say `PASS`.** A simulated run replays a
committed fixture; it exercises the engine and proves nothing about any database. It reports
`SIMULATED_PASS`, `SIMULATED_FAIL` or `SIMULATED_INCOMPLETE`, always with `simulated: true` and
`hosted_contacted: false`, and it is never eligible to satisfy a migration gate. The word `PASS`
never stands alone in such a report, so `grep -x PASS` finds nothing in it.

**A local run cannot satisfy a hosted gate.** The local foundation is already proven by pgTAP and
by the evidence scripts; a local audit re-proves the same catalogue against the same baseline and
reports `LOCAL_PASS`. `MIGRATION.md` §5.2 requires these checks against the hosted project, and a
local verdict is not that.

Only `PASS` -- bare, from a hosted run that actually connected, with `simulated: false` -- is
eligible, and `gate_eligible` says so explicitly rather than leaving it to be inferred.
"""

from __future__ import annotations

from dataclasses import dataclass

# Per-check outcomes.
PASS = "PASS"
FAIL = "FAIL"
RECORDED = "RECORDED"
CORROBORATED = "CORROBORATED"
ATTESTED = "ATTESTED"
NOT_RUN = "NOT_RUN"
ERROR = "ERROR"

STATUSES = (PASS, FAIL, RECORDED, CORROBORATED, ATTESTED, NOT_RUN, ERROR)

# Outcomes that satisfy a required check.
SATISFYING = frozenset({PASS, ATTESTED})
# Outcomes that fail a run outright, required or not.
BLOCKING = frozenset({FAIL, ERROR})

BASE_PASS = "PASS"
BASE_FAIL = "FAIL"
BASE_INCOMPLETE = "INCOMPLETE"

LOCAL_PREFIX = "LOCAL_"
SIMULATED_PREFIX = "SIMULATED_"


@dataclass(frozen=True)
class Outcome:
    """The whole-run conclusion, and everything needed to know what it is worth."""

    verdict: str
    base: str
    mode: str
    simulated: bool
    hosted_contacted: bool
    gate_eligible: bool
    blocking: tuple[str, ...]
    incomplete: tuple[str, ...]

    @property
    def exit_code(self) -> int:
        """0 only for a clean run of either kind. Incomplete and failed are both non-zero."""
        return 0 if self.base == BASE_PASS else 1


def base_verdict(results: list) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Fold per-check results into PASS / FAIL / INCOMPLETE.

    `results` is any sequence of objects carrying `check_id`, `status` and `required`.
    """
    blocking = tuple(r.check_id for r in results if r.status in BLOCKING)
    incomplete = tuple(
        r.check_id for r in results if r.required and r.status not in SATISFYING and r.status not in BLOCKING
    )
    if blocking:
        return BASE_FAIL, blocking, incomplete
    if incomplete:
        return BASE_INCOMPLETE, blocking, incomplete
    return BASE_PASS, blocking, incomplete


def decide(results: list, *, mode: str, simulated: bool, hosted_contacted: bool) -> Outcome:
    """Apply the algebra. The only path to a bare `PASS` is a real hosted run that connected."""
    base, blocking, incomplete = base_verdict(results)

    if simulated:
        verdict = SIMULATED_PREFIX + base
    elif mode == "local":
        verdict = LOCAL_PREFIX + base
    else:
        verdict = base

    gate_eligible = (
        mode == "hosted"
        and not simulated
        and hosted_contacted
        and base == BASE_PASS
        and verdict == BASE_PASS
    )

    return Outcome(
        verdict=verdict,
        base=base,
        mode=mode,
        simulated=simulated,
        hosted_contacted=hosted_contacted,
        gate_eligible=gate_eligible,
        blocking=blocking,
        incomplete=incomplete,
    )

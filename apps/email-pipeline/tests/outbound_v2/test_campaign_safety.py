"""The safety properties of the campaign package, pinned as tests rather than as prose.

The three functions in ``outbound_v2`` prepare an audience. They must be structurally
incapable of sending: no provider import, no send entry point, no embedded sender identity,
no I/O at all. These tests assert that from the package's own source and surface, so a later
edit that quietly adds a provider call fails here instead of at a mailbox.

Every address below is synthetic, on a reserved documentation domain (RFC 2606).
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import pathlib
import re
from datetime import datetime, timezone

import pytest

from origenlab_email_pipeline import outbound_v2
from origenlab_email_pipeline.outbound_v2 import (
    AudienceCriteria,
    CampaignContent,
    CampaignDraft,
    CampaignPolicy,
    ContactControlIndex,
    FreezeRefused,
    RecipientCandidate,
    RecontactOverride,
    build_audience_preview,
    freeze_campaign,
)
from origenlab_email_pipeline.outbound_v2.reasons import REASON_BLOCK

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
CRITERIA = AudienceCriteria(source_lane="lead_master")
CONTENT = CampaignContent(subject="Equipamiento de laboratorio", body_text="Hola")
DRAFT = CampaignDraft(campaign_id="camp-1", status="draft", version=3, max_sends=100)

PACKAGE_DIR = pathlib.Path(outbound_v2.__file__).resolve().parent
SOURCES = sorted(PACKAGE_DIR.glob("*.py"))


def _preview(candidates, controls=None, policy=None):
    return build_audience_preview(
        candidates=candidates,
        criteria=CRITERIA,
        controls=controls or ContactControlIndex(),
        policy=policy or CampaignPolicy(max_sends=100, recontact_interval_days=180),
        now=NOW,
    )


def _freeze(preview=None, campaign=DRAFT):
    return freeze_campaign(
        campaign=campaign,
        preview=preview if preview is not None else _preview(
            [RecipientCandidate(address_norm="a@uni.example")]
        ),
        content=CONTENT,
        criteria=CRITERIA,
        operator_id="op-1",
        now=NOW,
    )


# ── no send path can exist here ─────────────────────────────────────────────────────────


def test_the_package_has_sources_to_inspect() -> None:
    """Guards the three source-level tests below against silently matching nothing."""
    assert len(SOURCES) >= 5, SOURCES


#: Anything that could perform a send, a provider call, a database write or a clock read.
FORBIDDEN_IMPORTS = {
    "smtplib",
    "socket",
    "ssl",
    "http",
    "http.client",
    "httpx",
    "requests",
    "urllib",
    "urllib.request",
    "psycopg",
    "psycopg2",
    "sqlite3",
    "sqlalchemy",
    "googleapiclient",
    "google",
    "google.auth",
    "subprocess",
    "os",
    "time",
}


@pytest.mark.parametrize("source", SOURCES, ids=lambda p: p.name)
def test_no_module_imports_a_provider_a_database_or_a_clock(source: pathlib.Path) -> None:
    """A send needs a transport; a non-reproducible verdict needs a clock. Neither is importable."""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module)
    offenders = {
        name
        for name in imported
        if name in FORBIDDEN_IMPORTS or name.split(".")[0] in FORBIDDEN_IMPORTS
    }
    assert not offenders, f"{source.name} imports {sorted(offenders)}"


@pytest.mark.parametrize("source", SOURCES, ids=lambda p: p.name)
def test_no_module_reads_the_wall_clock(source: pathlib.Path) -> None:
    """``now`` is always an argument, which is what makes a frozen audience reproducible."""
    text = source.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    for forbidden in ("datetime.now(", "datetime.utcnow(", "date.today("):
        assert forbidden not in code, f"{source.name} reads the clock: {forbidden}"


def test_the_public_surface_exposes_no_send_or_dispatch_entry_point() -> None:
    """Preparation only. A send verb appearing here would be a new architecture decision."""
    forbidden = re.compile(
        r"send|dispatch|deliver|submit|smtp|gmail|provider|transport", re.IGNORECASE
    )
    offenders = [
        name
        for name in outbound_v2.__all__
        # ``max_sends`` is a budget *ceiling* read from the campaign row, not a send verb.
        if forbidden.search(name) and name not in {"RecipientInsert"}
    ]
    assert offenders == [], offenders


def test_no_module_embeds_a_sender_identity_or_any_address_literal() -> None:
    """No default From, no fallback mailbox, no hard-coded recipient anywhere in the package."""
    address = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    offenders: list[str] = []
    for source in SOURCES:
        for n, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            if address.search(line):
                offenders.append(f"{source.name}:{n}: {line.strip()}")
    assert offenders == [], offenders


@pytest.mark.parametrize(
    "cls", [CampaignContent, CampaignDraft, CampaignPolicy, AudienceCriteria]
)
def test_no_campaign_dataclass_carries_a_sender_field(cls) -> None:
    """The sending mailbox is `outbound.campaign.mailbox_id`, chosen at send time, not here."""
    names = {f.name for f in dataclasses.fields(cls)}
    assert not {n for n in names if re.search(r"sender|from_|mailbox|smtp|reply_to", n)}, names


# ── preparation does not send, and does not write ───────────────────────────────────────


def test_preparation_returns_intent_and_mutates_nothing() -> None:
    """``freeze_campaign`` describes one transaction; it never performs it."""
    before = dataclasses.replace(DRAFT)
    plan = _freeze(campaign=DRAFT)
    # The draft it was given is untouched — the caller's row is not advanced by planning it.
    assert DRAFT == before
    assert DRAFT.status == "draft"
    # The plan is inert data: no method on it applies anything.
    assert dataclasses.is_dataclass(plan)
    appliers = [
        name
        for name, _ in inspect.getmembers(plan, callable)
        if not name.startswith("_")
    ]
    assert appliers == [], appliers


def test_a_freeze_plan_never_advances_a_campaign_past_audience_frozen() -> None:
    """Approval and activation are separate commands; a freeze cannot reach them."""
    assert _freeze().campaign_updates["status"] == "audience_frozen"


def test_an_excluded_recipient_is_never_planned_as_sendable() -> None:
    """The one structural guarantee: `excluded` and `snapshotted` are decided by the verdict."""
    controls = ContactControlIndex(blocked_addresses=frozenset({"b@uni.example"}))
    plan = _freeze(
        preview=_preview(
            [
                RecipientCandidate(address_norm="a@uni.example"),
                RecipientCandidate(address_norm="b@uni.example"),
            ],
            controls=controls,
        )
    )
    rows = {r.address_norm: r for r in plan.recipient_rows}
    assert rows["b@uni.example"].state == "excluded"
    assert rows["b@uni.example"].exclusion_reasons == (REASON_BLOCK,)
    assert plan.eligible_count == 1
    # Every row is accounted for, so no recipient is silently dropped from the snapshot.
    assert plan.eligible_count + plan.excluded_count == len(plan.recipient_rows)


def test_an_override_cannot_make_a_suppressed_contact_sendable() -> None:
    """An override buys another contact; it never overrules a block. Fail-closed end to end."""
    controls = ContactControlIndex(blocked_addresses=frozenset({"a@uni.example"}))
    preview = build_audience_preview(
        candidates=[RecipientCandidate(address_norm="a@uni.example")],
        criteria=CRITERIA,
        controls=controls,
        policy=CampaignPolicy(max_sends=100, recontact_interval_days=180),
        now=NOW,
        overrides={"a@uni.example": RecontactOverride(operator_id="op-1", reason="insistir")},
    )
    plan = _freeze(preview=preview)
    assert plan.eligible_count == 0
    assert plan.recipient_rows[0].state == "excluded"


# ── duplicate prevention and concurrency ────────────────────────────────────────────────


def test_one_row_per_address_so_no_recipient_can_be_planned_twice() -> None:
    """`(campaign_id, address_norm)` is unique in the database; the plan never relies on that."""
    plan = _freeze(
        preview=_preview(
            [
                RecipientCandidate(address_norm="a@uni.example"),
                RecipientCandidate(address_norm="A@Uni.Example"),
                RecipientCandidate(address_norm="  a@uni.example  "),
            ]
        )
    )
    addresses = [r.address_norm for r in plan.recipient_rows]
    assert addresses == ["a@uni.example"]
    assert len(set(addresses)) == len(addresses)


def test_one_destination_per_person_survives_into_the_plan() -> None:
    """Frequency is a property of a person, so a second address is excluded, not sent to."""
    plan = _freeze(
        preview=_preview(
            [
                RecipientCandidate(address_norm="a@uni.example", person_id="p-1"),
                RecipientCandidate(address_norm="b@uni.example", person_id="p-1"),
            ]
        )
    )
    assert plan.eligible_count == 1


def test_a_retry_of_the_freeze_is_refused_rather_than_duplicating_the_audience() -> None:
    """Re-running a freeze that already succeeded cannot insert a second audience."""
    frozen = dataclasses.replace(DRAFT, status="audience_frozen", version=4)
    with pytest.raises(FreezeRefused, match="not 'draft'"):
        _freeze(campaign=frozen)


def test_two_concurrent_workers_plan_the_same_expected_version_so_one_loses() -> None:
    """Optimistic concurrency is the transaction boundary: the second UPDATE matches no row."""
    first = _freeze()
    second = _freeze()
    assert first.expected_version == second.expected_version == DRAFT.version
    assert first.campaign_updates["version"] == DRAFT.version + 1
    # Both plans are byte-identical, so whichever commits first is the only one that can.
    assert first == second


def test_the_plan_is_deterministic_for_identical_inputs() -> None:
    """A replayed preparation produces the same snapshot, including the content fingerprint."""
    candidates = [
        RecipientCandidate(address_norm="a@uni.example", person_id="p-1"),
        RecipientCandidate(address_norm="noreply@uni.example"),
    ]
    assert _freeze(preview=_preview(candidates)) == _freeze(preview=_preview(candidates))

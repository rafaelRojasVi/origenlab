"""The safety properties of the campaign package, pinned as tests rather than as prose.

The two functions in ``outbound_v2`` prepare an audience. They must be structurally
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
    CampaignPolicy,
    ContactControlIndex,
    RecipientCandidate,
    build_audience_preview,
)
from origenlab_email_pipeline.outbound_v2.reasons import REASON_BLOCK

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
CRITERIA = AudienceCriteria(source_lane="lead_master")

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



# ── no send path can exist here ─────────────────────────────────────────────────────────


def test_the_package_has_sources_to_inspect() -> None:
    """Guards the three source-level tests below against silently matching nothing."""
    assert len(SOURCES) >= 5, SOURCES


def test_the_package_holds_no_second_freeze() -> None:
    """The CRM command ``freeze-campaign-audience`` (apps/api) is the one freeze.

    A pure ``freeze_campaign`` lived here with no caller and contradicted it: no preheader in
    the content fingerprint, and "editing a frozen campaign returns it to draft", which the
    database refuses. It was deleted; neither a module nor an export may bring it back.
    """
    assert not (PACKAGE_DIR / "freeze.py").exists()
    assert [n for n in outbound_v2.__all__ if "freeze" in n.lower() or "frozen" in n.lower()] == []


def test_the_package_holds_no_second_recontact_override() -> None:
    """W12 is decided inside ``freeze-campaign-audience`` (apps/api), which waives
    ``prior_contact`` only and seals the override triple in the recipient snapshot.

    A ``RecontactOverride`` type lived here with no caller outside its own tests and
    contradicted it: it also cleared ``prior_reply`` and ``cooldown``. It was retired; neither
    the type, the parameter nor the verdict fields may come back.
    """
    from origenlab_email_pipeline.outbound_v2 import audience, eligibility, reasons

    assert [n for n in outbound_v2.__all__ if "overrid" in n.lower()] == []
    for module in (eligibility, audience, reasons):
        assert not [n for n in vars(module) if "overrid" in n.lower()], module.__name__
    assert "override" not in inspect.signature(eligibility.evaluate_recipient_eligibility).parameters
    assert "overrides" not in inspect.signature(audience.build_audience_preview).parameters
    assert not [f.name for f in dataclasses.fields(eligibility.EligibilityVerdict) if "overrid" in f.name]
    # A recipient with prior contact stays excluded: nothing in this package can waive it.
    verdict = eligibility.evaluate_recipient_eligibility(
        candidate=RecipientCandidate(address_norm="a@uni.example"),
        controls=ContactControlIndex(prior_contact_addresses=frozenset({"a@uni.example"})),
        policy=CampaignPolicy(max_sends=100, recontact_interval_days=180),
        now=NOW,
    )
    assert verdict.eligible is False


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
        if forbidden.search(name)
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


@pytest.mark.parametrize("cls", [CampaignPolicy, AudienceCriteria])
def test_no_campaign_dataclass_carries_a_sender_field(cls) -> None:
    """The sending mailbox is `outbound.campaign.mailbox_id`, chosen at send time, not here."""
    names = {f.name for f in dataclasses.fields(cls)}
    assert not {n for n in names if re.search(r"sender|from_|mailbox|smtp|reply_to", n)}, names


# ── preparation does not send, and does not write ───────────────────────────────────────


def _rows(preview):
    return {r.candidate.address_norm.strip().lower(): r for r in preview.rows}


def test_preparation_returns_inert_data() -> None:
    """A preview describes an audience; no method on it applies anything."""
    preview = _preview([RecipientCandidate(address_norm="a@uni.example")])
    assert dataclasses.is_dataclass(preview)
    appliers = [name for name, _ in inspect.getmembers(preview, callable) if not name.startswith("_")]
    assert appliers == [], appliers


def test_an_excluded_recipient_is_never_previewed_as_eligible() -> None:
    """`excluded` and eligible are decided by the verdict, and every row is accounted for."""
    controls = ContactControlIndex(blocked_addresses=frozenset({"b@uni.example"}))
    preview = _preview(
        [
            RecipientCandidate(address_norm="a@uni.example"),
            RecipientCandidate(address_norm="b@uni.example"),
        ],
        controls=controls,
    )
    rows = _rows(preview)
    assert not rows["b@uni.example"].verdict.eligible
    assert rows["b@uni.example"].verdict.reasons == (REASON_BLOCK,)
    assert preview.eligible_count == 1
    assert preview.eligible_count + preview.excluded_count == len(preview.rows)


# ── duplicate prevention ────────────────────────────────────────────────────────────────


def test_one_row_per_address_so_no_recipient_can_be_previewed_twice() -> None:
    """`(campaign_id, address_norm)` is unique in the database; the preview never relies on that."""
    preview = _preview(
        [
            RecipientCandidate(address_norm="a@uni.example"),
            RecipientCandidate(address_norm="A@Uni.Example"),
            RecipientCandidate(address_norm="  a@uni.example  "),
        ]
    )
    assert len(preview.rows) == 1
    assert preview.duplicate_addresses_dropped == 2


def test_one_destination_per_person() -> None:
    """Frequency is a property of a person, so a second address is excluded, not eligible."""
    preview = _preview(
        [
            RecipientCandidate(address_norm="a@uni.example", person_id="p-1"),
            RecipientCandidate(address_norm="b@uni.example", person_id="p-1"),
        ]
    )
    assert preview.eligible_count == 1


def test_the_preview_is_deterministic_for_identical_inputs() -> None:
    """A replayed preparation produces the same verdicts and the same criteria fingerprint."""
    candidates = [
        RecipientCandidate(address_norm="a@uni.example", person_id="p-1"),
        RecipientCandidate(address_norm="noreply@uni.example"),
    ]
    assert _preview(candidates) == _preview(candidates)

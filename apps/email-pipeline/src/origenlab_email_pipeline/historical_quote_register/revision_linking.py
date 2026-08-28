"""Revision-vs-resend distinction (design §3.2). A repeated quote number is
NOT a revision by itself — only a genuinely different document (different
SHA256) with corroborating evidence (an explicit revision/version marker) is
treated as a new revision. Anything else that looks like it *might* be a
revision is flagged for human review, never auto-linked.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

RevisionRelationship = Literal[
    "first_revision", "same_revision_resend", "corroborated_new_revision", "possible_revision_needs_review"
]

_REVISION_MARKER_RE = re.compile(
    r"\b(?:rev(?:isi[oó]n)?|vers[ií]?[óo]n)\.?[\s\-]*(?:no\.?|n°)?\s*\d+\b",
    re.IGNORECASE
)


@dataclass(frozen=True)
class PriorSend:
    historical_quote_key: str
    attachment_sha256: str | None


@dataclass(frozen=True)
class RevisionLinkResult:
    relationship: RevisionRelationship
    supersedes_historical_quote_key: str | None


def link_revision(
    *,
    prior_send: PriorSend | None,
    current_attachment_sha256: str | None,
    current_historical_quote_key: str,
    evidence_text: str,
) -> RevisionLinkResult:
    if prior_send is None:
        return RevisionLinkResult(relationship="first_revision", supersedes_historical_quote_key=None)

    if prior_send.attachment_sha256 is None or current_attachment_sha256 is None:
        return RevisionLinkResult(relationship="possible_revision_needs_review", supersedes_historical_quote_key=None)

    if prior_send.attachment_sha256 == current_attachment_sha256:
        return RevisionLinkResult(relationship="same_revision_resend", supersedes_historical_quote_key=None)

    if _REVISION_MARKER_RE.search(evidence_text or ""):
        return RevisionLinkResult(
            relationship="corroborated_new_revision",
            supersedes_historical_quote_key=prior_send.historical_quote_key,
        )

    return RevisionLinkResult(relationship="possible_revision_needs_review", supersedes_historical_quote_key=None)

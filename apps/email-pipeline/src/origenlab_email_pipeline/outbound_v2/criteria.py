"""The typed audience criteria behind ``outbound.campaign.audience_criteria``.

The database checks only that the column is a JSON object carrying version 1; the field
contract lives here, the same division ``send_attempt.search_evidence`` already uses.

``fingerprint`` is what ties a preview to a freeze: :func:`freeze_campaign` refuses a
preview whose fingerprint does not match the criteria being frozen, so an audience can
never be approved against criteria the operator has since edited.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

AUDIENCE_CRITERIA_VERSION = 1


@dataclass(frozen=True)
class AudienceCriteria:
    """A reproducible description of *which* candidates a campaign considers.

    The fields are the knobs the three current lanes already expose
    (``outbound_core.gate_context_for_*``, ``next_marketing_queue``,
    ``archive_send_batch_builder``), lifted into one typed struct.
    """

    source_lane: str
    version: int = AUDIENCE_CRITERIA_VERSION
    sent_folders: tuple[str, ...] = ()
    extra_exclude_domains: tuple[str, ...] = ()
    strict_contact_graph_noise: bool = False
    skip_supplier_domain_filter: bool = False
    research_statuses: tuple[str, ...] = ()
    limit: int | None = None

    def __post_init__(self) -> None:
        if self.version != AUDIENCE_CRITERIA_VERSION:
            raise ValueError(
                f"unsupported audience criteria version {self.version!r} "
                f"(campaign_audience_criteria_version_check pins {AUDIENCE_CRITERIA_VERSION})"
            )
        if not str(self.source_lane).strip():
            raise ValueError("audience criteria carry a source_lane")

    def to_json(self) -> dict[str, Any]:
        """The canonical serialization written to ``campaign.audience_criteria``."""
        payload = asdict(self)
        for key in ("sent_folders", "extra_exclude_domains", "research_statuses"):
            payload[key] = list(payload[key])
        return payload

    def fingerprint(self) -> str:
        """sha256 over the canonical serialization; stable across processes and runs."""
        canonical = json.dumps(self.to_json(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

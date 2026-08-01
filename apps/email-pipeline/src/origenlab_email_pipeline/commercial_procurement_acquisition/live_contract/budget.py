"""In-memory request budget ledger for PR5B.1 (no automatic retries)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

AuthKind = Literal["ticket", "public"]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class RequestBudget:
    authenticated_budget_max: int = 4
    public_budget_max: int = 4
    authenticated_attempted: int = 0
    authenticated_completed: int = 0
    public_attempted: int = 0
    public_completed: int = 0
    stopped_due_to_budget: bool = False
    no_automatic_retry: bool = True
    entries: list[dict[str, Any]] = field(default_factory=list)

    def remaining(self, kind: AuthKind) -> int:
        if kind == "ticket":
            return max(0, self.authenticated_budget_max - self.authenticated_attempted)
        return max(0, self.public_budget_max - self.public_attempted)

    def reserve(self, kind: AuthKind) -> int:
        """Reserve one request slot. Raises RuntimeError if exhausted."""
        if self.remaining(kind) <= 0:
            self.stopped_due_to_budget = True
            raise RuntimeError(f"{kind}_request_budget_exhausted")
        if kind == "ticket":
            self.authenticated_attempted += 1
            return self.authenticated_attempted
        self.public_attempted += 1
        return self.public_attempted

    def record(
        self,
        *,
        ordinal: int,
        authentication_kind: AuthKind,
        endpoint_kind: str,
        sanitized_endpoint_path: str,
        sanitized_query_fields: dict[str, Any],
        started_at_utc: str,
        completed_at_utc: str | None,
        http_status: int | None,
        response_byte_count: int | None,
        raw_byte_sha256: str | None,
        canonical_json_digest: str | None,
        parser_outcome: str | None,
        error_classification: str | None,
        completed: bool,
    ) -> None:
        if completed:
            if authentication_kind == "ticket":
                self.authenticated_completed += 1
            else:
                self.public_completed += 1
        entry = {
            "ordinal": ordinal,
            "authentication_kind": authentication_kind,
            "endpoint_kind": endpoint_kind,
            "sanitized_endpoint_path": sanitized_endpoint_path,
            "sanitized_query_fields": sanitized_query_fields,
            "started_at_utc": started_at_utc,
            "completed_at_utc": completed_at_utc,
            "http_status": http_status,
            "response_byte_count": response_byte_count,
            "raw_byte_sha256": raw_byte_sha256,
            "canonical_json_digest": canonical_json_digest,
            "parser_outcome": parser_outcome,
            "error_classification": error_classification,
        }
        # Hard ban: never persist credential-bearing fields.
        forbidden = (
            "ticket",
            "authorization",
            "url",
            "request_url",
            "ticket_hash",
            "ticket_length",
            "ticket_prefix",
            "ticket_suffix",
        )
        for key in forbidden:
            entry.pop(key, None)
        self.entries.append(entry)

    def to_dict(self) -> dict[str, Any]:
        return {
            "authenticated_budget_max": self.authenticated_budget_max,
            "authenticated_attempted": self.authenticated_attempted,
            "authenticated_completed": self.authenticated_completed,
            "public_budget_max": self.public_budget_max,
            "public_attempted": self.public_attempted,
            "public_completed": self.public_completed,
            "stopped_due_to_budget": self.stopped_due_to_budget,
            "no_automatic_retry": self.no_automatic_retry,
            "entries": list(self.entries),
        }

    @staticmethod
    def now() -> str:
        return _utcnow()

"""Ephemeral Mercado Público attachment-navigation response."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class TenderAttachmentNavigationResponse(BaseModel):
    """One-use navigation destination resolved from the live public portal."""

    tender_code: str
    destination_kind: Literal["attachments", "tender"]
    url: str
    ephemeral: Literal[True] = True


__all__ = ["TenderAttachmentNavigationResponse"]

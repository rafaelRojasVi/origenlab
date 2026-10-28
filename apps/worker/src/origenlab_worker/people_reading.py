"""Who wrote an inbound email (`people:v1`): the sender's address, the name their mail header gives,
what their signature states (read by the triage model, when it ran), and who else was on the email.

A `message_triage` assertion only — the worker never writes `crm.*`. The API later proposes or links
a contact from it; a person decides. Pure: values in, a dict or ``None`` out.

No reading for mail we sent, for a forward (its signature is the forwarder's, its history someone
else's), for our own addresses, or when nothing at all is known about the writer.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from origenlab_api.v2.mail_rules import OWN_DOMAINS
from origenlab_worker.triage_model import SENDER_FIELDS, ModelSender

#: Value norm of the reading (an `evidence.assertion` of kind `message_triage`).
PEOPLE_VALUE_NORM = "people:v1"
PEOPLE_VERSION = 1
MAX_CO_ADDRESSES = 20
MAX_SIGNATURE_RAW = 800


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower() if "@" in address else ""


def people_value(*, direction: str, sender_address: str | None, header_names: dict[str, str],
                 reading_sender: ModelSender | None, signature_text: str, co_addresses: Sequence[str],
                 forwarded: bool) -> dict[str, Any] | None:
    """The `people:v1` value of one email, or ``None`` when there is no outside writer to read.

    ``header_names`` maps an address to the display name its header gave; ``signature_text`` is the
    text the model was shown as the signature (`triage_model.signature_for`).
    """
    if direction != "inbound" or forwarded or not sender_address:
        return None
    sender_address = sender_address.strip().lower()
    if not sender_address or _domain(sender_address) in OWN_DOMAINS:
        return None
    header_name = (header_names.get(sender_address) or "").strip() or None
    signature = None
    if reading_sender is not None and any(getattr(reading_sender, f) for f in SENDER_FIELDS):
        signature = reading_sender.as_json()
    raw = (signature_text or "")[:MAX_SIGNATURE_RAW]
    if header_name is None and signature is None and not raw.strip():
        return None
    return {
        "version": PEOPLE_VERSION,
        "sender_address": sender_address,
        "header_name": header_name,
        "signature": signature,
        "co_addresses": list(co_addresses)[:MAX_CO_ADDRESSES],
        "signature_raw": raw,
    }


__all__ = ["MAX_CO_ADDRESSES", "PEOPLE_VALUE_NORM", "PEOPLE_VERSION", "people_value"]

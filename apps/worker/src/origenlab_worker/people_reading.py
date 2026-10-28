"""Who wrote an inbound email (`people:v1`): the sender's address, the name their mail header gives,
what their signature states (read by the triage model, when it ran), and who else was on the email.

A `message_triage` assertion only — the worker never writes `crm.*`. The API later proposes or links
a contact from it; a person decides. Pure: values in, a dict or ``None`` out.

No reading for mail we sent, for machine mail (`NO_PEOPLE_CLASSES`), for a forward (its signature
is the forwarder's, its history someone else's), for our own addresses (`OWN_DOMAINS`), or when
nothing at all is known about the writer.

The body is never stored: `signature_raw` keeps only the delimiter-cut signature block
(`MailText.signature_text`), never the body's last lines the model may be shown in its place.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from origenlab_worker.triage_model import SENDER_FIELDS, ModelSender
from origenlab_worker.triage_rules import NOISE_CLASSES, ROUTED_CLASSES

#: Value norm of the reading (an `evidence.assertion` of kind `message_triage`).
PEOPLE_VALUE_NORM = "people:v1"
PEOPLE_VERSION = 1
MAX_CO_ADDRESSES = 20
MAX_SIGNATURE_RAW = 800
#: Our own mail domains: never an outside writer, never a co-address. Both mailboxes are ours
#: (`contacto@labdelivery.cl` too); the API's `person_suggestions.OWN_DOMAINS` is the same set
#: (pinned by a test). `mail_rules.OWN_DOMAINS` is narrower on purpose and is not used here.
OWN_DOMAINS: frozenset[str] = frozenset({"origenlab.cl", "labdelivery.cl"})
#: Triage classes no person wrote — mail we sent, noise (bounces, auto-replies, calendar, bulk,
#: notifications, unsubscribes, empty) and platform alerts: no `people:v1` reading for them.
NO_PEOPLE_CLASSES: frozenset[str] = frozenset({"outbound", *NOISE_CLASSES, *ROUTED_CLASSES})


def is_own_address(address: str) -> bool:
    return _domain(address) in OWN_DOMAINS


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower() if "@" in address else ""


def people_value(*, direction: str, sender_address: str | None, header_names: dict[str, str],
                 reading_sender: ModelSender | None, signature_text: str, co_addresses: Sequence[str],
                 forwarded: bool, empty_ok: bool = False) -> dict[str, Any] | None:
    """The `people:v1` value of one email, or ``None`` when there is no outside writer to read.

    ``header_names`` maps an address to the display name its header gave; ``signature_text`` is the
    delimiter-cut signature block (`MailText.signature_text`), possibly empty. When nothing at all is
    known of the writer the value is ``None`` — unless ``empty_ok`` (only `people-backfill`, after a
    paid model call: the row marks the email read), then a value with only the address. The live
    triage never passes it.
    """
    if direction != "inbound" or forwarded or not sender_address:
        return None
    sender_address = sender_address.strip().lower()
    if not sender_address or is_own_address(sender_address):
        return None
    header_name = (header_names.get(sender_address) or "").strip() or None
    signature = None
    if reading_sender is not None and any(getattr(reading_sender, f) for f in SENDER_FIELDS):
        signature = reading_sender.as_json()
    raw = (signature_text or "")[:MAX_SIGNATURE_RAW]
    if header_name is None and signature is None and not raw.strip() and not empty_ok:
        return None
    return {
        "version": PEOPLE_VERSION,
        "sender_address": sender_address,
        "header_name": header_name,
        "signature": signature,
        "co_addresses": [a for a in co_addresses if not is_own_address(a)][:MAX_CO_ADDRESSES],
        "signature_raw": raw,
    }


__all__ = ["MAX_CO_ADDRESSES", "MAX_SIGNATURE_RAW", "NO_PEOPLE_CLASSES", "OWN_DOMAINS", "PEOPLE_VALUE_NORM",
           "PEOPLE_VERSION", "is_own_address", "people_value"]

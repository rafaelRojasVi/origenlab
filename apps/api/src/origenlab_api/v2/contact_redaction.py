"""Role-based redaction of contact addresses on the V2 read boundary.

`docs/OPERATIONS.md` §2: a `viewer` may read the dashboard, but a contact address — an email
address or a phone channel — is commercial contact data that only `sales` and `admin` may see.
The rule is enforced here, on the answer itself, rather than in each query:

* **The walk is value-based, not field-based.** Every string in a JSON answer is scanned for an
  email address and the local part is masked (`***@ejemplo.invalid`); a phone channel is masked
  by its `kind` or by a `phone` field name. A new column, a `Name <addr>` display string, a
  comma-separated recipient list and an address quoted inside a note are all caught without
  anyone remembering to list them.
* **The route class does it, not the endpoint.** Every `/v2` GET router is built with
  :class:`ContactRedactingRoute`, which reads the operator the identity dependency recorded on
  `request.state` and rewrites the JSON body when that operator may not see addresses. A route
  that recorded no operator is treated as least-privileged: it is redacted.
* **Only successful JSON answers are touched.** A 401 or 404 carries no contact data and is
  returned as the endpoint produced it; a file answer (a PDF) cannot be masked and is gated by
  role at its own route instead.

The header named by :data:`REDACTION_HEADER` tells the dashboard — and a curious operator with
the browser's network tab — that what they got was masked, so a `***@` is never mistaken for a
data quality problem.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Coroutine

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from origenlab_api.v2.identity import OperatorIdentity

#: Roles that read contact addresses as recorded. Everything else is masked — including an
#: unknown role, which the identity dependency refuses before it gets here anyway.
ROLES_THAT_SEE_CONTACT_ADDRESSES: frozenset[str] = frozenset({"sales", "admin"})

REDACTION_HEADER = "X-OrigenLab-Redaction"
REDACTION_VALUE = "contact-addresses"

EMAIL_MASK = "***"
PHONE_MASK = "***"

#: Contact-point kinds whose value is a phone number (`crm.contact_point.kind`).
PHONE_KINDS: frozenset[str] = frozenset({"phone", "whatsapp", "mobile"})
#: Fields that hold a phone number whatever their parent says.
PHONE_FIELDS: frozenset[str] = frozenset({"phone", "phone_norm", "phone_number", "telefono"})
#: Fields of a phone-kind object that hold its number.
PHONE_VALUE_FIELDS: frozenset[str] = frozenset(
    {"address", "address_norm", "value", "value_norm", "value_display"}
)

#: A local part, an `@`, and a host. The host need not carry a dotted TLD: `user@host`,
#: `root@localhost` and `a@srv-01` are contact addresses too, and a mask that misses them leaks
#: the local part. It does need a letter, so `a@1` is not mistaken for one. A bare `@handle`
#: has no local part and is left alone.
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@(?=[A-Za-z0-9.\-]*[A-Za-z])[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*"
)


def sees_contact_addresses(role: str | None) -> bool:
    return role in ROLES_THAT_SEE_CONTACT_ADDRESSES


def _mask_emails(text: str) -> str:
    return _EMAIL_RE.sub(lambda m: f"{EMAIL_MASK}@{m.group(0).split('@', 1)[1]}", text)


def redact_contact_addresses(value: Any) -> Any:
    """A copy of `value` with every contact address masked. Pure; the input is not mutated."""
    if isinstance(value, str):
        return _mask_emails(value)
    if isinstance(value, list):
        return [redact_contact_addresses(item) for item in value]
    if isinstance(value, dict):
        phone_object = value.get("kind") in PHONE_KINDS
        out: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(item, str) and (
                key in PHONE_FIELDS or (phone_object and key in PHONE_VALUE_FIELDS)
            ):
                out[key] = PHONE_MASK
            else:
                out[key] = redact_contact_addresses(item)
        return out
    return value


def remember_operator(request: Request, operator: OperatorIdentity) -> OperatorIdentity:
    """Record the resolved operator for the route class. Called by the identity dependencies."""
    request.state.operator = operator
    return operator


def _is_json(response: Response) -> bool:
    media_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    return media_type in {"application/json", "application/problem+json"}


def redact_response(response: Response) -> Response:
    """The same answer with its JSON body masked; anything that is not a JSON body is untouched."""
    body = getattr(response, "body", None)
    if not isinstance(body, (bytes, bytearray)) or not body or not _is_json(response):
        return response
    try:
        payload = json.loads(body)
    except ValueError:
        return response
    headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in {"content-length", "content-type"}
    }
    headers[REDACTION_HEADER] = REDACTION_VALUE
    return JSONResponse(
        content=redact_contact_addresses(payload),
        status_code=response.status_code,
        headers=headers,
        background=response.background,
    )


class ContactRedactingRoute(APIRoute):
    """An `APIRoute` whose successful JSON answers are masked for an operator without the role.

    Use as `APIRouter(route_class=ContactRedactingRoute)` on every `/v2` read router. The
    identity dependency calls :func:`remember_operator`; a route that never did — because it
    resolved no operator — is redacted, since the least-privileged reading is the safe one.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            response = await original(request)
            if response.status_code >= 300:
                return response
            operator = getattr(request.state, "operator", None)
            role = getattr(operator, "role", None)
            if sees_contact_addresses(role):
                return response
            return redact_response(response)

        return handler

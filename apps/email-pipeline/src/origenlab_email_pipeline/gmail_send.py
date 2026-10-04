from __future__ import annotations

import base64
import mimetypes
import re
from collections.abc import Sequence
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path


@dataclass(frozen=True)
class InlineImage:
    cid: str
    path: Path
    mime_type: str


_CATALOG_SRC_RE = re.compile(r"""src=["'](catalog_assets_premium/[^"']+)["']""", re.IGNORECASE)


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def extract_inline_images_from_html(html: str, *, html_dir: Path) -> tuple[str, list[InlineImage]]:
    """Rewrite local brochure image src -> cid:... and return attachments.

    Only rewrites `src="catalog_assets_premium/..."` references (relative to html_dir).
    """
    attachments: list[InlineImage] = []
    seen: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        rel_src = match.group(1)
        rel_path = Path(rel_src)
        abs_path = (html_dir / rel_path).resolve()
        cid = abs_path.name
        if cid not in seen:
            mime_type, _ = mimetypes.guess_type(abs_path.name)
            if mime_type is None:
                mime_type = "application/octet-stream"
            attachments.append(InlineImage(cid=cid, path=abs_path, mime_type=mime_type))
            seen.add(cid)
        return f'src="cid:{cid}"'

    rewritten = _CATALOG_SRC_RE.sub(_replace, html)
    return rewritten, attachments


def build_gmail_message_with_inline_images(
    *,
    sender_email: str,
    to_emails: str | Sequence[str],
    subject: str,
    html: str,
    html_dir: Path,
    cc_emails: Sequence[str] | None = None,
    list_unsubscribe: str | None = None,
) -> tuple[EmailMessage, list[InlineImage]]:
    rewritten_html, inline_images = extract_inline_images_from_html(html, html_dir=html_dir)

    if isinstance(to_emails, str):
        to_list = [to_emails]
    else:
        to_list = list(to_emails)
    if not to_list:
        raise ValueError("to_emails must contain at least one address")

    msg = EmailMessage()
    msg["From"] = sender_email
    msg["To"] = ", ".join(to_list)
    if cc_emails:
        cc_list = list(cc_emails)
        if cc_list:
            msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = subject
    if list_unsubscribe:
        # RFC 2369: mail clients (Gmail among them) render their own unsubscribe control from it.
        msg["List-Unsubscribe"] = list_unsubscribe

    msg.set_content(
        "Este correo contiene HTML. Si lo ve como texto plano, use un cliente que soporte HTML.",
        charset="utf-8",
    )

    msg.add_alternative(rewritten_html, subtype="html", charset="utf-8")

    # Attach images as related to the HTML part
    html_part = msg.get_payload()[-1]
    for img in inline_images:
        maintype, subtype = img.mime_type.split("/", 1)
        data = img.path.read_bytes()
        html_part.add_related(
            data,
            maintype=maintype,
            subtype=subtype,
            cid=f"<{img.cid}>",
            filename=img.path.name,
            disposition="inline",
        )

    return msg, inline_images


def gmail_api_send_message(*, access_token: str, raw_message_bytes: bytes) -> dict:
    """Send an email via Gmail REST API using an OAuth access token.

    Uses `users/me/messages/send`.
    """
    import json
    import urllib.request

    raw = _base64url(raw_message_bytes)
    payload = json.dumps({"raw": raw}).encode("utf-8")

    req = urllib.request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        data=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        err_body = ""
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            err_body = "<unable to read error body>"
        raise RuntimeError(f"Gmail API error {e.code}: {err_body}") from e


"""Product images in the private `catalog` Storage bucket — ARCHITECTURE §7.

- **The bucket is private; there are no storage policies and no public paths.** FastAPI reaches
  the Storage API with the dedicated `sb_secret_…` key (`ORIGENLAB_V2_STORAGE_SECRET_KEY`), from
  server environment only, and uses it for nothing else. A legacy JWT `service_role` key is
  refused, and the key is never logged, printed or put in an error.
- **A browser only ever receives a signed URL valid for at most 10 minutes**
  (`SIGNED_URL_SECONDS`), minted after the route has authorized that operator for that image.
- **Uploads go through FastAPI**: `check_image` decides what is an image here (≤ 8 MiB, JPEG, PNG
  or WebP, with the declared type and the magic bytes agreeing), and the object is stored under a
  content-addressed path (`image_path`), so writing it twice is harmless and an object left behind
  by a failed database transaction is reused by the next upload of the same bytes.

`FakeStorage` is the dict-backed stand-in every test uses: local Supabase runs with Storage
disabled (a hosted gate), so no test reaches a bucket.
"""
from __future__ import annotations

import logging
import re
from ipaddress import ip_address
from typing import Protocol
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx

logger = logging.getLogger(__name__)

BUCKET = "catalog"
#: 8 MiB. The proxy holds the same line for this path; the API does not rely on it.
MAX_IMAGE_BYTES = 8_388_608
#: What `GET /v2/catalog/images/{id}/url` mints, and the ceiling any signed URL may have (§7).
SIGNED_URL_SECONDS = 600
#: The allowed types and the extension each is stored under.
IMAGE_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SECRET_KEY_PREFIX = "sb_secret_"


class StorageUnavailable(RuntimeError):
    """Storage is not configured, cannot be reached, or did not do what was asked. Never names the key."""


class ImageRefused(ValueError):
    """The bytes are not an image this catalog accepts."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class CatalogStorage(Protocol):
    def put_if_absent(self, path: str, data: bytes, content_type: str) -> None: ...

    def signed_url(self, path: str, expires_s: int) -> str: ...


# ──────────────────────────────────────────────────────────── pure rules ──

def image_path(product_id: UUID, sha256: str, content_type: str) -> str:
    """`products/{product_id}/{sha256}.{jpg|png|webp}` — the bytes name their own object."""
    if not _SHA256.match(sha256):
        raise ValueError("sha256 must be 64 lowercase hex characters")
    if content_type not in IMAGE_EXTENSIONS:
        raise ValueError("unsupported image content type")
    return f"products/{UUID(str(product_id))}/{sha256}.{IMAGE_EXTENSIONS[content_type]}"


def sniff_image_type(data: bytes) -> str | None:
    """The image type the bytes themselves say they are, from their magic bytes, or None."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def check_image(data: bytes, declared_content_type: str | None) -> str:
    """The normalised content type of an acceptable image, or `ImageRefused`.

    Too large is 413 `image_too_large`. Anything else wrong — a type outside JPEG/PNG/WebP, no
    declared type, or bytes that are not what was declared (a PNG sent as a JPEG) — is 415
    `unsupported_image`. Both the declaration and the bytes must agree, so neither a mislabelled
    file nor a browser's guess decides what is stored.
    """
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageRefused(413, "image_too_large", "the image is larger than 8 MiB")
    declared = (declared_content_type or "").split(";", 1)[0].strip().lower()
    if declared not in IMAGE_EXTENSIONS or sniff_image_type(data) != declared:
        raise ImageRefused(415, "unsupported_image", "only JPEG, PNG or WebP images whose content matches their type")
    return declared


def _check_expiry(expires_s: int) -> None:
    if not 0 < expires_s <= SIGNED_URL_SECONDS:
        raise ValueError(f"a signed URL lives between 1 and {SIGNED_URL_SECONDS} seconds")


# ──────────────────────────────────────────────────────────── Supabase Storage ──

def _safe_base_url(raw: str) -> str:
    """`https://<ref>.supabase.co`, or plain http only to a loopback IP literal (local Supabase).

    The key travels in a header on every request, so a plain-http remote base would send it in
    clear; credentials in the URL would end up in logs.
    """
    base = (raw or "").strip().rstrip("/")
    parts = urlsplit(base)
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ValueError("the Storage URL must be a bare origin")
    if parts.scheme == "https" and parts.hostname:
        return base
    if parts.scheme == "http" and parts.hostname:
        try:
            if ip_address(parts.hostname).is_loopback:
                return base
        except ValueError:
            pass
    raise ValueError("the Storage URL must be https (plain http only to a loopback IP literal)")


class SupabaseStorage:
    """The Storage API of one Supabase project, as its dedicated secret key, for the `catalog` bucket."""

    def __init__(self, base_url: str, secret_key: str, client: httpx.Client) -> None:
        if not secret_key or not secret_key.startswith(_SECRET_KEY_PREFIX):
            # ARCHITECTURE §7: a current `sb_secret_…` key; legacy JWT service_role keys are not used.
            raise ValueError("the Storage key must be a Supabase secret key (sb_secret_…)")
        self._base = _safe_base_url(base_url)
        self._key = secret_key
        self._client = client

    def __repr__(self) -> str:
        return f"SupabaseStorage(base_url={self._base!r}, bucket={BUCKET!r})"

    __str__ = __repr__

    def _headers(self, **extra: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}", "apikey": self._key, **extra}

    def _url(self, kind: str, path: str) -> str:
        return f"{self._base}/storage/v1/object/{kind}{BUCKET}/{quote(path, safe='/')}"

    def _post(self, what: str, url: str, **kwargs: object) -> httpx.Response:
        try:
            return self._client.post(url, **kwargs)  # type: ignore[arg-type]
        except httpx.HTTPError as exc:
            # The exception names the URL at most; the key is only ever in a header.
            logger.warning("catalog storage %s failed: %s", what, type(exc).__name__)
            raise StorageUnavailable(f"storage {what} failed: {type(exc).__name__}") from None

    @staticmethod
    def _is_duplicate(response: httpx.Response) -> bool:
        """409, or the older Storage API's HTTP 400 carrying `statusCode: "409"` / `Duplicate`."""
        if response.status_code == 409:
            return True
        if response.status_code != 400:
            return False
        try:
            body = response.json()
        except ValueError:
            return False
        return isinstance(body, dict) and (str(body.get("statusCode")) == "409" or body.get("error") == "Duplicate")

    def put_if_absent(self, path: str, data: bytes, content_type: str) -> None:
        response = self._post("upload", self._url("", path), content=data,
                              headers=self._headers(**{"Content-Type": content_type, "x-upsert": "false"}))
        if response.is_success or self._is_duplicate(response):
            return  # stored now, or stored before: the path is the content, so either is the same object
        logger.warning("catalog storage upload answered HTTP %s", response.status_code)
        raise StorageUnavailable(f"storage upload answered HTTP {response.status_code}")

    def signed_url(self, path: str, expires_s: int) -> str:
        _check_expiry(expires_s)
        response = self._post("signing", self._url("sign/", path), json={"expiresIn": expires_s},
                              headers=self._headers())
        if not response.is_success:
            logger.warning("catalog storage signing answered HTTP %s", response.status_code)
            raise StorageUnavailable(f"storage signing answered HTTP {response.status_code}")
        try:
            signed = response.json().get("signedURL")
        except (ValueError, AttributeError):
            signed = None
        # Storage answers a path under /storage/v1; anything else is not a URL of this project.
        if not isinstance(signed, str) or not signed.startswith("/"):
            raise StorageUnavailable("storage signing returned no signed URL")
        return f"{self._base}/storage/v1{signed}"


# ──────────────────────────────────────────────────────────── tests ──

class FakeStorage:
    """A dict-backed bucket: what was put, how many puts were asked for, and fake signed URLs."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.puts = 0

    def put_if_absent(self, path: str, data: bytes, content_type: str) -> None:
        self.puts += 1
        self.objects.setdefault(path, (data, content_type))

    def signed_url(self, path: str, expires_s: int) -> str:
        _check_expiry(expires_s)
        return f"https://storage.invalid/storage/v1/object/sign/{BUCKET}/{path}?token=fake&expires_in={expires_s}"

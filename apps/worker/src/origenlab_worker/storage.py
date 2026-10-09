"""The `.eml` store: Supabase Storage's S3 protocol, bucket `mail`, one object per Gmail message.

The credential is a Storage S3 access key (spec §3, §5) — never the project's secret API key and
never a database credential. Supabase does not scope an S3 key to one bucket, so the restriction is
a rule of use: this module writes only under `mail/`, and only new objects. An object already
present under a key is never replaced: same size → `present` (a re-run), different size →
`StorageConflict` (the run stops; evidence is never overwritten).

"Never replaced" is HEAD-then-PUT, not an atomic conditional write (Supabase's support for
`If-None-Match` is unverified, and a 501 would break every write). Two concurrent writers are
excluded by the run's advisory lock (Task 6), not by this store.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from origenlab_worker.errors import ConfigRefused

BUCKET = "mail"
CONTENT_TYPE = "message/rfc822"
_GMAIL_ID = re.compile(r"^[0-9A-Za-z_-]{1,64}$")
_MAILBOX = re.compile(r"^[^/@\s]+@[^/@\s]+$")
_REGION = re.compile(r"^[a-z]{2}-[a-z]+-[0-9]{1,2}$")
S3_PATH = "/storage/v1/s3"

ENV_ENDPOINT = "ORIGENLAB_WORKER_STORAGE_S3_ENDPOINT"
ENV_REGION = "ORIGENLAB_WORKER_STORAGE_S3_REGION"
ENV_ACCESS_KEY_ID = "ORIGENLAB_WORKER_STORAGE_S3_ACCESS_KEY_ID"
ENV_SECRET = "ORIGENLAB_WORKER_STORAGE_S3_SECRET_ACCESS_KEY"


class StorageError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class StorageConflict(StorageError):
    pass


class StorageTooLarge(StorageError):
    pass


def eml_key(mailbox_address: str, internal_date_ms: int, gmail_message_id: str) -> str:
    """`contacto@origenlab.cl/<yyyy>/<mm>/<gmail_message_id>.eml` (spec §3), month in UTC."""
    if not _GMAIL_ID.fullmatch(gmail_message_id):
        raise ValueError("a Gmail message id is letters, digits, '-' or '_'")
    if not _MAILBOX.fullmatch(mailbox_address) or ".." in mailbox_address:
        raise ValueError("a mailbox address is a plain address")
    try:
        at = datetime.fromtimestamp(internal_date_ms / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise ValueError("internal date out of range") from None
    return f"{mailbox_address}/{at:%Y}/{at:%m}/{gmail_message_id}.eml"


def client_config() -> Any:
    from botocore.config import Config

    # Path-style (the bucket in the path, as Supabase's endpoint expects); checksums only when the
    # operation requires them (botocore >= 1.36 adds CRC32 trailers by default, which S3-compatible
    # services may refuse); bounded retries and timeouts so a stuck upload fails the run.
    return Config(
        s3={"addressing_style": "path"},
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
        retries={"max_attempts": 3, "mode": "standard"},
        connect_timeout=10,
        read_timeout=120,
    )


@dataclass(frozen=True)
class StorageConfig:
    endpoint: str
    region: str
    access_key_id: str
    secret_access_key: str = field(repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> StorageConfig:
        endpoint = (env.get(ENV_ENDPOINT) or "").strip().rstrip("/")
        if not endpoint:
            raise ConfigRefused("storage_endpoint_missing")
        parts = urlsplit(endpoint)
        if parts.scheme != "https":
            raise ConfigRefused("storage_endpoint_not_https")
        if not (parts.hostname or "").endswith(".supabase.co") or parts.path != S3_PATH:
            raise ConfigRefused("storage_endpoint_not_supabase_s3")
        if parts.query or parts.fragment or parts.username or parts.password:
            raise ConfigRefused("storage_endpoint_has_extras")
        region = (env.get(ENV_REGION) or "").strip()
        if not region:
            raise ConfigRefused("storage_region_missing")
        if not _REGION.match(region):
            raise ConfigRefused("storage_region_invalid")
        key_id = (env.get(ENV_ACCESS_KEY_ID) or "").strip()
        secret = (env.get(ENV_SECRET) or "").strip()
        if not key_id:
            raise ConfigRefused("storage_access_key_id_missing")
        if not secret:
            raise ConfigRefused("storage_secret_access_key_missing")
        return cls(endpoint=endpoint, region=region, access_key_id=key_id, secret_access_key=secret)

    def client(self) -> Any:
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            region_name=self.region,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            config=client_config(),
        )


def _error_code(exc: Any) -> str:
    return str((getattr(exc, "response", None) or {}).get("Error", {}).get("Code") or "")


def _status(exc: Any) -> int:
    return int((getattr(exc, "response", None) or {}).get("ResponseMetadata", {}).get("HTTPStatusCode") or 0)


class S3EmlStore:
    def __init__(self, client: Any, bucket: str = BUCKET) -> None:
        self._client = client
        self._bucket = bucket

    def check(self) -> None:
        """The bucket exists and the key may list it — the `--init` pre-flight."""
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self._client.list_objects_v2(Bucket=self._bucket, MaxKeys=1)
        except ClientError as exc:
            raise StorageError(f"storage_check_http_{_status(exc)}") from None
        except BotoCoreError as exc:
            raise StorageError(type(exc).__name__) from None

    def _stored_size(self, key: str) -> int | None:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            head = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if _status(exc) == 404:
                return None
            raise StorageError(f"storage_head_http_{_status(exc)}") from None
        except BotoCoreError as exc:
            raise StorageError(type(exc).__name__) from None
        return int(head["ContentLength"])

    def _same_bytes(self, key: str, raw: bytes) -> bool:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            body = self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        except ClientError as exc:
            raise StorageError(f"storage_get_http_{_status(exc)}") from None
        except BotoCoreError as exc:
            raise StorageError(type(exc).__name__) from None
        return body == raw

    def get(self, key: str) -> bytes:
        """The stored `.eml` (the Drive filing reads one attachment out of it)."""
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            return self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        except ClientError as exc:
            raise StorageError(f"storage_get_http_{_status(exc)}") from None
        except BotoCoreError as exc:
            raise StorageError(type(exc).__name__) from None

    def put_if_absent(self, key: str, raw: bytes) -> str:
        """`stored`, or `present` when an object with exactly these bytes is already there.

        A Gmail message's raw bytes never change, so an identical object under its own id is the
        upload of an earlier, interrupted run. A different size is an immediate conflict; an equal
        size is read back and compared (rare: a retry after a failed commit). This is HEAD then
        PUT, not an atomic conditional write: a concurrent writer is excluded by the run's
        advisory lock (Task 6), not by the store.
        """
        from botocore.exceptions import BotoCoreError, ClientError

        size = self._stored_size(key)
        if size is not None:
            if size != len(raw) or not self._same_bytes(key, raw):
                raise StorageConflict("stored_object_differs")
            return "present"
        try:
            self._client.put_object(Bucket=self._bucket, Key=key, Body=raw, ContentType=CONTENT_TYPE)
        except ClientError as exc:
            if _status(exc) == 413 or _error_code(exc) == "EntityTooLarge":
                raise StorageTooLarge("storage_object_too_large") from None
            raise StorageError(f"storage_put_http_{_status(exc)}") from None
        except BotoCoreError as exc:
            raise StorageError(type(exc).__name__) from None
        return "stored"

"""The `.eml` store against an in-process S3 server (moto), and its environment rules."""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from mailfixtures import INTERNAL_MS, MAILBOX
from origenlab_worker.errors import ConfigRefused
from origenlab_worker.storage import (
    BUCKET,
    S3EmlStore,
    StorageConfig,
    StorageConflict,
    StorageError,
    StorageTooLarge,
    eml_key,
)

ENDPOINT = "https://abcdefghijklmnopqrst.storage.supabase.co/storage/v1/s3"
GOOD_ENV = {
    "ORIGENLAB_WORKER_STORAGE_S3_ENDPOINT": ENDPOINT,
    "ORIGENLAB_WORKER_STORAGE_S3_REGION": "sa-east-1",
    "ORIGENLAB_WORKER_STORAGE_S3_ACCESS_KEY_ID": "kid",
    "ORIGENLAB_WORKER_STORAGE_S3_SECRET_ACCESS_KEY": "ksec",
}


def test_the_key_is_mailbox_year_month_and_gmail_id() -> None:
    assert eml_key(MAILBOX, INTERNAL_MS, "18c2f5e7d3a4b1c0") == "contacto@origenlab.cl/2026/10/18c2f5e7d3a4b1c0.eml"


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "x" * 65])
def test_a_gmail_id_that_could_leave_its_folder_is_refused(bad) -> None:
    with pytest.raises(ValueError):
        eml_key(MAILBOX, INTERNAL_MS, bad)


def test_an_eml_is_stored_once_and_a_rerun_finds_it_present(s3_client) -> None:
    store = S3EmlStore(s3_client)
    key = eml_key(MAILBOX, INTERNAL_MS, "18c2f5e7d3a4b1c1")
    assert store.put_if_absent(key, b"raw-1") == "stored"
    assert store.put_if_absent(key, b"raw-1") == "present"
    obj = s3_client.get_object(Bucket=BUCKET, Key=key)
    assert obj["Body"].read() == b"raw-1" and obj["ContentType"] == "message/rfc822"


def test_a_different_object_under_the_same_key_is_never_replaced(s3_client) -> None:
    store = S3EmlStore(s3_client)
    key = eml_key(MAILBOX, INTERNAL_MS, "18c2f5e7d3a4b1c2")
    store.put_if_absent(key, b"raw-2")
    with pytest.raises(StorageConflict):
        store.put_if_absent(key, b"raw-2-but-longer")
    assert s3_client.get_object(Bucket=BUCKET, Key=key)["Body"].read() == b"raw-2"


def test_the_bucket_check_passes_and_a_missing_bucket_fails(s3_client) -> None:
    S3EmlStore(s3_client).check()
    with pytest.raises(StorageError) as exc:
        S3EmlStore(s3_client, bucket="no-such-bucket").check()
    assert exc.value.code == "storage_check_http_404"


class _Refusing:
    def __init__(self, status: int) -> None:
        self.status = status

    def head_object(self, **_kw):
        raise ClientError({"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "HeadObject")

    def put_object(self, **_kw):
        raise ClientError({"Error": {"Code": "x"}, "ResponseMetadata": {"HTTPStatusCode": self.status}}, "PutObject")


def test_an_object_storage_refuses_as_too_large_is_too_large() -> None:
    with pytest.raises(StorageTooLarge):
        S3EmlStore(_Refusing(413)).put_if_absent("k", b"x")


def test_any_other_refusal_is_a_storage_error_named_by_status_only() -> None:
    with pytest.raises(StorageError) as exc:
        S3EmlStore(_Refusing(403)).put_if_absent("k", b"x")
    assert exc.value.code == "storage_put_http_403"


def test_the_environment_names_a_supabase_s3_endpoint() -> None:
    config = StorageConfig.from_env(GOOD_ENV)
    assert (config.endpoint, config.region) == (ENDPOINT, "sa-east-1")


@pytest.mark.parametrize("override,code", [
    ({"ORIGENLAB_WORKER_STORAGE_S3_ENDPOINT": ENDPOINT.replace("https", "http")}, "storage_endpoint_not_https"),
    ({"ORIGENLAB_WORKER_STORAGE_S3_ENDPOINT": "https://s3.example.invalid/storage/v1/s3"}, "storage_endpoint_not_supabase_s3"),
    ({"ORIGENLAB_WORKER_STORAGE_S3_ENDPOINT": ENDPOINT.replace("/s3", "/object")}, "storage_endpoint_not_supabase_s3"),
    ({"ORIGENLAB_WORKER_STORAGE_S3_ENDPOINT": ENDPOINT + "?x=1"}, "storage_endpoint_has_extras"),
    ({"ORIGENLAB_WORKER_STORAGE_S3_REGION": "sa east"}, "storage_region_invalid"),
    ({"ORIGENLAB_WORKER_STORAGE_S3_SECRET_ACCESS_KEY": ""}, "storage_key_missing"),
])
def test_an_unsafe_storage_environment_is_refused_by_code(override, code) -> None:
    with pytest.raises(ConfigRefused) as exc:
        StorageConfig.from_env({**GOOD_ENV, **override})
    assert exc.value.code == code

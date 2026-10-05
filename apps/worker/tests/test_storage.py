"""The `.eml` store against an in-process S3 server (moto), and its environment rules."""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from mailfixtures import INTERNAL_MS, MAILBOX
from origenlab_worker.errors import ConfigRefused
from origenlab_worker.storage import (
    BUCKET,
    S3EmlStore,
    StorageConfig,
    StorageConflict,
    StorageError,
    StorageTooLarge,
    client_config,
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


def _client_error(status: int, code: str, op: str) -> ClientError:
    return ClientError({"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, op)


class _Refusing:
    """A client whose HEAD finds nothing (or fails) and whose PUT fails as told."""

    def __init__(self, put=(500, "x"), head=(404, "404")) -> None:
        self.put, self.head = put, head

    def head_object(self, **_kw):
        raise _client_error(self.head[0], self.head[1], "HeadObject")

    def put_object(self, **_kw):
        raise _client_error(self.put[0], self.put[1], "PutObject")


class _Unreachable:
    def head_object(self, **_kw):
        raise EndpointConnectionError(endpoint_url="https://unreachable.invalid")


def test_an_object_storage_refuses_as_too_large_is_too_large() -> None:
    with pytest.raises(StorageTooLarge):
        S3EmlStore(_Refusing(put=(413, "x"))).put_if_absent("k", b"x")


def test_entity_too_large_on_any_status_is_too_large() -> None:
    with pytest.raises(StorageTooLarge):
        S3EmlStore(_Refusing(put=(400, "EntityTooLarge"))).put_if_absent("k", b"x")


def test_any_other_refusal_is_a_storage_error_named_by_status_only() -> None:
    with pytest.raises(StorageError) as exc:
        S3EmlStore(_Refusing(put=(403, "x"))).put_if_absent("k", b"x")
    assert exc.value.code == "storage_put_http_403"


def test_a_head_failure_other_than_404_is_named_by_status() -> None:
    with pytest.raises(StorageError) as exc:
        S3EmlStore(_Refusing(head=(403, "403"))).put_if_absent("k", b"x")
    assert exc.value.code == "storage_head_http_403"


def test_a_transport_failure_is_a_typed_code_without_the_exception_text() -> None:
    with pytest.raises(StorageError) as exc:
        S3EmlStore(_Unreachable()).put_if_absent("k", b"x")
    assert exc.value.code == "EndpointConnectionError" and "unreachable" not in str(exc.value)


def test_the_environment_names_a_supabase_s3_endpoint() -> None:
    config = StorageConfig.from_env(GOOD_ENV)
    assert (config.endpoint, config.region) == (ENDPOINT, "sa-east-1")


@pytest.mark.parametrize("override,code", [
    ({"ORIGENLAB_WORKER_STORAGE_S3_ENDPOINT": ENDPOINT.replace("https", "http")}, "storage_endpoint_not_https"),
    ({"ORIGENLAB_WORKER_STORAGE_S3_ENDPOINT": "https://s3.example.invalid/storage/v1/s3"}, "storage_endpoint_not_supabase_s3"),
    ({"ORIGENLAB_WORKER_STORAGE_S3_ENDPOINT": ENDPOINT.replace("/s3", "/object")}, "storage_endpoint_not_supabase_s3"),
    ({"ORIGENLAB_WORKER_STORAGE_S3_ENDPOINT": ENDPOINT + "?x=1"}, "storage_endpoint_has_extras"),
    ({"ORIGENLAB_WORKER_STORAGE_S3_REGION": "sa east"}, "storage_region_invalid"),
    ({"ORIGENLAB_WORKER_STORAGE_S3_ENDPOINT": ""}, "storage_endpoint_missing"),
    ({"ORIGENLAB_WORKER_STORAGE_S3_REGION": ""}, "storage_region_missing"),
    ({"ORIGENLAB_WORKER_STORAGE_S3_ACCESS_KEY_ID": ""}, "storage_access_key_id_missing"),
    ({"ORIGENLAB_WORKER_STORAGE_S3_SECRET_ACCESS_KEY": ""}, "storage_secret_access_key_missing"),
])
def test_an_unsafe_storage_environment_is_refused_by_code(override, code) -> None:
    with pytest.raises(ConfigRefused) as exc:
        StorageConfig.from_env({**GOOD_ENV, **override})
    assert exc.value.code == code


def test_same_size_different_bytes_is_a_conflict_and_same_bytes_is_present(s3_client) -> None:
    store = S3EmlStore(s3_client)
    key = eml_key(MAILBOX, INTERNAL_MS, "18c2f5e7d3a4b1c3")
    assert store.put_if_absent(key, b"aaaa") == "stored"
    with pytest.raises(StorageConflict):
        store.put_if_absent(key, b"bbbb")
    assert store.put_if_absent(key, b"aaaa") == "present"
    assert s3_client.get_object(Bucket=BUCKET, Key=key)["Body"].read() == b"aaaa"


def test_a_failing_read_back_of_an_equal_sized_object_is_typed() -> None:
    class _Head:
        def head_object(self, **_kw):
            return {"ContentLength": 1}

        def get_object(self, **_kw):
            raise _client_error(500, "x", "GetObject")

    with pytest.raises(StorageError) as exc:
        S3EmlStore(_Head()).put_if_absent("k", b"x")
    assert exc.value.code == "storage_get_http_500"


def test_a_gmail_id_ending_in_a_newline_is_refused() -> None:
    with pytest.raises(ValueError):
        eml_key(MAILBOX, INTERNAL_MS, "abc\n")


@pytest.mark.parametrize("bad", ["", "a/b@x.invalid", "../x@x.invalid", "a..b", "no-at-sign", "a@b@c", "a@x.invalid\n"])
def test_a_mailbox_that_could_leave_its_folder_is_refused(bad) -> None:
    with pytest.raises(ValueError):
        eml_key(bad, INTERNAL_MS, "abc")


@pytest.mark.parametrize("ms", [10**20, -10**20])
def test_an_out_of_range_date_is_a_value_error(ms) -> None:
    with pytest.raises(ValueError):
        eml_key(MAILBOX, ms, "abc")


def test_the_secret_is_not_in_the_repr() -> None:
    config = StorageConfig.from_env(GOOD_ENV)
    assert "ksec" not in repr(config) and "ksec" not in str(config)


def test_the_client_is_path_style_with_checksums_only_when_required() -> None:
    config = client_config()
    assert config.s3["addressing_style"] == "path"
    assert config.request_checksum_calculation == "when_required"
    assert config.response_checksum_validation == "when_required"

"""Shared fixtures: an in-process S3 server (moto) standing in for Supabase Storage."""

from __future__ import annotations

import socket
import urllib.request

import pytest

from origenlab_worker.storage import BUCKET, client_config

#: moto accepts any credential; these short constants are not secrets.
FAKE_KEY_ID = "test"
FAKE_KEY = "test"
REGION = "sa-east-1"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def s3_client():
    import boto3
    from moto.server import ThreadedMotoServer

    port = _free_port()
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
    server.start()
    try:
        # moto keeps its state per process, not per server: start every module from an empty S3.
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/moto-api/reset", method="POST"))
        client = boto3.client(
            "s3", endpoint_url=f"http://127.0.0.1:{port}", region_name=REGION,
            aws_access_key_id=FAKE_KEY_ID, aws_secret_access_key=FAKE_KEY, config=client_config(),
        )
        client.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        yield client
    finally:
        server.stop()

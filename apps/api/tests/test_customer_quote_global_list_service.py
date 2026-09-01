"""Unit tests for CustomerQuoteReadService.list_all_quotes."""

from __future__ import annotations

import pytest

from origenlab_api.services.customer_quote_read_service import CustomerQuoteReadService


class _FakeRepository:
    def list_all(self, **kwargs: object) -> tuple[list[object], int]:
        self.calls = kwargs  # type: ignore[attr-defined]
        return [], 0


def _service() -> tuple[CustomerQuoteReadService, _FakeRepository]:
    service = CustomerQuoteReadService.__new__(CustomerQuoteReadService)
    fake = _FakeRepository()
    service._repository = fake  # type: ignore[attr-defined]
    return service, fake


def test_passes_through_limit_offset_and_filters() -> None:
    service, fake = _service()

    service.list_all_quotes(limit=50, offset=10, drive_status=["failed"], stage=["quoting"])

    assert fake.calls == {  # type: ignore[attr-defined]
        "limit": 50,
        "offset": 10,
        "drive_status": ["failed"],
        "stage": ["quoting"],
    }


def test_rejects_limit_out_of_range() -> None:
    service, _ = _service()

    with pytest.raises(ValueError):
        service.list_all_quotes(limit=0, offset=0)

    with pytest.raises(ValueError):
        service.list_all_quotes(limit=201, offset=0)


def test_rejects_negative_offset() -> None:
    service, _ = _service()

    with pytest.raises(ValueError):
        service.list_all_quotes(limit=100, offset=-1)

"""Customer-quote command service (CRM-Q1).

Orchestrates the honest two-phase flow the durable model requires: Postgres
and Google Drive cannot share one transaction, so the durable quote (and its
number) is committed first, then Drive provisioning runs through the provider
boundary and its outcome is persisted as workspace state
(``pending`` / ``ready`` / ``failed``). External failure never destroys the
quote, retries are idempotent (artifacts are found by internal quote identity
before anything is created), and a partially provisioned workspace stays
discoverable -- nothing in Drive is ever deleted as compensation.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Callable, Protocol
from uuid import uuid4

from origenlab_api.drive.errors import DriveProvisioningError
from origenlab_api.drive.factory import build_drive_workspace_provider
from origenlab_api.drive.protocol import QuoteDriveWorkspaceProvider
from origenlab_api.repositories.postgres.commercial_operations import (
    CommercialOperationConflictError,
    CommercialOperationNotFoundError,
)
from origenlab_api.repositories.postgres.customer_quotes import (
    CustomerQuoteBundle,
    CustomerQuoteDriveWorkspace,
    PostgresCustomerQuoteRepository,
)
from origenlab_api.services.commercial_operations_service import (
    _fingerprint,
    _idempotency_key,
    _required_text,
)
from origenlab_api.settings import Settings


_MAX_WORKSPACE_NAME_LENGTH = 120

# Characters replaced by "-" in Drive artifact names (path-like separators),
# then any remaining control characters collapse into spaces.
_SEPARATOR_RE = re.compile(r"[/\\]+")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_WHITESPACE_RE = re.compile(r"\s+")


def build_quote_workspace_name(quote_number: str, title: str) -> str:
    """Folder/sheet display name: quote number + safely sanitized title.

    When no usable title remains after sanitization the name is just the
    quote number -- a customer name is never fabricated.
    """

    cleaned = _SEPARATOR_RE.sub("-", title)
    cleaned = _CONTROL_RE.sub(" ", cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()

    if not cleaned:
        return quote_number

    name = f"{quote_number} — {cleaned}"

    return name[:_MAX_WORKSPACE_NAME_LENGTH].rstrip()


class CustomerQuoteCommandRepository(Protocol):
    def create_quote(self, **kwargs: object) -> CustomerQuoteBundle: ...

    def get_quote_bundle(self, *, quote_id: str) -> CustomerQuoteBundle | None: ...

    def begin_drive_provision_attempt(
        self, **kwargs: object
    ) -> CustomerQuoteDriveWorkspace: ...

    def complete_drive_provision(
        self, **kwargs: object
    ) -> CustomerQuoteDriveWorkspace: ...

    def fail_drive_provision(
        self, **kwargs: object
    ) -> CustomerQuoteDriveWorkspace: ...


class CustomerQuoteService:
    def __init__(
        self,
        settings: Settings,
        *,
        repository: CustomerQuoteCommandRepository | None = None,
        drive_provider_factory: (
            Callable[[Settings], QuoteDriveWorkspaceProvider] | None
        ) = None,
    ) -> None:
        self._settings = settings
        self._repository: CustomerQuoteCommandRepository = (
            repository or PostgresCustomerQuoteRepository(settings)
        )
        self._drive_provider_factory = (
            drive_provider_factory or build_drive_workspace_provider
        )

    def create_quote(
        self,
        *,
        sales_opportunity_id: str,
        operator: str,
        idempotency_key: str,
    ) -> CustomerQuoteBundle:
        normalized_sales_id = _required_text(
            sales_opportunity_id,
            field="sales_opportunity_id",
            max_length=128,
        )
        normalized_operator = _required_text(
            operator,
            field="operator",
            max_length=320,
        ).lower()
        normalized_key = _idempotency_key(idempotency_key)

        template_reference = (
            (self._settings.drive_quote_template_file_id or "").strip() or None
        )

        fingerprint = _fingerprint(
            "customer_quote_create",
            {
                "sales_opportunity_id": normalized_sales_id,
            },
        )

        bundle = self._repository.create_quote(
            quote_id=f"quote_{uuid4().hex}",
            sales_opportunity_id=normalized_sales_id,
            operator=normalized_operator,
            idempotency_key=normalized_key,
            request_fingerprint=fingerprint,
            numbering=self._settings.quote_numbering_config(),
            template_reference=template_reference,
        )

        if bundle.workspace.provisioning_status == "ready":
            return bundle

        return self._provision(
            bundle,
            operator=normalized_operator,
            expected_version=bundle.workspace.version,
            raise_conflict=False,
        )

    def retry_drive_provisioning(
        self,
        *,
        quote_id: str,
        operator: str,
        expected_version: int,
    ) -> CustomerQuoteBundle:
        normalized_quote_id = _required_text(
            quote_id,
            field="quote_id",
            max_length=128,
        )
        normalized_operator = _required_text(
            operator,
            field="operator",
            max_length=320,
        ).lower()

        if expected_version < 1:
            raise ValueError("expected_version must be >= 1")

        bundle = self._repository.get_quote_bundle(quote_id=normalized_quote_id)

        if bundle is None:
            raise CommercialOperationNotFoundError(
                f"Customer quote not found: {normalized_quote_id}"
            )

        return self._provision(
            bundle,
            operator=normalized_operator,
            expected_version=expected_version,
            raise_conflict=True,
        )

    def _provision(
        self,
        bundle: CustomerQuoteBundle,
        *,
        operator: str,
        expected_version: int,
        raise_conflict: bool,
    ) -> CustomerQuoteBundle:
        quote_id = bundle.quote.quote_id

        try:
            self._repository.begin_drive_provision_attempt(
                quote_id=quote_id,
                operator=operator,
                expected_version=expected_version,
            )
        except CommercialOperationConflictError:
            if raise_conflict:
                raise

            # A concurrent request already owns this provisioning attempt;
            # the quote itself was created, so report the latest state.
            refreshed = self._repository.get_quote_bundle(quote_id=quote_id)
            return refreshed if refreshed is not None else bundle

        def fail(
            category: str,
            *,
            folder_id: str | None = None,
            folder_web_url: str | None = None,
        ) -> CustomerQuoteBundle:
            workspace = self._repository.fail_drive_provision(
                quote_id=quote_id,
                operator=operator,
                failure_category=category,
                folder_id=folder_id,
                folder_web_url=folder_web_url,
            )
            return replace(bundle, workspace=workspace)

        try:
            provider = self._drive_provider_factory(self._settings)
        except DriveProvisioningError as exc:
            return fail(exc.category)

        name = build_quote_workspace_name(
            bundle.quote.quote_number,
            bundle.sales_opportunity_title,
        )

        # Find-before-create on both artifacts: the internal quote identity
        # stamped into Drive appProperties makes retries reuse prior
        # artifacts instead of creating duplicates.
        try:
            folder = provider.find_folder(quote_id)
            if folder is None:
                folder = provider.create_folder(quote_id, name=name)
        except DriveProvisioningError as exc:
            return fail(exc.category)

        try:
            sheet = provider.find_sheet(quote_id, folder_id=folder.file_id)
            if sheet is None:
                sheet = provider.copy_template_sheet(
                    quote_id,
                    folder_id=folder.file_id,
                    name=name,
                )
        except DriveProvisioningError as exc:
            return fail(
                exc.category,
                folder_id=folder.file_id,
                folder_web_url=folder.web_url,
            )

        # If persisting success fails (e.g. DB outage after Drive succeeded)
        # the exception propagates: the workspace stays pending with the
        # attempt recorded, and the next retry finds both artifacts and
        # completes without new Drive writes.
        workspace = self._repository.complete_drive_provision(
            quote_id=quote_id,
            operator=operator,
            folder_id=folder.file_id,
            folder_web_url=folder.web_url,
            sheet_file_id=sheet.file_id,
            sheet_web_url=sheet.web_url,
        )

        return replace(bundle, workspace=workspace)

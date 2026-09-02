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


def build_quote_workspace_name(number: str, title: str) -> str:
    """Drive artifact display name: an identifier + safely sanitized title.

    Called with the human ``quote_number`` for the workspace folder and
    with the separate ``document_number`` for the copied template document
    -- the two Drive artifacts are named from two distinct identifiers, never
    the same string. When no usable title remains after sanitization the
    name is just the identifier -- a customer name is never fabricated.
    """

    cleaned = _SEPARATOR_RE.sub("-", title)
    cleaned = _CONTROL_RE.sub(" ", cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()

    if not cleaned:
        return number

    name = f"{number} — {cleaned}"

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

    def submit_for_review(self, **kwargs: object) -> CustomerQuoteBundle: ...

    def request_adjustments(self, **kwargs: object) -> CustomerQuoteBundle: ...

    def approve(self, **kwargs: object) -> CustomerQuoteBundle: ...

    def confirm_send(self, **kwargs: object) -> CustomerQuoteBundle: ...

    def adopt_drive_folder(self, **kwargs: object) -> CustomerQuoteBundle: ...


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

    def submit_for_review(
        self, *, quote_id: str, operator: str, expected_version: int
    ) -> CustomerQuoteBundle:
        return self._transition(
            self._repository.submit_for_review,
            quote_id=quote_id,
            operator=operator,
            expected_version=expected_version,
        )

    def request_adjustments(
        self, *, quote_id: str, operator: str, expected_version: int
    ) -> CustomerQuoteBundle:
        return self._transition(
            self._repository.request_adjustments,
            quote_id=quote_id,
            operator=operator,
            expected_version=expected_version,
        )

    def approve(
        self, *, quote_id: str, operator: str, expected_version: int
    ) -> CustomerQuoteBundle:
        return self._transition(
            self._repository.approve,
            quote_id=quote_id,
            operator=operator,
            expected_version=expected_version,
        )

    def confirm_send(
        self, *, quote_id: str, operator: str, expected_version: int
    ) -> CustomerQuoteBundle:
        return self._transition(
            self._repository.confirm_send,
            quote_id=quote_id,
            operator=operator,
            expected_version=expected_version,
        )

    @staticmethod
    def _transition(
        repository_method: Callable[..., CustomerQuoteBundle],
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

        return repository_method(
            quote_id=normalized_quote_id,
            operator=normalized_operator,
            expected_version=expected_version,
        )

    def adopt_drive_folder(
        self,
        *,
        sales_opportunity_id: str,
        document_number: str,
        quote_number: str,
        folder_id: str,
        folder_web_url: str,
        operator: str,
        idempotency_key: str,
    ) -> CustomerQuoteBundle:
        normalized_sales_id = _required_text(
            sales_opportunity_id,
            field="sales_opportunity_id",
            max_length=128,
        )
        normalized_document_number = _required_text(
            document_number,
            field="document_number",
            max_length=32,
        )
        normalized_quote_number = _required_text(
            quote_number,
            field="quote_number",
            max_length=32,
        )
        normalized_folder_id = _required_text(
            folder_id,
            field="folder_id",
            max_length=256,
        )
        normalized_folder_web_url = _required_text(
            folder_web_url,
            field="folder_web_url",
            max_length=2048,
        )
        normalized_operator = _required_text(
            operator,
            field="operator",
            max_length=320,
        ).lower()
        normalized_key = _idempotency_key(idempotency_key)

        # document_number and quote_number are independent, operator-
        # confirmed inputs -- deliberately no derivation between them.
        fingerprint = _fingerprint(
            "customer_quote_adopt_drive",
            {
                "sales_opportunity_id": normalized_sales_id,
                "document_number": normalized_document_number,
                "quote_number": normalized_quote_number,
                "folder_id": normalized_folder_id,
            },
        )

        return self._repository.adopt_drive_folder(
            quote_id=f"quote_{uuid4().hex}",
            sales_opportunity_id=normalized_sales_id,
            document_number=normalized_document_number,
            quote_number=normalized_quote_number,
            folder_id=normalized_folder_id,
            folder_web_url=normalized_folder_web_url,
            operator=normalized_operator,
            idempotency_key=normalized_key,
            request_fingerprint=fingerprint,
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
            attempt = self._repository.begin_drive_provision_attempt(
                quote_id=quote_id,
                operator=operator,
                expected_version=expected_version,
            )
        except CommercialOperationConflictError:
            if raise_conflict:
                raise

            # A concurrent request already owns this provisioning attempt
            # (or it is actively leased by one still in flight); the quote
            # itself was created, so report the latest state.
            refreshed = self._repository.get_quote_bundle(quote_id=quote_id)
            return refreshed if refreshed is not None else bundle

        # The version begin_drive_provision_attempt returned is this
        # attempt's fencing token: complete/fail below compare-and-set
        # against this exact value, so a stale attempt (this thread's lease
        # already expired and was reclaimed by a newer attempt) can never
        # overwrite what the newer attempt is doing.
        attempt_token = attempt.version

        def _refreshed_or_bundle() -> CustomerQuoteBundle:
            refreshed = self._repository.get_quote_bundle(quote_id=quote_id)
            return refreshed if refreshed is not None else bundle

        def fail(
            category: str,
            *,
            folder_id: str | None = None,
            folder_web_url: str | None = None,
        ) -> CustomerQuoteBundle:
            try:
                workspace = self._repository.fail_drive_provision(
                    quote_id=quote_id,
                    operator=operator,
                    attempt_version=attempt_token,
                    failure_category=category,
                    folder_id=folder_id,
                    folder_web_url=folder_web_url,
                )
            except CommercialOperationConflictError:
                # This attempt's own token is stale (its lease already
                # expired and a newer attempt reclaimed it, or that newer
                # attempt already completed/failed it): a benign lost race,
                # never a downgrade of a newer/ready state -- report the
                # latest durable state instead of raising.
                return _refreshed_or_bundle()
            return replace(bundle, workspace=workspace)

        try:
            provider = self._drive_provider_factory(self._settings)
            # Fail closed before any mutation when the destination is
            # unusable or incompatible with the configured auth mode (e.g.
            # a service account pointed at a personal My Drive folder).
            provider.verify_destination()
        except DriveProvisioningError as exc:
            return fail(exc.category)

        # Both Drive artifacts are named from document_number (CRM-Q2
        # follow-up): real Pendientes folders are named "CN01191 —
        # Customer" style, never from the human-facing quote_number, which
        # stays visible in the CRM/drawer without controlling the Drive
        # folder stem.
        workspace_name = build_quote_workspace_name(
            bundle.quote.document_number,
            bundle.sales_opportunity_title,
        )

        # Find-before-create on the folder: the internal quote identity
        # stamped into Drive appProperties makes retries reuse prior
        # artifacts instead of creating duplicates.
        try:
            folder = provider.find_folder(quote_id)
            if folder is None:
                folder = provider.create_folder(quote_id, name=workspace_name)
        except DriveProvisioningError as exc:
            return fail(exc.category)

        if not self._settings.drive_quote_template_provisioning_enabled:
            # Template-document provisioning is an explicit, separately-
            # activated step (the master template is not yet finalized): the
            # workspace is folder_ready, not ready -- the durable quote and
            # CRM card must still land normally on this.
            try:
                workspace = self._repository.complete_drive_provision(
                    quote_id=quote_id,
                    operator=operator,
                    attempt_version=attempt_token,
                    folder_id=folder.file_id,
                    folder_web_url=folder.web_url,
                )
            except CommercialOperationConflictError:
                return _refreshed_or_bundle()

            return replace(bundle, workspace=workspace)

        try:
            sheet = provider.find_sheet(quote_id, folder_id=folder.file_id)
            if sheet is None:
                sheet = provider.copy_template_sheet(
                    quote_id,
                    folder_id=folder.file_id,
                    name=workspace_name,
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
        try:
            workspace = self._repository.complete_drive_provision(
                quote_id=quote_id,
                operator=operator,
                attempt_version=attempt_token,
                folder_id=folder.file_id,
                folder_web_url=folder.web_url,
                sheet_file_id=sheet.file_id,
                sheet_web_url=sheet.web_url,
            )
        except CommercialOperationConflictError:
            # Stale attempt token: a newer attempt already reclaimed (and
            # likely already completed, via the same find-before-create
            # lookup) this workspace. Drive itself is unaffected -- report
            # the latest durable state rather than raising.
            return _refreshed_or_bundle()

        return replace(bundle, workspace=workspace)

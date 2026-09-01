"""Durable commercial operations command routes (ARCH-3B4)."""

from __future__ import annotations

from typing import NoReturn

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Path as PathParam,
    Query,
    Request,
    status,
)

from origenlab_api.commercial_operator_identity import (
    require_commercial_operator,
)
from origenlab_api.repositories.postgres.commercial_operations import (
    CommercialOperationConflictError,
    CommercialOperationNotFoundError,
)
from origenlab_api.repositories.postgres.customer_quotes import (
    QuoteNumberingNotConfiguredError,
    QuoteNumberingPolicyMismatchError,
)
from origenlab_api.schemas.commercial_operations import (
    ActivityCreateCommand,
    ActivityListResponse,
    ActivityResponse,
    CommercialWorkQueueOpportunity,
    CommercialWorkQueueResponse,
    CommercialWorkQueueTask,
    OpportunityStateCommand,
    OpportunityStateReadResponse,
    OpportunityStateResponse,
    SalesOpportunitiesMeta,
    SalesOpportunitiesResponse,
    SalesOpportunityListItem,
    SalesOpportunityManualCreateCommand,
    SalesOpportunityPromoteCommand,
    SalesOpportunityReadResponse,
    SalesOpportunityResponse,
    SalesOpportunityStageCommand,
    TaskCreateCommand,
    TaskListResponse,
    TaskResponse,
    TaskTransitionCommand,
)
from origenlab_api.schemas.customer_quotes import (
    CustomerQuoteCreateCommand,
    CustomerQuoteDriveWorkspaceRetryCommand,
    CustomerQuoteGlobalItem,
    CustomerQuoteGlobalListMeta,
    CustomerQuoteGlobalListResponse,
    CustomerQuoteListMeta,
    CustomerQuoteListResponse,
    CustomerQuoteReadResponse,
    CustomerQuoteResponse,
)
from origenlab_api.services.commercial_operations_read_service import (
    CommercialOperationsReadService,
)
from origenlab_api.services.commercial_operations_service import (
    CommercialOperationsService,
)
from origenlab_api.services.customer_quote_read_service import (
    CustomerQuoteReadService,
)
from origenlab_api.services.customer_quote_service import (
    CustomerQuoteService,
)
from origenlab_api.settings import Settings, get_settings


router = APIRouter(
    prefix="/operations",
    tags=["commercial-operations"],
)


def get_commercial_operations_service(
    settings: Settings = Depends(get_settings),
) -> CommercialOperationsService:
    return CommercialOperationsService(settings)


def get_commercial_operations_read_service(
    settings: Settings = Depends(get_settings),
) -> CommercialOperationsReadService:
    return CommercialOperationsReadService(settings)


def get_customer_quote_service(
    settings: Settings = Depends(get_settings),
) -> CustomerQuoteService:
    return CustomerQuoteService(settings)


def get_customer_quote_read_service(
    settings: Settings = Depends(get_settings),
) -> CustomerQuoteReadService:
    return CustomerQuoteReadService(settings)


def _raise_command_error(exc: Exception) -> NoReturn:
    if isinstance(exc, CommercialOperationNotFoundError):
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    if isinstance(exc, CommercialOperationConflictError):
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    if isinstance(exc, ValueError):
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    raise exc


@router.get(
    "/work-queue",
    response_model=CommercialWorkQueueResponse,
)
def get_commercial_work_queue(
    limit: int = 100,
    service: CommercialOperationsReadService = Depends(
        get_commercial_operations_read_service
    ),
) -> CommercialWorkQueueResponse:
    try:
        (
            open_tasks,
            review_opportunities,
            quote_followups,
        ) = service.get_work_queue(limit=limit)
    except ValueError as exc:
        _raise_command_error(exc)

    return CommercialWorkQueueResponse(
        open_tasks=[
            CommercialWorkQueueTask(
                task=TaskResponse.model_validate(
                    item.task,
                    from_attributes=True,
                ),
                contact_display_email=(item.contact_display_email),
                account_display_domain=(item.account_display_domain),
                canonical_stage=item.canonical_stage,
                machine_review_status=(item.machine_review_status),
            )
            for item in open_tasks
        ],
        review_opportunities=[
            CommercialWorkQueueOpportunity(
                opportunity_id=item.opportunity_id,
                contact_display_email=(item.contact_display_email),
                account_display_domain=(item.account_display_domain),
                canonical_stage=item.canonical_stage,
                machine_review_status=(item.machine_review_status),
                confirmation_status=(item.confirmation_status),
                manual_stage=item.manual_stage,
                owner_key=item.owner_key,
                operator_state_version=(item.operator_state_version),
            )
            for item in review_opportunities
        ],
        quote_followups=[
            CommercialWorkQueueOpportunity(
                opportunity_id=item.opportunity_id,
                contact_display_email=(item.contact_display_email),
                account_display_domain=(item.account_display_domain),
                canonical_stage=item.canonical_stage,
                machine_review_status=(item.machine_review_status),
                confirmation_status=(item.confirmation_status),
                manual_stage=item.manual_stage,
                owner_key=item.owner_key,
                operator_state_version=(item.operator_state_version),
            )
            for item in quote_followups
        ],
    )


@router.post(
    "/sales-opportunities/promote",
    response_model=SalesOpportunityResponse,
    status_code=status.HTTP_201_CREATED,
)
def promote_sales_opportunity(
    command: SalesOpportunityPromoteCommand,
    request: Request,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    settings: Settings = Depends(get_settings),
    service: CommercialOperationsService = Depends(get_commercial_operations_service),
) -> SalesOpportunityResponse:
    operator = require_commercial_operator(
        request,
        settings,
    )

    try:
        result = service.promote_sales_opportunity(
            source_opportunity_id=(command.source_opportunity_id),
            title=command.title,
            owner_key=command.owner_key,
            operator=operator,
            idempotency_key=idempotency_key,
        )
    except (
        CommercialOperationNotFoundError,
        CommercialOperationConflictError,
        ValueError,
    ) as exc:
        _raise_command_error(exc)

    return SalesOpportunityResponse.model_validate(
        result,
        from_attributes=True,
    )


@router.post(
    "/sales-opportunities/manual",
    response_model=SalesOpportunityResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_manual_sales_opportunity(
    command: SalesOpportunityManualCreateCommand,
    request: Request,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    settings: Settings = Depends(get_settings),
    service: CommercialOperationsService = Depends(get_commercial_operations_service),
) -> SalesOpportunityResponse:
    operator = require_commercial_operator(request, settings)
    try:
        result = service.create_manual_sales_opportunity(
            title=command.title,
            owner_key=command.owner_key,
            organization_id=command.organization_id,
            organization_display_name=command.organization_display_name,
            contact_id=command.contact_id,
            contact_display_name=command.contact_display_name,
            contact_email=command.contact_email,
            operator=operator,
            idempotency_key=idempotency_key,
        )
    except (
        CommercialOperationNotFoundError,
        CommercialOperationConflictError,
        ValueError,
    ) as exc:
        _raise_command_error(exc)

    return SalesOpportunityResponse.model_validate(result, from_attributes=True)


@router.post(
    "/sales-opportunities/{sales_opportunity_id}/stage",
    response_model=SalesOpportunityResponse,
)
def transition_sales_opportunity_stage(
    command: SalesOpportunityStageCommand,
    request: Request,
    sales_opportunity_id: str = PathParam(
        min_length=1,
        max_length=128,
    ),
    settings: Settings = Depends(get_settings),
    service: CommercialOperationsService = Depends(get_commercial_operations_service),
) -> SalesOpportunityResponse:
    operator = require_commercial_operator(
        request,
        settings,
    )

    try:
        result = service.transition_sales_opportunity_stage(
            sales_opportunity_id=sales_opportunity_id,
            stage=command.stage,
            operator=operator,
            expected_version=command.expected_version,
        )
    except (
        CommercialOperationNotFoundError,
        CommercialOperationConflictError,
        ValueError,
    ) as exc:
        _raise_command_error(exc)

    return SalesOpportunityResponse.model_validate(
        result,
        from_attributes=True,
    )


@router.get(
    "/sales-opportunities",
    response_model=SalesOpportunitiesResponse,
)
def list_sales_opportunities(
    stage: list[str] | None = Query(None),
    owner_key: str | None = Query(None, min_length=1, max_length=320),
    source_opportunity_id: list[str] | None = Query(None),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service: CommercialOperationsReadService = Depends(
        get_commercial_operations_read_service
    ),
) -> SalesOpportunitiesResponse:
    try:
        items, total_count = service.list_sales_opportunities(
            stages=stage,
            owner_key=owner_key,
            source_opportunity_ids=source_opportunity_id,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        _raise_command_error(exc)

    return SalesOpportunitiesResponse(
        meta=SalesOpportunitiesMeta(
            count=len(items),
            total_count=total_count,
            limit=limit,
            offset=offset,
        ),
        items=[
            SalesOpportunityListItem.model_validate(item, from_attributes=True)
            for item in items
        ],
    )


@router.get(
    "/sales-opportunities/{sales_opportunity_id}",
    response_model=SalesOpportunityReadResponse,
)
def get_sales_opportunity(
    sales_opportunity_id: str = PathParam(
        min_length=1,
        max_length=128,
    ),
    service: CommercialOperationsReadService = Depends(
        get_commercial_operations_read_service
    ),
) -> SalesOpportunityReadResponse:
    try:
        result = service.get_sales_opportunity(sales_opportunity_id)
    except ValueError as exc:
        _raise_command_error(exc)

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=(f"Sales opportunity not found: {sales_opportunity_id}"),
        )

    return SalesOpportunityReadResponse(
        item=SalesOpportunityResponse.model_validate(
            result,
            from_attributes=True,
        )
    )


@router.get(
    "/sales-opportunities/{sales_opportunity_id}/activities",
    response_model=ActivityListResponse,
)
def list_sales_opportunity_activities(
    sales_opportunity_id: str = PathParam(
        min_length=1,
        max_length=128,
    ),
    service: CommercialOperationsReadService = Depends(
        get_commercial_operations_read_service
    ),
) -> ActivityListResponse:
    try:
        items = service.list_sales_opportunity_activities(
            sales_opportunity_id,
            limit=100,
        )
    except ValueError as exc:
        _raise_command_error(exc)

    return ActivityListResponse(
        items=[
            ActivityResponse.model_validate(item, from_attributes=True)
            for item in items
        ]
    )


@router.get(
    "/sales-opportunities/{sales_opportunity_id}/tasks",
    response_model=TaskListResponse,
)
def list_sales_opportunity_tasks(
    sales_opportunity_id: str = PathParam(
        min_length=1,
        max_length=128,
    ),
    service: CommercialOperationsReadService = Depends(
        get_commercial_operations_read_service
    ),
) -> TaskListResponse:
    try:
        items = service.list_sales_opportunity_tasks(
            sales_opportunity_id,
            limit=100,
        )
    except ValueError as exc:
        _raise_command_error(exc)

    return TaskListResponse(
        items=[
            TaskResponse.model_validate(item, from_attributes=True)
            for item in items
        ]
    )


@router.get(
    "/opportunities/{opportunity_id}/state",
    response_model=OpportunityStateReadResponse,
)
def get_opportunity_state(
    opportunity_id: str = PathParam(
        min_length=1,
        max_length=128,
    ),
    service: CommercialOperationsReadService = Depends(
        get_commercial_operations_read_service
    ),
) -> OpportunityStateReadResponse:
    try:
        state = service.get_operator_state(opportunity_id)
    except ValueError as exc:
        _raise_command_error(exc)

    return OpportunityStateReadResponse(
        state=(
            OpportunityStateResponse.model_validate(
                state,
                from_attributes=True,
            )
            if state is not None
            else None
        )
    )


@router.get(
    "/opportunities/{opportunity_id}/activities",
    response_model=ActivityListResponse,
)
def list_opportunity_activities(
    opportunity_id: str = PathParam(
        min_length=1,
        max_length=128,
    ),
    service: CommercialOperationsReadService = Depends(
        get_commercial_operations_read_service
    ),
) -> ActivityListResponse:
    try:
        items = service.list_activities(
            opportunity_id,
            limit=100,
        )
    except ValueError as exc:
        _raise_command_error(exc)

    return ActivityListResponse(
        items=[
            ActivityResponse.model_validate(item, from_attributes=True)
            for item in items
        ]
    )


@router.get(
    "/opportunities/{opportunity_id}/tasks",
    response_model=TaskListResponse,
)
def list_opportunity_tasks(
    opportunity_id: str = PathParam(
        min_length=1,
        max_length=128,
    ),
    service: CommercialOperationsReadService = Depends(
        get_commercial_operations_read_service
    ),
) -> TaskListResponse:
    try:
        items = service.list_tasks(
            opportunity_id,
            limit=100,
        )
    except ValueError as exc:
        _raise_command_error(exc)

    return TaskListResponse(
        items=[
            TaskResponse.model_validate(item, from_attributes=True)
            for item in items
        ]
    )


@router.post(
    "/opportunities/{opportunity_id}/state",
    response_model=OpportunityStateResponse,
)
def set_opportunity_state(
    command: OpportunityStateCommand,
    request: Request,
    opportunity_id: str = PathParam(
        min_length=1,
        max_length=128,
    ),
    settings: Settings = Depends(get_settings),
    service: CommercialOperationsService = Depends(get_commercial_operations_service),
) -> OpportunityStateResponse:
    operator = require_commercial_operator(
        request,
        settings,
    )

    try:
        return service.set_opportunity_state(
            opportunity_id=opportunity_id,
            confirmation_status=command.confirmation_status,
            manual_stage=command.manual_stage,
            owner_key=command.owner_key,
            operator=operator,
            expected_version=command.expected_version,
        )
    except (
        CommercialOperationNotFoundError,
        CommercialOperationConflictError,
        ValueError,
    ) as exc:
        _raise_command_error(exc)


@router.post(
    "/activities",
    response_model=ActivityResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_activity(
    command: ActivityCreateCommand,
    request: Request,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    settings: Settings = Depends(get_settings),
    service: CommercialOperationsService = Depends(get_commercial_operations_service),
) -> ActivityResponse:
    operator = require_commercial_operator(
        request,
        settings,
    )

    try:
        return service.create_activity(
            sales_opportunity_id=command.sales_opportunity_id,
            opportunity_id=command.opportunity_id,
            account_id=command.account_id,
            contact_id=command.contact_id,
            activity_type=command.activity_type,
            occurred_at=command.occurred_at,
            summary=command.summary,
            detail=command.detail,
            operator=operator,
            idempotency_key=idempotency_key,
        )
    except (
        CommercialOperationNotFoundError,
        CommercialOperationConflictError,
        ValueError,
    ) as exc:
        _raise_command_error(exc)


@router.post(
    "/tasks",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_task(
    command: TaskCreateCommand,
    request: Request,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    settings: Settings = Depends(get_settings),
    service: CommercialOperationsService = Depends(get_commercial_operations_service),
) -> TaskResponse:
    operator = require_commercial_operator(
        request,
        settings,
    )

    try:
        return service.create_task(
            sales_opportunity_id=command.sales_opportunity_id,
            opportunity_id=command.opportunity_id,
            account_id=command.account_id,
            contact_id=command.contact_id,
            title=command.title,
            priority=command.priority,
            due_at=command.due_at,
            owner_key=command.owner_key,
            operator=operator,
            idempotency_key=idempotency_key,
        )
    except (
        CommercialOperationNotFoundError,
        CommercialOperationConflictError,
        ValueError,
    ) as exc:
        _raise_command_error(exc)


@router.post(
    "/tasks/{task_id}/complete",
    response_model=TaskResponse,
)
def complete_task(
    command: TaskTransitionCommand,
    request: Request,
    task_id: str = PathParam(
        min_length=1,
        max_length=128,
    ),
    settings: Settings = Depends(get_settings),
    service: CommercialOperationsService = Depends(get_commercial_operations_service),
) -> TaskResponse:
    operator = require_commercial_operator(
        request,
        settings,
    )

    try:
        return service.complete_task(
            task_id=task_id,
            operator=operator,
            expected_version=command.expected_version,
        )
    except (
        CommercialOperationNotFoundError,
        CommercialOperationConflictError,
        ValueError,
    ) as exc:
        _raise_command_error(exc)


@router.post(
    "/tasks/{task_id}/cancel",
    response_model=TaskResponse,
)
def cancel_task(
    command: TaskTransitionCommand,
    request: Request,
    task_id: str = PathParam(
        min_length=1,
        max_length=128,
    ),
    settings: Settings = Depends(get_settings),
    service: CommercialOperationsService = Depends(get_commercial_operations_service),
) -> TaskResponse:
    operator = require_commercial_operator(
        request,
        settings,
    )

    try:
        return service.cancel_task(
            task_id=task_id,
            operator=operator,
            expected_version=command.expected_version,
        )
    except (
        CommercialOperationNotFoundError,
        CommercialOperationConflictError,
        ValueError,
    ) as exc:
        _raise_command_error(exc)


# ---------------------------------------------------------------------------
# CRM-Q1: durable customer quotes + Google Drive workspace provisioning.
# ---------------------------------------------------------------------------


@router.post(
    "/sales-opportunities/{sales_opportunity_id}/quotes",
    response_model=CustomerQuoteResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_customer_quote(
    command: CustomerQuoteCreateCommand,
    request: Request,
    sales_opportunity_id: str = PathParam(
        min_length=1,
        max_length=128,
    ),
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    settings: Settings = Depends(get_settings),
    service: CustomerQuoteService = Depends(get_customer_quote_service),
) -> CustomerQuoteResponse:
    del command  # Empty body: every quote field is server-controlled.

    operator = require_commercial_operator(
        request,
        settings,
    )

    try:
        bundle = service.create_quote(
            sales_opportunity_id=sales_opportunity_id,
            operator=operator,
            idempotency_key=idempotency_key,
        )
    except QuoteNumberingNotConfiguredError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "quote_numbering_not_configured: quote numbering has not "
                "been activated"
            ),
        ) from exc
    except QuoteNumberingPolicyMismatchError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "quote_numbering_policy_mismatch: configured quote "
                "numbering disagrees with the already-activated durable "
                "series policy"
            ),
        ) from exc
    except (
        CommercialOperationNotFoundError,
        CommercialOperationConflictError,
        ValueError,
    ) as exc:
        _raise_command_error(exc)

    return CustomerQuoteResponse.from_bundle(bundle)


@router.get(
    "/sales-opportunities/{sales_opportunity_id}/quotes",
    response_model=CustomerQuoteListResponse,
)
def list_customer_quotes(
    sales_opportunity_id: str = PathParam(
        min_length=1,
        max_length=128,
    ),
    limit: int = Query(100, ge=1, le=200),
    service: CustomerQuoteReadService = Depends(
        get_customer_quote_read_service
    ),
) -> CustomerQuoteListResponse:
    try:
        bundles = service.list_quotes_for_sales_opportunity(
            sales_opportunity_id,
            limit=limit,
        )
    except ValueError as exc:
        _raise_command_error(exc)

    items = [CustomerQuoteResponse.from_bundle(bundle) for bundle in bundles]

    return CustomerQuoteListResponse(
        meta=CustomerQuoteListMeta(count=len(items)),
        items=items,
    )


@router.get(
    "/customer-quotes",
    response_model=CustomerQuoteGlobalListResponse,
)
def list_customer_quotes_global(
    stage: list[str] | None = Query(None),
    drive_status: list[str] | None = Query(None),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service: CustomerQuoteReadService = Depends(get_customer_quote_read_service),
) -> CustomerQuoteGlobalListResponse:
    try:
        entries, total_count = service.list_all_quotes(
            limit=limit,
            offset=offset,
            drive_status=drive_status,
            stage=stage,
        )
    except ValueError as exc:
        _raise_command_error(exc)

    items = [CustomerQuoteGlobalItem.from_entry(entry) for entry in entries]

    return CustomerQuoteGlobalListResponse(
        meta=CustomerQuoteGlobalListMeta(
            count=len(items),
            total_count=total_count,
            limit=limit,
            offset=offset,
        ),
        items=items,
    )


@router.get(
    "/customer-quotes/{quote_id}",
    response_model=CustomerQuoteReadResponse,
)
def get_customer_quote(
    quote_id: str = PathParam(
        min_length=1,
        max_length=128,
    ),
    service: CustomerQuoteReadService = Depends(
        get_customer_quote_read_service
    ),
) -> CustomerQuoteReadResponse:
    try:
        bundle = service.get_quote(quote_id)
    except ValueError as exc:
        _raise_command_error(exc)

    if bundle is None:
        raise HTTPException(
            status_code=404,
            detail=(f"Customer quote not found: {quote_id}"),
        )

    return CustomerQuoteReadResponse(
        item=CustomerQuoteResponse.from_bundle(bundle),
    )


@router.post(
    "/customer-quotes/{quote_id}/drive-workspace",
    response_model=CustomerQuoteResponse,
)
def retry_customer_quote_drive_workspace(
    command: CustomerQuoteDriveWorkspaceRetryCommand,
    request: Request,
    quote_id: str = PathParam(
        min_length=1,
        max_length=128,
    ),
    settings: Settings = Depends(get_settings),
    service: CustomerQuoteService = Depends(get_customer_quote_service),
) -> CustomerQuoteResponse:
    operator = require_commercial_operator(
        request,
        settings,
    )

    try:
        bundle = service.retry_drive_provisioning(
            quote_id=quote_id,
            operator=operator,
            expected_version=command.expected_version,
        )
    except (
        CommercialOperationNotFoundError,
        CommercialOperationConflictError,
        ValueError,
    ) as exc:
        _raise_command_error(exc)

    return CustomerQuoteResponse.from_bundle(bundle)

"""Control-plane API application."""

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

import structlog
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from temporalio.client import WorkflowUpdateFailedError, WorkflowUpdateRPCTimeoutOrCancelledError

from agents_should_survive_failure.agent_discovery import AgentDiscoveryError, discovered_agents
from agents_should_survive_failure.agent_registry import (
    AgentManifestError,
    AgentRegistrationConflict,
    MissingDeclaredTool,
    parse_registration,
    register_agent,
)
from agents_should_survive_failure.auth import AuthenticatedPrincipal, resolve_api_key
from agents_should_survive_failure.dependencies import (
    DependencySet,
    RuntimeResources,
    check_dependencies,
    create_resources,
)
from agents_should_survive_failure.evaluation import (
    EvaluationRequestFingerprintConflict,
    EvaluationRunner,
)
from agents_should_survive_failure.fault_injection import (
    FaultAction,
    FaultInjectionDisabled,
    FaultInjector,
    FaultPoint,
)
from agents_should_survive_failure.observability import configure_logging, configure_tracing
from agents_should_survive_failure.persistence.models import (
    Agent,
    AgentStatus,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    AuditEvent,
    EvaluationResult,
    EvaluationRun,
    FaultInjectionPlan,
    ModelCall,
    PrincipalType,
    RunArtifact,
    RunBudget,
    RunCheckpoint,
    RunDelegation,
    RunStatus,
    ToolInvocation,
    Vendor,
    VendorStatus,
    WorkflowEvent,
    WorkflowRun,
)
from agents_should_survive_failure.persistence.repositories import (
    AuditEventRepository,
    VendorRepository,
    WorkflowRunRepository,
)
from agents_should_survive_failure.persistence.seed import seed_id
from agents_should_survive_failure.persistence.session import Database
from agents_should_survive_failure.settings import Settings, get_settings
from agents_should_survive_failure.workflow_starts import (
    RequestFingerprintConflict,
    WorkflowStartCoordinator,
    WorkflowStartUnavailable,
)
from agents_should_survive_failure.workflows.contracts import (
    ApprovalDecisionInput,
    ApprovalDecisionType,
    WorkflowStatus,
)
from agents_should_survive_failure.workflows.vendor_onboarding import VendorOnboardingWorkflow

REQUESTS = Counter(
    "agents_http_requests_total",
    "HTTP requests processed by the control-plane API.",
    ("method", "path", "status"),
)

TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.REJECTED}
)
EVENT_STREAM_BATCH_SIZE = 100
EVENT_STREAM_POLL_SECONDS = 0.5


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id", str(uuid4()))
        request.state.request_id = request_id
        content_length = request.headers.get("content-length")
        max_body_bytes = request.app.state.settings.max_request_body_bytes
        if content_length is not None and int(content_length) > max_body_bytes:
            response = JSONResponse(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                content=ApiErrorResponse(
                    code="payload_too_large",
                    message="request payload exceeds the configured limit",
                    request_id=request_id,
                ).model_dump(),
            )
            response.headers["x-request-id"] = request_id
            return response
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
            response.headers["x-request-id"] = request_id
            route = request.scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            REQUESTS.labels(request.method, route_path, str(response.status_code)).inc()
            return response
        finally:
            structlog.contextvars.clear_contextvars()


class HealthResponse(BaseModel):
    """Stable health response contract."""

    status: str


class DependencyHealth(BaseModel):
    status: str
    detail: str | None = None


class ReadinessResponse(BaseModel):
    status: str
    dependencies: dict[str, DependencyHealth]


class ApiFieldError(BaseModel):
    field: str
    message: str


class ApiErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str
    field_errors: list[ApiFieldError] | None = None


class RequestBodyLimitMiddleware:
    """Reject oversized bodies before FastAPI parses them, including chunked requests."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self._app = app
        self._max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        if scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            await self._app(scope, receive, send)
            return

        messages: list[Message] = []
        body_size = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.disconnect":
                break
            if message["type"] != "http.request":
                continue
            body_size += len(message.get("body", b""))
            if body_size > self._max_body_bytes:
                request_id = next(
                    (
                        value.decode("latin-1")
                        for key, value in scope["headers"]
                        if key == b"x-request-id"
                    ),
                    str(uuid4()),
                )
                response = JSONResponse(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    content=ApiErrorResponse(
                        code="payload_too_large",
                        message="request payload exceeds the configured limit",
                        request_id=request_id,
                    ).model_dump(),
                )
                response.headers["x-request-id"] = request_id
                await response(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        async def replay_receive() -> Message:
            if messages:
                return messages.pop(0)
            # A streaming endpoint may ask whether its client disconnected after the request body
            # has been consumed. Returning a synthetic disconnect here would prematurely end SSE.
            return {"type": "http.request", "body": b"", "more_body": False}

        await self._app(scope, replay_receive, send)


class StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class VendorCreateRequest(StrictRequestModel):
    external_reference: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._:-]+$")
    legal_name: str = Field(min_length=1, max_length=240)
    jurisdiction: str = Field(min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")
    contact_email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class VendorResponse(BaseModel):
    id: UUID
    status: VendorStatus
    risk_score: int | None


class StartOnboardingRequest(StrictRequestModel):
    idempotency_key: str = Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9._:-]+$")


class StartManagedAgentRequest(StrictRequestModel):
    idempotency_key: str = Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9._:-]+$")
    task: dict[str, object]
    version: str | None = Field(default=None, min_length=1, max_length=40)


class WorkflowRunResponse(BaseModel):
    id: UUID
    status: RunStatus
    temporal_workflow_id: str


class WorkflowRunDetailResponse(WorkflowRunResponse):
    workflow_type: str
    vendor_id: UUID | None
    input_summary: dict[str, object]
    result_summary: dict[str, object] | None


class WorkflowRunPage(BaseModel):
    items: list[WorkflowRunDetailResponse]
    limit: int
    offset: int


class AgentResponse(BaseModel):
    id: UUID
    name: str
    version: str
    workflow_type: str
    status: str
    package_name: str
    entry_point: str
    manifest: dict[str, object]
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    compatibility: str
    integrity_digest: str
    configuration: dict[str, object]


class AgentPage(BaseModel):
    items: list[AgentResponse]
    limit: int
    offset: int


class AgentRegistrationRequest(StrictRequestModel):
    manifest: dict[str, object]
    package_name: str = Field(min_length=1, max_length=120)
    entry_point: str = Field(min_length=1, max_length=240)


class CheckpointResponse(BaseModel):
    id: UUID
    workflow_run_id: UUID
    agent_id: UUID
    name: str
    schema_version: str
    digest_sha256: str
    size_bytes: int


class ArtifactResponse(BaseModel):
    id: UUID
    workflow_run_id: UUID
    agent_id: UUID
    parent_artifact_id: UUID | None
    name: str
    content_type: str
    digest_sha256: str
    size_bytes: int


class BudgetResponse(BaseModel):
    workflow_run_id: UUID
    limits: dict[str, int]
    consumed: dict[str, int]
    exhausted_at: datetime | None


class ApprovalResponse(BaseModel):
    id: UUID
    workflow_run_id: UUID
    request_key: str
    status: str
    summary: str
    version: int


class ApprovalPage(BaseModel):
    items: list[ApprovalResponse]
    limit: int
    offset: int


class ToolInvocationResponse(BaseModel):
    id: UUID
    workflow_run_id: UUID
    tool_definition_id: UUID | None
    requested_tool_name: str
    requested_tool_version: str
    status: str
    result_summary: dict[str, object] | None
    error_category: str | None


class ToolInvocationPage(BaseModel):
    items: list[ToolInvocationResponse]
    limit: int
    offset: int


class WorkflowEventEvidence(BaseModel):
    sequence: int
    event_type: str
    summary: str
    payload: dict[str, object]


class WorkflowEventResponse(WorkflowEventEvidence):
    id: UUID
    workflow_run_id: UUID


class WorkflowEventPage(BaseModel):
    items: list[WorkflowEventResponse]
    limit: int
    offset: int


class ModelCallEvidence(BaseModel):
    provider: str
    model: str
    correlation_id: str
    status: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    error_category: str | None
    explanation_summary: str | None


class ModelCallResponse(ModelCallEvidence):
    id: UUID
    workflow_run_id: UUID


class ModelCallPage(BaseModel):
    items: list[ModelCallResponse]
    limit: int
    offset: int


class WorkflowEvidenceResponse(BaseModel):
    workflow_run_id: UUID
    events: list[WorkflowEventEvidence]
    model_calls: list[ModelCallEvidence]


class EvaluationResultReport(BaseModel):
    case_slug: str
    case_version: str
    case_content_sha256: str
    workflow_run_id: UUID | None
    status: str
    score: float
    expected_outcome: dict[str, object]
    actual_outcome: dict[str, object]
    failure_category: str | None
    duration_ms: int | None
    metrics: dict[str, object]
    evidence_summary: dict[str, object]
    summary: str


class EvaluationReportResponse(BaseModel):
    evaluation_run_id: UUID
    suite_slug: str
    suite_version: str
    suite_schema_version: str
    dataset_sha256: str
    status: str
    configuration: dict[str, object]
    results: list[EvaluationResultReport]


class EvaluationRunResponse(BaseModel):
    id: UUID
    suite_slug: str
    suite_version: str
    suite_schema_version: str
    dataset_sha256: str
    status: str
    configuration: dict[str, object]


class EvaluationRunPage(BaseModel):
    items: list[EvaluationRunResponse]
    limit: int
    offset: int


class EvaluationExecuteRequest(StrictRequestModel):
    idempotency_key: str = Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9._:-]+$")


class FaultPlanCreateRequest(StrictRequestModel):
    fault_point: FaultPoint
    action: FaultAction
    scope_key: str = Field(min_length=1, max_length=240)
    trigger_count: int = Field(default=1, ge=1, le=10)
    delay_ms: int = Field(default=0, ge=0, le=60_000)
    safe_metadata: dict[str, object] = Field(default_factory=dict)


class FaultPlanResponse(BaseModel):
    id: UUID
    fault_point: str
    scope_key: str
    action: str
    trigger_count: int
    remaining_triggers: int
    delay_ms: int
    status: str
    safe_metadata: dict[str, object]


class FaultConsumptionResponse(BaseModel):
    consumed: bool
    fault_point: str
    scope_key: str
    action: str | None
    remaining_triggers: int | None


class ApprovalRequestBody(StrictRequestModel):
    approval_request_id: UUID
    expected_version: int = Field(ge=1)
    decision: ApprovalDecisionType
    rationale: str = Field(min_length=1, max_length=1_000)
    idempotency_key: str = Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9._:-]+$")


async def liveness() -> HealthResponse:
    """Report process liveness without checking external dependencies."""
    return HealthResponse(status="ok")


def get_dependencies(request: Request) -> DependencySet:
    resources: RuntimeResources = request.app.state.resources
    return resources.dependencies


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_database(request: Request) -> Database:
    resources: RuntimeResources = request.app.state.resources
    return Database(resources.engine)


async def audit_authorization_denial(
    request: Request,
    principal: AuthenticatedPrincipal,
    required_scopes: tuple[str, ...],
) -> None:
    """Persist authenticated scope denials without recording credentials or request bodies."""
    try:
        resources: RuntimeResources = request.app.state.resources
    except (AttributeError, KeyError):
        # Unit-level dependency tests do not start the application lifespan.
        return
    route = request.scope.get("route")
    route_path = getattr(route, "path", "unmatched")
    from agents_should_survive_failure.metrics import AUTHORIZATION_DENIALS

    AUTHORIZATION_DENIALS.labels(route_path).inc()
    database = Database(resources.engine)
    async with database.session() as session:
        await AuditEventRepository(session).append(
            AuditEvent(
                actor_id=principal.id,
                action="api.authorization.denied",
                resource_type="api_route",
                idempotency_key=f"api-authorization-denied:{uuid4()}",
                summary="Authenticated principal was denied by the API authorization policy.",
                evidence={
                    "route": route_path,
                    "required_scopes": list(required_scopes),
                    "principal_type": principal.principal_type.value,
                },
            )
        )


def authentication_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid API key",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_authenticated_principal(
    request: Request,
) -> AuthenticatedPrincipal:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise authentication_error()
    database = get_database(request)
    async with database.session() as session:
        principal = await resolve_api_key(session, token)
    if principal is None:
        raise authentication_error()
    return principal


def require_scopes(
    *scopes: str,
    allowed_principal_types: frozenset[PrincipalType] | None = None,
) -> Callable[..., Any]:
    async def dependency(
        request: Request,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_authenticated_principal)],
    ) -> AuthenticatedPrincipal:
        if not principal.allows(*scopes):
            await audit_authorization_denial(request, principal, scopes)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient scope")
        if (
            allowed_principal_types is not None
            and principal.principal_type not in allowed_principal_types
        ):
            await audit_authorization_denial(request, principal, scopes)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="principal type is not permitted for this operation",
            )
        return principal

    return dependency


async def readiness(
    dependencies: Annotated[DependencySet, Depends(get_dependencies)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> Response:
    checks = await check_dependencies(dependencies, settings.dependency_timeout_seconds)
    ready = all(check.status == "ok" for check in checks.values())
    payload = ReadinessResponse(
        status="ok" if ready else "unavailable",
        dependencies={
            name: DependencyHealth(status=check.status, detail=check.detail)
            for name, check in checks.items()
        },
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload.model_dump(),
    )


async def metrics(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> Response:
    if not settings.metrics_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="metrics not found")
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    field_errors = [
        ApiFieldError(field=".".join(str(part) for part in item["loc"]), message=item["msg"])
        for item in error.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=ApiErrorResponse(
            code="validation_error",
            message="request validation failed",
            request_id=_request_id(request),
            field_errors=field_errors,
        ).model_dump(),
    )


async def http_error(request: Request, error: HTTPException) -> JSONResponse:
    codes = {
        status.HTTP_404_NOT_FOUND: "not_found",
        status.HTTP_409_CONFLICT: "conflict",
        status.HTTP_401_UNAUTHORIZED: "authentication_required",
        status.HTTP_403_FORBIDDEN: "authorization_denied",
        status.HTTP_503_SERVICE_UNAVAILABLE: "dependency_unavailable",
    }
    return JSONResponse(
        status_code=error.status_code,
        content=ApiErrorResponse(
            code=codes.get(error.status_code, "request_failed"),
            message=str(error.detail),
            request_id=_request_id(request),
        ).model_dump(),
    )


async def database_integrity_error(request: Request, error: IntegrityError) -> JSONResponse:
    """Map database-enforced uniqueness and relational invariants to a safe conflict contract."""
    del error
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=ApiErrorResponse(
            code="conflict",
            message="request conflicts with existing state",
            request_id=_request_id(request),
        ).model_dump(),
    )


async def database_unavailable_error(request: Request, error: OperationalError) -> JSONResponse:
    """Avoid leaking database endpoint details when an expected dependency is unavailable."""
    del error
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=ApiErrorResponse(
            code="dependency_unavailable",
            message="a required dependency is unavailable",
            request_id=_request_id(request),
        ).model_dump(),
    )


async def create_vendor(
    payload: VendorCreateRequest,
    request: Request,
    database: Annotated[Database, Depends(get_database)],
) -> Any:
    try:
        async with database.session() as session:
            vendor = await VendorRepository(session).add(
                Vendor(
                    external_reference=payload.external_reference,
                    legal_name=payload.legal_name,
                    jurisdiction=payload.jurisdiction.upper(),
                    contact_email=payload.contact_email,
                    status=VendorStatus.SUBMITTED,
                )
            )
            return VendorResponse(id=vendor.id, status=vendor.status, risk_score=vendor.risk_score)
    except IntegrityError as error:
        return await database_integrity_error(request, error)
    except OperationalError as error:
        return await database_unavailable_error(request, error)


async def start_onboarding(
    vendor_id: UUID,
    payload: StartOnboardingRequest,
    request: Request,
    database: Annotated[Database, Depends(get_database)],
    principal: Annotated[AuthenticatedPrincipal, Depends(get_authenticated_principal)],
) -> WorkflowRunResponse:
    async with database.session() as session:
        vendors = VendorRepository(session)
        vendor = await vendors.get(vendor_id)
        if vendor is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="vendor not found")
    resources: RuntimeResources = request.app.state.resources
    coordinator = WorkflowStartCoordinator(database, resources.temporal_client)
    try:
        run = await coordinator.create_or_get(
            vendor_id=vendor_id,
            requested_by_id=principal.id,
            agent_id=seed_id("agent:vendor-onboarding:v1"),
            idempotency_key=payload.idempotency_key,
        )
        await coordinator.start(run.id)
    except RequestFingerprintConflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="idempotency key was reused for a different onboarding request",
        ) from None
    except WorkflowStartUnavailable as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="workflow start is pending recovery",
            headers={"Retry-After": "1"},
        ) from error
    return WorkflowRunResponse(
        id=run.id, status=run.status, temporal_workflow_id=run.temporal_workflow_id
    )


async def start_managed_agent(
    agent_slug: str,
    payload: StartManagedAgentRequest,
    request: Request,
    database: Annotated[Database, Depends(get_database)],
    principal: Annotated[AuthenticatedPrincipal, Depends(get_authenticated_principal)],
) -> WorkflowRunResponse:
    statement = select(Agent).where(
        Agent.name == agent_slug,
        Agent.workflow_type == "managed_agent",
        Agent.status == AgentStatus.ACTIVE,
    )
    if payload.version is not None:
        statement = statement.where(Agent.version == payload.version)
    else:
        # Explicit selection policy: latest installed enabled version wins until a caller pins one.
        statement = statement.order_by(Agent.created_at.desc(), Agent.id.desc())
    async with database.session() as session:
        agent = await session.scalar(statement)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="managed agent not found")
    resources: RuntimeResources = request.app.state.resources
    coordinator = WorkflowStartCoordinator(database, resources.temporal_client)
    try:
        run = await coordinator.create_or_get_managed_agent(
            requested_by_id=principal.id,
            agent_id=agent.id,
            task=payload.task,
            idempotency_key=payload.idempotency_key,
        )
        await coordinator.start(run.id)
    except RequestFingerprintConflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="idempotency key was reused for a different managed-agent request",
        ) from None
    except WorkflowStartUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="workflow start is pending recovery",
            headers={"Retry-After": "1"},
        ) from None
    return WorkflowRunResponse(
        id=run.id, status=run.status, temporal_workflow_id=run.temporal_workflow_id
    )


async def decide_onboarding(
    run_id: UUID,
    payload: ApprovalRequestBody,
    request: Request,
    database: Annotated[Database, Depends(get_database)],
    principal: Annotated[AuthenticatedPrincipal, Depends(get_authenticated_principal)],
) -> Response:
    async with database.session() as session:
        run = await WorkflowRunRepository(session).get(run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="workflow run not found"
            )
        approval = await session.scalar(
            select(ApprovalRequest).where(
                ApprovalRequest.id == payload.approval_request_id,
                ApprovalRequest.workflow_run_id == run_id,
            )
        )
        if approval is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="approval request not found"
            )
        existing = await session.scalar(
            select(ApprovalDecision).where(
                ApprovalDecision.approval_request_id == approval.id,
                ApprovalDecision.idempotency_key == payload.idempotency_key,
            )
        )
        decision_status = (
            ApprovalStatus.APPROVED
            if payload.decision is ApprovalDecisionType.APPROVED
            else ApprovalStatus.REJECTED
        )
        if existing is not None:
            if (
                existing.decided_by_id != principal.id
                or existing.decision is not decision_status
                or existing.rationale != payload.rationale
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="idempotency key was reused for a different approval decision",
                )
            return Response(status_code=status.HTTP_202_ACCEPTED)
        if (
            approval.status is not ApprovalStatus.PENDING
            or approval.version != payload.expected_version
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="approval request is no longer pending at the expected version",
            )
        if getattr(run, "workflow_type", "vendor_onboarding") == "managed_agent":
            session.add(
                ApprovalDecision(
                    approval_request_id=approval.id,
                    decided_by_id=principal.id,
                    decision=decision_status,
                    rationale=payload.rationale,
                    idempotency_key=payload.idempotency_key,
                )
            )
            approval.status = decision_status
            if run.status is RunStatus.WAITING:
                run.status = RunStatus.RUNNING
            await AuditEventRepository(session).append(
                AuditEvent(
                    workflow_run_id=run.id,
                    actor_id=principal.id,
                    action="managed_agent.approval.decision",
                    resource_type="approval_request",
                    resource_id=approval.id,
                    idempotency_key=(
                        f"{run.id}:managed-agent:approval-decision:{payload.idempotency_key}"
                    ),
                    summary="Authorized managed-agent approval decision recorded.",
                    evidence={"decision": decision_status.value},
                )
            )
            return Response(status_code=status.HTTP_202_ACCEPTED)
        temporal_workflow_id = run.temporal_workflow_id
    resources: RuntimeResources = request.app.state.resources
    handle = resources.temporal_client.get_workflow_handle(temporal_workflow_id)
    decision = ApprovalDecisionInput(
        approval_request_id=str(payload.approval_request_id),
        expected_version=payload.expected_version,
        decision=payload.decision,
        decided_by_id=str(principal.id),
        rationale=payload.rationale,
        idempotency_key=payload.idempotency_key,
    )
    try:
        await handle.execute_update(  # pyright: ignore[reportUnknownMemberType]
            "decide",
            decision,
            id=payload.idempotency_key,
        )
    except WorkflowUpdateFailedError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="approval decision is no longer valid for the workflow state",
        ) from None
    except WorkflowUpdateRPCTimeoutOrCancelledError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="approval decision outcome is unknown; retry with the same idempotency key",
            headers={"Retry-After": "1"},
        ) from None
    return Response(status_code=status.HTTP_202_ACCEPTED)


async def onboarding_status(run_id: UUID, request: Request) -> WorkflowStatus:
    resources: RuntimeResources = request.app.state.resources
    async with Database(resources.engine).session() as session:
        run = await WorkflowRunRepository(session).get(run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="workflow run not found"
            )
        temporal_workflow_id = run.temporal_workflow_id
    return await resources.temporal_client.get_workflow_handle(temporal_workflow_id).query(
        VendorOnboardingWorkflow.status
    )


def workflow_run_response(run: WorkflowRun) -> WorkflowRunDetailResponse:
    return WorkflowRunDetailResponse(
        id=run.id,
        status=run.status,
        temporal_workflow_id=run.temporal_workflow_id,
        workflow_type=run.workflow_type,
        vendor_id=run.vendor_id,
        input_summary=run.input_summary,
        result_summary=run.result_summary,
    )


def agent_response(agent: Agent) -> AgentResponse:
    configuration = agent.configuration
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        version=agent.version,
        workflow_type=agent.workflow_type,
        status=agent.status.value,
        package_name=getattr(agent, "package_name", "legacy-agent"),
        entry_point=getattr(agent, "entry_point", "legacy:unavailable"),
        manifest=getattr(agent, "manifest", {}),
        input_schema=getattr(agent, "input_schema", {}),
        output_schema=getattr(agent, "output_schema", {}),
        compatibility=getattr(agent, "compatibility", "legacy"),
        integrity_digest=getattr(
            agent, "integrity_digest", configuration.get("registration_sha256", "0" * 64)
        ),
        configuration=configuration,
    )


def workflow_event_response(event: WorkflowEvent) -> WorkflowEventResponse:
    return WorkflowEventResponse(
        id=event.id,
        workflow_run_id=event.workflow_run_id,
        sequence=event.sequence,
        event_type=event.event_type,
        summary=event.summary,
        payload=event.payload,
    )


def approval_response(approval: ApprovalRequest) -> ApprovalResponse:
    return ApprovalResponse(
        id=approval.id,
        workflow_run_id=approval.workflow_run_id,
        request_key=approval.request_key,
        status=approval.status.value,
        summary=approval.summary,
        version=approval.version,
    )


def model_call_response(call: ModelCall) -> ModelCallResponse:
    return ModelCallResponse(
        id=call.id,
        workflow_run_id=call.workflow_run_id,
        provider=call.provider,
        model=call.model,
        correlation_id=call.correlation_id,
        status=call.status.value,
        input_tokens=call.input_tokens,
        output_tokens=call.output_tokens,
        latency_ms=call.latency_ms,
        error_category=call.error_category,
        explanation_summary=call.decision_summary,
    )


def tool_invocation_response(call: ToolInvocation) -> ToolInvocationResponse:
    return ToolInvocationResponse(
        id=call.id,
        workflow_run_id=call.workflow_run_id,
        tool_definition_id=call.tool_definition_id,
        requested_tool_name=call.requested_tool_name,
        requested_tool_version=call.requested_tool_version,
        status=call.status.value,
        result_summary=call.result_summary,
        error_category=call.error_category,
    )


def checkpoint_response(checkpoint: RunCheckpoint) -> CheckpointResponse:
    return CheckpointResponse(
        id=checkpoint.id,
        workflow_run_id=checkpoint.workflow_run_id,
        agent_id=checkpoint.agent_id,
        name=checkpoint.name,
        schema_version=checkpoint.schema_version,
        digest_sha256=checkpoint.digest_sha256,
        size_bytes=checkpoint.size_bytes,
    )


def artifact_response(artifact: RunArtifact) -> ArtifactResponse:
    return ArtifactResponse(
        id=artifact.id,
        workflow_run_id=artifact.workflow_run_id,
        agent_id=artifact.agent_id,
        parent_artifact_id=artifact.parent_artifact_id,
        name=artifact.name,
        content_type=artifact.content_type,
        digest_sha256=artifact.digest_sha256,
        size_bytes=artifact.size_bytes,
    )


def budget_response(budget: RunBudget) -> BudgetResponse:
    return BudgetResponse(
        workflow_run_id=budget.workflow_run_id,
        limits=budget.limits,
        consumed=budget.consumed,
        exhausted_at=budget.exhausted_at,
    )


async def list_workflow_runs(
    database: Annotated[Database, Depends(get_database)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> WorkflowRunPage:
    async with database.session() as session:
        runs = (
            await session.scalars(
                select(WorkflowRun)
                .order_by(WorkflowRun.created_at.desc(), WorkflowRun.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
    return WorkflowRunPage(
        items=[workflow_run_response(run) for run in runs], limit=limit, offset=offset
    )


async def workflow_run_detail(
    run_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> WorkflowRunDetailResponse:
    async with database.session() as session:
        run = await WorkflowRunRepository(session).get(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workflow run not found")
    return workflow_run_response(run)


async def list_run_checkpoints(
    run_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> list[CheckpointResponse]:
    async with database.session() as session:
        if await session.get(WorkflowRun, run_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="workflow run not found"
            )
        checkpoints = (
            await session.scalars(
                select(RunCheckpoint)
                .where(RunCheckpoint.workflow_run_id == run_id)
                .order_by(RunCheckpoint.name)
            )
        ).all()
    return [checkpoint_response(checkpoint) for checkpoint in checkpoints]


async def list_run_artifacts(
    run_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> list[ArtifactResponse]:
    async with database.session() as session:
        if await session.get(WorkflowRun, run_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="workflow run not found"
            )
        artifacts = (
            await session.scalars(
                select(RunArtifact)
                .where(RunArtifact.workflow_run_id == run_id)
                .order_by(RunArtifact.created_at, RunArtifact.id)
            )
        ).all()
    return [artifact_response(artifact) for artifact in artifacts]


async def download_run_artifact(
    run_id: UUID,
    artifact_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> Response:
    async with database.session() as session:
        artifact = await session.get(RunArtifact, artifact_id)
    if artifact is None or artifact.workflow_run_id != run_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="artifact not found")
    if hashlib.sha256(artifact.content).hexdigest() != artifact.digest_sha256:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="artifact integrity validation failed",
        )
    return Response(
        content=artifact.content,
        media_type=artifact.content_type,
        headers={"Content-Disposition": f'attachment; filename="{artifact.name}"'},
    )


async def run_budget(
    run_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> BudgetResponse:
    async with database.session() as session:
        if await session.get(WorkflowRun, run_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="workflow run not found"
            )
        budget = await session.scalar(
            select(RunBudget).where(RunBudget.workflow_run_id == run_id)
        )
    if budget is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="run budget is not initialized"
        )
    return budget_response(budget)


async def list_run_events(
    run_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> list[WorkflowEventEvidence]:
    async with database.session() as session:
        if await WorkflowRunRepository(session).get(run_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="workflow run not found"
            )
        events = await WorkflowRunRepository(session).events(run_id)
    return [
        WorkflowEventEvidence(
            sequence=event.sequence,
            event_type=event.event_type,
            summary=event.summary,
            payload=event.payload,
        )
        for event in events
    ]


async def list_events(
    database: Annotated[Database, Depends(get_database)],
    workflow_run_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> WorkflowEventPage:
    statement = select(WorkflowEvent).order_by(
        WorkflowEvent.occurred_at.desc(), WorkflowEvent.id.desc()
    )
    if workflow_run_id is not None:
        statement = statement.where(WorkflowEvent.workflow_run_id == workflow_run_id)
    async with database.session() as session:
        events = (await session.scalars(statement.limit(limit).offset(offset))).all()
    return WorkflowEventPage(
        items=[workflow_event_response(event) for event in events], limit=limit, offset=offset
    )


async def event_detail(
    event_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> WorkflowEventResponse:
    async with database.session() as session:
        event = await session.get(WorkflowEvent, event_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="workflow event not found"
        )
    return workflow_event_response(event)


def event_stream_cursor(request: Request, after_sequence: int) -> int:
    """Use the furthest supplied replay cursor without trusting an invalid header."""
    last_event_id = request.headers.get("last-event-id")
    if last_event_id is None:
        return after_sequence
    try:
        header_cursor = int(last_event_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Last-Event-ID must be a non-negative integer",
        ) from None
    if header_cursor < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Last-Event-ID must be a non-negative integer",
        )
    return max(after_sequence, header_cursor)


async def stream_run_events(
    run_id: UUID,
    request: Request,
    database: Annotated[Database, Depends(get_database)],
    after_sequence: Annotated[int, Query(ge=0, le=2_147_483_647)] = 0,
) -> StreamingResponse:
    cursor = event_stream_cursor(request, after_sequence)
    async with database.session() as session:
        if await WorkflowRunRepository(session).get(run_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="workflow run not found"
            )

    async def event_source() -> AsyncIterator[str]:
        nonlocal cursor
        while True:
            async with database.session() as session:
                runs = WorkflowRunRepository(session)
                events = await runs.events_after(
                    run_id,
                    after_sequence=cursor,
                    limit=EVENT_STREAM_BATCH_SIZE,
                )
                run = await runs.get(run_id)
            for event in events:
                cursor = event.sequence
                data = {
                    "sequence": event.sequence,
                    "event_type": event.event_type,
                    "summary": event.summary,
                    "payload": event.payload,
                    "occurred_at": event.occurred_at.isoformat(),
                }
                yield f"id: {cursor}\nevent: workflow_event\ndata: {json.dumps(data)}\n\n"
            if run is None or run.status in TERMINAL_RUN_STATUSES:
                return
            yield ": keepalive\n\n"
            await asyncio.sleep(EVENT_STREAM_POLL_SECONDS)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def list_agents(
    database: Annotated[Database, Depends(get_database)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> AgentPage:
    async with database.session() as session:
        agents = (
            await session.scalars(
                select(Agent).order_by(Agent.name, Agent.version).limit(limit).offset(offset)
            )
        ).all()
    return AgentPage(
        items=[agent_response(agent) for agent in agents],
        limit=limit,
        offset=offset,
    )


async def create_agent_registration(
    payload: AgentRegistrationRequest,
    database: Annotated[Database, Depends(get_database)],
) -> AgentResponse:
    try:
        registration = parse_registration(
            manifest=payload.manifest,
            package_name=payload.package_name,
            entry_point=payload.entry_point,
        )
    except AgentManifestError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="managed agent manifest or installation metadata is invalid",
        ) from None
    async with database.session() as session:
        try:
            agent = await register_agent(session, registration=registration)
        except MissingDeclaredTool as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(error),
            ) from None
        except AgentRegistrationConflict:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="agent version is already registered with different content",
            ) from None
    return agent_response(agent)


async def discover_agent_registrations(
    database: Annotated[Database, Depends(get_database)],
) -> list[AgentResponse]:
    """Register every validated trusted package installed in the control-plane environment."""

    try:
        discovered = discovered_agents()
    except AgentDiscoveryError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"installed managed-agent discovery failed: {error}",
        ) from None
    async with database.session() as session:
        registered: list[Agent] = []
        for installed in discovered:
            try:
                registered.append(
                    await register_agent(session, registration=installed.registration)
                )
            except MissingDeclaredTool as error:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=str(error),
                ) from None
            except AgentRegistrationConflict:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="agent version is already registered with different content",
                ) from None
    return [agent_response(agent) for agent in registered]


async def agent_detail(
    agent_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> AgentResponse:
    async with database.session() as session:
        agent = await session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")
    return agent_response(agent)


async def list_run_approvals(
    run_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> list[ApprovalResponse]:
    async with database.session() as session:
        approvals = (
            await session.scalars(
                select(ApprovalRequest)
                .where(ApprovalRequest.workflow_run_id == run_id)
                .order_by(ApprovalRequest.created_at)
            )
        ).all()
    return [approval_response(approval) for approval in approvals]


async def list_approvals(
    database: Annotated[Database, Depends(get_database)],
    workflow_run_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> ApprovalPage:
    statement = select(ApprovalRequest).order_by(
        ApprovalRequest.created_at.desc(), ApprovalRequest.id.desc()
    )
    if workflow_run_id is not None:
        statement = statement.where(ApprovalRequest.workflow_run_id == workflow_run_id)
    async with database.session() as session:
        approvals = (await session.scalars(statement.limit(limit).offset(offset))).all()
    return ApprovalPage(
        items=[approval_response(approval) for approval in approvals], limit=limit, offset=offset
    )


async def approval_detail(
    approval_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> ApprovalResponse:
    async with database.session() as session:
        approval = await session.get(ApprovalRequest, approval_id)
    if approval is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="approval request not found"
        )
    return approval_response(approval)


async def list_run_model_calls(
    run_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> list[ModelCallResponse]:
    async with database.session() as session:
        calls = (
            await session.scalars(
                select(ModelCall)
                .where(ModelCall.workflow_run_id == run_id)
                .order_by(ModelCall.created_at)
            )
        ).all()
    return [model_call_response(call) for call in calls]


async def list_model_calls(
    database: Annotated[Database, Depends(get_database)],
    workflow_run_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> ModelCallPage:
    statement = select(ModelCall).order_by(ModelCall.created_at.desc(), ModelCall.id.desc())
    if workflow_run_id is not None:
        statement = statement.where(ModelCall.workflow_run_id == workflow_run_id)
    async with database.session() as session:
        calls = (await session.scalars(statement.limit(limit).offset(offset))).all()
    return ModelCallPage(
        items=[model_call_response(call) for call in calls], limit=limit, offset=offset
    )


async def model_call_detail(
    model_call_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> ModelCallResponse:
    async with database.session() as session:
        call = await session.get(ModelCall, model_call_id)
    if call is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="model call not found")
    return model_call_response(call)


async def list_run_tool_calls(
    run_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> list[ToolInvocationResponse]:
    async with database.session() as session:
        calls = (
            await session.scalars(
                select(ToolInvocation)
                .where(ToolInvocation.workflow_run_id == run_id)
                .order_by(ToolInvocation.created_at)
            )
        ).all()
    return [tool_invocation_response(call) for call in calls]


async def list_tool_calls(
    database: Annotated[Database, Depends(get_database)],
    workflow_run_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> ToolInvocationPage:
    statement = select(ToolInvocation).order_by(
        ToolInvocation.created_at.desc(), ToolInvocation.id.desc()
    )
    if workflow_run_id is not None:
        statement = statement.where(ToolInvocation.workflow_run_id == workflow_run_id)
    async with database.session() as session:
        calls = (await session.scalars(statement.limit(limit).offset(offset))).all()
    return ToolInvocationPage(
        items=[tool_invocation_response(call) for call in calls], limit=limit, offset=offset
    )


async def tool_call_detail(
    tool_call_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> ToolInvocationResponse:
    async with database.session() as session:
        call = await session.get(ToolInvocation, tool_call_id)
    if call is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="tool invocation not found"
        )
    return tool_invocation_response(call)


async def onboarding_evidence(
    run_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> WorkflowEvidenceResponse:
    async with database.session() as session:
        run = await WorkflowRunRepository(session).get(run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="workflow run not found"
            )
        events = (
            await session.scalars(
                select(WorkflowEvent)
                .where(WorkflowEvent.workflow_run_id == run_id)
                .order_by(WorkflowEvent.sequence)
            )
        ).all()
        model_calls = (
            await session.scalars(
                select(ModelCall)
                .where(ModelCall.workflow_run_id == run_id)
                .order_by(ModelCall.created_at)
            )
        ).all()
    return WorkflowEvidenceResponse(
        workflow_run_id=run_id,
        events=[
            WorkflowEventEvidence(
                sequence=event.sequence,
                event_type=event.event_type,
                summary=event.summary,
                payload=event.payload,
            )
            for event in events
        ],
        model_calls=[
            ModelCallEvidence(
                provider=model_call.provider,
                model=model_call.model,
                correlation_id=model_call.correlation_id,
                status=model_call.status.value,
                input_tokens=model_call.input_tokens,
                output_tokens=model_call.output_tokens,
                latency_ms=model_call.latency_ms,
                error_category=model_call.error_category,
                explanation_summary=model_call.decision_summary,
            )
            for model_call in model_calls
        ],
    )


async def evaluation_report(
    evaluation_run_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> EvaluationReportResponse:
    async with database.session() as session:
        run = await session.get(EvaluationRun, evaluation_run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="evaluation run not found")
        results = (
            await session.scalars(
                select(EvaluationResult)
                .where(EvaluationResult.evaluation_run_id == evaluation_run_id)
                .order_by(EvaluationResult.case_slug, EvaluationResult.id)
            )
        ).all()
    return EvaluationReportResponse(
        evaluation_run_id=evaluation_run_id,
        suite_slug=run.suite_slug,
        suite_version=run.suite_version,
        suite_schema_version=run.suite_schema_version,
        dataset_sha256=run.dataset_sha256,
        status=run.status.value,
        configuration=run.configuration,
        results=[
            EvaluationResultReport(
                case_slug=result.case_slug,
                case_version=result.case_version,
                case_content_sha256=result.case_content_sha256,
                workflow_run_id=result.workflow_run_id,
                status=result.status.value,
                score=float(result.score),
                expected_outcome=result.expected_outcome,
                actual_outcome=result.actual_outcome,
                failure_category=result.failure_category,
                duration_ms=result.duration_ms,
                metrics=result.metrics,
                evidence_summary=result.evidence_summary,
                summary=result.summary,
            )
            for result in results
        ],
    )


async def list_evaluations(
    database: Annotated[Database, Depends(get_database)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> EvaluationRunPage:
    async with database.session() as session:
        runs = (
            await session.scalars(
                select(EvaluationRun)
                .order_by(EvaluationRun.created_at.desc(), EvaluationRun.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
    return EvaluationRunPage(
        items=[
            EvaluationRunResponse(
                id=run.id,
                suite_slug=run.suite_slug,
                suite_version=run.suite_version,
                suite_schema_version=run.suite_schema_version,
                dataset_sha256=run.dataset_sha256,
                status=run.status.value,
                configuration=run.configuration,
            )
            for run in runs
        ],
        limit=limit,
        offset=offset,
    )


async def execute_evaluation(
    payload: EvaluationExecuteRequest,
    database: Annotated[Database, Depends(get_database)],
    principal: Annotated[AuthenticatedPrincipal, Depends(get_authenticated_principal)],
) -> EvaluationRunResponse:
    """Run the explicitly limited B1 catalog-persistence integrity checks."""
    async with database.session() as session:
        try:
            run = await EvaluationRunner().run_vendor_onboarding(
                session,
                requested_by_id=principal.id,
                idempotency_key=payload.idempotency_key,
            )
        except EvaluationRequestFingerprintConflict:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="idempotency key was reused for a different evaluation request",
            ) from None
        return EvaluationRunResponse(
            id=run.id,
            suite_slug=run.suite_slug,
            suite_version=run.suite_version,
            suite_schema_version=run.suite_schema_version,
            dataset_sha256=run.dataset_sha256,
            status=run.status.value,
            configuration=run.configuration,
        )


def fault_plan_response(plan: FaultInjectionPlan) -> FaultPlanResponse:
    return FaultPlanResponse(
        id=plan.id,
        fault_point=plan.fault_point,
        scope_key=plan.scope_key,
        action=plan.category,
        trigger_count=plan.trigger_count,
        remaining_triggers=plan.remaining_triggers,
        delay_ms=plan.delay_ms,
        status=plan.status.value,
        safe_metadata=plan.safe_metadata,
    )


def fault_injector(database: Database, settings: Settings) -> FaultInjector:
    return FaultInjector(database, enabled=settings.fault_injection_enabled)


async def create_fault_plan(
    payload: FaultPlanCreateRequest,
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> FaultPlanResponse:
    try:
        plan = await fault_injector(database, settings).create(
            fault_point=payload.fault_point,
            action=payload.action,
            scope_key=payload.scope_key,
            trigger_count=payload.trigger_count,
            delay_ms=payload.delay_ms,
            safe_metadata=payload.safe_metadata,
        )
    except FaultInjectionDisabled:
        raise HTTPException(status_code=404, detail="fault injection is unavailable") from None
    return fault_plan_response(plan)


async def list_fault_plans(
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    scope_key: str | None = Query(default=None, min_length=1, max_length=240),
) -> list[FaultPlanResponse]:
    try:
        plans = await fault_injector(database, settings).list(scope_key=scope_key)
    except FaultInjectionDisabled:
        raise HTTPException(status_code=404, detail="fault injection is unavailable") from None
    return [fault_plan_response(plan) for plan in plans]


async def consume_fault_plan(
    fault_point: FaultPoint,
    scope_key: Annotated[str, Query(min_length=1, max_length=240)],
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> FaultConsumptionResponse:
    if not settings.fault_injection_enabled:
        raise HTTPException(status_code=404, detail="fault injection is unavailable")
    directive = await fault_injector(database, settings).consume(
        fault_point=fault_point, scope_key=scope_key
    )
    return FaultConsumptionResponse(
        consumed=directive is not None,
        fault_point=fault_point.value,
        scope_key=scope_key,
        action=directive.action.value if directive is not None else None,
        remaining_triggers=directive.remaining_triggers if directive is not None else None,
    )


async def clear_fault_plan(
    fault_point: FaultPoint,
    scope_key: Annotated[str, Query(min_length=1, max_length=240)],
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> Response:
    try:
        cleared = await fault_injector(database, settings).clear(
            fault_point=fault_point, scope_key=scope_key
        )
    except FaultInjectionDisabled:
        raise HTTPException(status_code=404, detail="fault injection is unavailable") from None
    if not cleared:
        raise HTTPException(status_code=404, detail="fault plan not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def cancel_onboarding(
    run_id: UUID,
    request: Request,
    database: Annotated[Database, Depends(get_database)],
    principal: Annotated[AuthenticatedPrincipal, Depends(get_authenticated_principal)],
) -> Response:
    cancellation_targets: list[tuple[str, str]] = []
    async with database.session() as session:
        run = await WorkflowRunRepository(session).get(run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="workflow run not found"
            )
        temporal_workflow_id = run.temporal_workflow_id
        workflow_type = getattr(run, "workflow_type", "vendor_onboarding")
        cancellation_targets.append((temporal_workflow_id, workflow_type))
        if workflow_type == "managed_agent" and run.status not in TERMINAL_RUN_STATUSES:
            run.status = RunStatus.CANCELLED
            run.result_summary = {"summary": "Cancelled by authenticated operator request."}
            pending_parent_ids: list[UUID] = [run.id]
            seen_run_ids = {run.id}
            while pending_parent_ids:
                child_ids = (
                    await session.scalars(
                        select(RunDelegation.child_workflow_run_id).where(
                            RunDelegation.parent_workflow_run_id.in_(pending_parent_ids)
                        )
                    )
                ).all()
                pending_parent_ids = []
                if not child_ids:
                    continue
                children = cast(
                    list[WorkflowRun],
                    (
                        await session.scalars(
                            select(WorkflowRun).where(WorkflowRun.id.in_(child_ids))
                        )
                    ).all(),
                )
                for child in children:
                    if child.id in seen_run_ids:
                        continue
                    seen_run_ids.add(child.id)
                    pending_parent_ids.append(child.id)
                    if child.status not in TERMINAL_RUN_STATUSES:
                        child.status = RunStatus.CANCELLED
                        child.result_summary = {
                            "summary": "Cancelled because its delegated parent was cancelled."
                        }
                    cancellation_targets.append((child.temporal_workflow_id, child.workflow_type))
        audit_key = f"{run.id}:api.workflow.cancel.request:{principal.id}"
        audits = AuditEventRepository(session)
        if await audits.get_by_idempotency_key(audit_key) is None:
            await audits.append(
                AuditEvent(
                    workflow_run_id=run.id,
                    actor_id=principal.id,
                    action="api.workflow.cancel.request",
                    resource_type="workflow_run",
                    resource_id=run.id,
                    idempotency_key=audit_key,
                    summary="Authenticated principal requested workflow cancellation.",
                    evidence={},
                )
            )
    resources: RuntimeResources = request.app.state.resources
    for workflow_id, target_type in cancellation_targets:
        signal: str | Callable[..., object]
        signal = VendorOnboardingWorkflow.cancel if target_type == "vendor_onboarding" else "cancel"
        await resources.temporal_client.get_workflow_handle(workflow_id).signal(signal)
    return Response(status_code=status.HTTP_202_ACCEPTED)


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resources = await create_resources(app_settings)
        app.state.resources = resources
        app.state.settings = app_settings
        try:
            yield
        finally:
            await resources.close()

    app = FastAPI(
        title="Agents Should Survive Failure",
        description="Control plane for durable, observable, permission-controlled AI agents.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.add_exception_handler(RequestValidationError, validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, http_error)  # type: ignore[arg-type]
    app.add_exception_handler(IntegrityError, database_integrity_error)  # type: ignore[arg-type]
    app.add_exception_handler(OperationalError, database_unavailable_error)  # type: ignore[arg-type]

    def add_v1_route(
        path: str,
        endpoint: Callable[..., Any],
        *,
        methods: list[str],
        scopes: tuple[str, ...],
        allowed_principal_types: frozenset[PrincipalType] | None = None,
        **kwargs: Any,
    ) -> None:
        dependencies = [
            Depends(
                require_scopes(
                    *scopes,
                    allowed_principal_types=allowed_principal_types,
                )
            )
        ]
        app.add_api_route(
            f"/api/v1{path}", endpoint, methods=methods, dependencies=dependencies, **kwargs
        )
        app.add_api_route(
            path, endpoint, methods=methods, deprecated=True, dependencies=dependencies, **kwargs
        )

    app.add_api_route(
        "/health/live",
        liveness,
        methods=["GET"],
        response_model=HealthResponse,
        tags=["health"],
    )
    app.add_api_route(
        "/health/ready",
        readiness,
        methods=["GET"],
        response_model=ReadinessResponse,
        responses={503: {"model": ReadinessResponse}},
        tags=["health"],
    )
    app.add_api_route("/metrics", metrics, methods=["GET"], include_in_schema=False)
    add_v1_route(
        "/vendors",
        create_vendor,
        methods=["POST"],
        scopes=("runs:write",),
        response_model=VendorResponse,
    )
    add_v1_route(
        "/vendors/{vendor_id}/onboarding",
        start_onboarding,
        methods=["POST"],
        scopes=("runs:write",),
        response_model=WorkflowRunResponse,
    )
    add_v1_route(
        "/workflow-runs/{run_id}/approval",
        decide_onboarding,
        methods=["POST"],
        scopes=("approvals:decide",),
        allowed_principal_types=frozenset({PrincipalType.USER, PrincipalType.SERVICE}),
        status_code=status.HTTP_202_ACCEPTED,
    )
    app.add_api_route(
        "/workflow-runs/{run_id}",
        onboarding_status,
        methods=["GET"],
        response_model=WorkflowStatus,
        deprecated=True,
        dependencies=[Depends(require_scopes("runs:read"))],
    )
    app.add_api_route(
        "/api/v1/workflow-runs/{run_id}/status",
        onboarding_status,
        methods=["GET"],
        response_model=WorkflowStatus,
        dependencies=[Depends(require_scopes("runs:read"))],
    )
    app.add_api_route(
        "/api/v1/workflow-runs",
        list_workflow_runs,
        methods=["GET"],
        response_model=WorkflowRunPage,
        dependencies=[Depends(require_scopes("runs:read"))],
    )
    app.add_api_route(
        "/api/v1/workflow-runs/{run_id}",
        workflow_run_detail,
        methods=["GET"],
        response_model=WorkflowRunDetailResponse,
        dependencies=[Depends(require_scopes("runs:read"))],
    )
    add_v1_route(
        "/workflow-runs/{run_id}/checkpoints",
        list_run_checkpoints,
        methods=["GET"],
        scopes=("runs:read",),
        response_model=list[CheckpointResponse],
    )
    add_v1_route(
        "/workflow-runs/{run_id}/artifacts",
        list_run_artifacts,
        methods=["GET"],
        scopes=("runs:read",),
        response_model=list[ArtifactResponse],
    )
    add_v1_route(
        "/workflow-runs/{run_id}/artifacts/{artifact_id}",
        download_run_artifact,
        methods=["GET"],
        scopes=("runs:read",),
    )
    add_v1_route(
        "/workflow-runs/{run_id}/budget",
        run_budget,
        methods=["GET"],
        scopes=("runs:read",),
        response_model=BudgetResponse,
    )
    app.add_api_route(
        "/api/v1/workflow-runs/{run_id}/events",
        list_run_events,
        methods=["GET"],
        response_model=list[WorkflowEventEvidence],
        dependencies=[Depends(require_scopes("runs:read"))],
    )
    app.add_api_route(
        "/api/v1/events",
        list_events,
        methods=["GET"],
        response_model=WorkflowEventPage,
        dependencies=[Depends(require_scopes("runs:read"))],
    )
    app.add_api_route(
        "/api/v1/events/{event_id}",
        event_detail,
        methods=["GET"],
        response_model=WorkflowEventResponse,
        dependencies=[Depends(require_scopes("runs:read"))],
    )
    app.add_api_route(
        "/api/v1/workflow-runs/{run_id}/events/stream",
        stream_run_events,
        methods=["GET"],
        dependencies=[Depends(require_scopes("runs:read"))],
    )
    app.add_api_route(
        "/api/v1/workflow-runs/{run_id}/approvals",
        list_run_approvals,
        methods=["GET"],
        response_model=list[ApprovalResponse],
        dependencies=[Depends(require_scopes("approvals:read"))],
    )
    app.add_api_route(
        "/api/v1/approvals",
        list_approvals,
        methods=["GET"],
        response_model=ApprovalPage,
        dependencies=[Depends(require_scopes("approvals:read"))],
    )
    app.add_api_route(
        "/api/v1/approvals/{approval_id}",
        approval_detail,
        methods=["GET"],
        response_model=ApprovalResponse,
        dependencies=[Depends(require_scopes("approvals:read"))],
    )
    app.add_api_route(
        "/api/v1/workflow-runs/{run_id}/model-calls",
        list_run_model_calls,
        methods=["GET"],
        response_model=list[ModelCallResponse],
        dependencies=[Depends(require_scopes("runs:read"))],
    )
    app.add_api_route(
        "/api/v1/model-calls",
        list_model_calls,
        methods=["GET"],
        response_model=ModelCallPage,
        dependencies=[Depends(require_scopes("runs:read"))],
    )
    app.add_api_route(
        "/api/v1/model-calls/{model_call_id}",
        model_call_detail,
        methods=["GET"],
        response_model=ModelCallResponse,
        dependencies=[Depends(require_scopes("runs:read"))],
    )
    app.add_api_route(
        "/api/v1/workflow-runs/{run_id}/tool-calls",
        list_run_tool_calls,
        methods=["GET"],
        response_model=list[ToolInvocationResponse],
        dependencies=[Depends(require_scopes("runs:read"))],
    )
    app.add_api_route(
        "/api/v1/tool-calls",
        list_tool_calls,
        methods=["GET"],
        response_model=ToolInvocationPage,
        dependencies=[Depends(require_scopes("runs:read"))],
    )
    app.add_api_route(
        "/api/v1/tool-calls/{tool_call_id}",
        tool_call_detail,
        methods=["GET"],
        response_model=ToolInvocationResponse,
        dependencies=[Depends(require_scopes("runs:read"))],
    )
    app.add_api_route(
        "/api/v1/agents",
        create_agent_registration,
        methods=["POST"],
        response_model=AgentResponse,
        dependencies=[Depends(require_scopes("agents:write"))],
    )
    app.add_api_route(
        "/api/v1/agents/discover",
        discover_agent_registrations,
        methods=["POST"],
        response_model=list[AgentResponse],
        dependencies=[Depends(require_scopes("agents:write"))],
    )
    app.add_api_route(
        "/api/v1/agents",
        list_agents,
        methods=["GET"],
        response_model=AgentPage,
        dependencies=[Depends(require_scopes("agents:read"))],
    )
    app.add_api_route(
        "/api/v1/agents/{agent_slug}/runs",
        start_managed_agent,
        methods=["POST"],
        response_model=WorkflowRunResponse,
        dependencies=[Depends(require_scopes("runs:write"))],
    )
    app.add_api_route(
        "/api/v1/agents/{agent_id}",
        agent_detail,
        methods=["GET"],
        response_model=AgentResponse,
        dependencies=[Depends(require_scopes("agents:read"))],
    )
    add_v1_route(
        "/workflow-runs/{run_id}/evidence",
        onboarding_evidence,
        methods=["GET"],
        scopes=("runs:read",),
        response_model=WorkflowEvidenceResponse,
    )
    add_v1_route(
        "/evaluation-runs/{evaluation_run_id}",
        evaluation_report,
        methods=["GET"],
        scopes=("evaluations:read",),
        response_model=EvaluationReportResponse,
    )
    app.add_api_route(
        "/api/v1/fault-plans",
        create_fault_plan,
        methods=["POST"],
        response_model=FaultPlanResponse,
        dependencies=[Depends(require_scopes("evaluations:execute"))],
    )
    app.add_api_route(
        "/api/v1/fault-plans",
        list_fault_plans,
        methods=["GET"],
        response_model=list[FaultPlanResponse],
        dependencies=[Depends(require_scopes("evaluations:execute"))],
    )
    app.add_api_route(
        "/api/v1/fault-plans/{fault_point}/consume",
        consume_fault_plan,
        methods=["POST"],
        response_model=FaultConsumptionResponse,
        dependencies=[Depends(require_scopes("evaluations:execute"))],
    )
    app.add_api_route(
        "/api/v1/fault-plans/{fault_point}",
        clear_fault_plan,
        methods=["DELETE"],
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_scopes("evaluations:execute"))],
    )
    app.add_api_route(
        "/api/v1/evaluations/execute",
        execute_evaluation,
        methods=["POST"],
        response_model=EvaluationRunResponse,
        dependencies=[Depends(require_scopes("evaluations:execute"))],
    )
    app.add_api_route(
        "/api/v1/evaluations",
        list_evaluations,
        methods=["GET"],
        response_model=EvaluationRunPage,
        dependencies=[Depends(require_scopes("evaluations:read"))],
    )
    app.add_api_route(
        "/api/v1/evaluations/{evaluation_run_id}",
        evaluation_report,
        methods=["GET"],
        response_model=EvaluationReportResponse,
        dependencies=[Depends(require_scopes("evaluations:read"))],
    )
    add_v1_route(
        "/workflow-runs/{run_id}",
        cancel_onboarding,
        methods=["DELETE"],
        scopes=("runs:write",),
        status_code=202,
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        RequestBodyLimitMiddleware, max_body_bytes=app_settings.max_request_body_bytes
    )

    configure_tracing(app, app_settings)

    return app


app = create_app()

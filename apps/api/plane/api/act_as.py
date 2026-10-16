# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Opt-in, API-key-only delegation for explicitly registered content handlers."""

from dataclasses import dataclass, replace
from functools import wraps
import logging
from uuid import UUID, uuid4

from django.conf import settings
from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.permissions import SAFE_METHODS

from plane.api.middleware.api_authentication import APIKeyAuthentication
from plane.app.permissions import ROLE
from plane.db.models import (
    ImpersonationAudit,
    Issue,
    IssueComment,
    IssueLink,
    IssueType,
    Project,
    ProjectMember,
    User,
    Workspace,
    WorkspaceMember,
    FileAsset,
)
from plane.license.api.permissions.instance import InstanceAdminPermission
from plane.utils.act_as import content_author
from plane.utils.host import base_host


USER_HEADER = "X-PlanePlus-Act-As-User"
REASON_HEADER = "X-PlanePlus-Act-As-Reason"
AUDIT_HEADER = "X-PlanePlus-Act-As-Audit-Id"
logger = logging.getLogger("plane.api.act_as")


class ActAsError(APIException):
    def __init__(self, code, message, status_code=403):
        self.status_code = status_code
        self.audit_code = code
        super().__init__({"error_code": code, "error": message}, code=code)


@dataclass(frozen=True)
class ContentOperation:
    action: str
    resource_type: str
    fields: frozenset[str]
    scope: str = "project"


@dataclass(frozen=True)
class ActAsContext:
    actor: User
    target: User
    workspace: Workspace
    project: Project | None
    operation: ContentOperation
    issue_id: UUID | None
    resource_id: UUID | None
    origin: str


class ContentRequest:
    """A view-local facade, never assigned to the Django request or a session.

    Existing permissions and handlers see the effective user; the authenticated
    DRF/Django request, token, throttle and request logger retain the real actor.
    """

    def __init__(self, request, context):
        self.original_request = request
        self.user = context.target
        self.authenticated_user = context.actor
        self.act_as_context = context

    def __getattr__(self, name):
        return getattr(self.original_request, name)


def act_as_content(action, resource_type, fields, scope="project"):
    """Explicit extension point. Adding a handler requires reviewing its scope/permissions."""
    operation = ContentOperation(action, resource_type, frozenset(fields), scope)

    def decorate(handler):
        @wraps(handler)
        def wrapped(view, request, *args, **kwargs):
            context = getattr(request, "act_as_context", None)
            if context is None:
                return handler(view, request, *args, **kwargs)
            proxy = ContentRequest(request, context)
            original = view.request
            try:
                view.request = proxy
                with (
                    timezone.override(context.target.user_timezone),
                    content_author(context if request.method not in SAFE_METHODS else None),
                ):
                    return handler(view, proxy, *args, **kwargs)
            finally:
                view.request = original

        wrapped.act_as_operation = operation
        return extend_schema(
            parameters=[
                OpenApiParameter(
                    USER_HEADER,
                    OpenApiTypes.UUID,
                    OpenApiParameter.HEADER,
                    description="Delegation target; requires PLANEPLUS_ENABLE_ACT_AS=1 and an administrator API key.",
                ),
                OpenApiParameter(
                    REASON_HEADER,
                    {"type": "string", "maxLength": 500},
                    OpenApiParameter.HEADER,
                    description="Optional audit context/reason for delegation; never send passwords or tokens here.",
                ),
                OpenApiParameter(AUDIT_HEADER, OpenApiTypes.UUID, OpenApiParameter.HEADER, response=True),
                OpenApiParameter(USER_HEADER, OpenApiTypes.UUID, OpenApiParameter.HEADER, response=True),
            ]
        )(wrapped)

    return decorate


WORK_ITEM_FIELDS = {
    "name",
    "description_html",
    "description_binary",
    "priority",
    "state",
    "parent",
    "assignees",
    "labels",
    "start_date",
    "target_date",
    "estimate_point",
    "type_id",
    "sort_order",
    "external_source",
    "external_id",
    "archived_at",
}
COMMENT_FIELDS = {"comment_html", "comment_json", "access", "external_source", "external_id"}


def has_act_as_headers(request):
    return USER_HEADER in request.headers or REASON_HEADER in request.headers


def is_instance_admin(request):
    return InstanceAdminPermission().has_permission(request, None)


def is_workspace_admin(user, workspace):
    return WorkspaceMember.objects.filter(
        workspace=workspace,
        member=user,
        role=ROLE.ADMIN.value,
        is_active=True,
    ).exists()


def authorize_act_as_actor(request, slug):
    """Also used for administrator-only discovery; never accepts a claimed identity."""
    authenticator = request.successful_authenticator
    token = getattr(authenticator, "api_token", None)
    if not isinstance(authenticator, APIKeyAuthentication) or token is None or request.user.is_bot:
        raise ActAsError("act_as_api_key_required", "An administrator's personal API key is required.")
    if token.user_type != 0 or token.is_service:
        raise ActAsError("act_as_api_key_required", "Bot and service tokens cannot impersonate users.")
    workspace = Workspace.objects.filter(slug=slug).first()
    if workspace is None or (token.workspace_id is not None and token.workspace_id != workspace.id):
        raise ActAsError("act_as_workspace_forbidden", "The API key cannot impersonate in this workspace.")
    if not is_instance_admin(request) and not is_workspace_admin(request.user, workspace):
        raise ActAsError("act_as_admin_required", "Instance or workspace administrator permission is required.")
    return workspace


def resolve_act_as(request, view, operation):
    """Authenticate first, authorize the actor, then resolve and scope the target."""
    if not settings.PLANEPLUS_ENABLE_ACT_AS:
        raise ActAsError("act_as_disabled", "API impersonation is disabled.")
    workspace = authorize_act_as_actor(request, view.kwargs.get("slug"))
    if operation is None:
        raise ActAsError("act_as_operation_not_supported", "Impersonation is not supported for this operation.")
    if request.method not in SAFE_METHODS and not isinstance(request.data, dict):
        raise ActAsError("act_as_invalid_payload", "A JSON object is required.", 400)
    reason = request.headers.get(REASON_HEADER, "")
    if len(reason) > 500 or any(char in reason for char in "\r\n\x00"):
        raise ActAsError(
            "act_as_invalid_reason", "The reason must be at most 500 characters without control lines.", 400
        )
    target_id = parse_uuid(request.headers.get(USER_HEADER))
    if target_id is None:
        raise ActAsError("act_as_invalid_user", "X-PlanePlus-Act-As-User must contain a user UUID.", 400)
    target = User.objects.filter(id=target_id, is_active=True, is_bot=False).first()
    if target is None:
        raise ActAsError("act_as_invalid_user", "The target must be an active regular user.", 400)
    request.act_as_target_user_id = target.id
    membership = WorkspaceMember.objects.filter(workspace=workspace, member=target, is_active=True).first()
    if membership is None:
        raise ActAsError("act_as_target_not_workspace_member", "The target is not an active workspace member.")
    project_id = view.kwargs.get("project_id")
    bound_asset = None
    asset_issue_id = None
    if operation.resource_type == "generic_asset":
        if view.kwargs.get("asset_id"):
            bound_asset = FileAsset.objects.filter(id=view.kwargs["asset_id"], workspace=workspace).first()
            if bound_asset is None:
                raise ActAsError("act_as_resource_forbidden", "The asset is not available in this workspace.")
            project_id = bound_asset.project_id
            asset_issue_id = bound_asset.issue_id
        elif request.method not in SAFE_METHODS:
            project_id = request.data.get("project_id")
        if request.method == "PATCH" and request.data.get("issue_id"):
            new_issue_id = parse_uuid(request.data["issue_id"])
            new_issue = Issue.objects.filter(id=new_issue_id, workspace=workspace).first() if new_issue_id else None
            if new_issue is None or (project_id and new_issue.project_id != project_id):
                raise ActAsError("act_as_resource_forbidden", "The work item is not available to bind this asset.")
            project_id, asset_issue_id = new_issue.project_id, new_issue.id
        if project_id is not None and parse_uuid(project_id) is None:
            raise ActAsError("act_as_invalid_payload", "project_id must be a UUID.", 400)
    if operation.resource_type == "project":
        project_id = project_id or view.kwargs.get("pk")
    project_lookup = (
        {"identifier": view.kwargs["project_identifier"]}
        if project_id is None and view.kwargs.get("project_identifier")
        else {"id": project_id}
    )
    project = Project.objects.filter(
        **project_lookup,
        workspace=workspace,
        archived_at__isnull=True,
    ).first()
    if project is None and (operation.scope != "workspace" or project_id is not None):
        raise ActAsError("act_as_resource_forbidden", "The resource is not available in this workspace.")
    if project is not None and operation.scope != "join":
        project_member = ProjectMember.objects.filter(project=project, member=target, is_active=True).first()
        public_project_read = (
            request.method in SAFE_METHODS and operation.resource_type == "project" and project.network == 2
        )
        if project_member is None and not public_project_read:
            raise ActAsError("act_as_target_permission_denied", "The target is not an active project member.")

    # Existing permission classes run against this facade after resolution.
    # Bind ALL URL resources before a handler can use a less-scoped lookup.
    issue_id = view.kwargs.get("issue_id") or asset_issue_id
    resource_id = view.kwargs.get("pk") or view.kwargs.get("asset_id")
    if view.kwargs.get("project_identifier") and project is not None:
        try:
            sequence_id = int(view.kwargs.get("issue_identifier", ""))
        except (ValueError, TypeError):
            raise ActAsError("act_as_invalid_identifier", "The work item number must be an integer.", 400)
        issue_id = (
            Issue.issue_objects.filter(project=project, workspace=workspace, sequence_id=sequence_id)
            .values_list(
                "id",
                flat=True,
            )
            .first()
        )
        if issue_id is None:
            raise ActAsError("act_as_resource_forbidden", "The work item is not available in this project.")
        resource_id = issue_id
    if operation.resource_type == "issue" and resource_id:
        issue_id = resource_id
    if issue_id:
        include_archived = request.method == "PATCH" and "archived_at" in request.data
        issue_manager = (
            Issue.objects
            if operation.resource_type in {"intake_issue", "inline_asset", "file_asset", "generic_asset"}
            or include_archived
            else Issue.issue_objects
        )
        if operation.resource_type == "inline_asset":
            issue_manager = issue_manager.filter(archived_at__isnull=True, is_draft=False)
        issue = issue_manager.filter(id=issue_id, workspace=workspace, project=project).first()
        if issue is None:
            raise ActAsError("act_as_resource_forbidden", "The work item is not available in this project.")
        project_membership = project.project_projectmember.filter(member=target, is_active=True).first()
        if (
            project_membership is not None
            and project_membership.role == ROLE.GUEST.value
            # Intake ownership lives on IntakeIssue; inline reads validate the
            # source body's visibility in their handler, including triage items.
            and operation.resource_type not in {"intake_issue", "inline_asset", "file_asset", "generic_asset"}
            and not project.guest_view_all_features
            and issue.created_by_id != target.id
        ):
            raise ActAsError("act_as_target_permission_denied", "The target cannot access this work item.")
    if resource_id and operation.resource_type in {"issue_comment", "issue_link"}:
        model = IssueComment if operation.resource_type == "issue_comment" else IssueLink
        resource = model.objects.filter(
            id=resource_id,
            workspace=workspace,
            project=project,
            issue_id=issue_id,
        ).first()
        if resource is None:
            raise ActAsError("act_as_resource_forbidden", "The resource is not available on this work item.")
        if operation.resource_type == "issue_comment" and request.method not in SAFE_METHODS:
            # Match the app's normal comment editing rule: creator, project
            # admin, or workspace admin who is also an active project member.
            project_admin = project.project_projectmember.filter(
                member=target,
                is_active=True,
                role=ROLE.ADMIN.value,
            ).exists()
            if resource.created_by_id != target.id and not project_admin and membership.role != ROLE.ADMIN.value:
                raise ActAsError("act_as_target_permission_denied", "The target cannot edit this comment.")

    if request.method in SAFE_METHODS:
        from plane.api.act_as_registry import validate_read_scope

        validate_read_scope(request, view, target, project, operation)
        resource_id = (
            resource_id
            or view.kwargs.get("estimate_point_id")
            or view.kwargs.get("cycle_id")
            or view.kwargs.get("module_id")
        )
        return ActAsContext(request.user, target, workspace, project, operation, issue_id, resource_id, "")
    if not isinstance(request.data, dict):
        raise ActAsError("act_as_invalid_payload", "A JSON object is required.", 400)
    forbidden_fields = set(request.data) - operation.fields
    if forbidden_fields:
        raise ActAsError(
            "act_as_fields_not_supported",
            "Unsupported fields for impersonation: " + ", ".join(sorted(forbidden_fields)),
            400,
        )
    from plane.api.act_as_registry import validate_write_scope

    validate_write_scope(request, view, target, workspace, project, operation)
    if operation.resource_type == "issue" and request.data.get("type_id") is not None:
        issue_type_id = parse_uuid(request.data["type_id"])
        if (
            issue_type_id is None
            or not IssueType.objects.filter(
                id=issue_type_id,
                workspace=workspace,
                is_active=True,
                project_issue_types__project=project,
                project_issue_types__deleted_at__isnull=True,
            ).exists()
        ):
            raise ActAsError("act_as_resource_forbidden", "The work item type is not available in this project.")
    return ActAsContext(
        request.user,
        target,
        workspace,
        project,
        operation,
        issue_id,
        resource_id
        or view.kwargs.get("estimate_point_id")
        or view.kwargs.get("cycle_id")
        or view.kwargs.get("module_id")
        or view.kwargs.get("estimate_id")
        or request.data.get("estimate_id"),
        base_host(request=request, is_app=True),
    )


def parse_uuid(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def append_audit(request, *, audit_id, response_status, context=None, actor=None, token=None, error_code=""):
    match = getattr(request, "resolver_match", None)
    url_kwargs = getattr(request, "act_as_url_kwargs", None) or (match.kwargs if match else {})
    operation = context.operation if context else getattr(request, "act_as_operation", None)
    workspace_id = (
        context.workspace.id
        if context
        else Workspace.objects.filter(
            slug=url_kwargs.get("slug"),
        )
        .values_list("id", flat=True)
        .first()
    )
    return ImpersonationAudit.objects.create(
        id=audit_id,
        actor_id=actor.id if actor is not None and actor.is_authenticated else None,
        target_user_id=context.target.id if context else getattr(request, "act_as_target_user_id", None),
        requested_user=request.headers.get(USER_HEADER, "").replace("\x00", "\\0")[:64],
        token_id=token.id if token is not None else None,
        workspace_id=workspace_id,
        project_id=context.project.id if context and context.project else parse_uuid(url_kwargs.get("project_id")),
        issue_id=context.issue_id if context else parse_uuid(url_kwargs.get("issue_id")),
        resource_id=context.resource_id if context else parse_uuid(url_kwargs.get("pk") or url_kwargs.get("asset_id")),
        resource_type=operation.resource_type if operation else "",
        action=operation.action if operation else "act_as.rejected",
        method=request.method,
        path=request.path,
        reason=request.headers.get(REASON_HEADER, "").replace("\x00", "\\0")[:500],
        succeeded=200 <= response_status < 300 or (response_status == 302 and request.method in SAFE_METHODS),
        response_status=response_status,
        error_code=error_code,
    )


class ActAsAPIMixin:
    """DRF lifecycle integration. Headerless requests take the original path."""

    act_as_context = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        from plane.api.act_as_registry import REGISTRY

        for (module, name, method), operation in REGISTRY.items():
            if (module, name) != (cls.__module__, cls.__name__) or method == "HEAD":
                continue
            names = (method.lower(),)
            if getattr(cls, method.lower(), None) is None:
                names = {
                    "GET": ("list", "retrieve"),
                    "POST": ("create",),
                    "PUT": ("update",),
                    "PATCH": ("partial_update",),
                    "DELETE": ("destroy",),
                }.get(method, ())
            for handler_name in names:
                handler = getattr(cls, handler_name, None)
                if handler is not None and not getattr(handler, "act_as_operation", None):
                    setattr(
                        cls,
                        handler_name,
                        act_as_content(
                            operation.action,
                            operation.resource_type,
                            operation.fields,
                            operation.scope,
                        )(handler),
                    )

    def perform_authentication(self, request):
        super().perform_authentication(request)
        if request.method == "OPTIONS" or not has_act_as_headers(request):
            return
        self.act_as_actor = request.user
        self.act_as_token = getattr(request.successful_authenticator, "api_token", None)
        handler = getattr(self, request.method.lower(), None)
        operation = getattr(handler, "act_as_operation", None)
        if request.method.lower() not in self.http_method_names:
            operation = None
        request.act_as_operation = operation
        request.act_as_url_kwargs = self.kwargs
        self.act_as_context = resolve_act_as(request, self, operation)
        request.act_as_context = self.act_as_context

    def check_permissions(self, request):
        if self.act_as_context is None:
            return super().check_permissions(request)
        try:
            return super().check_permissions(ContentRequest(request, self.act_as_context))
        except PermissionDenied:
            raise ActAsError("act_as_target_permission_denied", "The target lacks permission for this operation.")

    def check_object_permissions(self, request, obj):
        if self.act_as_context is not None:
            request = ContentRequest(request, self.act_as_context)
        return super().check_object_permissions(request, obj)

    def handle_exception(self, exc):
        self.act_as_error_code = getattr(exc, "audit_code", getattr(exc, "default_code", "request_failed"))
        return super().handle_exception(exc)

    def dispatch(self, request, *args, **kwargs):
        if request.method == "OPTIONS" or not has_act_as_headers(request):
            return super().dispatch(request, *args, **kwargs)
        from plane.utils.core import set_use_read_replica, should_use_read_replica

        previous = should_use_read_replica()
        set_use_read_replica(False)
        try:
            return self._dispatch_act_as(request, *args, **kwargs)
        finally:
            set_use_read_replica(previous)

    def _dispatch_act_as(self, request, *args, **kwargs):
        # Revocations and permissions must not be checked against a lagging
        # replica, even on GET. Success and audit must observe the same primary.
        audit_id = uuid4()
        try:
            with transaction.atomic():
                response = super().dispatch(request, *args, **kwargs)
                context = self.act_as_context
                successful_redirect = (
                    response.status_code == 302
                    and request.method in SAFE_METHODS
                    and context is not None
                    and context.operation.resource_type in {"file_asset", "inline_asset"}
                )
                if (200 <= response.status_code < 300 or successful_redirect) and context is not None:
                    # Validate serialization before committing content + audit.
                    if hasattr(response, "render"):
                        response.render()
                    data = getattr(response, "data", None)
                    resource_id = parse_uuid(data.get("id") or data.get("asset_id")) if isinstance(data, dict) else None
                    resource_id = resource_id or context.resource_id
                    if resource_id is None and request.method not in SAFE_METHODS:
                        resource_id = context.issue_id or (context.project.id if context.project else None)
                    if resource_id is None and request.method not in SAFE_METHODS:
                        raise RuntimeError("Act-as content response is missing its resource ID")
                    context = replace(
                        context,
                        resource_id=resource_id,
                        issue_id=resource_id if context.operation.resource_type == "issue" else context.issue_id,
                    )
                    append_audit(
                        self.request,
                        audit_id=audit_id,
                        response_status=response.status_code,
                        context=context,
                        actor=context.actor,
                        token=self.act_as_token,
                    )
                elif response.status_code < 400:
                    raise RuntimeError("An unaudited or redirected act-as operation cannot succeed")
                else:
                    # Also roll back handlers that returned a 4xx after writing.
                    transaction.set_rollback(True)
            if response.status_code >= 400:
                append_audit(
                    self.request,
                    audit_id=audit_id,
                    response_status=response.status_code,
                    context=self.act_as_context,
                    actor=getattr(self, "act_as_actor", None),
                    token=getattr(self, "act_as_token", None),
                    error_code=getattr(self, "act_as_error_code", "request_failed"),
                )
            response[AUDIT_HEADER] = str(audit_id)
            if context is not None:
                response["X-PlanePlus-Act-As-User"] = str(context.target.id)
            return response
        except Exception:
            # Never let a content write succeed without a durable audit row.
            logger.exception("Act-as transaction/audit failed", extra={"audit_id": str(audit_id)})
            response = JsonResponse(
                {"error_code": "act_as_audit_unavailable", "error": "The audited operation could not be completed."},
                status=503,
            )
            try:
                append_audit(
                    getattr(self, "request", request),
                    audit_id=audit_id,
                    response_status=503,
                    context=self.act_as_context,
                    actor=getattr(self, "act_as_actor", None),
                    token=getattr(self, "act_as_token", None),
                    error_code="act_as_audit_unavailable",
                )
                response[AUDIT_HEADER] = str(audit_id)
            except DatabaseError:
                logger.exception("Act-as failure audit could not be persisted")
            return response


class ActAsBoundaryMiddleware:
    """Never silently ignore delegation headers on session/admin/unsupported APIs."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, callback, callback_args, callback_kwargs):
        if request.method == "OPTIONS" or not has_act_as_headers(request):
            return None
        view_class = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
        if (
            request.path.startswith("/api/v1/")
            and isinstance(view_class, type)
            and issubclass(view_class, ActAsAPIMixin)
        ):
            return None
        audit_id = uuid4()
        try:
            append_audit(
                request,
                audit_id=audit_id,
                response_status=403,
                error_code="act_as_operation_not_supported",
            )
        except DatabaseError:
            logger.exception("Could not audit rejected act-as boundary request")
            return JsonResponse({"error_code": "act_as_audit_unavailable"}, status=503)
        response = JsonResponse({"error_code": "act_as_operation_not_supported"}, status=403)
        response[AUDIT_HEADER] = str(audit_id)
        return response

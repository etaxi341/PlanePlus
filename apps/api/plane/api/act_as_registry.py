# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Reviewed public API operations. An unlisted endpoint always fails closed.

Keep this registry independent of view imports (the base view imports act_as).
The module and exact class name are both checked, not merely a URL prefix.
"""

from django.db.models import Q

from plane.api.act_as import ContentOperation, ActAsError, parse_uuid
from plane.db import models as db


REGISTRY = {}


def register(module, classes, resource, methods, fields=(), scope="project"):
    for name in classes.split():
        for method in methods.split():
            REGISTRY[(f"plane.api.views.{module}", name, method)] = ContentOperation(
                f"{resource}.{method.lower()}",
                resource,
                frozenset(fields),
                scope,
            )


register("project", "ProjectListCreateAPIEndpoint ProjectListLiteAPIEndpoint", "project", "GET HEAD", scope="workspace")
register("project", "ProjectDetailAPIEndpoint", "project", "GET HEAD")
register("project", "ProjectSummaryAPIEndpoint", "project", "GET HEAD")
register(
    "project", "ProjectDetailAPIEndpoint", "project", "PATCH", {"name", "description", "description_text", "logo_props"}
)
register(
    "member",
    "WorkspaceMemberAPIEndpoint WorkspaceMemberLiteAPIEndpoint",
    "workspace_member",
    "GET HEAD",
    scope="workspace",
)
register(
    "member",
    "ProjectMemberListCreateAPIEndpoint ProjectMemberDetailAPIEndpoint ProjectMemberLiteAPIEndpoint",
    "project_member",
    "GET HEAD",
)
register("issue", "IssueListCreateAPIEndpoint IssueDetailAPIEndpoint", "issue", "GET HEAD")
register("issue", "IssueSearchEndpoint", "issue", "GET HEAD", scope="workspace")
register("issue", "IssueCommentListCreateAPIEndpoint IssueCommentDetailAPIEndpoint", "issue_comment", "GET HEAD")
register("issue", "IssueLinkListCreateAPIEndpoint IssueLinkDetailAPIEndpoint", "issue_link", "GET HEAD")
register("issue", "IssueActivityListAPIEndpoint IssueActivityDetailAPIEndpoint", "issue_activity", "GET HEAD")
register("issue", "IssueAttachmentListCreateAPIEndpoint IssueAttachmentDetailAPIEndpoint", "file_asset", "GET HEAD")
register("issue", "IssueAttachmentListCreateAPIEndpoint", "file_asset", "POST", {"name", "size", "type"})
register("issue", "IssueAttachmentDetailAPIEndpoint", "file_asset", "PATCH DELETE")
register("impersonation", "WorkItemInlineAssetEndpoint", "inline_asset", "GET HEAD")
register("issue", "IssueRelationListCreateAPIEndpoint", "issue_relation", "GET HEAD")
register("issue", "IssueRelationListCreateAPIEndpoint", "issue_relation", "POST", {"relation_type", "issues"})
register("issue", "IssueDetailAPIEndpoint", "issue", "DELETE")
register("issue", "IssueCommentDetailAPIEndpoint", "issue_comment", "DELETE")
register("issue", "IssueLinkDetailAPIEndpoint", "issue_link", "DELETE")
register("issue", "LabelListCreateAPIEndpoint LabelDetailAPIEndpoint", "label", "GET HEAD")
register(
    "issue",
    "LabelListCreateAPIEndpoint",
    "label",
    "POST",
    {"name", "description", "color", "parent", "sort_order", "external_id", "external_source"},
)
register(
    "issue",
    "LabelDetailAPIEndpoint",
    "label",
    "PATCH",
    {"name", "description", "color", "parent", "sort_order", "external_id", "external_source"},
)
register("issue", "LabelDetailAPIEndpoint", "label", "DELETE")
register("state", "StateListCreateAPIEndpoint StateDetailAPIEndpoint", "state", "GET HEAD")
register(
    "state",
    "StateListCreateAPIEndpoint",
    "state",
    "POST",
    {"name", "description", "color", "group", "sequence", "default", "external_id", "external_source"},
)
register(
    "state",
    "StateDetailAPIEndpoint",
    "state",
    "PATCH",
    {"name", "description", "color", "group", "sequence", "default", "external_id", "external_source"},
)
register("state", "StateDetailAPIEndpoint", "state", "DELETE")
register(
    "cycle",
    "CycleListCreateAPIEndpoint CycleListLiteAPIEndpoint CycleDetailAPIEndpoint "
    "CycleArchiveUnarchiveAPIEndpoint CycleIssueListCreateAPIEndpoint CycleIssueDetailAPIEndpoint",
    "cycle",
    "GET HEAD",
)
register(
    "cycle",
    "CycleListCreateAPIEndpoint",
    "cycle",
    "POST",
    {"name", "description", "start_date", "end_date", "owned_by", "external_id", "external_source", "timezone"},
)
register(
    "cycle",
    "CycleDetailAPIEndpoint",
    "cycle",
    "PATCH",
    {"name", "description", "start_date", "end_date", "owned_by", "external_id", "external_source", "timezone"},
)
register("cycle", "CycleDetailAPIEndpoint", "cycle", "DELETE")
register("cycle", "CycleArchiveUnarchiveAPIEndpoint", "cycle", "POST DELETE")
register("cycle", "CycleIssueListCreateAPIEndpoint", "cycle_issue", "POST", {"issues"})
register("cycle", "CycleIssueDetailAPIEndpoint", "cycle_issue", "DELETE")
register(
    "module",
    "ModuleListCreateAPIEndpoint ModuleListLiteAPIEndpoint ModuleDetailAPIEndpoint "
    "ModuleArchiveUnarchiveAPIEndpoint ModuleIssueListCreateAPIEndpoint ModuleIssueDetailAPIEndpoint",
    "module",
    "GET HEAD",
)
register(
    "module",
    "ModuleListCreateAPIEndpoint",
    "module",
    "POST",
    {
        "name",
        "description",
        "start_date",
        "target_date",
        "status",
        "lead",
        "members",
        "external_id",
        "external_source",
        "sort_order",
    },
)
register(
    "module",
    "ModuleDetailAPIEndpoint",
    "module",
    "PATCH",
    {
        "name",
        "description",
        "start_date",
        "target_date",
        "status",
        "lead",
        "members",
        "external_id",
        "external_source",
        "sort_order",
    },
)
register("module", "ModuleDetailAPIEndpoint", "module", "DELETE")
register("module", "ModuleArchiveUnarchiveAPIEndpoint", "module", "POST DELETE")
register("module", "ModuleIssueListCreateAPIEndpoint", "module_issue", "POST", {"issues"})
register("module", "ModuleIssueDetailAPIEndpoint", "module_issue", "DELETE")
register("estimate", "ProjectEstimateAPIEndpoint EstimatePointListCreateAPIEndpoint", "estimate", "GET HEAD")
register(
    "estimate",
    "ProjectEstimateAPIEndpoint",
    "estimate",
    "POST PATCH",
    {"name", "description", "type", "last_used", "estimate_id"},
)
register("estimate", "ProjectEstimateAPIEndpoint", "estimate", "DELETE")
register("estimate", "EstimatePointListCreateAPIEndpoint", "estimate_point", "POST", {"estimate_points"})
register("estimate", "EstimatePointDetailAPIEndpoint", "estimate_point", "PATCH", {"key", "value", "description"})
register("estimate", "EstimatePointDetailAPIEndpoint", "estimate_point", "DELETE")
register("intake", "IntakeIssueListCreateAPIEndpoint IntakeIssueDetailAPIEndpoint", "intake_issue", "GET HEAD")
register("intake", "IntakeIssueListCreateAPIEndpoint", "intake_issue", "POST", {"issue"})
register(
    "intake",
    "IntakeIssueDetailAPIEndpoint",
    "intake_issue",
    "PATCH",
    {"issue", "status", "duplicate_to", "snoozed_till"},
)
register("intake", "IntakeIssueDetailAPIEndpoint", "intake_issue", "DELETE")


def visible_issues(queryset, user):
    """Normal project membership plus PlanePlus guest-own-item visibility."""
    return queryset.filter(
        Q(project__project_projectmember__role__gte=15) | Q(project__guest_view_all_features=True) | Q(created_by=user),
        project__project_projectmember__member=user,
        project__project_projectmember__is_active=True,
        project__project_projectmember__deleted_at__isnull=True,
    ).distinct()


def project_visibility(request):
    if not getattr(request, "act_as_context", None):
        return Q()
    memberships = db.ProjectMember.objects.filter(member=request.user, is_active=True).values("project_id")
    return Q(network=2) | Q(id__in=memberships)


def scoped_object(model, value, workspace, project, **extra):
    pk = parse_uuid(value)
    obj = model.objects.filter(id=pk, workspace=workspace, project=project, **extra).first() if pk else None
    if obj is None:
        raise ActAsError("act_as_resource_forbidden", "A referenced resource is not available in this project.")
    return obj


def validate_read_scope(request, view, target, project, operation):
    if project:
        validate_url_scope(view, project.workspace, project, operation)
    # Aggregate family endpoints include work items. Guests must not obtain
    # hidden item counts/details through cycles, modules, or project summaries.
    if project and operation.resource_type not in {
        "project",
        "project_member",
        "issue",
        "issue_comment",
        "issue_link",
        "file_asset",
        "inline_asset",
    }:
        if not db.ProjectMember.objects.filter(project=project, member=target, is_active=True, role__gte=15).exists():
            raise ActAsError("act_as_target_permission_denied", "This resource requires project member access.")
    if (
        type(view).__name__ == "ProjectSummaryAPIEndpoint"
        and not db.ProjectMember.objects.filter(
            project=project,
            member=target,
            is_active=True,
            role__gte=15,
        ).exists()
    ):
        raise ActAsError("act_as_target_permission_denied", "Project summaries require project member access.")
    if operation.resource_type == "issue_comment" and view.kwargs.get("pk"):
        comment = scoped_object(db.IssueComment, view.kwargs["pk"], project.workspace, project)
        if db.ProjectMember.objects.filter(project=project, member=target, role=5, is_active=True).exists():
            if comment.access != "EXTERNAL" and comment.actor_id != target.id:
                raise ActAsError("act_as_target_permission_denied", "The target cannot read this internal comment.")
    if operation.resource_type == "file_asset" and view.kwargs.get("pk"):
        scoped_object(
            db.FileAsset,
            view.kwargs["pk"],
            project.workspace,
            project,
            issue_id=view.kwargs.get("issue_id"),
            entity_type=db.FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
        )


def validate_url_scope(view, workspace, project, operation):
    # Bind nested URL IDs, including handlers whose own lookup only uses a PK.
    for key, model in (("cycle_id", db.Cycle), ("module_id", db.Module), ("estimate_id", db.Estimate)):
        if view.kwargs.get(key):
            scoped_object(model, view.kwargs[key], workspace, project)
    pk_model = {
        "state": db.State,
        "label": db.Label,
        "cycle": db.Cycle,
        "module": db.Module,
        "cycle_issue": db.CycleIssue,
        "module_issue": db.ModuleIssue,
        "estimate_point": db.EstimatePoint,
    }.get(operation.resource_type)
    pk = view.kwargs.get("pk") or view.kwargs.get("estimate_point_id")
    if pk_model and pk:
        extra = {key: view.kwargs[key] for key in ("cycle_id", "module_id", "estimate_id") if key in view.kwargs}
        scoped_object(pk_model, pk, workspace, project, **extra)


def validate_write_scope(request, view, target, workspace, project, operation):
    if project is None:
        return
    validate_url_scope(view, workspace, project, operation)
    if operation.resource_type == "file_asset":
        if not db.ProjectMember.objects.filter(project=project, member=target, is_active=True, role__gte=15).exists():
            raise ActAsError("act_as_target_permission_denied", "Attachment writes require project member access.")
        if view.kwargs.get("pk"):
            scoped_object(
                db.FileAsset,
                view.kwargs["pk"],
                workspace,
                project,
                issue_id=view.kwargs.get("issue_id"),
                entity_type=db.FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
            )
    if operation.resource_type == "intake_issue":
        if not db.ProjectMember.objects.filter(project=project, member=target, is_active=True, role__gte=15).exists():
            raise ActAsError("act_as_target_permission_denied", "Intake writes require project member access.")
        nested = request.data.get("issue", {})
        allowed = {"name", "description_html", "priority", "target_date"}
        if not isinstance(nested, dict) or set(nested) - allowed:
            raise ActAsError(
                "act_as_fields_not_supported",
                "Intake issue only supports name, description_html, priority, target_date.", 400,
            )
        if not project.intake_view or not db.Intake.objects.filter(project=project, workspace=workspace).exists():
            raise ActAsError("act_as_resource_forbidden", "Intake must already be enabled and initialized.")
        if not db.State.triage_objects.filter(project=project, workspace=workspace).exists():
            raise ActAsError("act_as_resource_forbidden", "The intake triage state must already exist.")
        if request.data.get("duplicate_to"):
            scoped_object(db.Issue, request.data["duplicate_to"], workspace, project)
        if (
            view.kwargs.get("issue_id")
            and not db.IntakeIssue.objects.filter(
                workspace=workspace,
                project=project,
                issue_id=view.kwargs["issue_id"],
            ).exists()
        ):
            raise ActAsError("act_as_resource_forbidden", "The work item is not in this intake.")
    if operation.resource_type == "estimate_point" and request.method == "POST":
        points = request.data.get("estimate_points")
        if (
            not isinstance(points, list)
            or not points
            or any(not isinstance(point, dict) or set(point) - {"key", "value", "description"} for point in points)
        ):
            raise ActAsError(
                "act_as_invalid_payload", "estimate_points must contain only key, value and description.", 400
            )
    if operation.resource_type == "project":
        # Metadata editing is not a route to change network, membership or roles.
        if not db.ProjectMember.objects.filter(project=project, member=target, is_active=True, role=20).exists():
            raise ActAsError(
                "act_as_target_permission_denied", "Project metadata editing requires project admin access."
            )
    for field in ("owned_by", "lead"):
        if request.data.get(field):
            if not db.ProjectMember.objects.filter(
                project=project, member_id=parse_uuid(request.data[field]), is_active=True, member__is_active=True
            ).exists():
                raise ActAsError("act_as_resource_forbidden", "The content owner must be an active project member.")
    if "members" in request.data:
        values = request.data["members"]
        if not isinstance(values, list) or any(parse_uuid(value) is None for value in values):
            raise ActAsError("act_as_invalid_payload", "members must be a list of user UUIDs.", 400)
        if db.ProjectMember.objects.filter(
            project=project, member_id__in=values, is_active=True, member__is_active=True
        ).count() != len(set(values)):
            raise ActAsError("act_as_resource_forbidden", "Module members must be active project members.")
    if request.data.get("parent") and operation.resource_type == "label":
        scoped_object(db.Label, request.data["parent"], workspace, project)
    if "issues" in request.data:
        values = request.data["issues"]
        if not isinstance(values, list) or any(parse_uuid(value) is None for value in values):
            raise ActAsError("act_as_invalid_payload", "issues must be a list of work item UUIDs.", 400)
        if visible_issues(
            db.Issue.issue_objects.filter(workspace=workspace, project=project, id__in=values), target
        ).count() != len(set(values)):
            raise ActAsError("act_as_resource_forbidden", "All work items must be visible in this project.")
    if request.data.get("estimate_id"):
        scoped_object(db.Estimate, request.data["estimate_id"], workspace, project)


def capabilities():
    from plane.api.act_as import WORK_ITEM_FIELDS, COMMENT_FIELDS

    operations = [
        {"view": name, "method": method, "resource": op.resource_type, "fields": sorted(op.fields)}
        for (_, name, method), op in sorted(REGISTRY.items())
        if method != "HEAD"
    ]
    for name, method, resource, fields in (
        ("IssueListCreateAPIEndpoint", "POST", "issue", WORK_ITEM_FIELDS),
        ("IssueDetailAPIEndpoint", "PATCH", "issue", WORK_ITEM_FIELDS),
        ("IssueCommentListCreateAPIEndpoint", "POST", "issue_comment", COMMENT_FIELDS),
        ("IssueCommentDetailAPIEndpoint", "PATCH", "issue_comment", COMMENT_FIELDS),
        ("IssueLinkListCreateAPIEndpoint", "POST", "issue_link", {"title", "url"}),
        ("IssueLinkDetailAPIEndpoint", "PATCH", "issue_link", {"title", "url", "metadata"}),
        ("PublicProjectJoinEndpoint", "POST", "project_member", set()),
    ):
        operations.append({"view": name, "method": method, "resource": resource, "fields": sorted(fields)})
    return operations

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
from plane.utils.permissions.issue import guest_issue_visibility


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
register("issue", "WorkspaceIssueAPIEndpoint", "issue", "GET HEAD")
register("issue", "IssueCommentListCreateAPIEndpoint IssueCommentDetailAPIEndpoint", "issue_comment", "GET HEAD")
register("issue", "IssueLinkListCreateAPIEndpoint IssueLinkDetailAPIEndpoint", "issue_link", "GET HEAD")
register("issue", "IssueActivityListAPIEndpoint IssueActivityDetailAPIEndpoint", "issue_activity", "GET HEAD")
register("issue", "IssueAttachmentListCreateAPIEndpoint IssueAttachmentDetailAPIEndpoint", "file_asset", "GET HEAD")
register(
    "issue",
    "IssueAttachmentListCreateAPIEndpoint",
    "file_asset",
    "POST",
    {"name", "size", "type", "external_id", "external_source"},
)
register("issue", "IssueAttachmentDetailAPIEndpoint", "file_asset", "PATCH", {"is_uploaded"})
register("issue", "IssueAttachmentDetailAPIEndpoint", "file_asset", "DELETE")
register("asset", "GenericAssetEndpoint", "generic_asset", "GET HEAD", scope="workspace")
register(
    "asset",
    "GenericAssetEndpoint",
    "generic_asset",
    "POST",
    {"name", "size", "type", "project_id", "external_id", "external_source"},
    scope="workspace",
)
register("asset", "GenericAssetEndpoint", "generic_asset", "PATCH", {"is_uploaded", "issue_id"}, scope="workspace")
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
register("cycle", "TransferCycleIssueAPIEndpoint", "cycle_transfer", "POST", {"new_cycle_id"})
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
register("sticky", "StickyViewSet", "sticky", "GET HEAD", scope="workspace")
register(
    "sticky",
    "StickyViewSet",
    "sticky",
    "POST PATCH PUT",
    {
        "name",
        "description",
        "description_html",
        "description_binary",
        "logo_props",
        "color",
        "background_color",
        "sort_order",
    },
    scope="workspace",
)
register("sticky", "StickyViewSet", "sticky", "DELETE", scope="workspace")


def visible_issues(queryset, user):
    """Normal project membership plus PlanePlus guest-own-item visibility."""
    return queryset.filter(guest_issue_visibility(user)).distinct()


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
    # Role permissions belong to the existing endpoint, not a second act-as
    # role hierarchy. Resource scope and guest ticket visibility remain checked.
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
    if operation.resource_type == "intake_issue" and view.kwargs.get("issue_id"):
        intake_issue = db.IntakeIssue.objects.filter(
            workspace=project.workspace,
            project=project,
            issue_id=view.kwargs["issue_id"],
        ).first()
        if intake_issue is None:
            raise ActAsError("act_as_resource_forbidden", "The work item is not in this intake.")
        if not project.guest_view_all_features and intake_issue.created_by_id != target.id:
            if db.ProjectMember.objects.filter(project=project, member=target, is_active=True, role=5).exists():
                raise ActAsError("act_as_target_permission_denied", "The target cannot read this intake work item.")


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
    if operation.resource_type == "cycle_transfer":
        scoped_object(db.Cycle, request.data.get("new_cycle_id"), workspace, project)
    if operation.resource_type == "file_asset":
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
        member = db.ProjectMember.objects.filter(project=project, member=target, is_active=True).first()
        if member is None:
            raise ActAsError("act_as_target_permission_denied", "Intake writes require active project membership.")
        nested = request.data.get("issue", {})
        allowed = {"name", "description_html", "priority", "target_date"}
        if not isinstance(nested, dict) or set(nested) - allowed:
            raise ActAsError(
                "act_as_fields_not_supported",
                "Intake issue only supports name, description_html, priority, target_date.",
                400,
            )
        if not project.intake_view:
            raise ActAsError("act_as_resource_forbidden", "Intake is not enabled for this project.")
        if request.data.get("duplicate_to"):
            scoped_object(db.Issue, request.data["duplicate_to"], workspace, project)
        if view.kwargs.get("issue_id"):
            intake_issue = (
                db.IntakeIssue.objects.filter(
                    workspace=workspace,
                    project=project,
                    issue_id=view.kwargs["issue_id"],
                )
                .select_related("issue")
                .first()
            )
            if intake_issue is None:
                raise ActAsError("act_as_resource_forbidden", "The work item is not in this intake.")
            if member.role == 5:
                if intake_issue.created_by_id != target.id:
                    raise ActAsError(
                        "act_as_target_permission_denied", "Guests can only change their own intake entries."
                    )
                if request.method == "PATCH" and (
                    set(request.data) - {"issue"} or set(nested) - {"name", "description_html"}
                ):
                    raise ActAsError(
                        "act_as_target_permission_denied", "Guests can only edit their intake name and description."
                    )
                if request.method == "DELETE" and intake_issue.issue.created_by_id != target.id:
                    raise ActAsError(
                        "act_as_target_permission_denied", "Guests can only delete their own intake work items."
                    )
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
    # ProjectBasePermission already checks the effective project's/workspace's
    # administrator. The field allowlist still excludes privacy/role changes.
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

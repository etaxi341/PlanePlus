# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.db.models import Q
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import BasePermission, SAFE_METHODS

from plane.db.models import IntakeIssue, Issue, ProjectMember, WorkspaceMember


class AggregateIssueVisibilityPermission(BasePermission):
    """Block unfiltered Cycle/Module aggregates for guests with hidden tickets.

    Direct and delegated reads follow the same rule; guests with full feature
    visibility do not need an artificial member-role restriction.
    """

    def has_permission(self, request, view):
        if request.method not in SAFE_METHODS or not request.user.is_authenticated:
            return True  # Membership/write permissions are enforced by the view.
        return not ProjectMember.objects.filter(
            workspace__slug=view.kwargs.get("slug"),
            project_id=view.kwargs.get("project_id"),
            member=request.user,
            is_active=True,
            role=5,
            project__guest_view_all_features=False,
        ).exists()


def require_issue_access(request, slug, project_id, issue_id, include_archived=False):
    """Shared ticket/asset visibility for direct and delegated API requests.

    Intake ownership is the submission creator, not necessarily an imported
    work item's author. Membership is always required, even for creators.
    """
    member = (
        ProjectMember.objects.select_related("project", "workspace")
        .filter(
            project_id=project_id,
            workspace__slug=slug,
            member=request.user,
            is_active=True,
            project__workspace__slug=slug,
            project__deleted_at__isnull=True,
            project__archived_at__isnull=True,
            workspace__deleted_at__isnull=True,
        )
        .first()
    )
    if (
        member is None
        or not WorkspaceMember.objects.filter(
            workspace__slug=slug,
            member=request.user,
            is_active=True,
        ).exists()
    ):
        raise PermissionDenied("Active workspace and project membership is required.")
    token = getattr(request.successful_authenticator, "api_token", None)
    if token and token.workspace_id is not None and token.workspace_id != member.workspace_id:
        raise PermissionDenied("The API key is restricted to a different workspace.")
    issues = Issue.objects.select_related("state").filter(
        id=issue_id,
        project_id=project_id,
        workspace=member.workspace,
        is_draft=False,
    )
    if not include_archived:
        issues = issues.filter(archived_at__isnull=True)
    issue = issues.first()
    if issue is None:
        raise NotFound("Work item not found in this project.")
    owner_id = issue.created_by_id
    if issue.state and issue.state.group == "triage":
        intake = IntakeIssue.objects.filter(
            workspace=member.workspace,
            project=member.project,
            issue=issue,
            intake__deleted_at__isnull=True,
        ).first()
        if not member.project.intake_view or intake is None:
            raise PermissionDenied("The work item is not available in this intake.")
        owner_id = intake.created_by_id
    if member.role == 5 and not member.project.guest_view_all_features and owner_id != request.user.id:
        raise PermissionDenied("The guest cannot access this work item.")
    return issue, member


def require_upload_access(request, slug, asset):
    """Unbound uploads remain personal; bound assets use ticket permissions."""
    if asset.workspace.slug != slug or asset.entity_type != "ISSUE_ATTACHMENT" or asset.is_deleted or asset.is_archived:
        raise PermissionDenied("This asset is not an available content attachment.")
    token = getattr(request.successful_authenticator, "api_token", None)
    if token and token.workspace_id is not None and token.workspace_id != asset.workspace_id:
        raise PermissionDenied("The API key is restricted to a different workspace.")
    if not WorkspaceMember.objects.filter(
        workspace=asset.workspace, member=request.user, is_active=True, workspace__deleted_at__isnull=True
    ).exists():
        raise PermissionDenied("Active workspace membership is required.")
    if asset.issue_id:
        require_issue_access(request, slug, asset.project_id, asset.issue_id, include_archived=True)
    else:
        if asset.created_by_id != request.user.id:
            raise PermissionDenied("Unbound uploads can only be accessed by their uploader.")
        if (
            asset.project_id
            and not ProjectMember.objects.filter(
                project_id=asset.project_id,
                workspace=asset.workspace,
                member=request.user,
                is_active=True,
                project__workspace=asset.workspace,
                project__archived_at__isnull=True,
                project__deleted_at__isnull=True,
            ).exists()
        ):
            raise PermissionDenied("The uploader is not an active project member.")


def guest_issue_visibility(user):
    """Reusable same-join condition, avoiding extra-role filters on GET."""
    return Q(
        project__project_projectmember__member=user,
        project__project_projectmember__is_active=True,
        project__project_projectmember__deleted_at__isnull=True,
    ) & (
        Q(project__project_projectmember__role__gte=15) | Q(project__guest_view_all_features=True) | Q(created_by=user)
    )

# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from django.db.models import Q

from plane.api.act_as import is_instance_admin, is_workspace_admin
from plane.app.permissions import ROLE
from plane.db.models import ImpersonationAudit, Issue, IssueComment, ProjectMember, Workspace, WorkspaceMember
from .base import BaseAPIView


class ImpersonationProvenanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImpersonationAudit
        fields = (
            "id",
            "actor_id",
            "target_user_id",
            "workspace_id",
            "project_id",
            "issue_id",
            "resource_id",
            "resource_type",
            "action",
            "created_at",
            "succeeded",
            "response_status",
            "error_code",
        )
        read_only_fields = fields


class ImpersonationAuditSerializer(ImpersonationProvenanceSerializer):
    class Meta(ImpersonationProvenanceSerializer.Meta):
        fields = ImpersonationProvenanceSerializer.Meta.fields + (
            "reason",
            "method",
            "path",
            "requested_user",
            "token_id",
        )
        read_only_fields = fields


def check_provenance_access(request, workspace, project_id, issue_id):
    if not WorkspaceMember.objects.filter(workspace=workspace, member=request.user, is_active=True).exists():
        raise PermissionDenied()
    member = (
        ProjectMember.objects.filter(
            workspace=workspace,
            project_id=project_id,
            member=request.user,
            is_active=True,
            project__archived_at__isnull=True,
        )
        .select_related("project")
        .first()
    )
    issue = Issue.issue_objects.filter(id=issue_id, project_id=project_id, workspace=workspace).first()
    if member is None or issue is None:
        raise PermissionDenied()
    if member.role == ROLE.GUEST.value and not member.project.guest_view_all_features:
        if issue.created_by_id != request.user.id:
            raise PermissionDenied()
    return member


def visible_provenance(queryset, member, user):
    if member.role == ROLE.GUEST.value:
        # Do not expose metadata for internal comments invisible to a guest.
        visible_comments = IssueComment.objects.filter(
            Q(access="EXTERNAL") | Q(actor=user),
            project_id=member.project_id,
        ).values("id")
        queryset = queryset.filter(~Q(resource_type="issue_comment") | Q(resource_id__in=visible_comments))
    return queryset


def check_audit_token_scope(request, workspace):
    token = getattr(request.successful_authenticator, "api_token", None)
    if token is not None and token.workspace_id is not None and token.workspace_id != workspace.id:
        raise PermissionDenied()


class ImpersonationAuditDetailEndpoint(BaseAPIView):
    """Immutable provenance by the server-generated ID returned on writes."""

    serializer_class = ImpersonationProvenanceSerializer

    def get(self, request, slug, audit_id):
        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            raise NotFound()
        check_audit_token_scope(request, workspace)
        audit = ImpersonationAudit.objects.filter(id=audit_id, workspace_id=workspace.id).first()
        if audit is None:
            raise NotFound()
        if is_instance_admin(request) or is_workspace_admin(request.user, workspace):
            return Response(ImpersonationAuditSerializer(audit).data)
        if not audit.succeeded:
            raise PermissionDenied()
        member = check_provenance_access(request, workspace, audit.project_id, audit.issue_id)
        if not visible_provenance(ImpersonationAudit.objects.filter(id=audit.id), member, request.user).exists():
            raise PermissionDenied()
        return Response(ImpersonationProvenanceSerializer(audit).data)


class WorkItemImpersonationProvenanceEndpoint(BaseAPIView):
    serializer_class = ImpersonationProvenanceSerializer

    def get(self, request, slug, project_id, issue_id):
        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            raise NotFound()
        check_audit_token_scope(request, workspace)
        member = check_provenance_access(request, workspace, project_id, issue_id)
        queryset = visible_provenance(
            ImpersonationAudit.objects.filter(
                workspace_id=workspace.id,
                project_id=project_id,
                issue_id=issue_id,
                succeeded=True,
            ),
            member,
            request.user,
        )
        return self.paginate(
            request=request,
            queryset=queryset,
            on_results=lambda entries: ImpersonationProvenanceSerializer(entries, many=True).data,
        )

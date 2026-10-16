# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from django.db.models import Q

from django.conf import settings
from django.db import transaction
from django.http import HttpResponseRedirect
from django.utils.cache import patch_vary_headers
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse
from plane.api.act_as import is_instance_admin, is_workspace_admin, authorize_act_as_actor, act_as_content, ActAsError
from plane.app.permissions import ROLE
from plane.db.models import (
    ImpersonationAudit,
    FileAsset,
    Issue,
    IssueComment,
    Project,
    ProjectMember,
    ProjectUserProperty,
    User,
    Workspace,
    WorkspaceMember,
)
from .base import BaseAPIView
from plane.utils.permissions.issue import require_issue_access


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


def check_provenance_access(request, workspace, project_id, issue_id, include_intake=False):
    if include_intake:
        return require_issue_access(request, workspace.slug, project_id, issue_id)[1]
    if not WorkspaceMember.objects.filter(workspace=workspace, member=request.user, is_active=True).exists():
        raise PermissionDenied()
    member = (
        ProjectMember.objects.filter(
            workspace=workspace,
            project_id=project_id,
            member=request.user,
            is_active=True,
            project__archived_at__isnull=True,
            project__deleted_at__isnull=True,
        )
        .select_related("project")
        .first()
    )
    if issue_id is None:
        if member is None or member.role < ROLE.MEMBER.value:
            raise PermissionDenied()
        return member
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
        visible_images = FileAsset.objects.filter(
            Q(entity_type=FileAsset.EntityTypeContext.ISSUE_DESCRIPTION)
            | Q(entity_type=FileAsset.EntityTypeContext.COMMENT_DESCRIPTION, comment_id__in=visible_comments),
            project_id=member.project_id,
            is_uploaded=True,
            is_deleted=False,
            is_archived=False,
        ).values("id")
        queryset = queryset.filter(~Q(resource_type="inline_asset") | Q(resource_id__in=visible_images))
    return queryset


def check_audit_token_scope(request, workspace):
    token = getattr(request.successful_authenticator, "api_token", None)
    if token is not None and token.workspace_id is not None and token.workspace_id != workspace.id:
        raise PermissionDenied()


class ImpersonationAuditDetailEndpoint(BaseAPIView):
    """Immutable provenance by the server-generated ID returned on writes."""

    serializer_class = ImpersonationProvenanceSerializer
    use_read_replica = False

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
    use_read_replica = False

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


class ActAsCapabilitiesSerializer(serializers.Serializer):
    version = serializers.IntegerField(read_only=True)
    enabled = serializers.BooleanField(read_only=True)
    user_header = serializers.CharField(read_only=True)
    reason_header = serializers.CharField(read_only=True)
    audit_header = serializers.CharField(read_only=True)
    reads_as_target = serializers.BooleanField(read_only=True)
    public_project_join = serializers.BooleanField(read_only=True)
    intake_target_date = serializers.BooleanField(read_only=True)
    ticket_inline_assets = serializers.DictField(read_only=True)
    attachments = serializers.DictField(read_only=True)
    operations = serializers.ListField(child=serializers.DictField(), read_only=True)


class ActAsUserResolveSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    is_bot = serializers.BooleanField(read_only=True)
    workspace_id = serializers.UUIDField(read_only=True)
    workspace_member = serializers.DictField(read_only=True)
    eligible = serializers.BooleanField(read_only=True)


class PublicProjectJoinSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    project_id = serializers.UUIDField(read_only=True)
    member_id = serializers.UUIDField(read_only=True)
    role = serializers.IntegerField(read_only=True)
    joined = serializers.BooleanField(read_only=True)


class ActAsCapabilitiesEndpoint(BaseAPIView):
    serializer_class = ActAsCapabilitiesSerializer
    use_read_replica = False

    def get(self, request, slug):
        authorize_act_as_actor(request, slug)
        from plane.api.act_as_registry import capabilities
        from django.urls import get_resolver, URLPattern, URLResolver
        from django.urls.resolvers import RegexPattern
        import re
        from plane.utils.inline_assets import INLINE_URL_MAX_SECONDS

        routes = {}

        def walk(patterns, prefix=""):
            for pattern in patterns:
                suffix = str(pattern.pattern)
                if isinstance(pattern.pattern, RegexPattern):
                    if "(?P<format>" in suffix:
                        continue  # Publish canonical router paths, not .json aliases.
                    suffix = suffix.removeprefix("^").removesuffix("$")
                    suffix = re.sub(r"\(\?P<(\w+)>[^)]+\)", r"<str:\1>", suffix)
                route = prefix + suffix
                if isinstance(pattern, URLResolver):
                    walk(pattern.url_patterns, route)
                elif isinstance(pattern, URLPattern) and route.startswith("api/v1/"):
                    cls = getattr(pattern.callback, "cls", None)
                    if cls:
                        methods = getattr(pattern.callback, "actions", None) or pattern.callback.initkwargs.get(
                            "http_method_names",
                            cls.http_method_names,
                        )
                        for method in methods:
                            routes.setdefault((cls.__name__, method.upper()), []).append("/" + route)

        walk(get_resolver().url_patterns)
        operations = capabilities()
        for operation in operations:
            operation["paths"] = routes.get((operation.pop("view"), operation["method"]), [])
        operations = [operation for operation in operations if operation["paths"]]
        return Response(
            {
                "version": 4,
                "enabled": settings.PLANEPLUS_ENABLE_ACT_AS,
                "user_header": "X-PlanePlus-Act-As-User",
                "reason_header": "X-PlanePlus-Act-As-Reason",
                "audit_header": "X-PlanePlus-Act-As-Audit-Id",
                "reads_as_target": True,
                "public_project_join": True,
                "intake_target_date": True,
                "attachments": {
                    "guest_uploads": True,
                    "intake_uploads": True,
                    "generic_uploads": True,
                    "confirmation_field": "is_uploaded",
                    "generic_bind_field": "issue_id",
                    "transfer": "presigned_multipart_post",
                    "storage_receives_admin_headers": False,
                },
                "ticket_inline_assets": {
                    "supported": True,
                    "https_only": True,
                    "max_expires_in": INLINE_URL_MAX_SECONDS,
                    "redirect_status": 302,
                    "json_query": "redirect=false",
                },
                "operations": operations,
            }
        )


class ActAsUserResolveEndpoint(BaseAPIView):
    serializer_class = ActAsUserResolveSerializer
    use_read_replica = False

    def get(self, request, slug):
        workspace = authorize_act_as_actor(request, slug)
        email = request.query_params.get("email", "").strip()
        if not email or len(email) > 254:
            raise ActAsError("act_as_invalid_email", "An email query parameter is required.", 400)
        users = list(
            User.objects.filter(
                email__iexact=email,
                member_workspace__workspace=workspace,
                member_workspace__deleted_at__isnull=True,
            ).distinct()[:2]
        )
        if not users:
            raise NotFound({"error_code": "act_as_user_not_found"})
        if len(users) != 1:
            raise ActAsError("act_as_ambiguous_user", "The email does not uniquely identify a workspace user.", 409)
        user = users[0]
        member = WorkspaceMember.objects.get(workspace=workspace, member=user)
        return Response(
            {
                "id": str(user.id),
                "email": user.email,
                "is_active": user.is_active,
                "is_bot": user.is_bot,
                "workspace_id": str(workspace.id),
                "workspace_member": {"is_active": member.is_active, "role": member.role},
                "eligible": user.is_active and not user.is_bot and member.is_active,
            }
        )


class PublicProjectJoinEndpoint(BaseAPIView):
    """Join only yourself (or a validated delegate), never assign arbitrary roles."""

    serializer_class = PublicProjectJoinSerializer

    @act_as_content("project.join", "project_member", set(), scope="join")
    def post(self, request, slug, project_id):
        if request.data:
            raise ActAsError("act_as_invalid_payload", "Public join accepts an empty JSON object only.", 400)
        with transaction.atomic():
            # Serialize concurrent joins and project network/archive changes.
            project = Project.objects.select_for_update().filter(id=project_id, workspace__slug=slug).first()
            if project is None or project.network != 2 or project.archived_at is not None:
                raise ActAsError("act_as_public_project_required", "Only active public projects can be joined.")
            member = (
                WorkspaceMember.objects.select_for_update()
                .filter(
                    workspace=project.workspace,
                    member=request.user,
                    is_active=True,
                    member__is_active=True,
                    member__is_bot=False,
                )
                .first()
            )
            if member is None:
                raise PermissionDenied()
            existing = (
                ProjectMember.all_objects.filter(project=project, member=request.user).order_by("-created_at").first()
            )
            if existing is not None:
                if existing.deleted_at is not None or not existing.is_active:
                    raise ActAsError(
                        "act_as_membership_inactive", "An administrator must restore a removed membership."
                    )
                return Response(
                    {
                        "id": str(existing.id),
                        "project_id": str(project.id),
                        "member_id": str(request.user.id),
                        "role": existing.role,
                        "joined": False,
                    }
                )
            # A workspace admin does NOT become project admin through auto-join.
            membership = ProjectMember(
                project=project,
                workspace=project.workspace,
                member=request.user,
                role=min(member.role, 15),
                created_by=request.user,
                updated_by=request.user,
            )
            # The existing ProjectMember.save unconditionally creates preferences;
            # use Plane's bulk-join pattern so an existing preference cannot break
            # a legitimate first membership. The project lock serializes joins.
            ProjectMember.objects.bulk_create([membership])
            ProjectUserProperty.objects.get_or_create(
                project=project,
                workspace=project.workspace,
                user=request.user,
                defaults={"created_by": request.user, "updated_by": request.user},
            )
            return Response(
                {
                    "id": str(membership.id),
                    "project_id": str(project.id),
                    "member_id": str(request.user.id),
                    "role": membership.role,
                    "joined": True,
                },
                status=201,
            )


class SignedInlineAssetSerializer(serializers.Serializer):
    asset_id = serializers.UUIDField(read_only=True)
    url = serializers.URLField(read_only=True)
    expires_in = serializers.IntegerField(read_only=True)


class WorkItemInlineAssetEndpoint(BaseAPIView):
    """Read an uploaded image only through its current, authorized ticket body."""

    serializer_class = SignedInlineAssetSerializer
    use_read_replica = False

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        response["Referrer-Policy"] = "no-referrer"
        patch_vary_headers(response, ["X-API-Key", "X-PlanePlus-Act-As-User"])
        # Signed URLs are bearer credentials, not ordinary content to persist
        # in the existing API-token response-body logger.
        response.planeplus_sensitive_body = True
        return response

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "redirect",
                OpenApiTypes.BOOL,
                OpenApiParameter.QUERY,
                description="Default true: 302; false: JSON with a short-lived signed HTTPS URL.",
            ),
            OpenApiParameter("Location", OpenApiTypes.URI, OpenApiParameter.HEADER, response=[302]),
        ],
        responses={200: SignedInlineAssetSerializer, 302: OpenApiResponse(description="Signed HTTPS storage redirect")},
    )
    def get(self, request, slug, project_id, issue_id, asset_id, comment_id=None):
        from plane.utils.inline_assets import INLINE_IMAGE_TYPES, body_references_asset, signed_inline_asset_url

        redirect = request.query_params.get("redirect", "true")
        if redirect not in {"true", "false"}:
            raise ActAsError("inline_asset_invalid_redirect", "redirect must be true or false.", 400)
        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            raise NotFound()
        check_audit_token_scope(request, workspace)
        member = check_provenance_access(request, workspace, project_id, issue_id, include_intake=True)
        asset = FileAsset.objects.filter(
            id=asset_id,
            workspace=workspace,
            project_id=project_id,
            is_uploaded=True,
            is_deleted=False,
            is_archived=False,
            page_id__isnull=True,
            draft_issue_id__isnull=True,
            user_id__isnull=True,
        ).first()
        if asset is None:
            raise NotFound()
        attributes = asset.attributes if isinstance(asset.attributes, dict) else {}
        metadata = asset.storage_metadata if isinstance(asset.storage_metadata, dict) else {}
        content_type = str(metadata.get("ContentType") or attributes.get("type") or "").split(";", 1)[0].lower().strip()
        if content_type not in INLINE_IMAGE_TYPES:
            raise PermissionDenied("This asset is not a supported inline image.")
        if asset.entity_type == FileAsset.EntityTypeContext.ISSUE_DESCRIPTION:
            if asset.issue_id != issue_id or asset.comment_id is not None or comment_id is not None:
                raise PermissionDenied("This image does not belong to this ticket description.")
            body = Issue.objects.get(id=issue_id, project_id=project_id, workspace=workspace).description_html
        elif asset.entity_type == FileAsset.EntityTypeContext.COMMENT_DESCRIPTION:
            comment = IssueComment.objects.filter(
                id=asset.comment_id,
                issue_id=issue_id,
                project_id=project_id,
                workspace=workspace,
            ).first()
            if comment is None or asset.issue_id not in {None, issue_id} or (comment_id and comment_id != comment.id):
                raise PermissionDenied("This image does not belong to a current comment on this ticket.")
            if member.role == ROLE.GUEST.value and comment.access != "EXTERNAL" and comment.actor_id != request.user.id:
                raise PermissionDenied("The target cannot read this internal comment.")
            body = comment.comment_html
        else:
            raise PermissionDenied("Only ticket-description and comment-description images are supported.")
        if not body_references_asset(body, asset):
            raise PermissionDenied("The current body does not reference this inline image.")
        url, expires_in = signed_inline_asset_url(request, asset)
        if redirect == "false":
            return Response({"asset_id": str(asset.id), "url": url, "expires_in": expires_in})
        return HttpResponseRedirect(url)

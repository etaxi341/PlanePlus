# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.api.views.impersonation import (
    ImpersonationAuditDetailEndpoint,
    WorkItemImpersonationProvenanceEndpoint,
    ActAsCapabilitiesEndpoint,
    ActAsUserResolveEndpoint,
    PublicProjectJoinEndpoint,
    WorkItemInlineAssetEndpoint,
)


urlpatterns = [
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/work-items/<uuid:issue_id>/inline-assets/<uuid:asset_id>/",
        WorkItemInlineAssetEndpoint.as_view(http_method_names=["get", "head"]), name="work-item-inline-asset",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/work-items/<uuid:issue_id>/comments/<uuid:comment_id>/inline-assets/<uuid:asset_id>/",
        WorkItemInlineAssetEndpoint.as_view(http_method_names=["get", "head"]), name="comment-inline-asset",
    ),
    path(
        "workspaces/<str:slug>/act-as-capabilities/",
        ActAsCapabilitiesEndpoint.as_view(http_method_names=["get"]),
        name="act-as-capabilities",
    ),
    path(
        "workspaces/<str:slug>/act-as-users/resolve/",
        ActAsUserResolveEndpoint.as_view(http_method_names=["get"]),
        name="act-as-user-resolve",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/join/",
        PublicProjectJoinEndpoint.as_view(http_method_names=["post"]),
        name="public-project-join",
    ),
    path(
        "workspaces/<str:slug>/act-as-audit/<uuid:audit_id>/",
        ImpersonationAuditDetailEndpoint.as_view(http_method_names=["get"]),
        name="act-as-audit-detail",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/work-items/<uuid:issue_id>/act-as-provenance/",
        WorkItemImpersonationProvenanceEndpoint.as_view(http_method_names=["get"]),
        name="work-item-act-as-provenance",
    ),
]

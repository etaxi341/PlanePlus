# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.api.views.impersonation import (
    ImpersonationAuditDetailEndpoint,
    WorkItemImpersonationProvenanceEndpoint,
)


urlpatterns = [
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

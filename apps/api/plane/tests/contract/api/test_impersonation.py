# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from concurrent.futures import ThreadPoolExecutor
import importlib
import json
import re
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.core.cache import cache
from django.db import DatabaseError, IntegrityError, close_old_connections, connection, transaction
from django.urls import reverse, resolve
from django.utils import timezone
from rest_framework.test import APIClient

from plane.api.act_as import AUDIT_HEADER
from plane.api.views.issue import IssueCommentSerializer
from plane.db.models import (
    APIToken,
    ImpersonationAudit,
    Issue,
    IssueActivity,
    IssueComment,
    IssueLink,
    Label,
    IssueLabel,
    Project,
    ProjectMember,
    State,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.license.models import Instance, InstanceAdmin
from plane.utils.act_as import get_act_as_context
from plane.db import models as db_models


pytestmark = [pytest.mark.contract, pytest.mark.django_db]
GUARD = importlib.import_module("plane.db.migrations.0123_impersonation_audit")


@pytest.fixture(autouse=True)
def isolated_act_as(settings, monkeypatch, db):
    settings.PLANEPLUS_ENABLE_ACT_AS = True
    settings.DEBUG = False
    settings.WEB_URL = "http://testserver"
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    settings.STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}}
    cache.clear()
    tasks = SimpleNamespace(activity=Mock(), webhook=Mock(), crawl=Mock(), logs=Mock())
    monkeypatch.setattr("plane.api.views.issue.issue_activity.delay", tasks.activity)
    monkeypatch.setattr("plane.api.views.issue.model_activity.delay", tasks.webhook)
    monkeypatch.setattr("plane.api.views.issue.crawl_work_item_link_title.delay", tasks.crawl)
    monkeypatch.setattr("plane.middleware.logger.process_logs.delay", tasks.logs)
    monkeypatch.setattr("plane.db.mixins.soft_delete_related_objects.delay", Mock())
    monkeypatch.setattr("plane.api.views.issue.get_asset_object_metadata.delay", Mock())
    # The repository's default --nomigrations skips RunSQL. Exercise the REAL
    # PostgreSQL guard even in that mode, then remove it only for pytest's flush.
    with connection.cursor() as cursor:
        cursor.execute(GUARD.DROP_AUDIT_GUARD_SQL)
        cursor.execute(GUARD.AUDIT_GUARD_SQL)
    yield tasks
    with connection.cursor() as cursor:
        cursor.execute(GUARD.DROP_AUDIT_GUARD_SQL)
    cache.clear()
    assert get_act_as_context() is None


@pytest.fixture
def team():
    admin = User.objects.create(email="admin@act-as.example", username="admin", is_active=True)
    target = User.objects.create(email="target@act-as.example", username="target", is_active=True)
    other = User.objects.create(email="other@act-as.example", username="other", is_active=True)
    workspace = Workspace.objects.create(name="Act As", slug="act-as", owner=admin)
    for user, role in ((admin, 20), (target, 15), (other, 15)):
        WorkspaceMember.objects.create(workspace=workspace, member=user, role=role, is_active=True)
    project = Project.objects.create(workspace=workspace, name="Content", identifier="CONTENT", network=0)
    for user, role in ((admin, 20), (target, 15), (other, 15)):
        ProjectMember.objects.create(project=project, member=user, role=role, is_active=True)
    state = State.objects.create(workspace=workspace, project=project, name="Todo", group="unstarted", default=True)
    issue = Issue.objects.create(
        project=project,
        state=state,
        name="Existing work item",
        created_by_id=target.id,
    )
    issue.save(created_by_id=target.id)
    token = APIToken.objects.create(user=admin)
    return SimpleNamespace(
        admin=admin,
        target=target,
        other=other,
        workspace=workspace,
        project=project,
        state=state,
        issue=issue,
        token=token,
    )


def client_for(token):
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=token.token)
    return client


def headers(user, reason="chatbot: conversation-123"):
    return {"HTTP_X_PLANEPLUS_ACT_AS_USER": str(user.id), "HTTP_X_PLANEPLUS_ACT_AS_REASON": reason}


def items(team):
    return f"/api/v1/workspaces/{team.workspace.slug}/projects/{team.project.id}/work-items/"


def comments(team):
    return f"{items(team)}{team.issue.id}/comments/"


def audit_for(response):
    return ImpersonationAudit.objects.get(id=response[AUDIT_HEADER])


def test_normal_api_without_header_is_unchanged(team, settings, isolated_act_as):
    settings.PLANEPLUS_ENABLE_ACT_AS = False
    response = client_for(team.token).post(comments(team), {"comment_html": "<p>Normal</p>"}, format="json")
    assert response.status_code == 201, response.data
    comment = IssueComment.objects.get(id=response.data["id"])
    assert comment.actor_id == team.admin.id
    assert comment.created_by_id == team.admin.id
    assert AUDIT_HEADER not in response
    assert not ImpersonationAudit.objects.exists()
    assert isolated_act_as.webhook.call_args.kwargs["actor_id"] == team.admin.id


def test_comment_has_target_author_and_durable_provenance(team, isolated_act_as, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        response = client_for(team.token).post(
            comments(team),
            {"comment_html": "<p>On behalf of a user</p>"},
            format="json",
            **headers(team.target),
        )
    assert response.status_code == 201, response.data
    comment = IssueComment.objects.get(id=response.data["id"])
    assert comment.actor_id == team.target.id
    assert comment.created_by_id == team.target.id
    assert comment.updated_by_id == team.target.id
    assert comment.description.created_by_id == team.target.id
    audit = audit_for(response)
    assert audit.actor_id == team.admin.id
    assert audit.target_user_id == team.target.id
    assert audit.token_id == team.token.id
    assert audit.workspace_id == team.workspace.id
    assert audit.project_id == team.project.id
    assert audit.issue_id == team.issue.id
    assert audit.resource_id == comment.id
    assert audit.resource_type == "issue_comment"
    assert audit.action == "comment.create"
    assert audit.reason == "chatbot: conversation-123"
    assert audit.succeeded and audit.response_status == 201
    assert isolated_act_as.activity.call_args.kwargs["actor_id"] == str(team.target.id)
    assert isolated_act_as.activity.call_args.kwargs["notification"] is True
    assert isolated_act_as.webhook.call_args.kwargs["actor_id"] == team.target.id
    assert get_act_as_context() is None


def test_work_item_create_and_update_use_target_without_rewriting_creator(team, django_capture_on_commit_callbacks):
    client = client_for(team.token)
    with django_capture_on_commit_callbacks(execute=True):
        created = client.post(items(team), {"name": "Delegated item"}, format="json", **headers(team.target))
    assert created.status_code == 201, created.data
    issue = Issue.objects.get(id=created.data["id"])
    assert issue.created_by_id == team.target.id
    assert audit_for(created).resource_id == issue.id
    patched = client.patch(
        f"{items(team)}{issue.id}/",
        {"name": "Edited by another target"},
        format="json",
        **headers(team.other),
    )
    assert patched.status_code == 200, patched.data
    issue.refresh_from_db()
    assert issue.created_by_id == team.target.id
    assert issue.updated_by_id == team.other.id
    assert audit_for(patched).action == "work_item.update"


def test_related_work_item_author_fields_use_effective_writer(team):
    label = Label.objects.create(project=team.project, name="Delegated", color="#ffffff")
    client = client_for(team.token)
    created = client.post(
        items(team), {"name": "Relations", "labels": [str(label.id)]}, format="json", **headers(team.target)
    )
    assert created.status_code == 201, created.data
    assert IssueLabel.objects.get(issue_id=created.data["id"]).created_by_id == team.target.id
    updated = client.patch(
        f"{items(team)}{created.data['id']}/", {"labels": [str(label.id)]}, format="json", **headers(team.other)
    )
    assert updated.status_code == 200, updated.data
    assert IssueLabel.objects.get(issue_id=created.data["id"]).created_by_id == team.other.id


def test_comment_edit_matches_normal_owner_or_admin_permissions(team):
    client = client_for(team.token)
    created = client.post(comments(team), {"comment_html": "<p>Original</p>"}, format="json", **headers(team.target))
    url = f"{comments(team)}{created.data['id']}/"
    denied = client.patch(url, {"comment_html": "<p>Not mine</p>"}, format="json", **headers(team.other))
    assert denied.status_code == 403
    allowed = client.patch(url, {"comment_html": "<p>Mine</p>"}, format="json", **headers(team.target))
    assert allowed.status_code == 200, allowed.data
    comment = IssueComment.objects.get(id=created.data["id"])
    assert comment.actor_id == comment.created_by_id == comment.updated_by_id == team.target.id
    ProjectMember.objects.filter(project=team.project, member=team.other).update(role=20)
    admin_edit = client.patch(url, {"comment_html": "<p>Project admin</p>"}, format="json", **headers(team.other))
    assert admin_edit.status_code == 200
    comment.refresh_from_db()
    assert comment.created_by_id == team.target.id and comment.updated_by_id == team.other.id


def test_non_admin_cannot_impersonate_even_self(team):
    token = APIToken.objects.create(user=team.target)
    response = client_for(token).post(
        comments(team), {"comment_html": "<p>No</p>"}, format="json", **headers(team.target)
    )
    assert response.status_code == 403
    assert response.data["error_code"] == "act_as_admin_required"
    audit = audit_for(response)
    assert not audit.succeeded and audit.actor_id == team.target.id and audit.workspace_id == team.workspace.id
    assert not IssueComment.objects.exists()


@pytest.mark.parametrize("invalid", ["not-a-uuid", "", str(uuid4())])
def test_invalid_target_has_clear_error_and_never_falls_back(team, invalid):
    response = client_for(team.token).post(
        comments(team),
        {"comment_html": "<p>No fallback</p>"},
        format="json",
        HTTP_X_PLANEPLUS_ACT_AS_USER=invalid,
    )
    assert response.status_code == 400
    assert response.data["error_code"] == "act_as_invalid_user"
    assert not IssueComment.objects.exists()
    audit = audit_for(response)
    assert not audit.succeeded and audit.target_user_id is None
    assert audit.requested_user == invalid


@pytest.mark.parametrize("field", ["is_active", "is_bot"])
def test_inactive_or_bot_target_is_rejected(team, field):
    setattr(team.target, field, field == "is_bot")
    team.target.save()
    response = client_for(team.token).post(items(team), {"name": "No"}, format="json", **headers(team.target))
    assert response.status_code == 400
    assert response.data["error_code"] == "act_as_invalid_user"


def test_target_must_be_active_workspace_and_project_member(team):
    client = client_for(team.token)
    WorkspaceMember.objects.filter(workspace=team.workspace, member=team.target).update(is_active=False)
    response = client.post(items(team), {"name": "No"}, format="json", **headers(team.target))
    assert response.status_code == 403
    assert response.data["error_code"] == "act_as_target_not_workspace_member"
    WorkspaceMember.objects.filter(workspace=team.workspace, member=team.target).update(is_active=True)
    ProjectMember.objects.filter(project=team.project, member=team.target).update(is_active=False)
    response = client.post(items(team), {"name": "No"}, format="json", **headers(team.target))
    assert response.status_code == 403
    assert response.data["error_code"] == "act_as_target_permission_denied"


def test_project_roles_are_not_elevated_and_guests_can_comment_only_where_allowed(team):
    ProjectMember.objects.filter(project=team.project, member=team.target).update(role=5)
    client = client_for(team.token)
    denied = client.post(items(team), {"name": "No elevation"}, format="json", **headers(team.target))
    assert denied.status_code == 403
    allowed = client.post(comments(team), {"comment_html": "<p>Own item</p>"}, format="json", **headers(team.target))
    assert allowed.status_code == 201, allowed.data
    Issue.objects.filter(id=team.issue.id).update(created_by=team.other)
    denied = client.post(comments(team), {"comment_html": "<p>Hidden item</p>"}, format="json", **headers(team.target))
    assert denied.status_code == 403


def test_workspace_admin_cannot_cross_workspace_or_mix_resource_ids(team):
    foreign = Workspace.objects.create(name="Foreign", slug="foreign", owner=team.other)
    foreign_project = Project.objects.create(workspace=foreign, name="Private", identifier="PRIVATE", network=0)
    WorkspaceMember.objects.create(workspace=foreign, member=team.target, role=15, is_active=True)
    ProjectMember.objects.create(project=foreign_project, member=team.target, role=15)
    client = client_for(team.token)
    foreign_url = f"/api/v1/workspaces/{foreign.slug}/projects/{foreign_project.id}/work-items/"
    response = client.post(foreign_url, {"name": "No"}, format="json", **headers(team.target))
    assert response.status_code == 403
    mixed_url = f"/api/v1/workspaces/{team.workspace.slug}/projects/{foreign_project.id}/work-items/"
    response = client.post(mixed_url, {"name": "No"}, format="json", **headers(team.target))
    assert response.status_code == 403
    assert not Issue.objects.filter(project=foreign_project).exists()


def test_target_from_another_workspace_is_denied(team):
    outsider = User.objects.create(email="outsider@act-as.example", username="outsider", is_active=True)
    response = client_for(team.token).post(items(team), {"name": "No"}, format="json", **headers(outsider))
    assert response.status_code == 403
    assert response.data["error_code"] == "act_as_target_not_workspace_member"


def test_instance_admin_need_not_be_project_member_but_scoped_token_stays_scoped(team):
    instance = Instance.objects.create(
        instance_name="Test",
        instance_id=str(uuid4()),
        current_version="1.4.3",
        last_checked_at=timezone.now(),
    )
    InstanceAdmin.objects.create(instance=instance, user=team.admin)
    ProjectMember.objects.filter(project=team.project, member=team.admin).delete(soft=False)
    WorkspaceMember.objects.filter(workspace=team.workspace, member=team.admin).delete(soft=False)
    response = client_for(team.token).post(
        items(team), {"name": "Instance admin"}, format="json", **headers(team.target)
    )
    assert response.status_code == 201, response.data
    foreign = Workspace.objects.create(name="Other", slug="other", owner=team.admin)
    team.token.workspace = foreign
    team.token.save()
    denied = client_for(team.token).post(items(team), {"name": "No"}, format="json", **headers(team.target))
    assert denied.status_code == 403
    assert denied.data["error_code"] == "act_as_workspace_forbidden"


def test_feature_disabled_and_reason_only_fail_closed(team, settings):
    settings.PLANEPLUS_ENABLE_ACT_AS = False
    client = client_for(team.token)
    response = client.post(comments(team), {"comment_html": "<p>No</p>"}, format="json", **headers(team.target))
    assert response.status_code == 403 and response.data["error_code"] == "act_as_disabled"
    settings.PLANEPLUS_ENABLE_ACT_AS = True
    reason_only = client.post(items(team), {"name": "No"}, format="json", HTTP_X_PLANEPLUS_ACT_AS_REASON="context")
    assert reason_only.status_code == 400 and reason_only.data["error_code"] == "act_as_invalid_user"
    assert not IssueComment.objects.exists()


@pytest.mark.parametrize("field", ["created_by", "updated_by", "actor", "project", "workspace", "deleted_at"])
def test_payload_cannot_override_identity_or_scope(team, field):
    response = client_for(team.token).post(
        items(team), {"name": "No", field: str(team.other.id)}, format="json", **headers(team.target)
    )
    assert response.status_code == 400
    assert response.data["error_code"] == "act_as_fields_not_supported"
    assert Issue.objects.count() == 1


@pytest.mark.parametrize("method", ["put"])
def test_unregistered_methods_do_not_execute_as_admin(team, method):
    response = getattr(client_for(team.token), method)(
        f"{items(team)}{team.issue.id}/",
        {},
        format="json",
        **headers(team.target),
    )
    assert response.status_code == 403
    assert response.data["error_code"] == "act_as_operation_not_supported"
    assert Issue.objects.filter(id=team.issue.id).exists()


def test_administration_operations_are_excluded_in_both_api_and_session_routes(team):
    response = client_for(team.token).post(
        f"/api/v1/workspaces/{team.workspace.slug}/projects/",
        {"name": "No admin write"},
        format="json",
        **headers(team.target),
    )
    assert response.status_code == 403 and response.data["error_code"] == "act_as_operation_not_supported"
    client = APIClient()
    client.force_login(team.admin)
    session_id = client.session["_auth_user_id"]
    denied = client.post(
        reverse("sign-in"), {"email": "x@example.com", "password": "ignored"}, format="json", **headers(team.target)
    )
    assert denied.status_code == 403
    assert client.session["_auth_user_id"] == session_id


def test_boundary_rejection_is_audited_before_csrf_can_short_circuit(team):
    client = APIClient(enforce_csrf_checks=True)
    response = client.post(reverse("sign-in"), {}, format="json", **headers(team.target))
    assert response.status_code == 403
    assert response.json()["error_code"] == "act_as_operation_not_supported"
    assert not audit_for(response).succeeded


def test_read_headers_fail_closed_when_disabled(team, settings):
    settings.PLANEPLUS_ENABLE_ACT_AS = False
    client = client_for(team.token)
    url = f"{items(team)}{team.issue.id}/"
    normal = client.get(url)
    with_header = client.get(url, HTTP_X_PLANEPLUS_ACT_AS_USER="invalid")
    assert normal.status_code == 200
    assert with_header.status_code == 403
    assert with_header.data["error_code"] == "act_as_disabled"
    assert not audit_for(with_header).succeeded


def test_authentication_still_requires_callers_valid_key_and_never_uses_session_or_bearer(team):
    client = APIClient()
    client.force_login(team.admin)
    response = client.post(items(team), {"name": "No"}, format="json", **headers(team.target))
    assert response.status_code == 403
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {team.token.token}")
    response = client.post(items(team), {"name": "No"}, format="json", **headers(team.target))
    assert response.status_code == 403
    team.token.is_active = False
    team.token.save()
    response = client_for(team.token).post(items(team), {"name": "No"}, format="json", **headers(team.target))
    assert response.status_code == 403
    assert Issue.objects.count() == 1


def test_audit_failure_rolls_back_content_and_sends_no_tasks(team, monkeypatch, isolated_act_as):
    monkeypatch.setattr(ImpersonationAudit.objects, "create", Mock(side_effect=IntegrityError("audit unavailable")))
    response = client_for(team.token).post(
        comments(team), {"comment_html": "<p>Rollback</p>"}, format="json", **headers(team.target)
    )
    assert response.status_code == 503
    assert not IssueComment.objects.exists()
    isolated_act_as.activity.assert_not_called()
    isolated_act_as.webhook.assert_not_called()


def test_handler_failure_rolls_back_but_audit_survives(team, monkeypatch, isolated_act_as):
    monkeypatch.setattr(IssueCommentSerializer, "to_representation", Mock(side_effect=ValueError("render failed")))
    response = client_for(team.token).post(
        comments(team), {"comment_html": "<p>Rollback</p>"}, format="json", **headers(team.target)
    )
    assert response.status_code == 500
    assert not IssueComment.objects.exists()
    assert not audit_for(response).succeeded
    isolated_act_as.activity.assert_not_called()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE planeplus_impersonation_audit SET reason = 'forged'",
        "DELETE FROM planeplus_impersonation_audit",
        "TRUNCATE planeplus_impersonation_audit",
    ],
)
def test_audit_is_immutable_in_postgresql_not_only_the_orm(team, sql):
    response = client_for(team.token).post(items(team), {"name": "Evidence"}, format="json", **headers(team.target))
    entry = audit_for(response)
    with pytest.raises(TypeError):
        ImpersonationAudit.objects.filter(id=entry.id).update(reason="forged")
    with pytest.raises(TypeError):
        entry.delete()
    with pytest.raises(TypeError):
        entry.save()
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(sql)
    assert ImpersonationAudit.objects.get(id=entry.id).reason == "chatbot: conversation-123"


def test_provenance_is_readable_by_content_members_but_private_reason_is_admin_only(team):
    response = client_for(team.token).post(
        comments(team), {"comment_html": "<p>Evidence</p>"}, format="json", **headers(team.target)
    )
    url = f"/api/v1/workspaces/{team.workspace.slug}/act-as-audit/{response[AUDIT_HEADER]}/"
    admin = client_for(team.token).get(url)
    assert admin.status_code == 200 and admin.data["reason"] == "chatbot: conversation-123"
    token = APIToken.objects.create(user=team.other)
    member = client_for(token).get(url)
    assert member.status_code == 200 and "reason" not in member.data and "token_id" not in member.data
    provenance = client_for(token).get(f"{items(team)}{team.issue.id}/act-as-provenance/")
    assert provenance.status_code == 200
    assert provenance.data["results"][0]["actor_id"] == str(team.admin.id)
    assert client_for(team.token).patch(url, {"reason": "forged"}, format="json").status_code == 405
    ProjectMember.objects.filter(project=team.project, member=team.other).update(is_active=False)
    assert client_for(token).get(url).status_code == 403


def test_audit_evidence_survives_target_deletion(team):
    response = client_for(team.token).post(
        items(team), {"name": "Evidence survives"}, format="json", **headers(team.target)
    )
    entry = audit_for(response)
    target_id = team.target.id
    team.target.delete()
    entry.refresh_from_db()
    assert entry.target_user_id == target_id and entry.actor_id == team.admin.id


def test_guest_cannot_retrieve_provenance_of_internal_or_deleted_comment(team):
    created = client_for(team.token).post(
        comments(team), {"comment_html": "<p>Internal</p>"}, format="json", **headers(team.other)
    )
    ProjectMember.objects.filter(project=team.project, member=team.target).update(role=5)
    guest = client_for(APIToken.objects.create(user=team.target))
    url = f"/api/v1/workspaces/{team.workspace.slug}/act-as-audit/{created[AUDIT_HEADER]}/"
    assert guest.get(url).status_code == 403
    IssueComment.objects.filter(id=created.data["id"]).delete()
    assert guest.get(url).status_code == 403


def test_links_use_same_audited_infrastructure(team):
    client = client_for(team.token)
    url = f"{items(team)}{team.issue.id}/links/"
    response = client.post(
        url, {"url": "https://example.com", "title": "Reference"}, format="json", **headers(team.target)
    )
    assert response.status_code == 201, response.data
    link = IssueLink.objects.get(id=response.data["id"])
    assert link.created_by_id == team.target.id
    patched = client.patch(f"{url}{link.id}/", {"title": "Edited"}, format="json", **headers(team.other))
    assert patched.status_code == 200, patched.data
    link.refresh_from_db()
    assert link.updated_by_id == team.other.id and link.created_by_id == team.target.id
    assert audit_for(patched).resource_type == "issue_link"


def test_comment_url_cannot_bind_a_foreign_issue_or_comment(team):
    foreign_project = Project.objects.create(workspace=team.workspace, name="Hidden", identifier="HIDDEN", network=0)
    foreign_issue = Issue.objects.create(project=foreign_project, name="Secret")
    forged_url = f"{items(team)}{foreign_issue.id}/comments/"
    response = client_for(team.token).post(
        forged_url, {"comment_html": "<p>No IDOR</p>"}, format="json", **headers(team.target)
    )
    assert response.status_code == 403
    assert not IssueComment.objects.exists()
    created = client_for(team.token).post(
        comments(team), {"comment_html": "<p>Own project</p>"}, format="json", **headers(team.target)
    )
    forged_detail = f"{items(team)}{foreign_issue.id}/comments/{created.data['id']}/"
    response = client_for(team.token).patch(
        forged_detail, {"comment_html": "<p>No IDOR</p>"}, format="json", **headers(team.target)
    )
    assert response.status_code == 403


@pytest.mark.parametrize("reason", ["x" * 501, "contains\x00nul", "contains\r\nlines"])
def test_invalid_reason_is_audited_and_rejected_without_a_write(team, reason):
    response = client_for(team.token).post(items(team), {"name": "No"}, format="json", **headers(team.target, reason))
    assert response.status_code == 400, response.content
    assert response.data["error_code"] == "act_as_invalid_reason"
    assert not audit_for(response).succeeded
    assert Issue.objects.count() == 1


@pytest.mark.parametrize("attribute", ["is_service", "user_type"])
def test_service_or_bot_tokens_cannot_be_used_for_delegation(team, attribute):
    setattr(team.token, attribute, True if attribute == "is_service" else 1)
    team.token.save()
    response = client_for(team.token).post(items(team), {"name": "No"}, format="json", **headers(team.target))
    assert response.status_code == 403
    assert response.data["error_code"] == "act_as_api_key_required"


def test_delegated_description_activity_never_rewrites_a_historical_activity(team):
    from plane.bgtasks.issue_activities_task import update_issue_activity

    old = IssueActivity.objects.create(
        project=team.project,
        issue=team.issue,
        actor=team.target,
        field="description",
        verb="updated",
        old_value="<p>Before</p>",
        new_value="<p>Previously</p>",
        comment="updated the description to",
    )
    original_timestamp = old.created_at
    activities = []
    update_issue_activity(
        requested_data=json.dumps({"description_html": "<p>Delegated change</p>"}),
        current_instance=json.dumps({"description_html": "<p>Previously</p>"}),
        issue_id=str(team.issue.id),
        project_id=str(team.project.id),
        workspace_id=str(team.workspace.id),
        actor_id=str(team.target.id),
        issue_activities=activities,
        epoch=timezone.now().timestamp(),
        preserve_history=True,
    )
    assert len(activities) == 1 and activities[0].actor_id == str(team.target.id)
    old.refresh_from_db()
    assert old.created_at == original_timestamp
    assert old.new_value == "<p>Previously</p>"


def test_oauth_callback_retains_its_provider_identity_without_impersonation(team, monkeypatch):
    provider = Mock()
    provider.return_value.authenticate.return_value = team.other
    login = Mock()
    monkeypatch.setattr("plane.authentication.views.app.google.GoogleOAuthProvider", provider)
    monkeypatch.setattr("plane.authentication.views.app.google.user_login", login)
    monkeypatch.setattr("plane.authentication.views.app.google.get_redirection_path", lambda user: "/")
    for extra_headers in ({}, headers(team.target)):
        client = APIClient()
        session = client.session
        session["state"] = "provider-state"
        session.save()
        response = client.get(
            reverse("google-callback"), {"code": "provider-code", "state": "provider-state"}, **extra_headers
        )
        if extra_headers:
            assert response.status_code == 403
        else:
            assert response.status_code == 302
            assert login.call_args.kwargs["user"] is team.other
    assert ImpersonationAudit.objects.count() == 1


def test_delegated_reads_use_target_not_admin_membership(team):
    ProjectMember.objects.filter(project=team.project, member=team.admin).delete(soft=False)
    client = client_for(team.token)
    for url in (
        f"/api/v1/workspaces/{team.workspace.slug}/projects/{team.project.id}/",
        f"{items(team)}{team.issue.id}/",
        comments(team),
    ):
        response = client.get(url, **headers(team.target))
        assert response.status_code == 200, response.content
        audit = audit_for(response)
        assert audit.actor_id == team.admin.id and audit.target_user_id == team.target.id
        assert audit.method == "GET" and audit.succeeded
    ProjectMember.objects.filter(project=team.project, member=team.target).update(is_active=False)
    assert client.get(f"{items(team)}{team.issue.id}/", **headers(team.target)).status_code == 403


def test_delegated_reads_require_admin_valid_target_and_supported_route(team):
    client = client_for(APIToken.objects.create(user=team.other))
    assert client.get(comments(team), **headers(team.target)).status_code == 403
    client = client_for(team.token)
    assert client.get(comments(team), HTTP_X_PLANEPLUS_ACT_AS_USER="invalid").status_code == 400
    assert client.get("/api/v1/users/me/", **headers(team.target)).status_code == 403


def test_guest_reads_hide_other_items_and_internal_comments(team):
    Project.objects.filter(id=team.project.id).update(guest_view_all_features=False)
    hidden = Issue.objects.create(project=team.project, name="Other user's hidden work")
    client = client_for(team.token)
    internal = client.post(comments(team), {"comment_html": "<p>Private</p>"}, format="json", **headers(team.other))
    public = client.post(
        comments(team), {"comment_html": "<p>Public</p>", "access": "EXTERNAL"}, format="json", **headers(team.other)
    )
    ProjectMember.objects.filter(project=team.project, member=team.target).update(role=5)
    response = client.get(items(team), **headers(team.target))
    assert response.status_code == 200, response.content
    assert [str(item["id"]) for item in response.data["results"]] == [str(team.issue.id)]
    assert client.get(f"{items(team)}{hidden.id}/", **headers(team.target)).status_code == 403
    response = client.get(comments(team), **headers(team.target))
    assert response.status_code == 200
    assert [str(comment["id"]) for comment in response.data["results"]] == [str(public.data["id"])]
    assert client.get(f"{comments(team)}{internal.data['id']}/", **headers(team.target)).status_code == 403


def test_capabilities_and_resolver_are_admin_only_and_work_when_disabled(team, settings):
    settings.PLANEPLUS_ENABLE_ACT_AS = False
    base = f"/api/v1/workspaces/{team.workspace.slug}/"
    client = client_for(team.token)
    response = client.get(f"{base}act-as-capabilities/")
    assert response.status_code == 200, response.content
    assert response.data["version"] == 4 and response.data["enabled"] is False
    assert response.data["attachments"]["guest_uploads"] and response.data["attachments"]["generic_uploads"]
    assert response.data["intake_target_date"] is True
    assert response.data["ticket_inline_assets"]["https_only"] is True
    assert response.data["reads_as_target"] and response.data["public_project_join"]
    assert all(operation["paths"] for operation in response.data["operations"])
    for operation in response.data["operations"]:
        for path in operation["paths"]:
            concrete = re.sub(r"<uuid:\w+>", str(uuid4()), path)
            concrete = re.sub(r"<(?:str:)?\w+>", "example", concrete)
            callback = resolve(concrete).func
            methods = callback.initkwargs.get("http_method_names", callback.cls.http_method_names)
            assert operation["method"].lower() in methods
    resolved = client.get(f"{base}act-as-users/resolve/", {"email": team.target.email.upper()})
    assert resolved.status_code == 200 and resolved.data["id"] == str(team.target.id)
    assert resolved.data["eligible"] and resolved.data["is_active"] and not resolved.data["is_bot"]
    team.target.is_active = False
    team.target.save()
    WorkspaceMember.objects.filter(workspace=team.workspace, member=team.target).update(is_active=False)
    resolved = client.get(f"{base}act-as-users/resolve/", {"email": team.target.email})
    assert resolved.status_code == 200 and not resolved.data["is_active"] and not resolved.data["eligible"]
    outsider = User.objects.create(email="elsewhere@example.com", username="elsewhere")
    assert client.get(f"{base}act-as-users/resolve/", {"email": outsider.email}).status_code == 404
    unauthorized = client_for(APIToken.objects.create(user=team.other))
    assert unauthorized.get(f"{base}act-as-capabilities/").status_code == 403
    assert unauthorized.get(f"{base}act-as-users/resolve/", {"email": team.admin.email}).status_code == 403


def public_project(team):
    return Project.objects.create(workspace=team.workspace, name="Public join", identifier="PUBLIC", network=2)


def join_url(team, project):
    return f"/api/v1/workspaces/{team.workspace.slug}/projects/{project.id}/join/"


def test_public_join_is_audited_idempotent_and_does_not_require_admin_project_membership(team):
    project = public_project(team)
    client = client_for(team.token)
    joined = client.post(join_url(team, project), {}, format="json", **headers(team.target))
    assert joined.status_code == 201, joined.content
    membership = ProjectMember.objects.get(project=project, member=team.target)
    assert membership.role == 15 and membership.created_by_id == team.target.id
    assert db_models.ProjectUserProperty.objects.filter(project=project, user=team.target).count() == 1
    audit = audit_for(joined)
    assert audit.resource_id == membership.id and audit.actor_id == team.admin.id and audit.action == "project.join"
    repeated = client.post(join_url(team, project), {}, format="json", **headers(team.target))
    assert repeated.status_code == 200 and repeated.data["joined"] is False
    assert ProjectMember.objects.filter(project=project, member=team.target).count() == 1
    assert not ProjectMember.objects.filter(project=project, member=team.admin).exists()


@pytest.mark.parametrize("workspace_role,expected", [(5, 5), (15, 15), (20, 15)])
def test_public_join_never_grants_project_admin(team, workspace_role, expected):
    WorkspaceMember.objects.filter(workspace=team.workspace, member=team.target).update(role=workspace_role)
    project = public_project(team)
    response = client_for(team.token).post(join_url(team, project), {}, format="json", **headers(team.target))
    assert response.status_code == 201 and response.data["role"] == expected


def test_public_join_rejects_private_archived_removed_and_role_payload(team):
    client = client_for(team.token)
    project = public_project(team)
    assert client.post(join_url(team, team.project), {}, format="json", **headers(team.target)).status_code == 403
    assert client.post(join_url(team, project), {"role": 20}, format="json", **headers(team.target)).status_code == 400
    Project.objects.filter(id=project.id).update(archived_at=timezone.now())
    assert client.post(join_url(team, project), {}, format="json", **headers(team.target)).status_code == 403
    Project.objects.filter(id=project.id).update(archived_at=None)
    ProjectMember.objects.create(project=project, member=team.target, role=5, is_active=False)
    assert client.post(join_url(team, project), {}, format="json", **headers(team.target)).status_code == 403


@pytest.mark.parametrize(
    "family,model,payload",
    [
        ("labels", "Label", {"name": "Delegated label", "color": "#00ff00"}),
        ("states", "State", {"name": "Delegated state", "color": "#00ff00", "group": "started"}),
        ("cycles", "Cycle", {"name": "Delegated cycle"}),
        ("modules", "Module", {"name": "Delegated module"}),
        ("estimates", "Estimate", {"name": "Delegated estimate", "type": "points"}),
    ],
)
def test_extended_content_create_update_and_delete_are_audited(team, family, model, payload):
    Project.objects.filter(id=team.project.id).update(cycle_view=True, module_view=True)
    url = f"/api/v1/workspaces/{team.workspace.slug}/projects/{team.project.id}/{family}/"
    client = client_for(team.token)
    response = client.post(url, payload, format="json", **headers(team.target))
    assert response.status_code in (200, 201), response.content
    obj = getattr(db_models, model).objects.get(id=response.data["id"])
    assert obj.created_by_id == team.target.id and audit_for(response).resource_id == obj.id
    detail = url if family == "estimates" else f"{url}{obj.id}/"
    if family == "estimates":
        update = {"estimate_id": str(obj.id)}
    else:
        update = {"name": "Edited through delegation"}
    edited = client.patch(detail, update, format="json", **headers(team.other))
    assert edited.status_code in (200, 204), edited.content
    if family != "estimates":
        obj.refresh_from_db()
        assert obj.updated_by_id == team.other.id
    # Existing cycle/module handlers require project-admin deletion.
    target = team.admin if family in ("cycles", "modules") else team.target
    deleted = client.delete(detail, {}, format="json", **headers(target))
    assert deleted.status_code == 204, deleted.content
    assert audit_for(deleted).succeeded


def test_expanded_fields_do_not_allow_identity_scope_or_project_role_changes(team):
    client = client_for(team.token)
    base = f"/api/v1/workspaces/{team.workspace.slug}/projects/{team.project.id}/"
    for family in ("states", "labels", "cycles", "modules", "estimates"):
        response = client.post(
            f"{base}{family}/", {"name": "No", "created_by": str(team.other.id)}, format="json", **headers(team.target)
        )
        assert response.status_code == 400
    response = client.patch(base, {"network": 2}, format="json", **headers(team.admin))
    assert response.status_code == 400
    response = client.patch(base, {"name": "No member administration"}, format="json", **headers(team.target))
    assert response.status_code == 403
    response = client.patch(base, {"name": "Admin metadata"}, format="json", **headers(team.admin))
    assert response.status_code == 200, response.content


def test_work_item_archive_restore_delete_and_comment_delete(team):
    client = client_for(team.token)
    detail = f"{items(team)}{team.issue.id}/"
    archived = client.patch(
        detail, {"archived_at": timezone.now().date().isoformat()}, format="json", **headers(team.target)
    )
    assert archived.status_code == 200, archived.content
    team.issue.refresh_from_db()
    assert team.issue.archived_at is not None
    restored = client.patch(detail, {"archived_at": None}, format="json", **headers(team.target))
    assert restored.status_code == 200, restored.content
    comment = client.post(comments(team), {"comment_html": "<p>Remove me</p>"}, format="json", **headers(team.target))
    denied = client.delete(f"{comments(team)}{comment.data['id']}/", {}, format="json", **headers(team.other))
    assert denied.status_code == 403
    removed = client.delete(f"{comments(team)}{comment.data['id']}/", {}, format="json", **headers(team.target))
    assert (
        removed.status_code == 204
        and audit_for(removed).resource_id == db_models.IssueComment.all_objects.get(id=comment.data["id"]).id
    )
    removed = client.delete(detail, {}, format="json", **headers(team.target))
    assert removed.status_code == 204 and audit_for(removed).resource_id == team.issue.id


def test_bulk_assignments_reject_cross_project_and_track_effective_author(team):
    foreign = Project.objects.create(workspace=team.workspace, name="Other", identifier="OTHER", network=0)
    issue = Issue.objects.create(project=foreign, name="Not in target project")
    cycle = db_models.Cycle.objects.create(project=team.project, name="Cycle", owned_by=team.target)
    url = f"/api/v1/workspaces/{team.workspace.slug}/projects/{team.project.id}/cycles/{cycle.id}/cycle-issues/"
    client = client_for(team.token)
    denied = client.post(url, {"issues": [str(issue.id)]}, format="json", **headers(team.target))
    assert denied.status_code == 403, denied.content
    response = client.post(url, {"issues": [str(team.issue.id)]}, format="json", **headers(team.target))
    assert response.status_code == 200, response.content
    assert db_models.CycleIssue.objects.get(cycle=cycle, issue=team.issue).created_by_id == team.target.id


def test_relations_require_same_project_and_are_audited(team):
    other_issue = Issue.objects.create(project=team.project, name="Related")
    foreign_project = Project.objects.create(workspace=team.workspace, name="Forbidden", identifier="FORBID", network=0)
    foreign_issue = Issue.objects.create(project=foreign_project, name="Secret")
    url = f"{items(team)}{team.issue.id}/relations/"
    client = client_for(team.token)
    denied = client.post(
        url, {"relation_type": "relates_to", "issues": [str(foreign_issue.id)]}, format="json", **headers(team.target)
    )
    assert denied.status_code == 403, denied.content
    created = client.post(
        url, {"relation_type": "relates_to", "issues": [str(other_issue.id)]}, format="json", **headers(team.target)
    )
    assert created.status_code in (200, 201), created.content
    relation = db_models.IssueRelation.objects.get(issue=team.issue, related_issue=other_issue)
    assert relation.created_by_id == team.target.id
    db_models.IssueRelation.objects.create(
        project=team.project, issue=team.issue, related_issue=foreign_issue, relation_type="relates_to"
    )
    read = client.get(url, **headers(team.target))
    assert read.status_code == 200
    assert str(foreign_issue.id) not in str(read.data)


def test_attachment_lifecycle_and_download_are_scoped_and_audited(team, monkeypatch):
    storage = Mock()
    storage.return_value.generate_presigned_post.return_value = {"url": "https://storage.example/upload", "fields": {}}
    storage.return_value.generate_presigned_url.return_value = "https://storage.example/download"
    monkeypatch.setattr("plane.api.views.issue.S3Storage", storage)
    url = f"{items(team)}{team.issue.id}/attachments/"
    client = client_for(team.token)
    response = client.post(
        url, {"name": "test.txt", "type": "text/plain", "size": 10}, format="json", **headers(team.target)
    )
    assert response.status_code == 200, response.content
    asset = db_models.FileAsset.objects.get(id=response.data["asset_id"])
    assert asset.created_by_id == team.target.id and audit_for(response).resource_id == asset.id
    confirmed = client.patch(f"{url}{asset.id}/", {}, format="json", **headers(team.other))
    assert confirmed.status_code == 204, confirmed.content
    asset.refresh_from_db()
    assert asset.created_by_id == team.target.id and asset.updated_by_id == team.other.id
    assert client.get(url, **headers(team.target)).status_code == 200
    downloaded = client.get(f"{url}{asset.id}/", **headers(team.target))
    assert downloaded.status_code == 302, downloaded.content
    assert audit_for(downloaded).succeeded
    other_issue = Issue.objects.create(project=team.project, name="Other attachment scope")
    forged = f"{items(team)}{other_issue.id}/attachments/{asset.id}/"
    for method in ("get", "patch", "delete"):
        response = getattr(client, method)(forged, **headers(team.target))
        assert response.status_code == 403, response.content
    deleted = client.delete(f"{url}{asset.id}/", {}, format="json", **headers(team.target))
    assert deleted.status_code == 204 and audit_for(deleted).resource_id == asset.id


def test_intake_create_edit_delete_keep_target_authorship_and_reject_nested_identity(team):
    Project.objects.filter(id=team.project.id).update(intake_view=True)
    db_models.Intake.objects.create(project=team.project, name="Intake", is_default=True)
    State.objects.create(project=team.project, name="Triage", group="triage", is_triage=True)
    url = f"/api/v1/workspaces/{team.workspace.slug}/projects/{team.project.id}/intake-issues/"
    client = client_for(team.token)
    denied = client.post(
        url, {"issue": {"name": "No spoof", "created_by": str(team.other.id)}}, format="json", **headers(team.target)
    )
    assert denied.status_code == 400
    created = client.post(
        url, {"issue": {"name": "Delegated intake", "priority": "low"}}, format="json", **headers(team.target)
    )
    assert created.status_code == 201, created.content
    issue = Issue.objects.get(name="Delegated intake")
    assert issue.created_by_id == team.target.id
    assert db_models.IntakeIssue.objects.get(issue=issue).created_by_id == team.target.id
    updated = client.patch(
        f"{url}{issue.id}/", {"issue": {"name": "Edited intake"}}, format="json", **headers(team.other)
    )
    assert updated.status_code == 200, updated.content
    issue.refresh_from_db()
    assert issue.updated_by_id == team.other.id
    deleted = client.delete(f"{url}{issue.id}/", {}, format="json", **headers(team.target))
    assert deleted.status_code == 204, deleted.content


def test_estimate_points_cannot_bind_foreign_estimate_and_record_target(team):
    estimate = db_models.Estimate.objects.create(project=team.project, name="Points")
    url = (
        f"/api/v1/workspaces/{team.workspace.slug}/projects/{team.project.id}/estimates/{estimate.id}/estimate-points/"
    )
    client = client_for(team.token)
    created = client.post(url, {"estimate_points": [{"key": 1, "value": "One"}]}, format="json", **headers(team.target))
    assert created.status_code == 201, created.content
    point = db_models.EstimatePoint.objects.get(id=created.data[0]["id"])
    assert point.created_by_id == team.target.id
    edited = client.patch(f"{url}{point.id}/", {"value": "Two"}, format="json", **headers(team.other))
    assert edited.status_code == 200, edited.content
    assert audit_for(edited).resource_id == point.id
    deleted = client.delete(f"{url}{point.id}/", {}, format="json", **headers(team.target))
    assert deleted.status_code == 204


def test_removed_memberships_never_leak_through_project_lists_search_or_resolver(team):
    private = Project.objects.create(workspace=team.workspace, name="Removed access", identifier="REMOVED", network=0)
    member = ProjectMember.objects.create(project=private, member=team.target, role=15)
    member.delete()
    issue = Issue.objects.create(project=private, name="Needle inaccessible")
    client = client_for(team.token)
    for suffix in ("projects/", "projects-lite/"):
        response = client.get(f"/api/v1/workspaces/{team.workspace.slug}/{suffix}", **headers(team.target))
        assert response.status_code == 200
        assert str(private.id) not in str(response.data["results"])
    response = client.get(
        f"/api/v1/workspaces/{team.workspace.slug}/work-items/search/",
        {"search": "Needle", "workspace_search": "true"},
        **headers(team.target),
    )
    assert response.status_code == 200 and str(issue.id) not in str(response.data)
    WorkspaceMember.objects.filter(workspace=team.workspace, member=team.other).delete()
    response = client.get(
        f"/api/v1/workspaces/{team.workspace.slug}/act-as-users/resolve/", {"email": team.other.email}
    )
    assert response.status_code == 404


def test_delegated_authentication_and_permissions_use_primary_and_restore_routing(team, monkeypatch):
    from rest_framework.test import APIRequestFactory
    from plane.api.middleware.api_authentication import APIKeyAuthentication
    from plane.api.views.issue import IssueDetailAPIEndpoint
    from plane.utils.core import set_use_read_replica, should_use_read_replica, clear_read_replica_context

    original = APIKeyAuthentication.validate_api_token
    calls = []

    def authenticate(authenticator, token):
        calls.append(should_use_read_replica())
        return original(authenticator, token)

    monkeypatch.setattr(APIKeyAuthentication, "validate_api_token", authenticate)
    request = APIRequestFactory().get(
        f"{items(team)}{team.issue.id}/", HTTP_X_API_KEY=team.token.token, **headers(team.target)
    )
    set_use_read_replica(True)
    try:
        response = IssueDetailAPIEndpoint.as_view()(
            request, slug=team.workspace.slug, project_id=team.project.id, pk=team.issue.id
        )
        assert response.status_code == 200 and calls == [False]
        assert should_use_read_replica() is True
    finally:
        clear_read_replica_context()


def test_join_audit_failure_rolls_back_membership_and_preferences(team, monkeypatch):
    project = public_project(team)
    monkeypatch.setattr(ImpersonationAudit.objects, "create", Mock(side_effect=IntegrityError("unavailable")))
    response = client_for(team.token).post(join_url(team, project), {}, format="json", **headers(team.target))
    assert response.status_code == 503
    assert not ProjectMember.objects.filter(project=project, member=team.target).exists()
    assert not db_models.ProjectUserProperty.objects.filter(project=project, user=team.target).exists()


@pytest.mark.parametrize("family", ["states", "labels", "cycles", "modules", "estimates"])
def test_expanded_writes_never_borrow_the_admins_project_rights(team, family):
    ProjectMember.objects.filter(project=team.project, member=team.target).update(role=5)
    WorkspaceMember.objects.filter(workspace=team.workspace, member=team.target).update(role=5)
    url = f"/api/v1/workspaces/{team.workspace.slug}/projects/{team.project.id}/{family}/"
    data = {"name": "No escalation"}
    response = client_for(team.token).post(url, data, format="json", **headers(team.target))
    assert response.status_code == 403, response.content
    assert not audit_for(response).succeeded


def test_label_creation_preserves_existing_workspace_member_permission_for_project_guests(team):
    # Plane's ProjectMemberPermission authorizes POST by workspace role, unlike
    # the PATCH/DELETE rule. Delegation must not invent an elevated project role.
    ProjectMember.objects.filter(project=team.project, member=team.target).update(role=5)
    url = f"/api/v1/workspaces/{team.workspace.slug}/projects/{team.project.id}/labels/"
    direct = client_for(APIToken.objects.create(user=team.target)).post(url, {"name": "Direct"}, format="json")
    delegated = client_for(team.token).post(url, {"name": "Delegated"}, format="json", **headers(team.target))
    assert direct.status_code == delegated.status_code == 201
    assert Label.objects.get(id=delegated.data["id"]).created_by_id == team.target.id
    assert ProjectMember.objects.get(project=team.project, member=team.target).role == 5


def test_discovery_and_reads_cannot_escape_token_workspace_scope(team):
    workspace = Workspace.objects.create(name="Scope", slug="token-scope", owner=team.admin)
    team.token.workspace = workspace
    team.token.save()
    base = f"/api/v1/workspaces/{team.workspace.slug}/"
    client = client_for(team.token)
    assert client.get(f"{base}act-as-capabilities/").status_code == 403
    assert client.get(f"{base}act-as-users/resolve/", {"email": team.target.email}).status_code == 403
    assert client.get(comments(team), **headers(team.target)).status_code == 403


def test_headerless_public_join_uses_real_user_even_when_delegation_disabled(team, settings):
    settings.PLANEPLUS_ENABLE_ACT_AS = False
    project = public_project(team)
    token = APIToken.objects.create(user=team.target)
    response = client_for(token).post(join_url(team, project), {}, format="json")
    assert response.status_code == 201 and response.data["member_id"] == str(team.target.id)
    assert AUDIT_HEADER not in response
    assert not ImpersonationAudit.objects.exists()


@pytest.mark.parametrize("family", ["cycles", "modules"])
def test_cycle_and_module_archive_and_restore_keep_resource_audit(team, family):
    if family == "cycles":
        obj = db_models.Cycle.objects.create(
            project=team.project,
            name="Finished cycle",
            owned_by=team.target,
            start_date=timezone.now() - timedelta(days=4),
            end_date=timezone.now() - timedelta(days=2),
        )
    else:
        obj = db_models.Module.objects.create(project=team.project, name="Finished module", status="completed")
    url = f"/api/v1/workspaces/{team.workspace.slug}/projects/{team.project.id}/{family}/{obj.id}/archive/"
    client = client_for(team.token)
    archived = client.post(url, {}, format="json", **headers(team.target))
    assert archived.status_code == 204, archived.content
    assert audit_for(archived).resource_id == obj.id
    obj.refresh_from_db()
    assert obj.archived_at is not None and obj.updated_by_id == team.target.id
    restore_url = (
        f"/api/v1/workspaces/{team.workspace.slug}/projects/{team.project.id}/archived-{family}/{obj.id}/unarchive/"
    )
    restored = client.delete(restore_url, {}, format="json", **headers(team.other))
    assert restored.status_code == 204, restored.content
    obj.refresh_from_db()
    assert obj.archived_at is None and obj.updated_by_id == team.other.id


@pytest.fixture
def intake_url(team):
    Project.objects.filter(id=team.project.id).update(intake_view=True)
    db_models.Intake.objects.create(project=team.project, name="Intake", is_default=True)
    State.objects.create(project=team.project, name="Triage", group="triage", is_triage=True)
    return f"/api/v1/workspaces/{team.workspace.slug}/projects/{team.project.id}/intake-issues/"


@pytest.mark.parametrize("delegated", [False, True])
@pytest.mark.parametrize("date", ["2026-12-15", None, "omitted"])
def test_optional_intake_due_date_create_edit_clear(team, intake_url, delegated, date):
    extra = headers(team.target) if delegated else {}
    payload = {"name": "Intake with optional date"}
    if date != "omitted":
        payload["target_date"] = date
    client = client_for(team.token)
    created = client.post(intake_url, {"issue": payload}, format="json", **extra)
    assert created.status_code == 201, created.content
    issue = Issue.objects.get(name=payload["name"])
    assert (issue.target_date.isoformat() if issue.target_date else None) == (
        date if date not in {None, "omitted"} else None
    )
    assert created.data["issue_detail"]["target_date"] == (date if date not in {None, "omitted"} else None)
    url = f"{intake_url}{issue.id}/"
    for value in ("2027-01-20", None):
        edited = client.patch(url, {"issue": {"target_date": value}}, format="json", **extra)
        assert edited.status_code == 200, edited.content
        issue.refresh_from_db()
        assert (issue.target_date.isoformat() if issue.target_date else None) == value
    if delegated:
        assert issue.created_by_id == issue.updated_by_id == team.target.id
        assert audit_for(created).actor_id == team.admin.id


@pytest.mark.parametrize("delegated", [False, True])
@pytest.mark.parametrize("date", ["2026-02-31", "not-a-date", "2026-12-15T12:00:00Z"])
def test_invalid_intake_due_date_is_rejected_without_content(team, intake_url, delegated, date):
    extra = headers(team.target) if delegated else {}
    response = client_for(team.token).post(
        intake_url, {"issue": {"name": "Invalid date", "target_date": date}}, format="json", **extra
    )
    assert response.status_code == 400, response.content
    assert not Issue.objects.filter(name="Invalid date").exists()
    if delegated:
        assert not audit_for(response).succeeded


@pytest.fixture
def inline_image(team, monkeypatch):
    asset = db_models.FileAsset.objects.create(
        workspace=team.workspace,
        project=team.project,
        issue=team.issue,
        is_uploaded=True,
        asset=f"{team.workspace.id}/{uuid4()}.png",
        entity_type=db_models.FileAsset.EntityTypeContext.ISSUE_DESCRIPTION,
        attributes={"name": "image.png", "type": "image/png"},
    )
    team.issue.description_html = f'<p>Body</p><image-component src="{asset.id}"></image-component>'
    team.issue.save(created_by_id=team.target.id)
    storage = Mock()
    storage.return_value.signed_url_expiration = 3600
    storage.return_value.generate_presigned_url.return_value = (
        "https://storage.example/image.png?X-Amz-Signature=secret"
    )
    monkeypatch.setattr("plane.utils.inline_assets.S3Storage", storage)
    return SimpleNamespace(asset=asset, storage=storage, url=f"{items(team)}{team.issue.id}/inline-assets/{asset.id}/")


@pytest.mark.parametrize("redirect,expected", [("true", 302), ("false", 200)])
def test_ticket_inline_image_returns_https_target_and_audit_without_admin_membership(
    team, inline_image, isolated_act_as, redirect, expected
):
    ProjectMember.objects.filter(project=team.project, member=team.admin).delete(soft=False)
    response = client_for(team.token).get(inline_image.url, {"redirect": redirect}, **headers(team.target))
    assert response.status_code == expected, response.content
    assert response["X-PlanePlus-Act-As-User"] == str(team.target.id)
    assert response["Cache-Control"] == "private, no-store"
    url = response["Location"] if expected == 302 else response.data["url"]
    assert url.startswith("https://")
    entry = audit_for(response)
    assert entry.succeeded and entry.response_status == expected
    assert entry.actor_id == team.admin.id and entry.target_user_id == team.target.id
    assert entry.resource_id == inline_image.asset.id and entry.issue_id == team.issue.id
    assert entry.resource_type == "inline_asset"
    inline_image.storage.return_value.generate_presigned_url.assert_called_once()
    assert inline_image.storage.return_value.generate_presigned_url.call_args.kwargs["expiration"] == 300
    assert isolated_act_as.logs.call_args.kwargs["log_data"]["response_body"] == "[REDACTED]"


def test_inline_image_headerless_reads_use_real_user_not_admin_override(team, inline_image):
    client = client_for(APIToken.objects.create(user=team.target))
    assert client.get(inline_image.url).status_code == 302
    assert not ImpersonationAudit.objects.exists()
    ProjectMember.objects.filter(project=team.project, member=team.admin).update(is_active=False)
    assert client_for(team.token).get(inline_image.url).status_code == 403


@pytest.mark.parametrize(
    "body",
    ["<p>No image</p>", '<a href="{id}">Not an inline image</a>', '<img src="https://evil.example/?asset={id}">'],
)
def test_unreferenced_image_cannot_be_read_and_never_falls_back(team, inline_image, body):
    Issue.objects.filter(id=team.issue.id).update(description_html=body.format(id=inline_image.asset.id))
    response = client_for(team.token).get(inline_image.url, **headers(team.target))
    assert response.status_code == 403 and not audit_for(response).succeeded
    inline_image.storage.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("is_uploaded", False),
        ("is_deleted", True),
        ("is_archived", True),
        ("entity_type", "ISSUE_ATTACHMENT"),
        ("attributes", {"type": "image/svg+xml"}),
    ],
)
def test_inline_images_reject_unavailable_noninline_or_unsafe_assets(team, inline_image, field, value):
    db_models.FileAsset.objects.filter(id=inline_image.asset.id).update(**{field: value})
    response = client_for(team.token).get(inline_image.url, **headers(team.target))
    assert response.status_code in (403, 404)
    assert not audit_for(response).succeeded
    inline_image.storage.assert_not_called()


def test_inline_image_binds_ticket_project_workspace_and_active_target(team, inline_image):
    other_issue = Issue.objects.create(
        project=team.project, name="Different ticket", description_html=team.issue.description_html
    )
    url = f"{items(team)}{other_issue.id}/inline-assets/{inline_image.asset.id}/"
    client = client_for(team.token)
    assert client.get(url, **headers(team.target)).status_code == 403
    foreign_project = Project.objects.create(workspace=team.workspace, name="Other", identifier="OTHER")
    db_models.FileAsset.objects.filter(id=inline_image.asset.id).update(project=foreign_project)
    assert client.get(inline_image.url, **headers(team.target)).status_code == 404
    db_models.FileAsset.objects.filter(id=inline_image.asset.id).update(project=team.project)
    ProjectMember.objects.filter(project=team.project, member=team.target).update(is_active=False)
    assert client.get(inline_image.url, **headers(team.target)).status_code == 403
    inline_image.storage.assert_not_called()


def test_comment_inline_image_enforces_comment_binding_and_guest_visibility(team, inline_image):
    client = client_for(team.token)
    comment = IssueComment.objects.create(
        project=team.project,
        issue=team.issue,
        workspace=team.workspace,
        actor=team.other,
        comment_html=f'<img src="{inline_image.asset.id}">',
        access="INTERNAL",
    )
    db_models.FileAsset.objects.filter(id=inline_image.asset.id).update(
        entity_type="COMMENT_DESCRIPTION",
        issue=None,
        comment=comment,
    )
    url = f"{comments(team)}{comment.id}/inline-assets/{inline_image.asset.id}/"
    assert client.get(url, **headers(team.target)).status_code == 302
    ProjectMember.objects.filter(project=team.project, member=team.target).update(role=5)
    assert client.get(url, **headers(team.target)).status_code == 403
    # The ticket-only route must enforce the same internal-comment rule.
    assert client.get(inline_image.url, **headers(team.target)).status_code == 403
    IssueComment.objects.filter(id=comment.id).update(access="EXTERNAL")
    assert client.get(url, **headers(team.target)).status_code == 302
    assert client.get(inline_image.url, **headers(team.target)).status_code == 302
    forged = f"{comments(team)}{uuid4()}/inline-assets/{inline_image.asset.id}/"
    assert client.get(forged, **headers(team.target)).status_code == 403
    IssueComment.objects.filter(id=comment.id).delete()
    assert client.get(url, **headers(team.target)).status_code == 403


def test_inline_image_disabled_feature_and_audit_failure_never_return_url(team, inline_image, settings, monkeypatch):
    client = client_for(team.token)
    settings.PLANEPLUS_ENABLE_ACT_AS = False
    response = client.get(inline_image.url, **headers(team.target))
    assert response.status_code == 403 and "Location" not in response
    inline_image.storage.assert_not_called()
    settings.PLANEPLUS_ENABLE_ACT_AS = True
    monkeypatch.setattr(ImpersonationAudit.objects, "create", Mock(side_effect=IntegrityError("no audit")))
    response = client.get(inline_image.url, **headers(team.target))
    assert response.status_code == 503 and "Location" not in response


@pytest.mark.parametrize("url", [None, "http://storage.example/image.png?signature=x"])
def test_inline_image_insecure_storage_has_audited_error_without_location(team, inline_image, url):
    inline_image.storage.return_value.generate_presigned_url.return_value = url
    response = client_for(team.token).get(inline_image.url, **headers(team.target))
    assert response.status_code == 503 and "Location" not in response
    assert response.data["error_code"] == "inline_asset_https_unavailable"
    assert not audit_for(response).succeeded and audit_for(response).error_code == "inline_asset_https_unavailable"


@pytest.mark.parametrize("delegated", [False, True])
def test_intake_partial_update_preserves_due_date_and_invalid_patch_cannot_change_it(team, intake_url, delegated):
    client = client_for(team.token)
    extra = headers(team.target) if delegated else {}
    created = client.post(
        intake_url, {"issue": {"name": "Keep date", "target_date": "2026-12-15"}}, format="json", **extra
    )
    assert created.status_code == 201
    issue = Issue.objects.get(name="Keep date")
    url = f"{intake_url}{issue.id}/"
    changed = client.patch(url, {"issue": {"name": "Keep existing date"}}, format="json", **extra)
    assert changed.status_code == 200
    invalid = client.patch(url, {"issue": {"target_date": "2026-02-31"}}, format="json", **extra)
    assert invalid.status_code == 400
    issue.refresh_from_db()
    assert issue.target_date.isoformat() == "2026-12-15"


def test_inline_image_references_survive_regular_body_api_roundtrip(team, inline_image):
    client = client_for(team.token)
    url = f"{items(team)}{team.issue.id}/"
    html = f'<p>Image</p><image-component src="{inline_image.asset.id}"></image-component>'
    written = client.patch(url, {"description_html": html}, format="json", **headers(team.target))
    assert written.status_code == 200, written.content
    body = client.get(url, **headers(team.target))
    assert str(inline_image.asset.id) in body.data["description_html"]
    assert client.get(inline_image.url, **headers(team.target)).status_code == 302
    comment = client.post(comments(team), {"comment_html": html}, format="json", **headers(team.target))
    assert comment.status_code == 201, comment.content
    db_models.FileAsset.objects.filter(id=inline_image.asset.id).update(
        entity_type="COMMENT_DESCRIPTION", issue=None, comment_id=comment.data["id"]
    )
    assert client.get(inline_image.url, **headers(team.target)).status_code == 302


def test_inline_head_has_same_audit_and_target_headers(team, inline_image):
    response = client_for(team.token).head(inline_image.url, **headers(team.target))
    assert response.status_code == 302 and response.content == b""
    assert response["X-PlanePlus-Act-As-User"] == str(team.target.id)
    assert audit_for(response).succeeded and audit_for(response).method == "HEAD"


def test_inline_asset_audit_does_not_leak_internal_comment_images_to_guests(team, inline_image):
    comment = IssueComment.objects.create(
        project=team.project,
        workspace=team.workspace,
        issue=team.issue,
        actor=team.other,
        comment_html=team.issue.description_html,
        access="INTERNAL",
    )
    db_models.FileAsset.objects.filter(id=inline_image.asset.id).update(
        entity_type="COMMENT_DESCRIPTION", issue=None, comment=comment
    )
    client = client_for(team.token)
    read = client.get(inline_image.url, **headers(team.other))
    assert read.status_code == 302
    ProjectMember.objects.filter(project=team.project, member=team.target).update(role=5)
    guest = client_for(APIToken.objects.create(user=team.target))
    detail = f"/api/v1/workspaces/{team.workspace.slug}/act-as-audit/{read[AUDIT_HEADER]}/"
    assert guest.get(detail).status_code == 403
    listed = guest.get(f"{items(team)}{team.issue.id}/act-as-provenance/")
    assert listed.status_code == 200 and not listed.data["results"]


@pytest.fixture
def guest_intake(team, intake_url):
    WorkspaceMember.objects.filter(workspace=team.workspace, member=team.target).update(role=5)
    ProjectMember.objects.filter(project=team.project, member=team.target).update(role=5)
    Project.objects.filter(id=team.project.id).update(guest_view_all_features=False)
    return intake_url


def guest_intake_client(team, delegated):
    token = team.token if delegated else APIToken.objects.create(user=team.target)
    return client_for(token), headers(team.target) if delegated else {}


def submit_intake(team, url, target, name):
    response = client_for(team.token).post(url, {"issue": {"name": name}}, format="json", **headers(target))
    assert response.status_code == 201, response.content
    return db_models.IntakeIssue.objects.get(id=response.data["id"])


@pytest.mark.parametrize("delegated", [False, True])
def test_guest_can_submit_intake_with_optional_due_date_and_correct_authorship(team, guest_intake, delegated):
    if delegated:
        ProjectMember.objects.filter(project=team.project, member=team.admin).delete(soft=False)
    client, extra = guest_intake_client(team, delegated)
    response = client.post(
        guest_intake,
        {"issue": {"name": "Guest submission", "priority": "low", "target_date": "2026-12-15"}},
        format="json",
        **extra,
    )
    assert response.status_code == 201, response.content
    entry = db_models.IntakeIssue.objects.select_related("issue").get(id=response.data["id"])
    assert entry.created_by_id == entry.issue.created_by_id == team.target.id
    assert entry.issue.target_date.isoformat() == "2026-12-15"
    assert entry.issue.priority == "low"
    assert ProjectMember.objects.get(project=team.project, member=team.target).role == 5
    if delegated:
        audit = audit_for(response)
        assert audit.succeeded and audit.actor_id == team.admin.id and audit.target_user_id == team.target.id
        assert audit.resource_id == entry.id
    else:
        assert AUDIT_HEADER not in response


@pytest.mark.parametrize("delegated", [False, True])
@pytest.mark.parametrize("view_all", [False, True])
def test_guest_intake_read_visibility_matches_existing_project_setting(team, guest_intake, delegated, view_all):
    own = submit_intake(team, guest_intake, team.target, "Guest's own")
    foreign = submit_intake(team, guest_intake, team.other, "Other user's submission")
    Project.objects.filter(id=team.project.id).update(guest_view_all_features=view_all)
    client, extra = guest_intake_client(team, delegated)
    listed = client.get(guest_intake, **extra)
    assert listed.status_code == 200, listed.content
    actual = {str(entry["id"]) for entry in listed.data["results"]}
    assert actual == ({str(own.id), str(foreign.id)} if view_all else {str(own.id)})
    assert client.get(f"{guest_intake}{own.issue_id}/", **extra).status_code == 200
    other = client.get(f"{guest_intake}{foreign.issue_id}/", **extra)
    assert other.status_code == (200 if view_all else 403 if delegated else 404)
    if delegated:
        assert audit_for(other).succeeded is view_all


@pytest.mark.parametrize("delegated", [False, True])
def test_guest_can_edit_own_intake_name_and_description_but_not_another_authors(team, guest_intake, delegated):
    own = submit_intake(team, guest_intake, team.target, "Guest's draft")
    foreign = submit_intake(team, guest_intake, team.other, "Other draft")
    Project.objects.filter(id=team.project.id).update(guest_view_all_features=True)
    client, extra = guest_intake_client(team, delegated)
    updated = client.patch(
        f"{guest_intake}{own.issue_id}/",
        {"issue": {"name": "Guest edited", "description_html": "<p>My update</p>"}},
        format="json",
        **extra,
    )
    assert updated.status_code == 200, updated.content
    own.issue.refresh_from_db()
    assert own.issue.name == "Guest edited" and own.issue.updated_by_id == team.target.id
    denied = client.patch(f"{guest_intake}{foreign.issue_id}/", {"issue": {"name": "Not mine"}}, format="json", **extra)
    assert denied.status_code in (400, 403)
    foreign.issue.refresh_from_db()
    assert foreign.issue.name == "Other draft"


@pytest.mark.parametrize(
    "payload",
    [
        {"status": 1},
        {"status": -1, "issue": {"name": "Atomic denial"}},
        {"snoozed_till": None},
        {"issue": {"target_date": "2026-12-15"}},
        {"issue": {"priority": "urgent"}},
    ],
)
def test_delegated_guest_cannot_borrow_admin_triage_or_content_edit_rights(team, guest_intake, payload):
    own = submit_intake(team, guest_intake, team.target, "Keep original")
    response = client_for(team.token).patch(
        f"{guest_intake}{own.issue_id}/", payload, format="json", **headers(team.target)
    )
    assert response.status_code == 403, response.content
    assert not audit_for(response).succeeded
    own.refresh_from_db()
    own.issue.refresh_from_db()
    assert own.status == -2 and own.issue.name == "Keep original" and own.issue.target_date is None


@pytest.mark.parametrize("delegated", [False, True])
@pytest.mark.parametrize("accepted", [False, True])
def test_guest_can_delete_own_intake_but_not_foreign_entries_even_after_acceptance(
    team, guest_intake, delegated, accepted
):
    own = submit_intake(team, guest_intake, team.target, "Own removal")
    foreign = submit_intake(team, guest_intake, team.other, "Foreign removal")
    if accepted:
        db_models.IntakeIssue.objects.filter(id__in=[own.id, foreign.id]).update(status=1)
    Project.objects.filter(id=team.project.id).update(guest_view_all_features=True)
    client, extra = guest_intake_client(team, delegated)
    denied = client.delete(f"{guest_intake}{foreign.issue_id}/", {}, format="json", **extra)
    assert denied.status_code == 403, denied.content
    assert db_models.IntakeIssue.objects.filter(id=foreign.id).exists()
    deleted = client.delete(f"{guest_intake}{own.issue_id}/", {}, format="json", **extra)
    assert deleted.status_code == 204, deleted.content
    assert not db_models.IntakeIssue.objects.filter(id=own.id).exists()
    assert Issue.objects.filter(id=own.issue_id).exists() is accepted


@pytest.mark.parametrize("scope", ["workspace", "project"])
def test_guest_intake_act_as_requires_active_membership_in_both_scopes(team, guest_intake, scope):
    if scope == "workspace":
        WorkspaceMember.objects.filter(workspace=team.workspace, member=team.target).update(is_active=False)
    else:
        ProjectMember.objects.filter(project=team.project, member=team.target).update(is_active=False)
    response = client_for(team.token).post(
        guest_intake, {"issue": {"name": "No inactive access"}}, format="json", **headers(team.target)
    )
    assert response.status_code == 403
    assert not Issue.objects.filter(name="No inactive access").exists()


def test_guest_intake_visibility_uses_intake_creator_not_imported_issue_author(team, guest_intake):
    entry = submit_intake(team, guest_intake, team.target, "Imported authorship")
    Issue.objects.filter(id=entry.issue_id).update(created_by=team.other)
    response = client_for(team.token).get(f"{guest_intake}{entry.issue_id}/", **headers(team.target))
    assert response.status_code == 200, response.content


def test_guest_can_read_inline_images_on_visible_intakes_but_not_hidden_tickets(team, guest_intake, inline_image):
    # Rebind the fixture image to an actual guest intake submission and exercise
    # the same body ownership rules as the guest's intake detail/list.
    own = submit_intake(team, guest_intake, team.target, "Guest image submission")
    Issue.objects.filter(id=own.issue_id).update(description_html=team.issue.description_html)
    db_models.FileAsset.objects.filter(id=inline_image.asset.id).update(issue_id=own.issue_id)
    url = f"{items(team)}{own.issue_id}/inline-assets/{inline_image.asset.id}/"
    client = client_for(team.token)
    assert client.get(url, **headers(team.target)).status_code == 302
    db_models.IntakeIssue.objects.filter(id=own.id).update(created_by=team.other)
    assert client.get(url, **headers(team.target)).status_code == 403
    Project.objects.filter(id=team.project.id).update(guest_view_all_features=True)
    assert client.get(url, **headers(team.target)).status_code == 302
    # Reading a foreign regular ticket must not become possible as a side effect.
    Project.objects.filter(id=team.project.id).update(guest_view_all_features=False)
    Issue.objects.filter(id=team.issue.id).update(created_by=team.other)
    db_models.FileAsset.objects.filter(id=inline_image.asset.id).update(issue=team.issue)
    assert client.get(inline_image.url, **headers(team.target)).status_code == 403


@pytest.fixture
def file_storage(monkeypatch):
    storage = Mock()
    storage.return_value.generate_presigned_post.return_value = {
        "url": "https://storage.example/upload",
        "fields": {"policy": "signed-policy"},
    }
    storage.return_value.generate_presigned_url.return_value = "https://storage.example/download?signature=example"
    monkeypatch.setattr("plane.api.views.issue.S3Storage", storage)
    monkeypatch.setattr("plane.api.views.asset.S3Storage", storage)
    return storage


@pytest.mark.parametrize("role", [5, 15, 20])
@pytest.mark.parametrize("intake", [False, True])
@pytest.mark.parametrize("delegated", [False, True])
def test_attachment_full_lifecycle_has_same_user_rights_for_all_roles(
    team, intake_url, file_storage, role, intake, delegated
):
    WorkspaceMember.objects.filter(workspace=team.workspace, member=team.target).update(role=role)
    ProjectMember.objects.filter(project=team.project, member=team.target).update(role=role)
    Project.objects.filter(id=team.project.id).update(guest_view_all_features=False)
    issue = submit_intake(team, intake_url, team.target, "Attachment intake").issue if intake else team.issue
    client, extra = guest_intake_client(team, delegated)
    url = f"{items(team)}{issue.id}/attachments/"
    created = client.post(
        url,
        {
            "name": "message.eml",
            "type": "message/rfc822",
            "size": 12,
            "external_id": "external-message",
            "external_source": "chatbot",
        },
        format="json",
        **extra,
    )
    assert created.status_code == 200, created.content
    asset = db_models.FileAsset.objects.get(id=created.data["asset_id"])
    assert asset.created_by_id == team.target.id and asset.external_id == "external-message"
    confirmed = client.patch(f"{url}{asset.id}/", {"is_uploaded": True}, format="json", **extra)
    assert confirmed.status_code == 204, confirmed.content
    asset.refresh_from_db()
    assert asset.is_uploaded and asset.created_by_id == asset.updated_by_id == team.target.id
    listed = client.get(url, **extra)
    assert listed.status_code == 200 and str(asset.id) in str(listed.data)
    downloaded = client.get(f"{url}{asset.id}/", **extra)
    assert downloaded.status_code == 302 and downloaded["Location"].startswith("https://")
    removed = client.delete(f"{url}{asset.id}/", {}, format="json", **extra)
    assert removed.status_code == 204, removed.content
    if delegated:
        for response in (created, confirmed, listed, downloaded, removed):
            audit = audit_for(response)
            assert audit.actor_id == team.admin.id and audit.target_user_id == team.target.id and audit.succeeded
            assert response["X-PlanePlus-Act-As-User"] == str(team.target.id)


@pytest.mark.parametrize("role", [5, 15, 20])
@pytest.mark.parametrize("delegated", [False, True])
def test_generic_upload_confirm_and_bind_is_usable_as_the_effective_user(
    team, intake_url, file_storage, role, delegated
):
    WorkspaceMember.objects.filter(workspace=team.workspace, member=team.target).update(role=role)
    ProjectMember.objects.filter(project=team.project, member=team.target).update(role=role)
    issue = submit_intake(team, intake_url, team.target, "Bind upload").issue
    client, extra = guest_intake_client(team, delegated)
    url = f"/api/v1/workspaces/{team.workspace.slug}/assets/"
    created = client.post(url, {"name": "archive.zip", "size": 12, "type": "application/zip"}, format="json", **extra)
    assert created.status_code == 200, created.content
    asset = db_models.FileAsset.objects.get(id=created.data["asset_id"])
    assert asset.project_id is None and asset.issue_id is None and asset.created_by_id == team.target.id
    confirmed = client.patch(f"{url}{asset.id}/", {"is_uploaded": True}, format="json", **extra)
    assert confirmed.status_code == 204, confirmed.content
    bound = client.patch(f"{url}{asset.id}/", {"issue_id": str(issue.id)}, format="json", **extra)
    assert bound.status_code == 204, bound.content
    asset.refresh_from_db()
    assert asset.issue_id == issue.id and asset.project_id == team.project.id and asset.is_uploaded
    downloaded = client.get(f"{url}{asset.id}/", **extra)
    assert downloaded.status_code == 200, downloaded.content
    assert str(asset.id) in str(client.get(f"{items(team)}{issue.id}/attachments/", **extra).data)
    if delegated:
        assert audit_for(bound).project_id == team.project.id and audit_for(bound).issue_id == issue.id


@pytest.mark.parametrize(
    "family",
    [
        "states/",
        "labels/",
        "cycles/",
        "cycles-lite/",
        "modules/",
        "modules-lite/",
        "estimates/",
        "members/",
        "project-members-lite/",
        "intake-issues/",
    ],
)
@pytest.mark.parametrize("role", [5, 15, 20])
def test_registered_content_read_permissions_match_direct_user_calls(team, family, role):
    WorkspaceMember.objects.filter(workspace=team.workspace, member=team.target).update(role=role)
    ProjectMember.objects.filter(project=team.project, member=team.target).update(role=role)
    direct = client_for(APIToken.objects.create(user=team.target))
    delegated = client_for(team.token)
    url = f"/api/v1/workspaces/{team.workspace.slug}/projects/{team.project.id}/{family}"
    normal = direct.get(url)
    acting = delegated.get(url, **headers(team.target))
    assert normal.status_code == acting.status_code, (normal.content, acting.content)
    if normal.status_code < 300:
        assert normal.data == acting.data


@pytest.mark.parametrize("intake", [False, True])
def test_guest_cannot_upload_to_hidden_foreign_ticket_even_via_generic_assets(team, guest_intake, file_storage, intake):
    issue = (
        submit_intake(team, guest_intake, team.other, "Hidden intake").issue
        if intake
        else Issue.objects.create(
            project=team.project,
            name="Hidden ticket",
            created_by_id=team.other.id,
        )
    )
    client = client_for(team.token)
    url = f"{items(team)}{issue.id}/attachments/"
    response = client.post(
        url, {"name": "file.pdf", "type": "application/pdf", "size": 12}, format="json", **headers(team.target)
    )
    assert response.status_code == 403, response.content
    assert not db_models.FileAsset.objects.filter(issue=issue).exists()
    file_storage.assert_not_called()


@pytest.mark.parametrize("delegated", [False, True])
@pytest.mark.parametrize("size", [0, -1, "not-size", 1001])
def test_upload_size_validation_happens_before_signing_or_persisting(team, file_storage, settings, delegated, size):
    settings.FILE_SIZE_LIMIT = 1000
    client, extra = guest_intake_client(team, delegated)
    for url in (f"{items(team)}{team.issue.id}/attachments/", f"/api/v1/workspaces/{team.workspace.slug}/assets/"):
        response = client.post(url, {"name": "file.bin", "size": size}, format="json", **extra)
        assert response.status_code == 400, response.content
    assert not db_models.FileAsset.objects.exists()
    file_storage.assert_not_called()


@pytest.mark.parametrize("delegated", [False, True])
def test_generic_uploads_cannot_take_other_users_unbound_files_or_move_projects(team, file_storage, delegated):
    asset = db_models.FileAsset.objects.create(
        workspace=team.workspace,
        asset=f"{team.workspace.id}/private.pdf",
        created_by=team.other,
        entity_type="ISSUE_ATTACHMENT",
    )
    # Outside a request BaseModel does not preserve a supplied created_by; make
    # the identity snapshot explicit for this owner-isolation regression.
    db_models.FileAsset.objects.filter(id=asset.id).update(created_by=team.other)
    client, extra = guest_intake_client(team, delegated)
    url = f"/api/v1/workspaces/{team.workspace.slug}/assets/{asset.id}/"
    assert client.get(url, **extra).status_code == 403
    assert (
        client.patch(url, {"is_uploaded": True, "issue_id": str(team.issue.id)}, format="json", **extra).status_code
        == 403
    )
    asset.refresh_from_db()
    assert asset.issue_id is None and not asset.is_uploaded
    file_storage.assert_not_called()


def test_attachment_storage_failure_is_not_reported_as_success(team, file_storage):
    file_storage.return_value.generate_presigned_post.return_value = None
    response = client_for(team.token).post(
        f"{items(team)}{team.issue.id}/attachments/",
        {"name": "file.pdf", "size": 12},
        format="json",
        **headers(team.target),
    )
    assert response.status_code == 503, response.content
    assert not audit_for(response).succeeded and not db_models.FileAsset.objects.exists()


@pytest.fixture
def local_s3_transport(monkeypatch):
    """A local S3 POST/GET protocol fixture, not a live MinIO deployment.

    Uses the REAL boto3 signer and real HTTP/multipart bytes. The receiver checks
    the POST-policy HMAC and size/type bounds; no external network is involved.
    """
    import base64
    import hashlib
    import hmac
    from io import BytesIO
    from django.http.multipartparser import MultiPartParser
    from django.core.files.uploadhandler import MemoryFileUploadHandler
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from urllib.parse import unquote, urlsplit, parse_qs

    class StorageHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            fields, files = MultiPartParser(
                {"CONTENT_TYPE": self.headers["Content-Type"], "CONTENT_LENGTH": len(body)},
                BytesIO(body),
                [MemoryFileUploadHandler()],
                "utf-8",
            ).parse()
            content = files["file"].read()
            scope = fields["x-amz-credential"].split("/")
            key = b"AWS4testing-secret"
            for value in scope[1:]:
                key = hmac.new(key, value.encode(), hashlib.sha256).digest()
            signature = hmac.new(key, fields["policy"].encode(), hashlib.sha256).hexdigest()
            policy = json.loads(base64.b64decode(fields["policy"]))
            bounds = next(
                condition
                for condition in policy["conditions"]
                if isinstance(condition, list) and condition[0] == "content-length-range"
            )
            valid = hmac.compare_digest(signature, fields["x-amz-signature"]) and bounds[1] <= len(content) <= bounds[2]
            valid = valid and {"Content-Type": fields["Content-Type"]} in policy["conditions"]
            self.server.received_headers.append(dict(self.headers))
            if valid:
                self.server.objects[fields["key"]] = content
            self.send_response(204 if valid else 403)
            self.end_headers()

        def do_GET(self):
            parsed = urlsplit(self.path)
            key = unquote(parsed.path).removeprefix("/uploads/")
            content = self.server.objects.get(key)
            valid = content is not None and bool(parse_qs(parsed.query).get("X-Amz-Signature"))
            self.server.received_headers.append(dict(self.headers))
            self.send_response(200 if valid else 404)
            self.end_headers()
            if valid:
                self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), StorageHandler)
    server.objects, server.received_headers = {}, []
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for key, value in {
        "USE_MINIO": "0",
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing-secret",
        "AWS_REGION": "us-east-1",
        "AWS_S3_BUCKET_NAME": "uploads",
        "AWS_S3_ENDPOINT_URL": f"http://127.0.0.1:{server.server_port}",
    }.items():
        monkeypatch.setenv(key, value)
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("delegated", [False, True])
@pytest.mark.parametrize("generic", [False, True])
def test_guest_intake_attachment_real_signed_multipart_roundtrip(
    team, guest_intake, local_s3_transport, delegated, generic
):
    import requests

    issue = submit_intake(team, guest_intake, team.target, "Real file transport").issue
    client, extra = guest_intake_client(team, delegated)
    attachments = f"{items(team)}{issue.id}/attachments/"
    assets = f"/api/v1/workspaces/{team.workspace.slug}/assets/"
    payload = b"From: person@example.com\r\nSubject: An attachment\r\n\r\nReal file content\x00\xff"
    created = client.post(
        assets if generic else attachments,
        {"name": "message.eml", "type": "message/rfc822", "size": len(payload)},
        format="json",
        **extra,
    )
    assert created.status_code == 200, created.content
    upload = created.data["upload_data"]
    transferred = requests.post(
        upload["url"], data=upload["fields"], files={"file": ("message.eml", payload, "message/rfc822")}, timeout=5
    )
    assert transferred.status_code == 204, transferred.content
    asset_id = created.data["asset_id"]
    confirmation = {"is_uploaded": True, **({"issue_id": str(issue.id)} if generic else {})}
    confirmed = client.patch(f"{assets if generic else attachments}{asset_id}/", confirmation, format="json", **extra)
    assert confirmed.status_code == 204, confirmed.content
    downloaded = client.get(f"{attachments}{asset_id}/", **extra)
    assert downloaded.status_code == 302, downloaded.content
    data = requests.get(downloaded["Location"], timeout=5)
    assert data.status_code == 200 and data.content == payload
    for request_headers in local_s3_transport.received_headers:
        assert not {"x-api-key", "x-planeplus-act-as-user", "x-planeplus-act-as-reason"} & {
            name.lower() for name in request_headers
        }
    asset = db_models.FileAsset.objects.get(id=asset_id)
    assert asset.created_by_id == team.target.id and asset.issue_id == issue.id and asset.is_uploaded


@pytest.mark.parametrize("role", [5, 15, 20])
@pytest.mark.parametrize(
    "family,payload",
    [
        ("work-items/", {"name": "Parity item"}),
        ("labels/", {"name": "Parity label"}),
        ("states/", {"name": "Parity state", "color": "#00ff00", "group": "started"}),
        ("cycles/", {"name": "Parity cycle"}),
        ("modules/", {"name": "Parity module"}),
        ("estimates/", {"name": "Parity estimate", "type": "points"}),
        ("intake-issues/", {"issue": {"name": "Parity intake"}}),
    ],
)
def test_content_creation_permission_matrix_matches_direct_user(team, intake_url, file_storage, role, family, payload):
    WorkspaceMember.objects.filter(workspace=team.workspace, member=team.target).update(role=role)
    ProjectMember.objects.filter(project=team.project, member=team.target).update(role=role)
    Project.objects.filter(id=team.project.id).update(cycle_view=True, module_view=True)
    url = f"/api/v1/workspaces/{team.workspace.slug}/projects/{team.project.id}/{family}"
    direct = client_for(APIToken.objects.create(user=team.target))
    with transaction.atomic():
        normal = direct.post(url, payload, format="json")
        transaction.set_rollback(True)
    acting = client_for(team.token).post(url, payload, format="json", **headers(team.target))
    assert normal.status_code == acting.status_code, (normal.content, acting.content)
    assert audit_for(acting).succeeded is (acting.status_code < 300)


@pytest.mark.parametrize("role", [5, 15, 20])
def test_personal_notes_support_normal_crud_without_access_to_other_owners(team, role):
    WorkspaceMember.objects.filter(workspace=team.workspace, member=team.target).update(role=role)
    url = f"/api/v1/workspaces/{team.workspace.slug}/stickies/"
    client = client_for(team.token)
    created = client.post(
        url, {"name": "Personal note", "description_html": "<p>Mine</p>"}, format="json", **headers(team.target)
    )
    assert created.status_code == 201, created.content
    sticky = db_models.Sticky.objects.get(id=created.data["id"])
    assert sticky.owner_id == sticky.created_by_id == team.target.id
    assert client.get(url, **headers(team.target)).status_code == 200
    detail = f"{url}{sticky.id}/"
    assert client.get(detail, **headers(team.target)).status_code == 200
    for method in ("patch", "put"):
        updated = getattr(client, method)(
            detail, {"name": "Edited personal note"}, format="json", **headers(team.target)
        )
        assert updated.status_code == 200, updated.content
    foreign = db_models.Sticky.objects.create(workspace=team.workspace, owner=team.other, name="Not yours")
    assert (
        client.patch(f"{url}{foreign.id}/", {"name": "Takeover"}, format="json", **headers(team.target)).status_code
        == 404
    )
    assert client.patch(detail, {"owner": str(team.other.id)}, format="json", **headers(team.target)).status_code == 400
    assert client.delete(detail, {}, format="json", **headers(team.target)).status_code == 204


def test_identifier_reads_and_target_timezone_match_self_interaction(team):
    User.objects.filter(id=team.target.id).update(user_timezone="Asia/Tokyo")
    normal = client_for(APIToken.objects.create(user=team.target)).get(f"{items(team)}{team.issue.id}/")
    url = f"/api/v1/workspaces/{team.workspace.slug}/work-items/{team.project.identifier}-{team.issue.sequence_id}/"
    response = client_for(team.token).get(url, **headers(team.target))
    assert response.status_code == 200, response.content
    assert normal.data == response.data
    assert audit_for(response).resource_id == audit_for(response).issue_id == team.issue.id


def test_cycle_transfer_is_audited_scoped_and_attributed_to_the_target(team):
    old = db_models.Cycle.objects.create(
        project=team.project, name="Source", owned_by=team.target, end_date=timezone.now() - timedelta(days=1)
    )
    new = db_models.Cycle.objects.create(project=team.project, name="Destination", owned_by=team.target)
    db_models.CycleIssue.objects.create(project=team.project, cycle=old, issue=team.issue)
    url = f"/api/v1/workspaces/{team.workspace.slug}/projects/{team.project.id}/cycles/{old.id}/transfer-issues/"
    response = client_for(team.token).post(url, {"new_cycle_id": str(new.id)}, format="json", **headers(team.target))
    assert response.status_code == 200, response.content
    assignment = db_models.CycleIssue.objects.get(issue=team.issue)
    assert assignment.cycle_id == new.id and assignment.updated_by_id == team.target.id
    assert audit_for(response).resource_id == old.id
    foreign = Project.objects.create(workspace=team.workspace, name="Foreign", identifier="FOREIGN")
    foreign_cycle = db_models.Cycle.objects.create(project=foreign, name="Secret", owned_by=team.other)
    denied = client_for(team.token).post(
        url, {"new_cycle_id": str(foreign_cycle.id)}, format="json", **headers(team.target)
    )
    assert denied.status_code == 403


def test_guest_submission_can_initialize_missing_intake_and_triage_like_normal_create(team, guest_intake):
    db_models.Intake.objects.filter(project=team.project).delete()
    State.triage_objects.filter(project=team.project).delete()
    response = client_for(team.token).post(
        guest_intake, {"issue": {"name": "First guest submission"}}, format="json", **headers(team.target)
    )
    assert response.status_code == 201, response.content
    assert db_models.Intake.objects.filter(project=team.project, is_default=True).exists()
    assert State.triage_objects.filter(project=team.project).exists()


def test_workspace_admin_target_can_edit_metadata_without_artificial_project_admin_role(team):
    WorkspaceMember.objects.filter(workspace=team.workspace, member=team.target).update(role=20)
    ProjectMember.objects.filter(project=team.project, member=team.target).update(role=5)
    url = f"/api/v1/workspaces/{team.workspace.slug}/projects/{team.project.id}/"
    normal = client_for(APIToken.objects.create(user=team.target)).patch(url, {"name": "Self metadata"}, format="json")
    acting = client_for(team.token).patch(url, {"name": "Delegated metadata"}, format="json", **headers(team.target))
    assert normal.status_code == acting.status_code == 200, (normal.content, acting.content)


@pytest.mark.parametrize("delegated", [False, True])
@pytest.mark.parametrize("visible", [False, True])
def test_guest_attachment_visibility_cannot_be_bypassed_by_generic_endpoint(
    team,
    guest_intake,
    file_storage,
    delegated,
    visible,
):
    intake = submit_intake(team, guest_intake, team.other, "Foreign submission")
    Project.objects.filter(id=team.project.id).update(guest_view_all_features=visible)
    asset = db_models.FileAsset.objects.create(
        workspace=team.workspace,
        project=team.project,
        issue=intake.issue,
        asset=f"{team.workspace.id}/foreign.pdf",
        created_by=team.other,
        entity_type="ISSUE_ATTACHMENT",
        is_uploaded=True,
    )
    client, extra = guest_intake_client(team, delegated)
    attachments = f"{items(team)}{intake.issue_id}/attachments/"
    generic = f"/api/v1/workspaces/{team.workspace.slug}/assets/{asset.id}/"
    assert client.get(attachments, **extra).status_code == (200 if visible else 403)
    assert client.get(f"{attachments}{asset.id}/", **extra).status_code == (302 if visible else 403)
    assert client.get(generic, **extra).status_code == (200 if visible else 403)
    if not visible:
        assert client.patch(generic, {"is_uploaded": True}, format="json", **extra).status_code == 403
        assert client.delete(f"{attachments}{asset.id}/", {}, format="json", **extra).status_code == 403
        file_storage.assert_not_called()


@pytest.mark.parametrize("delegated", [False, True])
def test_attachment_upload_uses_intake_submission_owner_not_imported_issue_author(
    team,
    guest_intake,
    file_storage,
    delegated,
):
    intake = submit_intake(team, guest_intake, team.target, "Imported ticket")
    Issue.objects.filter(id=intake.issue_id).update(created_by=team.other)
    client, extra = guest_intake_client(team, delegated)
    response = client.post(
        f"{items(team)}{intake.issue_id}/attachments/", {"name": "file.eml", "size": 12}, format="json", **extra
    )
    assert response.status_code == 200, response.content


@pytest.mark.parametrize("delegated", [False, True])
def test_generic_upload_cannot_be_moved_to_a_different_project(team, file_storage, delegated):
    client, extra = guest_intake_client(team, delegated)
    url = f"/api/v1/workspaces/{team.workspace.slug}/assets/"
    created = client.post(
        url, {"name": "file.pdf", "size": 12, "project_id": str(team.project.id)}, format="json", **extra
    )
    assert created.status_code == 200, created.content
    project = Project.objects.create(workspace=team.workspace, name="Other", identifier="OTHER")
    ProjectMember.objects.create(workspace=team.workspace, project=project, member=team.target, role=15)
    issue = Issue.objects.create(project=project, name="Other project item")
    denied = client.patch(f"{url}{created.data['asset_id']}/", {"issue_id": str(issue.id)}, format="json", **extra)
    assert denied.status_code in {403, 404}, denied.content
    asset = db_models.FileAsset.objects.get(id=created.data["asset_id"])
    assert asset.project_id == team.project.id and asset.issue_id is None


@pytest.mark.parametrize("delegated", [False, True])
@pytest.mark.parametrize("revocation", ["project_member", "workspace_member", "archived_project", "draft"])
def test_attachment_access_is_rechecked_after_membership_or_ticket_changes(team, file_storage, delegated, revocation):
    client, extra = guest_intake_client(team, delegated)
    url = f"{items(team)}{team.issue.id}/attachments/"
    created = client.post(url, {"name": "file.pdf", "size": 12}, format="json", **extra)
    assert created.status_code == 200, created.content
    if revocation == "project_member":
        ProjectMember.objects.filter(project=team.project, member=team.target).update(is_active=False)
    elif revocation == "workspace_member":
        WorkspaceMember.objects.filter(workspace=team.workspace, member=team.target).update(is_active=False)
    elif revocation == "archived_project":
        Project.objects.filter(id=team.project.id).update(archived_at=timezone.now())
    else:
        Issue.objects.filter(id=team.issue.id).update(is_draft=True)
    response = client.patch(f"{url}{created.data['asset_id']}/", {"is_uploaded": True}, format="json", **extra)
    assert response.status_code in {403, 404}, response.content
    assert not db_models.FileAsset.objects.get(id=created.data["asset_id"]).is_uploaded


@pytest.mark.parametrize("generic", [False, True])
def test_attachment_audit_failure_rolls_back_file_record_and_redacts_signed_policy(
    team,
    file_storage,
    monkeypatch,
    isolated_act_as,
    generic,
):
    client = client_for(team.token)
    url = (
        f"/api/v1/workspaces/{team.workspace.slug}/assets/" if generic else f"{items(team)}{team.issue.id}/attachments/"
    )
    created = client.post(url, {"name": "file.pdf", "size": 12}, format="json", **headers(team.target))
    assert created.status_code == 200, created.content
    assert created["Cache-Control"] == "private, no-store"
    assert isolated_act_as.logs.call_args.kwargs["log_data"]["response_body"] == "[REDACTED]"
    monkeypatch.setattr(ImpersonationAudit.objects, "create", Mock(side_effect=IntegrityError("audit unavailable")))
    failed = client.post(url, {"name": "second.pdf", "size": 12}, format="json", **headers(team.target))
    assert failed.status_code == 503, failed.content
    assert "signed-policy" not in str(failed.content)
    assert db_models.FileAsset.objects.count() == 1


@pytest.mark.parametrize("family", ["cycles/", "cycles-lite/", "modules/", "modules-lite/"])
@pytest.mark.parametrize("visible", [False, True])
def test_guest_aggregate_reads_never_bypass_hidden_ticket_visibility(team, guest_intake, family, visible):
    Project.objects.filter(id=team.project.id).update(guest_view_all_features=visible)
    url = f"/api/v1/workspaces/{team.workspace.slug}/projects/{team.project.id}/{family}"
    direct, _extra = guest_intake_client(team, False)
    normal = direct.get(url)
    acting = client_for(team.token).get(url, **headers(team.target))
    assert normal.status_code == acting.status_code == (200 if visible else 403)


@pytest.mark.django_db(transaction=True)
def test_concurrent_public_join_creates_one_membership_and_two_audits(team):
    project = public_project(team)
    second_token = APIToken.objects.create(user=team.admin)
    barrier = Barrier(2)

    def join(token):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            response = client_for(token).post(join_url(team, project), {}, format="json", **headers(team.target))
            return response.status_code, response.content
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(join, token) for token in (team.token, second_token)]
        results = [future.result(timeout=30) for future in futures]
    assert sorted(status for status, _ in results) == [200, 201], results
    assert ProjectMember.objects.filter(project=project, member=team.target).count() == 1
    assert db_models.ProjectUserProperty.objects.filter(project=project, user=team.target).count() == 1
    assert ImpersonationAudit.objects.filter(project_id=project.id, action="project.join", succeeded=True).count() == 2


@pytest.mark.django_db(transaction=True)
def test_parallel_requests_isolate_target_authorship_and_audit(team):
    second_token = APIToken.objects.create(user=team.admin)
    barrier = Barrier(2)

    def write(token, target):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            response = client_for(token).post(
                comments(team),
                {"comment_html": f"<p>{target.id}</p>"},
                format="json",
                **headers(target),
            )
            assert response.status_code == 201, response.data
            assert get_act_as_context() is None
            return response.data["id"], response[AUDIT_HEADER], target.id
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(write, token, target)
            for token, target in ((team.token, team.target), (second_token, team.other))
        ]
        results = [future.result(timeout=30) for future in futures]
    for comment_id, audit_id, target_id in results:
        comment = IssueComment.objects.get(id=comment_id)
        audit = ImpersonationAudit.objects.get(id=audit_id)
        assert comment.actor_id == comment.created_by_id == target_id
        assert audit.target_user_id == target_id and audit.actor_id == team.admin.id
    normal = client_for(team.token).post(comments(team), {"comment_html": "<p>Admin again</p>"}, format="json")
    assert normal.status_code == 201
    assert IssueComment.objects.get(id=normal.data["id"]).actor_id == team.admin.id

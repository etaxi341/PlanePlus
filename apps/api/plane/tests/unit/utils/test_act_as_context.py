# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from rest_framework.exceptions import PermissionDenied

from plane.api.act_as import ContentRequest
from plane.utils.act_as import content_author, content_task_delay, content_user_for_model, get_act_as_context


pytestmark = pytest.mark.unit


def context():
    return SimpleNamespace(
        operation=SimpleNamespace(resource_type="issue"),
        actor=SimpleNamespace(id=uuid4()),
        target=SimpleNamespace(id=uuid4()),
        project=SimpleNamespace(id=uuid4()),
        workspace=SimpleNamespace(id=uuid4()),
        origin="https://planeplus.example",
    )


def test_facade_never_changes_original_user_auth_or_underlying_request():
    scope = context()
    django_request = SimpleNamespace(user=scope.actor)
    original = SimpleNamespace(user=scope.actor, auth="original-token", _request=django_request)
    facade = ContentRequest(original, scope)
    assert facade.user is scope.target
    assert facade.authenticated_user is scope.actor
    assert facade.auth == original.auth
    assert original.user is django_request.user is scope.actor


def test_scope_is_reset_after_exception_and_nested_requests():
    first, second = context(), context()
    with content_author(first):
        assert get_act_as_context() is first
        with pytest.raises(ValueError), content_author(second):
            assert get_act_as_context() is second
            raise ValueError("handler failed")
        assert get_act_as_context() is first
    assert get_act_as_context() is None


def test_even_an_allowlisted_handler_cannot_write_administration_models_or_other_projects():
    scope = context()
    instance = SimpleNamespace(
        _meta=SimpleNamespace(label_lower="db.apitoken"),
        project_id=scope.project.id,
        workspace_id=scope.workspace.id,
    )
    with content_author(scope), pytest.raises(PermissionDenied):
        content_user_for_model(instance)
    instance._meta.label_lower = "db.issue"
    instance.project_id = uuid4()
    with content_author(scope), pytest.raises(PermissionDenied):
        content_user_for_model(instance)
    instance.project_id = scope.project.id
    with content_author(scope):
        assert content_user_for_model(instance) is scope.target
    assert content_user_for_model(instance) is None


def test_async_tasks_with_different_targets_do_not_leak_context():
    async def run():
        async def write(scope):
            with content_author(scope):
                await asyncio.sleep(0)
                assert get_act_as_context() is scope
                return get_act_as_context().target.id

        scopes = [context(), context()]
        assert await asyncio.gather(*(write(scope) for scope in scopes)) == [scope.target.id for scope in scopes]
        assert get_act_as_context() is None

    asyncio.run(run())


def test_normal_task_dispatch_is_unchanged():
    task = Mock()
    content_task_delay(task, "arg", actor_id="original")
    task.delay.assert_called_once_with("arg", actor_id="original")

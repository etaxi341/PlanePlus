# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Request-local content authorship, deliberately separate from authentication."""

from contextlib import contextmanager
from contextvars import ContextVar

from django.db import transaction
from rest_framework.exceptions import PermissionDenied


_content_context = ContextVar("planeplus_content_author", default=None)
CONTENT_MODELS = frozenset(
    {
        "db.issue",
        "db.issuecomment",
        "db.issuelink",
        "db.issuesequence",
        "db.issueassignee",
        "db.issuelabel",
        "db.description",
    }
)


def get_act_as_context():
    return _content_context.get()


@contextmanager
def content_author(context):
    token = _content_context.set(context)
    try:
        yield
    finally:
        _content_context.reset(token)


def content_user_for_model(instance):
    context = get_act_as_context()
    if context is None:
        return None
    # Fail closed if a future handler adds identity/administration writes, or
    # accidentally saves content belonging to a different project/workspace.
    if (
        instance._meta.label_lower not in CONTENT_MODELS
        or str(getattr(instance, "project_id", None)) != str(context.project.id)
        or str(getattr(instance, "workspace_id", None)) != str(context.workspace.id)
    ):
        raise PermissionDenied("This model cannot be written using act-as.")
    return context.target


def content_task_delay(task, *args, **kwargs):
    """Keep normal API behaviour; publish impersonated side effects only after commit."""
    context = get_act_as_context()
    if context is None:
        return task.delay(*args, **kwargs)
    if "type" in kwargs and "issue_id" in kwargs:
        kwargs.setdefault("notification", True)
        kwargs.setdefault("origin", context.origin)
        if kwargs["type"] == "issue.activity.updated":
            kwargs["preserve_history"] = True
    # The audited content transaction must commit before a worker can see it.
    # Broker errors are logged by Django, not reported as an uncommitted write
    # that clients might retry and duplicate. Delivery uses Plane's existing tasks.
    transaction.on_commit(lambda: task.delay(*args, **kwargs), robust=True)

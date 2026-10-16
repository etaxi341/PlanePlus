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
    resource = context.operation.resource_type
    allowed = (
        set(CONTENT_MODELS)
        if resource in {"issue", "issue_comment", "issue_link"}
        else {
            "state": {"db.state"},
            "label": {"db.label"},
            "cycle": {"db.cycle"},
            "module": {"db.module", "db.modulemember"},
            "cycle_issue": {"db.cycleissue"},
            "cycle_transfer": {"db.cycle", "db.cycleissue"},
            "module_issue": {"db.moduleissue"},
            "project": {"db.project", "db.projectidentifier", "db.intake"},
            "estimate": {"db.estimate", "db.estimatepoint", "db.project"},
            "estimate_point": {"db.estimatepoint"},
            "issue_relation": {"db.issuerelation"},
            "file_asset": {"db.fileasset"},
            "generic_asset": {"db.fileasset"},
            "sticky": {"db.sticky"},
            "intake_issue": set(CONTENT_MODELS) | {"db.intakeissue", "db.state", "db.intake"},
            "project_member": {"db.projectmember", "db.projectuserproperty"},
        }.get(resource, set())
    )
    model = instance._meta.label_lower
    project_id = instance.id if model == "db.project" else getattr(instance, "project_id", None)
    if (
        model not in allowed
        or (context.project is None and resource not in {"generic_asset", "sticky"})
        or str(project_id) != str(context.project.id if context.project else None)
        or str(getattr(instance, "workspace_id", None)) != str(context.workspace.id)
    ):
        raise PermissionDenied("This model cannot be written using act-as.")
    if resource == "project_member":
        member_id = getattr(instance, "member_id", None) or getattr(instance, "user_id", None)
        if member_id != context.target.id:
            raise PermissionDenied("Public join cannot change another user's membership.")
    if resource == "sticky" and instance.owner_id != context.target.id:
        raise PermissionDenied("Personal notes can only be written by their owner.")
    if resource == "intake_issue" and model in {"db.state", "db.intake"}:
        if not instance._state.adding or (model == "db.state" and instance.group != "triage"):
            raise PermissionDenied("Intake may initialize triage, not modify project workflow configuration.")
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


def content_bulk_authors(objects, update=False):
    """Django bulk operations bypass save(); retain scoped attribution there too."""
    if get_act_as_context() is not None:
        for obj in objects:
            user = content_user_for_model(obj)
            if not update:
                obj.created_by = user
            obj.updated_by = user
    return objects

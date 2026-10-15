# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import uuid

from django.db import models


class AppendOnlyAuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise TypeError("Impersonation audit entries are append-only.")

    def delete(self):
        raise TypeError("Impersonation audit entries are append-only.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise TypeError("Impersonation audit entries are append-only.")


class ImpersonationAudit(models.Model):
    """Immutable identity snapshots, intentionally without cascading foreign keys.

    Application guards complement the PostgreSQL UPDATE/DELETE/TRUNCATE trigger
    installed by the migration. Even deleting a user/workspace preserves evidence.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor_id = models.UUIDField(null=True)
    target_user_id = models.UUIDField(null=True)
    requested_user = models.CharField(max_length=64, blank=True)
    token_id = models.UUIDField(null=True)
    workspace_id = models.UUIDField(null=True)
    project_id = models.UUIDField(null=True)
    issue_id = models.UUIDField(null=True)
    resource_id = models.UUIDField(null=True)
    resource_type = models.CharField(max_length=32, blank=True)
    action = models.CharField(max_length=64)
    method = models.CharField(max_length=10)
    path = models.TextField()
    reason = models.CharField(max_length=500, blank=True)
    succeeded = models.BooleanField()
    response_status = models.PositiveSmallIntegerField()
    error_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyAuditQuerySet.as_manager()

    class Meta:
        db_table = "planeplus_impersonation_audit"
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["workspace_id", "created_at"], name="act_as_ws_time_idx"),
            models.Index(fields=["project_id", "issue_id", "created_at"], name="act_as_issue_time_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise TypeError("Impersonation audit entries are append-only.")
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise TypeError("Impersonation audit entries are append-only.")

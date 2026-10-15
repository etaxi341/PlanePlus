# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import uuid

from django.db import migrations, models


AUDIT_GUARD_SQL = """
CREATE OR REPLACE FUNCTION planeplus_reject_audit_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'PlanePlus impersonation audit is append-only' USING ERRCODE = '42501';
END;
$$;
CREATE TRIGGER planeplus_audit_immutable
BEFORE UPDATE OR DELETE OR TRUNCATE ON planeplus_impersonation_audit
FOR EACH STATEMENT EXECUTE FUNCTION planeplus_reject_audit_mutation();
"""

DROP_AUDIT_GUARD_SQL = """
DROP TRIGGER IF EXISTS planeplus_audit_immutable ON planeplus_impersonation_audit;
DROP FUNCTION IF EXISTS planeplus_reject_audit_mutation();
"""


class Migration(migrations.Migration):
    dependencies = [("db", "0122_alter_draftissue_assignees_alter_issue_assignees_and_more")]

    operations = [
        migrations.CreateModel(
            name="ImpersonationAudit",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("actor_id", models.UUIDField(null=True)),
                ("target_user_id", models.UUIDField(null=True)),
                ("requested_user", models.CharField(blank=True, max_length=64)),
                ("token_id", models.UUIDField(null=True)),
                ("workspace_id", models.UUIDField(null=True)),
                ("project_id", models.UUIDField(null=True)),
                ("issue_id", models.UUIDField(null=True)),
                ("resource_id", models.UUIDField(null=True)),
                ("resource_type", models.CharField(blank=True, max_length=32)),
                ("action", models.CharField(max_length=64)),
                ("method", models.CharField(max_length=10)),
                ("path", models.TextField()),
                ("reason", models.CharField(blank=True, max_length=500)),
                ("succeeded", models.BooleanField()),
                ("response_status", models.PositiveSmallIntegerField()),
                ("error_code", models.CharField(blank=True, max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "planeplus_impersonation_audit",
                "ordering": ("-created_at", "-id"),
                "indexes": [
                    models.Index(fields=["workspace_id", "created_at"], name="act_as_ws_time_idx"),
                    models.Index(fields=["project_id", "issue_id", "created_at"], name="act_as_issue_time_idx"),
                ],
            },
        ),
        migrations.RunSQL(AUDIT_GUARD_SQL, DROP_AUDIT_GUARD_SQL),
    ]

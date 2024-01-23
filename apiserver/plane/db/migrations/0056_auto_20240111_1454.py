# Generated by Django 4.2.7 on 2024-01-11 14:54

from django.db import migrations


def create_notification_preferences(apps, schema_editor):
    UserNotificationPreference = apps.get_model("db", "UserNotificationPreference")
    User = apps.get_model("db", "User")

    bulk_notification_preferences = []
    for user_id in User.objects.filter(is_bot=False).values_list("id", flat=True):
        bulk_notification_preferences.append(
            UserNotificationPreference(
                user_id=user_id,
                created_by_id=user_id,
            )
        )
    UserNotificationPreference.objects.bulk_create(
        bulk_notification_preferences, batch_size=1000, ignore_conflicts=True
    )


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0055_usernotificationpreference_emailnotificationlog"),
    ]

    operations = [
        migrations.RunPython(create_notification_preferences),
    ]

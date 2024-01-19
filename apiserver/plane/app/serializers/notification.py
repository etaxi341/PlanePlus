# Module imports
from .base import BaseSerializer
from .user import UserLiteSerializer
from plane.db.models import Notification, EmailNotificationLog, UserNotificationPreference



class NotificationSerializer(BaseSerializer):
    triggered_by_details = UserLiteSerializer(
        read_only=True, source="triggered_by"
    )

    class Meta:
        model = Notification
        fields = "__all__"


class UserNotificationPreferenceSerializer(BaseSerializer):

    class Meta:
        model = UserNotificationPreference
        fields = "__all__"


class EmailNotificationLogSerializer(BaseSerializer):

    class Meta:
        model = EmailNotificationLog
        fields = "__all__"


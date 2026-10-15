# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from unittest.mock import Mock, patch

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIRequestFactory

from plane.authentication.rate_limit import AuthenticationThrottle
from plane.authentication.views.app.email import SignInAuthEndpoint


MODULE = "plane.authentication.views.app.email"


@pytest.mark.unit
class TestSwgAuthentication:
    @pytest.fixture(autouse=True)
    def setup(self, settings, monkeypatch):
        settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
        cache.clear()
        monkeypatch.delenv("ALLOWED_EMAIL_DOMAIN", raising=False)
        self.factory = APIRequestFactory()
        self.credentials = {"email": "user@example.com", "password": "example-password"}
        with (
            patch(f"{MODULE}.Instance.objects.first", return_value=Mock(is_setup_done=True)),
            patch(f"{MODULE}.http_requests.post") as swg,
            patch(f"{MODULE}.get_client_ip_details", return_value={"resolved_ip": "203.0.113.42"}),
        ):
            self.swg = swg
            self.swg.return_value.json.return_value = {"Success": False, "ErrorCode": "WrongPassword"}
            yield
        cache.clear()

    def post(self, data):
        request = self.factory.post("/auth/sign-in/", data, format="json")
        return SignInAuthEndpoint.as_view(authentication_classes=[])(request)

    def test_external_login_keeps_two_factor_code_and_client_ip(self):
        self.swg.return_value.json.return_value = {
            "Success": False,
            "TwoFactorSettings": {"MissingTwoFactorCode": True},
        }
        response = self.post({**self.credentials, "two_factor_code": "123456"})

        assert response.status_code == status.HTTP_200_OK
        assert response.data["requires_two_factor"] is True
        kwargs = self.swg.call_args.kwargs
        assert kwargs["json"] == {
            "UPN": "user@example.com",
            "Password": "example-password",
            "TwoFactorCode": "123456",
            "ClientIPAddress": "203.0.113.42",
        }
        assert kwargs["headers"]["X-SWG-Client-IP"] == "203.0.113.42"
        assert kwargs["timeout"] == 60

    def test_password_sign_in_throttles_with_json_before_calling_swg(self):
        with patch.object(AuthenticationThrottle, "rate", "1/minute"):
            first = self.post(self.credentials)
            second = self.post(self.credentials)

        assert first.status_code == status.HTTP_401_UNAUTHORIZED
        assert first.data["error_code"] == "WRONG_PASSWORD"
        assert second.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert "Location" not in second
        self.swg.assert_called_once()

    @pytest.mark.parametrize(
        ("is_bot", "is_active", "last_logout_time", "error"),
        [
            (True, True, None, "BOT_USER_LOGIN_FORBIDDEN"),
            (False, False, timezone.now(), "USER_ACCOUNT_DEACTIVATED"),
        ],
    )
    def test_swg_success_cannot_bypass_upstream_account_guards(self, is_bot, is_active, last_logout_time, error):
        user = Mock(is_bot=is_bot, is_active=is_active, last_logout_time=last_logout_time)
        self.swg.return_value.json.return_value = {"Success": True, "Token": "test-token"}
        with patch(f"{MODULE}.User.objects.filter") as users, patch(f"{MODULE}.user_login") as login:
            users.return_value.first.return_value = user
            response = self.post(self.credentials)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error_message"] == error
        user.set_password.assert_not_called()
        user.save.assert_not_called()
        login.assert_not_called()

    def test_swg_success_logs_in_existing_human(self):
        user = Mock(is_bot=False, is_active=True, last_logout_time=None)
        self.swg.return_value.json.return_value = {"Success": True, "Token": "test-token"}
        with (
            patch(f"{MODULE}.User.objects.filter") as users,
            patch(f"{MODULE}.user_login") as login,
            patch(f"{MODULE}.get_redirection_path", return_value="/workspace/"),
        ):
            users.return_value.first.return_value = user
            response = self.post(self.credentials)

        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"success": True, "redirect_path": "/workspace/"}
        user.set_password.assert_called_once_with(self.credentials["password"])
        login.assert_called_once()

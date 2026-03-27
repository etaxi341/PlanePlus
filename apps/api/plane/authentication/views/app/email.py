# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import logging
import os
import requests as http_requests

# Django imports
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import HttpResponseRedirect
from django.views import View

# Third party imports
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

# Module imports
from plane.authentication.provider.credentials.email import EmailProvider
from plane.authentication.utils.login import user_login
from plane.license.models import Instance
from plane.authentication.utils.host import base_host
from plane.authentication.utils.redirection_path import get_redirection_path
from plane.authentication.utils.user_auth_workflow import post_user_auth_workflow
from plane.db.models import User
from plane.authentication.adapter.error import (
    AuthenticationException,
    AUTHENTICATION_ERROR_CODES,
)
from plane.authentication.rate_limit import AuthenticationThrottle
from plane.utils.path_validator import get_safe_redirect_url
from plane.utils.ip_address import get_client_ip_details

auth_logger = logging.getLogger("plane.api.auth")

# SWG Auth API base URL
SWG_AUTH_API = os.environ.get("SWG_RESTSERVICE_URL", "https://services.swg.de/api/Auth").rstrip("/")


class SignInAuthEndpoint(APIView):
    """
    JSON-based sign-in endpoint that authenticates against the SWG internal
    auth service (https://services.swg.de/api/Auth/Login).

    Accepts JSON body:
      - email (string, required) — used as UPN
      - password (string, required)
      - two_factor_code (string, optional)

    Returns JSON with authentication result and 2FA status.
    """
    permission_classes = [AllowAny]
    throttle_classes = [AuthenticationThrottle]

    def post(self, request):
        client_ip_details = get_client_ip_details(request)
        # Check instance configuration
        instance = Instance.objects.first()
        if instance is None or not instance.is_setup_done:
            return Response(
                {
                    "error_code": "INSTANCE_NOT_CONFIGURED",
                    "error_message": "Instance not configured. Please contact your administrator.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        email = request.data.get("email", "").strip().lower()
        password = request.data.get("password", "")
        two_factor_code = request.data.get("two_factor_code", "")

        # Validate required fields
        if not email or not password:
            return Response(
                {
                    "error_code": "REQUIRED_EMAIL_PASSWORD_SIGN_IN",
                    "error_message": "Email and password are required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate email format
        try:
            validate_email(email)
        except ValidationError:
            return Response(
                {
                    "error_code": "INVALID_EMAIL_SIGN_IN",
                    "error_message": "Invalid email address.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Build the payload for SWG Auth API
        login_payload = {
            "UPN": email,
            "Password": password,
        }
        if two_factor_code:
            login_payload["TwoFactorCode"] = two_factor_code
        if client_ip_details.get("resolved_ip"):
            login_payload["ClientIPAddress"] = client_ip_details["resolved_ip"]

        request_headers = {
            "Content-Type": "application/json",
        }
        if client_ip_details.get("resolved_ip"):
            request_headers["X-SWG-Client-IP"] = client_ip_details["resolved_ip"]

        auth_logger.info(
            "SWG auth request started",
            extra={
                "remote_addr": client_ip_details.get("remote_addr"),
                "x_forwarded_for": client_ip_details.get("x_forwarded_for"),
                "x_real_ip": client_ip_details.get("x_real_ip"),
                "x_swg_client_ip": client_ip_details.get("x_swg_client_ip"),
                "forwarded": client_ip_details.get("forwarded"),
                "resolved_client_ip": client_ip_details.get("resolved_ip"),
                "trusted_proxy": client_ip_details.get("trusted_proxy"),
                "auth_has_two_factor_code": bool(two_factor_code),
                "auth_endpoint": f"{SWG_AUTH_API}/Login",
            },
        )

        # Call SWG Auth API
        try:
            swg_response = http_requests.post(
                f"{SWG_AUTH_API}/Login",
                json=login_payload,
                headers=request_headers,
                timeout=60,
            )
        except http_requests.RequestException:
            auth_logger.exception(
                "SWG auth request failed",
                extra={
                    "resolved_client_ip": client_ip_details.get("resolved_ip"),
                    "auth_endpoint": f"{SWG_AUTH_API}/Login",
                },
            )
            return Response(
                {
                    "error_code": "AUTH_SERVICE_UNAVAILABLE",
                    "error_message": "Authentication service is currently unavailable. Please try again later.",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            swg_data = swg_response.json()
        except ValueError:
            return Response(
                {
                    "error_code": "AUTH_SERVICE_ERROR",
                    "error_message": "Unexpected response from authentication service.",
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # Check if login succeeded
        if swg_data.get("Success") is True and swg_data.get("Token"):
            # Authentication successful — provision or login the user
            return self._handle_successful_login(request, email, password, swg_data)

        # Login failed — check 2FA status and error codes
        two_factor_settings = swg_data.get("twoFactorSettings") or swg_data.get("TwoFactorSettings") or {}
        error_code_swg = swg_data.get("ErrorCode") or swg_data.get("errorCode") or ""

        auth_logger.info(
            "SWG auth response received",
            extra={
                "resolved_client_ip": client_ip_details.get("resolved_ip"),
                "auth_success": swg_data.get("Success") is True and bool(swg_data.get("Token")),
                "auth_error_code": error_code_swg,
                "auth_requires_two_factor": two_factor_settings.get("MissingTwoFactorCode", False),
                "auth_needs_two_factor_setup": two_factor_settings.get("NeedsToEnableTwoFactorCode", False),
            },
        )

        # 2FA: User needs to enable 2FA first (NeedsToEnableTwoFactorCode)
        needs_enable_2fa = two_factor_settings.get("NeedsToEnableTwoFactorCode", False)
        if needs_enable_2fa:
            return Response(
                {
                    "error_code": "NEEDS_TWO_FACTOR_SETUP",
                    "error_message": "Bitte richten Sie zuerst die Zwei-Faktor-Authentifizierung (2FA) ein, bevor Sie sich anmelden können.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # 2FA: Code is missing or wrong (MissingTwoFactorCode)
        missing_2fa = two_factor_settings.get("MissingTwoFactorCode", False)
        if missing_2fa:
            return Response(
                {
                    "error_code": "MISSING_TWO_FACTOR_CODE",
                    "error_message": "Two-factor authentication code required.",
                    "requires_two_factor": True,
                },
                status=status.HTTP_200_OK,
            )

        # Map SWG error codes to our error responses
        error_map = {
            "WrongUsername": ("WRONG_USERNAME", "User not found. Please check your email address."),
            "WrongPassword": ("WRONG_PASSWORD", "Incorrect password. Please try again."),
            "TwoFactorCodeWrong": ("TWO_FACTOR_CODE_WRONG", "Invalid two-factor code. Please try again."),
            "NoPermission": ("NO_PERMISSION", "You do not have permission to access this application."),
        }

        if error_code_swg in error_map:
            code, message = error_map[error_code_swg]
            return Response(
                {"error_code": code, "error_message": message},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Fallback for unknown errors
        return Response(
            {
                "error_code": "AUTHENTICATION_FAILED_SIGN_IN",
                "error_message": "Authentication failed. Please try again.",
            },
            status=status.HTTP_401_UNAUTHORIZED,
        )

    def _handle_successful_login(self, request, email, password, swg_data):
        """Handle successful SWG authentication — provision or log in the user."""
        # Correct email domain if needed
        allowed_domain = os.environ.get("ALLOWED_EMAIL_DOMAIN")
        if allowed_domain and "@" in email and email.split("@")[1] != allowed_domain:
            email = email.split("@")[0] + "@" + allowed_domain

        existing_user = User.objects.filter(email=email).first()

        if not existing_user:
            # Auto-provision new users on first successful login
            try:
                provider = EmailProvider(
                    request=request,
                    key=email,
                    code=password,
                    is_signup=True,
                    callback=post_user_auth_workflow,
                )
                user = provider.authenticate()
                user_login(request=request, user=user, is_app=True)
                path = get_redirection_path(user=user)
                return Response(
                    {
                        "success": True,
                        "redirect_path": path,
                    },
                    status=status.HTTP_200_OK,
                )
            except AuthenticationException as e:
                return Response(
                    e.get_error_dict(),
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            # Existing user — sync password and log in
            existing_user.set_password(password)
            existing_user.save(update_fields=["password"])
            user_login(request=request, user=existing_user, is_app=True)
            path = get_redirection_path(user=existing_user)
            return Response(
                {
                    "success": True,
                    "redirect_path": path,
                },
                status=status.HTTP_200_OK,
            )


class SignUpAuthEndpoint(View):
    def post(self, request):
        # Direct sign-up is disabled — authentication is handled via SWG Auth.
        # New users are auto-provisioned on first login via SignInAuthEndpoint.
        next_path = request.POST.get("next_path")
        params = {"error_code": 5065, "error_message": "AUTHENTICATION_FAILED_SIGN_IN"}
        url = get_safe_redirect_url(
            base_url=base_host(request=request, is_app=True),
            next_path=next_path,
            params=params,
        )
        return HttpResponseRedirect(url)

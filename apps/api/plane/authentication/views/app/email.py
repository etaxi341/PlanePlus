# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import ldap
import os

# Django imports
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import HttpResponseRedirect
from django.views import View

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
from plane.utils.path_validator import get_safe_redirect_url


class SignInAuthEndpoint(View):
    def post(self, request):
        next_path = request.POST.get("next_path")
        # Check instance configuration
        instance = Instance.objects.first()
        if instance is None or not instance.is_setup_done:
            # Redirection params
            exc = AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["INSTANCE_NOT_CONFIGURED"],
                error_message="INSTANCE_NOT_CONFIGURED",
            )
            params = exc.get_error_dict()
            # Base URL join
            url = get_safe_redirect_url(
                base_url=base_host(request=request, is_app=True),
                next_path=next_path,
                params=params,
            )
            return HttpResponseRedirect(url)

        # set the referer as session to redirect after login
        email = request.POST.get("email", False)
        password = request.POST.get("password", False)

        ## Raise exception if any of the above are missing
        if not email or not password:
            # Redirection params
            exc = AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["REQUIRED_EMAIL_PASSWORD_SIGN_IN"],
                error_message="REQUIRED_EMAIL_PASSWORD_SIGN_IN",
                payload={"email": str(email)},
            )
            params = exc.get_error_dict()
            # Next path
            url = get_safe_redirect_url(
                base_url=base_host(request=request, is_app=True),
                next_path=next_path,
                params=params,
            )
            return HttpResponseRedirect(url)

        # Validate email
        email = email.strip().lower()
        try:
            validate_email(email)
        except ValidationError:
            exc = AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["INVALID_EMAIL_SIGN_IN"],
                error_message="INVALID_EMAIL_SIGN_IN",
                payload={"email": str(email)},
            )
            params = exc.get_error_dict()
            url = get_safe_redirect_url(
                base_url=base_host(request=request, is_app=True),
                next_path=next_path,
                params=params,
            )
            return HttpResponseRedirect(url)

        # LDAP Authentication
        conn = ldap.initialize(os.environ.get('AUTH_LDAP_SERVER_URI'))

        # Attempt binding to the LDAP server with the given credentials
        try:
            conn.simple_bind_s(email, password)
        except ldap.INVALID_CREDENTIALS:
            params = {
                "error_code": 5065,
                "error_message": "AUTHENTICATION_FAILED_SIGN_IN",
                "email": email,
            }
            url = get_safe_redirect_url(
                base_url=base_host(request=request, is_app=True),
                next_path=next_path,
                params=params,
            )
            return HttpResponseRedirect(url)

        # Correct email domain if needed
        allowed_domain = os.environ.get('ALLOWED_EMAIL_DOMAIN')
        if allowed_domain and email.split('@')[1] != allowed_domain:
            email = email.split('@')[0] + '@' + allowed_domain

        existing_user = User.objects.filter(email=email).first()

        if not existing_user:
            # Auto-signup new LDAP users
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
                if next_path:
                    path = next_path
                else:
                    path = get_redirection_path(user=user)
                url = get_safe_redirect_url(
                    base_url=base_host(request=request, is_app=True),
                    next_path=path,
                    params={},
                )
                return HttpResponseRedirect(url)
            except AuthenticationException as e:
                params = e.get_error_dict()
                url = get_safe_redirect_url(
                    base_url=base_host(request=request, is_app=True),
                    next_path=next_path,
                    params=params,
                )
                return HttpResponseRedirect(url)

        # Existing user: LDAP bind already succeeded above — log in directly
        # without re-checking the local Django password (which may be stale).
        existing_user.set_password(password)
        existing_user.save(update_fields=["password"])
        user_login(request=request, user=existing_user, is_app=True)
        if next_path:
            path = next_path
        else:
            path = get_redirection_path(user=existing_user)
        url = get_safe_redirect_url(
            base_url=base_host(request=request, is_app=True),
            next_path=path,
            params={},
        )
        return HttpResponseRedirect(url)


class SignUpAuthEndpoint(View):
    def post(self, request):
        # Direct sign-up is disabled — authentication is handled via LDAP.
        # New users are auto-provisioned on first LDAP login via SignInAuthEndpoint.
        next_path = request.POST.get("next_path")
        params = {"error_code": 5065, "error_message": "AUTHENTICATION_FAILED_SIGN_IN"}
        url = get_safe_redirect_url(
            base_url=base_host(request=request, is_app=True),
            next_path=next_path,
            params=params,
        )
        return HttpResponseRedirect(url)


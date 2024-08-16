# Python imports
from urllib.parse import urlencode, urljoin

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
from plane.authentication.utils.user_auth_workflow import (
    post_user_auth_workflow,
)
from plane.db.models import User
from plane.authentication.adapter.error import (
    AuthenticationException,
    AUTHENTICATION_ERROR_CODES,
)

from django_auth_ldap.config import LDAPSearch
import ldap

import os


class SignInAuthEndpoint(View):

    def post(self, request):
        next_path = request.POST.get("next_path")
        # Check instance configuration
        instance = Instance.objects.first()
        if instance is None or not instance.is_setup_done:
            # Redirection params
            exc = AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES[
                    "INSTANCE_NOT_CONFIGURED"
                ],
                error_message="INSTANCE_NOT_CONFIGURED",
            )
            params = exc.get_error_dict()
            if next_path:
                params["next_path"] = str(next_path)
            # Base URL join
            url = urljoin(
                base_host(request=request, is_app=True),
                "sign-in?" + urlencode(params),
            )
            return HttpResponseRedirect(url)

        # set the referer as session to redirect after login
        email = request.POST.get("email", False)
        password = request.POST.get("password", False)

        ## Raise exception if any of the above are missing
        if not email or not password:
            # Redirection params
            exc = AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES[
                    "REQUIRED_EMAIL_PASSWORD_SIGN_IN"
                ],
                error_message="REQUIRED_EMAIL_PASSWORD_SIGN_IN",
                payload={"email": str(email)},
            )
            params = exc.get_error_dict()
            # Next path
            if next_path:
                params["next_path"] = str(next_path)
            url = urljoin(
                base_host(request=request, is_app=True),
                "sign-in?" + urlencode(params),
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
            if next_path:
                params["next_path"] = str(next_path)
            url = urljoin(
                base_host(request=request, is_app=True),
                "sign-in?" + urlencode(params),
            )
            return HttpResponseRedirect(url)


        conn = ldap.initialize(os.getenv('AUTH_LDAP_SERVER_URI'))

        # attempt binding to the server with the given credentials
        # if incorrect, gtfo
        try:
            conn.simple_bind_s(email, password)
        except ldap.INVALID_CREDENTIALS:
            params = {
                "error_code": 5065,
                "error_message": "AUTHENTICATION_FAILED_SIGN_IN",
                "email": email,
            }
            if next_path:
                params["next_path"] = str(next_path)
            url = urljoin(
                base_host(request=request, is_app=True),
                "sign-in?" + urlencode(params),
            )
            return HttpResponseRedirect(url)

        # check if the user put in @swg.de domain, if not correct it
        if email.split('@')[1] != os.getenv('ALLOWED_EMAIL_DOMAIN'):
            email = email.split('@')[0] + '@' + os.getenv('ALLOWED_EMAIL_DOMAIN')
            

        existing_user = User.objects.filter(email=email).first()

        if not existing_user:
            # sign up the new account using the SignUpAuthEndpoint
            try:
                provider = EmailProvider(
                    request=request,
                    key=email,
                    is_signup=True,
                    callback=post_user_auth_workflow,
                )
                user = provider.authenticate()
                # Login the user and record his device info
                user_login(request=request, user=user, is_app=True)
                # Get the redirection path
                if next_path:
                    path = next_path
                else:
                    path = get_redirection_path(user=user)
                # redirect to referer path
                url = urljoin(base_host(request=request, is_app=True), path)
                return HttpResponseRedirect(url)
            except AuthenticationException as e:
                params = e.get_error_dict()
                if next_path:
                    params["next_path"] = str(next_path)
                url = urljoin(
                    base_host(request=request, is_app=True),
                    "?" + urlencode(params),
                )
                return HttpResponseRedirect(url)

        try:
            provider = EmailProvider(
                request=request,
                key=email,
                is_signup=False,
                callback=post_user_auth_workflow,
            )
            user = provider.authenticate()
            # Login the user and record his device info
            user_login(request=request, user=user, is_app=True)
            # Get the redirection path
            if next_path:
                path = str(next_path)
            else:
                path = get_redirection_path(user=user)

            # redirect to referer path
            url = urljoin(base_host(request=request, is_app=True), path)
            return HttpResponseRedirect(url)
        except AuthenticationException as e:
            params = e.get_error_dict()
            if next_path:
                params["next_path"] = str(next_path)
            url = urljoin(
                base_host(request=request, is_app=True),
                "sign-in?" + urlencode(params),
            )
            return HttpResponseRedirect(url)

class SignUpAuthEndpoint(View):
    def post(self, request):
        return HttpResponseRedirect("gtfo")
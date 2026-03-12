# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

# Third party imports
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

## Module imports
from plane.license.models import Instance
from plane.authentication.adapter.error import (
    AuthenticationException,
    AUTHENTICATION_ERROR_CODES,
)
from plane.authentication.rate_limit import AuthenticationThrottle


class EmailCheckEndpoint(APIView):
    permission_classes = [AllowAny]

    throttle_classes = [AuthenticationThrottle]

    def post(self, request):
        # Check instance configuration
        instance = Instance.objects.first()
        if instance is None or not instance.is_setup_done:
            exc = AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["INSTANCE_NOT_CONFIGURED"],
                error_message="INSTANCE_NOT_CONFIGURED",
            )
            return Response(exc.get_error_dict(), status=status.HTTP_400_BAD_REQUEST)

        email = request.data.get("email", False)

        # Return error if email is not present
        if not email:
            exc = AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["EMAIL_REQUIRED"],
                error_message="EMAIL_REQUIRED",
            )
            return Response(exc.get_error_dict(), status=status.HTTP_400_BAD_REQUEST)

        # Lower the email
        email = str(email).lower().strip()

        # Validate email
        try:
            validate_email(email)
        except ValidationError:
            exc = AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["INVALID_EMAIL"],
                error_message="INVALID_EMAIL",
            )
            return Response(exc.get_error_dict(), status=status.HTTP_400_BAD_REQUEST)

        # LDAP-only mode: always route through sign-in (CREDENTIAL), regardless of
        # whether the user already has a local account or not.
        # New users are auto-provisioned on first successful LDAP bind in SignInAuthEndpoint.
        return Response(
            {
                "existing": True,
                "status": "CREDENTIAL",
            },
            status=status.HTTP_200_OK,
        )

# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from urllib.parse import urlsplit

from django.utils.cache import patch_vary_headers
from rest_framework.exceptions import APIException


class AssetStorageUnavailable(APIException):
    status_code = 503
    default_code = "asset_storage_unavailable"

    def __init__(self):
        super().__init__(
            {"error_code": self.default_code, "error": "Storage could not generate an upload/download URL."}
        )


class AssetResponseMixin:
    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        response["Referrer-Policy"] = "no-referrer"
        patch_vary_headers(response, ["X-API-Key", "X-PlanePlus-Act-As-User"])
        response.planeplus_sensitive_body = True
        return response


def require_storage_url(value):
    try:
        parsed = urlsplit(value or "")
        if parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except (ValueError, TypeError):
        pass
    raise AssetStorageUnavailable()


def require_upload_data(value):
    if not isinstance(value, dict) or not isinstance(value.get("fields"), dict):
        raise AssetStorageUnavailable()
    require_storage_url(value.get("url"))
    return value

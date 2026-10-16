# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Strict current-body references; never fetch a URL supplied by the caller."""

from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings
from lxml import etree, html
from rest_framework.exceptions import APIException

from plane.settings.storage import S3Storage


INLINE_IMAGE_TYPES = frozenset({"image/jpeg", "image/jpg", "image/png", "image/gif", "image/webp", "image/avif"})
INLINE_URL_MAX_SECONDS = 300


def body_references_asset(body, asset):
    """Plane stores image-component src as a UUID; legacy img/src uses local URLs.

    UUIDs in text/attributes/links and URLs on external origins are not evidence
    of an inline-image reference. Ownership is checked separately by the view.
    """
    if not body:
        return False
    try:
        document = html.fromstring(body, parser=html.HTMLParser(no_network=True))
    except (etree.ParserError, ValueError, TypeError):
        return False
    origins = {
        (parsed.scheme.lower(), parsed.netloc.lower())
        for value in (settings.WEB_URL, settings.APP_BASE_URL)
        if value
        for parsed in (urlsplit(value),)
    }
    for node in document.iter("img", "image-component"):
        source = (node.get("src") or "").strip()
        try:
            if UUID(source) == asset.id:
                return True
        except (ValueError, AttributeError):
            pass
        try:
            parsed = urlsplit(source)
        except ValueError:
            continue
        if parsed.scheme or parsed.netloc:
            if (
                parsed.scheme.lower() not in {"https", "http"}
                or (parsed.scheme.lower(), parsed.netloc.lower()) not in origins
            ):
                continue
        # Only the canonical local asset path is accepted, never arbitrary
        # URL suffixes, substrings, file keys, ../ paths or query-string UUIDs.
        if parsed.path == asset.asset_url:
            return True
    return False


class InlineAssetURLUnavailable(APIException):
    status_code = 503
    default_code = "inline_asset_https_unavailable"
    default_detail = "A signed HTTPS URL is unavailable; configure HTTPS on the public storage endpoint."

    def __init__(self):
        super().__init__({"error_code": self.default_code, "error": self.default_detail}, code=self.default_code)


class HTTPSStorageRequest:
    """Sign MinIO's public HTTPS host before signing, without mutating the request.

    Rewriting an already-signed URL can invalidate its signature. Other S3
    endpoints must already be configured with HTTPS; insecure results fail closed.
    """

    scheme = "https"

    def __init__(self, request):
        self.request = request

    def __getattr__(self, name):
        return getattr(self.request, name)


def signed_inline_asset_url(request, asset):
    storage = S3Storage(request=HTTPSStorageRequest(request))
    expires_in = min(storage.signed_url_expiration, INLINE_URL_MAX_SECONDS)
    if expires_in <= 0:
        raise InlineAssetURLUnavailable()
    url = storage.generate_presigned_url(
        object_name=asset.asset.name,
        expiration=expires_in,
        disposition="inline",
        filename=asset.attributes.get("name"),
    )
    try:
        parsed = urlsplit(url or "")
        valid = (
            parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not any(ord(char) < 33 for char in (url or ""))
        )
    except ValueError:
        valid = False
    if not valid:
        raise InlineAssetURLUnavailable()
    return url, expires_in

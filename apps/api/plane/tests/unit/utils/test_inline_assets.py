# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest

from plane.utils.inline_assets import body_references_asset, signed_inline_asset_url, InlineAssetURLUnavailable


pytestmark = pytest.mark.unit


@pytest.fixture
def image(settings):
    settings.WEB_URL = "https://plane.example"
    settings.APP_BASE_URL = ""
    return SimpleNamespace(
        id=uuid4(),
        asset_url=f"/api/assets/v2/workspaces/test/projects/{uuid4()}/{uuid4()}/",
        asset=SimpleNamespace(name=f"{uuid4()}/picture.png"),
        attributes={"name": "picture.png"},
    )


@pytest.mark.parametrize("tag", ["img", "image-component"])
def test_real_image_sources_are_references(image, tag):
    assert body_references_asset(f'<{tag} src="{image.id}"></{tag}>', image)
    assert body_references_asset(f'<{tag} src="{image.asset_url}"></{tag}>', image)
    assert body_references_asset(f'<{tag} src="https://plane.example{image.asset_url}?download=1"></{tag}>', image)


@pytest.mark.parametrize(
    "body",
    [
        "<p>{id}</p>",
        '<a href="{path}">image</a>',
        '<img alt="{id}" src="external.png">',
        '<img src="https://evil.example{path}">',
        '<img src="//evil.example{path}">',
        '<img src="/not-the-asset/{id}/">',
        '<img src="https://evil.example/?id={id}">',
        '<script>var image="{id}";</script>',
        '<!-- <img src="{id}"> -->',
    ],
)
def test_text_external_urls_and_false_references_are_not_authorization(image, body):
    assert not body_references_asset(body.format(id=image.id, path=image.asset_url), image)


def test_real_s3_signature_uses_https_before_signing_and_short_expiration(image, monkeypatch):
    # boto3 signs locally with fake credentials: no storage/network call.
    for key, value in {
        "USE_MINIO": "1",
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing-secret",
        "AWS_REGION": "us-east-1",
        "AWS_S3_BUCKET_NAME": "uploads",
        "SIGNED_URL_EXPIRATION": "3600",
    }.items():
        monkeypatch.setenv(key, value)
    request = SimpleNamespace(scheme="http", get_host=lambda: "assets.example")
    url, expires_in = signed_inline_asset_url(request, image)
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https" and parsed.hostname == "assets.example"
    assert len(query["X-Amz-Signature"][0]) == 64
    assert query["X-Amz-Expires"] == ["300"] and expires_in == 300
    assert request.scheme == "http"


@pytest.mark.parametrize(
    "url",
    [
        None,
        "http://storage.example/image?signature=x",
        "https://user:pass@storage.example/image",
        "https://storage.example/\nimage",
        "//storage.example/image",
    ],
)
def test_insecure_or_invalid_signed_results_fail_closed(image, monkeypatch, url):
    storage = Mock()
    storage.return_value.signed_url_expiration = 300
    storage.return_value.generate_presigned_url.return_value = url
    monkeypatch.setattr("plane.utils.inline_assets.S3Storage", storage)
    with pytest.raises(InlineAssetURLUnavailable):
        signed_inline_asset_url(SimpleNamespace(), image)

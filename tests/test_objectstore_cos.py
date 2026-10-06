# -*- coding: utf-8 -*-
"""Issue #83：腾讯云 COS 兼容钩子（Content-MD5 注入）单测。零网络。"""
from __future__ import annotations

import base64
import hashlib
from types import SimpleNamespace

from web.backend.objectstore import _inject_content_md5


def test_inject_content_md5_uses_serialized_body():
    request = SimpleNamespace(body=b"<LifecycleConfiguration/>", headers={})
    _inject_content_md5(request)
    expected = base64.b64encode(hashlib.md5(b"<LifecycleConfiguration/>").digest()).decode()
    assert request.headers["Content-MD5"] == expected


def test_inject_content_md5_without_body_is_noop():
    request = SimpleNamespace(body=None, headers={})
    _inject_content_md5(request)
    assert request.headers == {}

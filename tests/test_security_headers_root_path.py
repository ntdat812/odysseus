"""`SecurityHeadersMiddleware` must classify routes by application path.

`core/middleware.py` already documents the rule, on the helper it exports:

    Uvicorn prefixes ``scope["path"]`` with a configured ASGI ``root_path``;
    Starlette removes that prefix before matching routes. Middleware policy
    must use the same path form or a deployment prefix can change which policy
    applies to an otherwise unchanged application route.

`AuthMiddleware` follows it (see tests/test_auth_root_path.py).
`SecurityHeadersMiddleware` read `request.url.path`, which still carries the
prefix — so behind a reverse proxy mount (`--root-path /odysseus`, the
deployment SECURITY.md recommends) its three special-cased routes silently fell
through to the generic policy:

  * `/api/research/report/*` needs `script-src 'unsafe-inline'` — self-contained
    report HTML.
  * `/api/tools/*/render` needs framing headers omitted — it is iframed.
  * `/api/document/*/render-pdf` needs `SAMEORIGIN` / `frame-ancestors 'self'`.

Each is a same-origin feature that stops rendering under a mount path, with no
error to point at the cause. These tests pin the classification for both the
unmounted and mounted forms of the same application route.
"""

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from core.middleware import SecurityHeadersMiddleware


ROUTES = (
    "/api/research/report/abc",
    "/api/tools/mytool/render",
    "/api/document/doc1/render-pdf",
    "/api/other",
)


def _client(root_path=""):
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    for route in ROUTES:
        app.add_api_route(
            route,
            lambda: HTMLResponse("<html></html>"),
            methods=["GET"],
        )

    # Mirror what uvicorn's --root-path does: the ASGI scope keeps the prefixed
    # path, and root_path tells Starlette how much of it to strip before route
    # matching. TestClient(root_path=...) sets exactly that.
    return TestClient(app, root_path=root_path)


def _headers(route, root_path=""):
    return _client(root_path).get(root_path + route).headers


@pytest.mark.parametrize("root_path", ["", "/odysseus"])
def test_report_route_keeps_its_inline_script_policy(root_path):
    csp = _headers("/api/research/report/abc", root_path)["content-security-policy"]
    assert "script-src 'self' 'unsafe-inline'" in csp
    assert "nonce-" not in csp


@pytest.mark.parametrize("root_path", ["", "/odysseus"])
def test_tool_render_route_keeps_framing_headers_omitted(root_path):
    headers = _headers("/api/tools/mytool/render", root_path)
    assert "x-frame-options" not in headers
    assert "content-security-policy" not in headers


@pytest.mark.parametrize("root_path", ["", "/odysseus"])
def test_pdf_preview_route_keeps_same_origin_framing(root_path):
    headers = _headers("/api/document/doc1/render-pdf", root_path)
    assert headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in headers["content-security-policy"]


@pytest.mark.parametrize("root_path", ["", "/odysseus"])
def test_ordinary_route_keeps_the_strict_default_policy(root_path):
    headers = _headers("/api/other", root_path)
    assert headers["x-frame-options"] == "DENY"
    csp = headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "nonce-" in csp


@pytest.mark.parametrize("root_path", ["", "/odysseus"])
def test_baseline_headers_are_unconditional(root_path):
    # These must not depend on which branch the route classifies into.
    for route in ROUTES:
        headers = _headers(route, root_path)
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "no-referrer"
        assert "camera=()" in headers["permissions-policy"]


def test_a_mount_prefix_cannot_be_spelled_to_claim_a_relaxed_policy():
    """A route is classified by what Starlette routes, not by the raw URL.

    The relaxed branches are the security-relevant direction: a request whose
    *prefixed* path happens to look like `/api/tools/.../render` must not
    inherit the no-framing-headers policy when the application route it
    actually reached is an ordinary one.
    """
    headers = _headers("/api/other", "/api/tools/x/render")
    assert headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]

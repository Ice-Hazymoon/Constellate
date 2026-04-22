from __future__ import annotations

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import Response

from star_server.cors import apply_cors_headers, is_cors_preflight_request


def build_request(method: str, headers: dict[str, str]) -> Request:
    raw_headers = [(key.lower().encode("latin-1"), value.encode("latin-1")) for key, value in headers.items()]
    return Request({"type": "http", "method": method, "headers": raw_headers})


def test_cors_allows_wildcard_origin_for_actual_requests() -> None:
    request_headers = Headers({"origin": "http://localhost:5173"})
    response = Response("ok")
    assert apply_cors_headers(response.headers, request_headers, "*") is True
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    assert response.headers["Access-Control-Expose-Headers"] == "X-Request-Id"


def test_cors_handles_preflight_for_allowlisted_origin() -> None:
    request = build_request(
        "OPTIONS",
        {
            "Origin": "https://app.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type, X-Demo",
        },
    )
    response = Response()
    assert is_cors_preflight_request(request) is True
    assert apply_cors_headers(response.headers, request.headers, ["https://app.example.com"], preflight=True) is True
    assert response.headers["Access-Control-Allow-Origin"] == "https://app.example.com"
    assert "POST" in response.headers["Access-Control-Allow-Methods"]
    assert response.headers["Access-Control-Allow-Headers"] == "Content-Type, X-Demo"
    assert "Origin" in response.headers["Vary"]


def test_cors_rejects_disallowed_origin() -> None:
    request_headers = Headers({"origin": "https://blocked.example.com"})
    response = Response()
    assert apply_cors_headers(response.headers, request_headers, ["https://app.example.com"]) is False
    assert "Access-Control-Allow-Origin" not in response.headers

from __future__ import annotations

from collections.abc import MutableMapping
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.requests import Request

DEFAULT_CORS_METHODS = ("GET", "POST", "OPTIONS")
DEFAULT_CORS_HEADERS = ("Accept", "Accept-Language", "Content-Language", "Content-Type", "Origin")
DEFAULT_CORS_EXPOSED_HEADERS = ("X-Request-Id",)


def append_header_token(headers: MutableMapping[str, str], key: str, value: str) -> None:
    current = headers.get(key)
    if not current:
        headers[key] = value
        return

    tokens = [token.strip() for token in current.split(",") if token.strip()]
    if any(token.lower() == value.lower() for token in tokens):
        return

    headers[key] = ", ".join([*tokens, value])


def normalize_origin(origin: str) -> str:
    parsed = urlsplit(origin)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def resolve_allowed_origin(request_origin: str | None, allowed_origins: str | list[str]) -> str | None:
    if not request_origin:
        return None

    normalized_origin = normalize_origin(request_origin)
    if not normalized_origin or normalized_origin == "null":
        return None

    if allowed_origins == "*":
        return "*"

    return normalized_origin if normalized_origin in allowed_origins else None


def apply_cors_headers(
    headers: MutableMapping[str, str],
    request_headers: Headers,
    allowed_origins: str | list[str],
    *,
    preflight: bool = False,
) -> bool:
    allowed_origin = resolve_allowed_origin(request_headers.get("origin"), allowed_origins)
    if not allowed_origin:
        return False

    headers["Access-Control-Allow-Origin"] = allowed_origin
    if allowed_origin != "*":
        append_header_token(headers, "Vary", "Origin")

    headers["Access-Control-Expose-Headers"] = ", ".join(DEFAULT_CORS_EXPOSED_HEADERS)
    if not preflight:
        return True

    headers["Access-Control-Allow-Methods"] = ", ".join(DEFAULT_CORS_METHODS)
    request_allow_headers = request_headers.get("access-control-request-headers", "").strip()
    headers["Access-Control-Allow-Headers"] = request_allow_headers or ", ".join(DEFAULT_CORS_HEADERS)
    headers["Access-Control-Max-Age"] = "86400"
    append_header_token(headers, "Vary", "Access-Control-Request-Method")
    append_header_token(headers, "Vary", "Access-Control-Request-Headers")
    return True


def is_cors_preflight_request(request: Request) -> bool:
    return (
        request.method == "OPTIONS"
        and bool(request.headers.get("origin"))
        and bool(request.headers.get("access-control-request-method"))
    )

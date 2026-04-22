from __future__ import annotations

import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request, Response

from .annotation_runner import AnnotationRunner
from .config import Settings, get_settings
from .cors import apply_cors_headers, is_cors_preflight_request
from .errors import HttpError, exception_to_response
from .limiter import JobLimiter
from .routes import include_routes


@dataclass(slots=True)
class AppContext:
    settings: Settings
    limiter: JobLimiter
    runner: AnnotationRunner
    started_at: float = field(default_factory=time.perf_counter)
    ready: bool = False


def assert_path_exists(target_path: Path, label: str) -> None:
    if not target_path.exists():
        raise RuntimeError(f"{label} is missing: {target_path}")


def run_command_check(command: list[str], label: str) -> None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError(f"{label} unavailable: {exc}") from exc

    if result.returncode == 0:
        return

    message = (result.stderr or result.stdout or f"exit code {result.returncode}").strip()
    raise RuntimeError(f"{label} unavailable: {message}")


def validate_runtime_prerequisites(settings: Settings) -> None:
    assert_path_exists(settings.catalog_path, "minimal Hipparcos catalog")
    assert_path_exists(settings.modern_constellations_path, "constellation reference")
    assert_path_exists(settings.star_names_path, "star names reference")
    assert_path_exists(settings.ngc_path, "deep sky catalog")
    assert_path_exists(settings.stardroid_english_localization_path, "Stardroid English localization")
    assert_path_exists(settings.supplemental_dso_path, "supplemental deep sky objects reference")

    for index in settings.required_astrometry_indexes:
        assert_path_exists(
            settings.astrometry_dir / f"index-{index}.fits",
            f"astrometry index {index}",
        )

    run_command_check([sys.executable, "--version"], "python")
    run_command_check(["solve-field", "--help"], "solve-field")


def apply_common_response_headers(
    request: Request,
    response: Response,
    context: AppContext,
    request_id: str,
) -> Response:
    response.headers["X-Request-Id"] = request_id
    if "Cache-Control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob:; script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; connect-src 'self'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    )
    apply_cors_headers(response.headers, request.headers, context.settings.cors_allowed_origins)
    return response


def create_app(
    settings: Settings | None = None,
    *,
    runner: AnnotationRunner | None = None,
    validate_prerequisites: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    runner = runner or AnnotationRunner(settings)
    context = AppContext(
        settings=settings,
        limiter=JobLimiter(settings.max_concurrent_jobs, settings.max_queued_jobs),
        runner=runner,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if validate_prerequisites:
            validate_runtime_prerequisites(settings)
        await context.runner.preload_async()
        context.ready = True
        try:
            yield
        finally:
            context.runner.shutdown()

    app = FastAPI(lifespan=lifespan)
    app.state.context = context

    @app.middleware("http")
    async def common_middleware(request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        started_at = time.perf_counter()

        if is_cors_preflight_request(request):
            response = Response(status_code=204)
            is_allowed = apply_cors_headers(
                response.headers,
                request.headers,
                context.settings.cors_allowed_origins,
                preflight=True,
            )
            if not is_allowed:
                response = Response(status_code=403)
        else:
            try:
                content_length = request.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > context.settings.max_request_body_size_bytes:
                            raise HttpError(413, "request body exceeds configured limit")
                    except ValueError:
                        pass

                response = await call_next(request)
            except Exception as error:
                response = exception_to_response(error)

        response = apply_common_response_headers(request, response, context, request_id)

        if context.settings.log_requests:
            duration_ms = round((time.perf_counter() - started_at) * 1000)
            print(f"[star-server] {request.method} {request.url.path} {response.status_code} {duration_ms}ms")

        return response

    include_routes(app)
    return app


app = create_app()

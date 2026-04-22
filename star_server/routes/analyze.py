from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile as StarletteUploadFile

from annotate_options import clone_overlay_options, normalize_overlay_options
from render_mode import DEFAULT_RENDER_MODE, normalize_render_mode, should_render_on_server

from ..errors import HttpError, create_request_aborted_error
from ..limiter import JobQueueAbortedError, JobQueueFullError
from ..locale import normalize_locale_tag, parse_locale_from_form_data, parse_primary_accept_language
from ..uploads import guess_extension, save_upload_file

router = APIRouter()


async def monitor_disconnect(
    request: Request,
    abort_event: asyncio.Event,
    done_event: asyncio.Event,
) -> None:
    while not abort_event.is_set() and not done_event.is_set():
        if await request.is_disconnected():
            abort_event.set()
            return
        await asyncio.sleep(0.1)


def parse_overlay_options_from_form_data(form_data: Any) -> dict[str, Any]:
    raw_options = form_data.get("options")
    if raw_options is None or raw_options == "":
        return clone_overlay_options()
    if not isinstance(raw_options, str):
        raise HttpError(400, "invalid overlay options payload")
    try:
        parsed = json.loads(raw_options)
    except json.JSONDecodeError as exc:
        raise HttpError(400, "invalid overlay options JSON") from exc
    return normalize_overlay_options(parsed)


def parse_render_mode_from_form_data(form_data: Any) -> str:
    raw_render_mode = form_data.get("render_mode")
    if raw_render_mode is None or raw_render_mode == "":
        return DEFAULT_RENDER_MODE
    if not isinstance(raw_render_mode, str):
        raise HttpError(400, "invalid render mode payload")
    return normalize_render_mode(raw_render_mode)


def resolve_public_image_url(samples_dir: Path, input_image_path: Path) -> str | None:
    samples_dir = samples_dir.resolve()
    input_image_path = input_image_path.resolve()
    if input_image_path.parent == samples_dir:
        return f"/samples/{input_image_path.name}"
    return None


def omit_internal_paths(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in result.items()
        if key not in {"input_image", "output_image"}
    }


def build_annotation_response(
    request: Request,
    raw_result: dict[str, Any],
    *,
    input_image_path: Path,
    output_image_path: Path | None,
    overlay_options: dict[str, Any],
    render_mode: str,
    started_at: float,
) -> dict[str, Any]:
    annotated_image_base64: str | None = None
    if output_image_path is not None and output_image_path.exists():
        annotated_image_base64 = base64.b64encode(output_image_path.read_bytes()).decode("ascii")

    sanitized = omit_internal_paths(raw_result)
    return {
        **sanitized,
        "render_options": overlay_options,
        "render_mode": render_mode,
        "available_renders": {
            "server": bool(annotated_image_base64),
            "client": True,
            "default_view": "server" if annotated_image_base64 else "client",
        },
        "inputImageUrl": resolve_public_image_url(
            request.app.state.context.settings.samples_dir,
            input_image_path,
        ),
        "annotatedImageBase64": annotated_image_base64,
        "annotatedImageMimeType": "image/png" if annotated_image_base64 else None,
        "processingMs": round((time.perf_counter() - started_at) * 1000),
    }


async def run_with_job_slot(
    request: Request,
    coro_factory,
    *,
    abort_event: asyncio.Event,
):
    context = request.app.state.context
    try:
        return await context.limiter.run(coro_factory, abort_event=abort_event)
    except JobQueueFullError as exc:
        raise HttpError(429, "server is busy, retry later") from exc
    except JobQueueAbortedError as exc:
        raise create_request_aborted_error() from exc


async def ensure_ready(request: Request) -> None:
    if not request.app.state.context.ready:
        raise HttpError(503, "worker not ready")


@router.post("/api/analyze")
async def analyze_upload(request: Request) -> JSONResponse:
    await ensure_ready(request)
    form_data = await request.form()
    file_field = form_data.get("image")
    if not isinstance(file_field, StarletteUploadFile):
        raise HttpError(400, "missing file field 'image'")

    overlay_options = parse_overlay_options_from_form_data(form_data)
    render_mode = parse_render_mode_from_form_data(form_data)
    locale = parse_locale_from_form_data(form_data, request.headers.get("accept-language"))

    context = request.app.state.context
    request_id = request.state.request_id
    abort_event = asyncio.Event()
    done_event = asyncio.Event()
    disconnect_task = asyncio.create_task(monitor_disconnect(request, abort_event, done_event))

    try:
        with tempfile.TemporaryDirectory(prefix="star-upload-") as workspace_dir_text:
            workspace_dir = Path(workspace_dir_text)
            input_image_path = workspace_dir / f"{request_id}{guess_extension(file_field.filename or '', file_field.content_type or None)}"
            await save_upload_file(file_field, input_image_path, context.settings.max_upload_bytes)

            started_at = time.perf_counter()

            async def run_annotation():
                output_image_path = (
                    workspace_dir / f"{uuid.uuid4()}.png"
                    if should_render_on_server(render_mode)
                    else None
                )
                raw_result = await context.runner.run_annotate(
                    input_path=input_image_path,
                    output_image_path=output_image_path,
                    overlay_options=overlay_options,
                    locale=locale,
                    timeout_s=context.settings.worker_job_timeout_ms / 1000.0,
                )
                if abort_event.is_set():
                    raise create_request_aborted_error()
                return build_annotation_response(
                    request,
                    raw_result,
                    input_image_path=input_image_path,
                    output_image_path=output_image_path,
                    overlay_options=overlay_options,
                    render_mode=render_mode,
                    started_at=started_at,
                )

            result = await run_with_job_slot(request, run_annotation, abort_event=abort_event)
            return JSONResponse(result)
    finally:
        done_event.set()
        disconnect_task.cancel()
        await asyncio.gather(disconnect_task, return_exceptions=True)
        await file_field.close()


@router.post("/api/analyze-sample")
async def analyze_sample(request: Request) -> JSONResponse:
    await ensure_ready(request)

    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise HttpError(400, "invalid JSON body") from exc

    if not isinstance(body, dict):
        raise HttpError(400, "invalid JSON body")

    context = request.app.state.context
    sample = next((entry for entry in context.settings.sample_images if entry["id"] == body.get("id")), None)
    if sample is None:
        raise HttpError(400, "unknown sample id")

    overlay_options = normalize_overlay_options(body.get("options"))
    render_mode = normalize_render_mode(body.get("render_mode"))
    locale = (
        normalize_locale_tag(body.get("locale"))
        or parse_primary_accept_language(request.headers.get("accept-language"))
        or context.settings.default_locale
    )

    abort_event = asyncio.Event()
    done_event = asyncio.Event()
    disconnect_task = asyncio.create_task(monitor_disconnect(request, abort_event, done_event))

    try:
        with tempfile.TemporaryDirectory(prefix=f"star-sample-{sample['id']}-") as workspace_dir_text:
            workspace_dir = Path(workspace_dir_text)
            input_image_path = context.settings.samples_dir / sample["filename"]
            started_at = time.perf_counter()

            async def run_annotation():
                output_image_path = (
                    workspace_dir / f"{uuid.uuid4()}.png"
                    if should_render_on_server(render_mode)
                    else None
                )
                raw_result = await context.runner.run_annotate(
                    input_path=input_image_path,
                    output_image_path=output_image_path,
                    overlay_options=overlay_options,
                    locale=locale,
                    timeout_s=context.settings.worker_job_timeout_ms / 1000.0,
                )
                if abort_event.is_set():
                    raise create_request_aborted_error()
                return build_annotation_response(
                    request,
                    raw_result,
                    input_image_path=input_image_path,
                    output_image_path=output_image_path,
                    overlay_options=overlay_options,
                    render_mode=render_mode,
                    started_at=started_at,
                )

            result = await run_with_job_slot(request, run_annotation, abort_event=abort_event)
            return JSONResponse(result)
    finally:
        done_event.set()
        disconnect_task.cancel()
        await asyncio.gather(disconnect_task, return_exceptions=True)

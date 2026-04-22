from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response

router = APIRouter()


def _resolve_file(base_dir: Path, relative_path: str) -> Path | None:
    absolute_base_dir = base_dir.resolve()
    absolute_path = (base_dir / relative_path).resolve()
    if absolute_path != absolute_base_dir and absolute_base_dir not in absolute_path.parents:
        return None
    if not absolute_path.exists() or not absolute_path.is_file():
        return None
    return absolute_path


@router.get("/")
async def index(request: Request) -> Response:
    return FileResponse(request.app.state.context.settings.public_dir / "index.html")


@router.get("/app.js")
async def app_js(request: Request) -> Response:
    return FileResponse(request.app.state.context.settings.public_dir / "app.js")


@router.get("/samples/{filename:path}")
async def sample_image(request: Request, filename: str) -> Response:
    resolved = _resolve_file(request.app.state.context.settings.samples_dir, filename)
    if resolved is None:
        return PlainTextResponse("Not Found", status_code=404)
    return FileResponse(resolved, headers={"Cache-Control": "public, max-age=86400"})

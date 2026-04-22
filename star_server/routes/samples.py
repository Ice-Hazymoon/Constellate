from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/api/samples")
async def list_samples(request: Request) -> JSONResponse:
    return JSONResponse(request.app.state.context.settings.sample_images)

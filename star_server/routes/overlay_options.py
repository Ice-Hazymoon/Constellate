from __future__ import annotations

from copy import deepcopy

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from annotate_options import DEFAULT_OVERLAY_OPTIONS, OVERLAY_PRESETS, clone_overlay_options

router = APIRouter()


@router.get("/api/overlay-options")
async def overlay_options(request: Request) -> JSONResponse:
    context = request.app.state.context
    return JSONResponse(
        {
            "defaults": clone_overlay_options(),
            "presets": deepcopy(OVERLAY_PRESETS),
            "localization": {
                "default_locale": context.settings.default_locale,
                "available_locales": context.runner.available_locales,
            },
        }
    )

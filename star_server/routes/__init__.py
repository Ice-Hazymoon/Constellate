from __future__ import annotations

from fastapi import FastAPI

from .analyze import router as analyze_router
from .health import router as health_router
from .overlay_options import router as overlay_options_router
from .samples import router as samples_router
from .static_assets import router as static_assets_router


def include_routes(app: FastAPI) -> None:
    app.include_router(health_router)
    app.include_router(samples_router)
    app.include_router(overlay_options_router)
    app.include_router(analyze_router)
    app.include_router(static_assets_router)

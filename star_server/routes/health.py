from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


def health_payload(request: Request) -> dict[str, object]:
    context = request.app.state.context
    limiter_stats = context.limiter.stats()
    return {
        "ok": True,
        "uptimeMs": round((time.perf_counter() - context.started_at) * 1000),
        "activeJobs": limiter_stats["activeJobs"],
        "queuedJobs": limiter_stats["queuedJobs"],
        "workerReady": context.ready,
        "pendingWorkerRequests": context.runner.active_job_count,
        "config": {
            "maxUploadBytes": context.settings.max_upload_bytes,
            "maxConcurrentJobs": context.settings.max_concurrent_jobs,
            "maxQueuedJobs": context.settings.max_queued_jobs,
        },
    }


@router.get("/healthz")
async def healthz(request: Request) -> JSONResponse:
    return JSONResponse(health_payload(request))


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    context = request.app.state.context
    if not context.ready:
        return JSONResponse({"ok": False, "error": "worker not ready"}, status_code=503)
    return JSONResponse(health_payload(request))

from __future__ import annotations

import base64
from pathlib import Path

import httpx
import pytest

from star_server.app import create_app
from star_server.config import get_settings


class FakeRunner:
    def __init__(self) -> None:
        self.available_locales = ["en", "ja"]
        self.active_job_count = 0
        self.preloaded = False

    async def preload_async(self) -> dict[str, object]:
        self.preloaded = True
        return {"status": "ok"}

    def shutdown(self) -> None:
        return None

    async def run_annotate(
        self,
        *,
        input_path: Path,
        output_image_path: Path | None,
        overlay_options: dict[str, object],
        locale: str,
        timeout_s: float,
    ) -> dict[str, object]:
        self.active_job_count += 1
        try:
            if output_image_path is not None:
                output_image_path.write_bytes((Path("samples") / "apod4.jpg").read_bytes())
            return {
                "input_image": str(input_path),
                "output_image": str(output_image_path) if output_image_path is not None else None,
                "image_width": 1024,
                "image_height": 768,
                "solve": {
                    "center_ra_deg": 165.6,
                    "center_dec_deg": 56.3,
                    "field_width_deg": 34.1,
                    "field_height_deg": 23.8,
                    "crop": None,
                },
                "solve_verification": {},
                "attempts": [],
                "source_analysis": {},
                "localization": {
                    "requested_locale": locale,
                    "resolved_locale": locale,
                    "available_locales": self.available_locales,
                },
                "visible_named_stars": [],
                "visible_constellations": [],
                "visible_deep_sky_objects": [],
                "render_options": overlay_options,
                "sky_mask_status": {
                    "requested": True,
                    "applied": False,
                    "reason": "not_requested",
                },
                "overlay_scene": {
                    "image_width": 1024,
                    "image_height": 768,
                    "crop": {
                        "name": "full",
                        "x": 0,
                        "y": 0,
                        "width": 1024,
                        "height": 768,
                    },
                    "bounds": {
                        "left": 0,
                        "top": 0,
                        "right": 1024,
                        "bottom": 768,
                    },
                    "constellation_lines": [],
                    "constellation_labels": [],
                    "deep_sky_markers": [],
                    "deep_sky_labels": [],
                    "star_markers": [],
                    "star_labels": [],
                },
                "solver_log_tail": "",
                "timings_ms": {
                    "normalize": 1.0,
                    "solve": 2.0,
                    "scene": 3.0,
                    "sky_mask": 4.0,
                    "overlay_scene": 5.0,
                    "render": 6.0,
                    "total": 21.0,
                },
            }
        finally:
            self.active_job_count -= 1


@pytest.mark.asyncio
async def test_readyz_returns_503_before_lifespan_startup() -> None:
    app = create_app(
        get_settings({"LOG_REQUESTS": "false"}),
        runner=FakeRunner(),
        validate_prerequisites=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"ok": False, "error": "worker not ready"}


@pytest.mark.asyncio
async def test_endpoints_preserve_contract() -> None:
    runner = FakeRunner()
    app = create_app(
        get_settings({"LOG_REQUESTS": "false"}),
        runner=runner,
        validate_prerequisites=False,
    )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            health_response = await client.get("/healthz")
            ready_response = await client.get("/readyz")
            samples_response = await client.get("/api/samples")
            overlay_response = await client.get("/api/overlay-options")

            with (Path("samples") / "apod4.jpg").open("rb") as handle:
                analyze_response = await client.post(
                    "/api/analyze",
                    files={"image": ("apod4.jpg", handle, "image/jpeg")},
                )

            analyze_sample_response = await client.post(
                "/api/analyze-sample",
                json={"id": "apod4", "locale": "ja"},
            )

    assert runner.preloaded is True

    health_payload = health_response.json()
    assert health_response.status_code == 200
    assert health_payload["workerReady"] is True
    assert health_payload["activeJobs"] == 0
    assert health_payload["queuedJobs"] == 0

    assert ready_response.status_code == 200
    assert ready_response.json()["workerReady"] is True

    assert samples_response.status_code == 200
    assert len(samples_response.json()) > 0

    overlay_payload = overlay_response.json()
    assert overlay_response.status_code == 200
    assert overlay_payload["defaults"]["preset"] == "max"
    assert overlay_payload["localization"]["default_locale"] == "en"
    assert overlay_payload["localization"]["available_locales"] == ["en", "ja"]

    analyze_payload = analyze_response.json()
    assert analyze_response.status_code == 200
    assert isinstance(analyze_payload["processingMs"], int)
    assert analyze_payload["render_mode"] == "server"
    assert analyze_payload["available_renders"] == {
        "server": True,
        "client": True,
        "default_view": "server",
    }
    assert analyze_payload["annotatedImageMimeType"] == "image/png"
    assert base64.b64decode(analyze_payload["annotatedImageBase64"])
    assert analyze_payload["inputImageUrl"] is None

    analyze_sample_payload = analyze_sample_response.json()
    assert analyze_sample_response.status_code == 200
    assert analyze_sample_payload["inputImageUrl"] == "/samples/apod4.jpg"
    assert analyze_sample_payload["localization"]["requested_locale"] == "ja"

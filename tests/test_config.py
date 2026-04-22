from __future__ import annotations

from star_server.config import get_settings


def test_config_uses_expected_defaults() -> None:
    settings = get_settings({})
    assert settings.port == 3000
    assert settings.idle_timeout_seconds == 30
    assert settings.max_upload_bytes == 25 * 1024 * 1024
    assert settings.max_request_body_size_bytes == 30 * 1024 * 1024
    assert settings.max_concurrent_jobs == 1
    assert settings.max_queued_jobs == 8
    assert settings.worker_job_timeout_ms == 120_000
    assert settings.log_requests is True
    assert settings.cors_allowed_origins == "*"
    assert settings.annotation_worker_asset_cache_size == 4


def test_config_clamps_all_numeric_ranges() -> None:
    settings = get_settings(
        {
            "PORT": "99999",
            "IDLE_TIMEOUT_SECONDS": "1",
            "MAX_UPLOAD_BYTES": "1",
            "MAX_REQUEST_BODY_BYTES": "1",
            "MAX_CONCURRENT_JOBS": "999",
            "MAX_QUEUED_JOBS": "-5",
            "WORKER_JOB_TIMEOUT_MS": "100000000",
            "ANNOTATION_WORKER_ASSET_CACHE_SIZE": "99",
        }
    )
    assert settings.port == 65_535
    assert settings.idle_timeout_seconds == 5
    assert settings.max_upload_bytes == 1_024 * 1_024
    assert settings.max_request_body_size_bytes == settings.max_upload_bytes + 1_024 * 1_024
    assert settings.max_concurrent_jobs == 32
    assert settings.max_queued_jobs == 0
    assert settings.worker_job_timeout_ms == 15 * 60_000
    assert settings.annotation_worker_asset_cache_size == 16


def test_config_treats_empty_strings_as_missing() -> None:
    settings = get_settings(
        {
            "PORT": "",
            "IDLE_TIMEOUT_SECONDS": "",
            "MAX_UPLOAD_BYTES": "",
            "MAX_REQUEST_BODY_BYTES": "",
            "MAX_CONCURRENT_JOBS": "",
            "MAX_QUEUED_JOBS": "",
            "WORKER_JOB_TIMEOUT_MS": "",
            "LOG_REQUESTS": "",
            "CORS_ALLOWED_ORIGINS": "",
            "ANNOTATION_WORKER_ASSET_CACHE_SIZE": "",
        }
    )
    assert settings.port == 3000
    assert settings.idle_timeout_seconds == 30
    assert settings.max_upload_bytes == 25 * 1024 * 1024
    assert settings.max_request_body_size_bytes == 30 * 1024 * 1024
    assert settings.max_concurrent_jobs == 1
    assert settings.max_queued_jobs == 8
    assert settings.worker_job_timeout_ms == 120_000
    assert settings.log_requests is True
    assert settings.cors_allowed_origins == []
    assert settings.annotation_worker_asset_cache_size == 4


def test_config_parses_boolean_and_cors_values() -> None:
    settings = get_settings(
        {
            "LOG_REQUESTS": "off",
            "CORS_ALLOWED_ORIGINS": "http://localhost:5173, https://example.com, invalid-origin, http://localhost:5173",
        }
    )
    assert settings.log_requests is False
    assert settings.cors_allowed_origins == ["http://localhost:5173", "https://example.com"]

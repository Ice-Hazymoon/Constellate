from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from . import ROOT_DIR

Env = Mapping[str, str | None]

DATA_DIR = ROOT_DIR / "data"
ASTROMETRY_DIR = DATA_DIR / "astrometry"
CATALOG_DIR = DATA_DIR / "catalog"
REFERENCE_DIR = DATA_DIR / "reference"
SAMPLES_DIR = ROOT_DIR / "samples"
PUBLIC_DIR = ROOT_DIR / "public"
STARDROID_CONSTELLATIONS_PATH = REFERENCE_DIR / "stardroid-constellations.ascii"
STARDROID_DSO_PATH = REFERENCE_DIR / "stardroid-deep_sky_objects.csv"
STARDROID_LOCALES_DIR = REFERENCE_DIR / "stardroid-locales"
STARDROID_ENGLISH_LOCALIZATION_PATH = STARDROID_LOCALES_DIR / "values" / "celestial_objects.xml"
SUPPLEMENTAL_DSO_PATH = REFERENCE_DIR / "supplemental-deep-sky-objects.json"
CATALOG_PATH = CATALOG_DIR / "minimal_hipparcos.csv"
MODERN_CONSTELLATIONS_PATH = REFERENCE_DIR / "modern_st.json"
STAR_NAMES_PATH = REFERENCE_DIR / "common_star_names.fab"
NGC_PATH = REFERENCE_DIR / "NGC.csv"
DEFAULT_LOCALE = "en"

REQUIRED_ASTROMETRY_INDEXES = tuple(4107 + offset for offset in range(13))

SAMPLE_IMAGES = [
    {
        "id": "apod4",
        "title": "APOD Big Dipper",
        "filename": "apod4.jpg",
        "url": "https://raw.githubusercontent.com/dstndstn/astrometry.net/master/demo/apod4.jpg",
        "note": "34x24 degree field, suitable for testing the Big Dipper / Ursa Major overlay.",
    },
    {
        "id": "orion-over-pines",
        "title": "Orion Over Pine Trees",
        "filename": "orion-over-pines.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/6/69/Orion%27s_wide_field_over_pine_trees.jpg",
        "note": "Earth-view nightscape with foreground trees, useful for testing sky-only crop solving.",
    },
    {
        "id": "apod5",
        "title": "APOD Wide Winter Sky",
        "filename": "apod5.jpg",
        "url": "https://raw.githubusercontent.com/dstndstn/astrometry.net/master/demo/apod5.jpg",
        "note": "Very wide winter-sky stress sample for the plate-solving pipeline.",
    },
]


@dataclass(frozen=True, slots=True)
class Settings:
    port: int
    idle_timeout_seconds: int
    max_request_body_size_bytes: int
    max_upload_bytes: int
    max_concurrent_jobs: int
    max_queued_jobs: int
    worker_job_timeout_ms: int
    log_requests: bool
    cors_allowed_origins: str | list[str]
    annotation_worker_asset_cache_size: int
    root_dir: Path = ROOT_DIR
    data_dir: Path = DATA_DIR
    astrometry_dir: Path = ASTROMETRY_DIR
    catalog_dir: Path = CATALOG_DIR
    reference_dir: Path = REFERENCE_DIR
    samples_dir: Path = SAMPLES_DIR
    public_dir: Path = PUBLIC_DIR
    stardroid_constellations_path: Path = STARDROID_CONSTELLATIONS_PATH
    stardroid_dso_path: Path = STARDROID_DSO_PATH
    stardroid_locales_dir: Path = STARDROID_LOCALES_DIR
    stardroid_english_localization_path: Path = STARDROID_ENGLISH_LOCALIZATION_PATH
    supplemental_dso_path: Path = SUPPLEMENTAL_DSO_PATH
    catalog_path: Path = CATALOG_PATH
    modern_constellations_path: Path = MODERN_CONSTELLATIONS_PATH
    star_names_path: Path = STAR_NAMES_PATH
    ngc_path: Path = NGC_PATH
    sample_images: list[dict[str, str]] = field(
        default_factory=lambda: [dict(item) for item in SAMPLE_IMAGES]
    )
    required_astrometry_indexes: tuple[int, ...] = REQUIRED_ASTROMETRY_INDEXES
    default_locale: str = DEFAULT_LOCALE


def parse_integer(value: str | None, fallback: int, minimum: int, maximum: int) -> int:
    try:
        numeric = int((value or "").strip())
    except ValueError:
        return fallback
    return max(minimum, min(maximum, numeric))


def parse_boolean(value: str | None, fallback: bool) -> bool:
    if value is None or value == "":
        return fallback
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return fallback


def parse_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def parse_cors_allowed_origins(value: str | None) -> str | list[str]:
    if value is None:
        return "*"

    trimmed = value.strip()
    if not trimmed:
        return []
    if trimmed == "*":
        return "*"

    origins: list[str] = []
    for entry in trimmed.split(","):
        origin = parse_origin(entry.strip())
        if origin and origin not in origins:
            origins.append(origin)
    return origins


def get_settings(env: Env | None = None) -> Settings:
    env = env or os.environ

    max_upload_bytes = parse_integer(
        env.get("MAX_UPLOAD_BYTES"),
        25 * 1024 * 1024,
        1_024 * 1_024,
        100 * 1024 * 1024,
    )
    max_request_body_size_bytes = max(
        max_upload_bytes + 1_024 * 1_024,
        parse_integer(
            env.get("MAX_REQUEST_BODY_BYTES"),
            30 * 1024 * 1024,
            max_upload_bytes,
            128 * 1024 * 1024,
        ),
    )

    return Settings(
        port=parse_integer(env.get("PORT"), 3000, 1, 65_535),
        idle_timeout_seconds=parse_integer(env.get("IDLE_TIMEOUT_SECONDS"), 30, 5, 255),
        max_request_body_size_bytes=max_request_body_size_bytes,
        max_upload_bytes=max_upload_bytes,
        max_concurrent_jobs=parse_integer(env.get("MAX_CONCURRENT_JOBS"), 1, 1, 32),
        max_queued_jobs=parse_integer(env.get("MAX_QUEUED_JOBS"), 8, 0, 256),
        worker_job_timeout_ms=parse_integer(
            env.get("WORKER_JOB_TIMEOUT_MS"),
            120_000,
            5_000,
            15 * 60_000,
        ),
        log_requests=parse_boolean(env.get("LOG_REQUESTS"), True),
        cors_allowed_origins=parse_cors_allowed_origins(env.get("CORS_ALLOWED_ORIGINS")),
        annotation_worker_asset_cache_size=parse_integer(
            env.get("ANNOTATION_WORKER_ASSET_CACHE_SIZE"),
            4,
            1,
            16,
        ),
    )

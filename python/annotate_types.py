#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class CropCandidate:
    name: str
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class SourceDetection:
    x: float
    y: float
    flux: float
    peak: float
    major: float
    minor: float
    npix: int
    elongation: float
    star_score: float
    sort_flux: float


@dataclass
class SourceAnalysis:
    mode: str
    detections: list[SourceDetection]
    tile_scores: np.ndarray
    diagnostics: dict[str, Any]


@dataclass
class SolveResult:
    crop: CropCandidate
    downsample: int
    scale_low: float
    scale_high: float
    input_mode: str
    wcs_path: Path
    stdout: str
    stderr: str
    corr_path: Path | None = None
    verification: dict[str, Any] | None = None


@dataclass(frozen=True)
class LocalizationBundle:
    requested_locale: str
    resolved_locale: str
    available_locales: list[str]
    strings: dict[str, str]

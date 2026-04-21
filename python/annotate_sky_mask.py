#!/usr/bin/env python3
"""
Sky/foreground segmentation used to suppress annotations that would otherwise
be drawn on top of terrestrial features.

Primary path: `JianyuanWang/skyseg`, a U-2-Net sky-segmentation ONNX model
hosted on Hugging Face. The runtime averages the model's side outputs and
builds an initial mask with Otsu thresholding.

Night-sky images are outside the model's main training domain, so two guards
stay in front of the final mask:

1. obviously bad model masks are rejected before use,
2. rejected masks fall back to a skyline heuristic; if that heuristic decides
   the whole frame is sky, we simply return a full-frame sky mask.
"""
from __future__ import annotations

import logging
import os
import warnings
from pathlib import Path
from typing import Any

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")
warnings.filterwarnings("ignore", module=r"huggingface_hub.*")

import numpy as np
from PIL import Image
from scipy import ndimage as _ndi

MODEL_REPO_ID = "JianyuanWang/skyseg"
ONNX_MODEL_FILENAME = "skyseg.onnx"
MODEL_INPUT_SIZE = 320
MODEL_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
MODEL_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

# Model masks that barely touch the top edge or cover almost no area are not
# usable for foreground suppression on night landscapes.
MIN_MODEL_MASK_AREA_RATIO = 0.25
SMALL_MODEL_MASK_AREA_RATIO = 0.40
MIN_MODEL_TOP_COVERAGE = 0.35
MIN_SMALL_MASK_TOP_COVERAGE = 0.70

ANALYSIS_SIZE = 512
BACKGROUND_SIGMA = 6.0
STAR_SMALL_SIGMA = 0.7
STAR_MEDIUM_SIGMA = 2.0
STAR_SMOOTH_SIGMA = 2.0
TOP_SEED_BAND_RATIO = 0.10
FOREGROUND_STAR_WEIGHT = 0.75
FOREGROUND_DARK_WEIGHT = 0.25
EDGE_BONUS_WEIGHT = 0.30
LOWER_BOUNDARY_PRIOR = 0.40
BOUNDARY_STEP_DIVISOR = 90.0
BOUNDARY_STEP_PENALTY = 0.28
PURE_SKY_MIN_AREA_RATIO = 0.75
PURE_SKY_MAX_EDGE_SCORE = 0.35

_logger = logging.getLogger(__name__)
_load_attempted = False
_session: Any = None


def _default_hf_home() -> Path:
    return Path(
        os.environ.get(
            "HF_HOME",
            str(Path(__file__).resolve().parent.parent / "hf_cache"),
        )
    )


def _candidate_onnx_paths() -> list[Path]:
    hf_home = _default_hf_home()
    candidates = [
        os.environ.get("SKYSEG_ONNX_PATH"),
        os.environ.get("SKY_MASK_ONNX_PATH"),
        str(hf_home / ONNX_MODEL_FILENAME),
        str(Path(__file__).resolve().parent.parent / "hf_cache" / ONNX_MODEL_FILENAME),
    ]
    result: list[Path] = []
    for candidate in candidates:
        if candidate:
            result.append(Path(candidate))
    return result


def _find_onnx_model() -> Path | None:
    for candidate in _candidate_onnx_paths():
        if candidate.is_file():
            return candidate
    return None


def _download_onnx_model() -> Path | None:
    try:
        from huggingface_hub import hf_hub_download
    except Exception as exc:  # noqa: BLE001
        _logger.warning("huggingface_hub unavailable for sky-mask download (%s)", exc)
        return None

    hf_home = _default_hf_home()
    hf_home.mkdir(parents=True, exist_ok=True)
    try:
        downloaded = hf_hub_download(
            repo_id=MODEL_REPO_ID,
            filename=ONNX_MODEL_FILENAME,
            local_dir=str(hf_home),
        )
        return Path(downloaded)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("failed to download %s (%s)", MODEL_REPO_ID, exc)
        return None


def _load_model() -> bool:
    global _load_attempted, _session

    if _session is not None:
        return True
    if _load_attempted:
        return False
    _load_attempted = True

    onnx_path = _find_onnx_model()
    if onnx_path is None:
        onnx_path = _download_onnx_model()
    if onnx_path is None:
        _logger.warning("sky-mask ONNX model unavailable; using heuristic fallback only")
        return False

    try:
        import onnxruntime as ort

        session_options = ort.SessionOptions()
        session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        _session = ort.InferenceSession(
            str(onnx_path),
            sess_options=session_options,
            providers=["CPUExecutionProvider"],
        )
        _logger.info("sky-mask model loaded via ONNX: %s", onnx_path)
        return True
    except Exception as exc:  # noqa: BLE001
        _logger.warning("failed to load sky-mask ONNX model (%s)", exc)
        return False


def preload() -> bool:
    """Explicit warmup hook. Returns True when the ONNX model is ready."""
    return _load_model()


def _resize_for_model(image: Image.Image) -> Image.Image:
    return image.convert("RGB").resize((MODEL_INPUT_SIZE, MODEL_INPUT_SIZE), Image.BILINEAR)


def _prepare_model_input(image: Image.Image) -> np.ndarray:
    arr = np.asarray(_resize_for_model(image), dtype=np.float32) / 255.0
    normalized = (arr - MODEL_MEAN) / MODEL_STD
    return normalized.transpose(2, 0, 1)[None, ...].astype(np.float32)


def _normalize_score_map(values: np.ndarray) -> np.ndarray:
    minimum = float(values.min())
    maximum = float(values.max())
    if maximum - minimum <= 1e-6:
        fill = 1.0 if maximum >= 0.5 else 0.0
        return np.full(values.shape, fill, dtype=np.float32)
    return ((values - minimum) / (maximum - minimum)).astype(np.float32)


def _run_model(image: Image.Image) -> np.ndarray:
    pixel_values = _prepare_model_input(image)
    input_name = _session.get_inputs()[0].name
    outputs = _session.run(None, {input_name: pixel_values})
    normalized_outputs = [
        _normalize_score_map(np.asarray(item, dtype=np.float32).squeeze())
        for item in outputs
    ]
    return np.mean(normalized_outputs, axis=0).astype(np.float32)


def _otsu_threshold(values: np.ndarray) -> float:
    histogram, _ = np.histogram(values.ravel(), bins=256, range=(0.0, 1.0))
    histogram = histogram.astype(np.float64)
    total = float(histogram.sum())
    if total <= 0.0:
        return 0.5

    probability = histogram / total
    omega = np.cumsum(probability)
    mu = np.cumsum(probability * np.arange(256, dtype=np.float64))
    mu_total = float(mu[-1])
    sigma = (mu_total * omega - mu) ** 2 / np.maximum(omega * (1.0 - omega), 1e-12)
    return (int(np.nanargmax(sigma)) + 0.5) / 256.0


def _seed_band_height(height: int) -> int:
    return max(2, int(round(height * TOP_SEED_BAND_RATIO)))


def _mask_stats(mask: np.ndarray) -> tuple[float, float, float]:
    band = _seed_band_height(mask.shape[0])
    return (
        float(mask.mean()),
        float(mask[:band, :].mean()),
        float(mask[-band:, :].mean()),
    )


def _mask_from_model_score(score_map: np.ndarray) -> np.ndarray:
    threshold = _otsu_threshold(score_map)
    mask = (score_map >= threshold).astype(np.uint8)
    _, top_coverage, bottom_coverage = _mask_stats(mask)
    if top_coverage < bottom_coverage:
        mask = 1 - mask
    return mask


def _model_mask_is_reasonable(mask: np.ndarray) -> bool:
    area_ratio, top_coverage, bottom_coverage = _mask_stats(mask)
    if area_ratio >= 0.96 and bottom_coverage >= 0.95:
        return True
    if top_coverage < MIN_MODEL_TOP_COVERAGE:
        return False
    if area_ratio < MIN_MODEL_MASK_AREA_RATIO:
        return False
    if (
        area_ratio < SMALL_MODEL_MASK_AREA_RATIO
        and top_coverage < MIN_SMALL_MASK_TOP_COVERAGE
    ):
        return False
    if bottom_coverage > 0.85 and area_ratio < 0.95:
        return False
    return True


def _should_bypass_to_full_sky(
    score_map: np.ndarray,
    mask: np.ndarray,
) -> bool:
    area_ratio, top_coverage, bottom_coverage = _mask_stats(mask)
    return (
        area_ratio >= 0.96
        and top_coverage >= 0.95
        and bottom_coverage >= 0.90
        and float(np.percentile(score_map, 5.0)) >= 0.95
    )


def _downsample(image: Image.Image) -> Image.Image:
    rgb = image.convert("RGB")
    width, height = rgb.size
    scale = ANALYSIS_SIZE / max(width, height)
    if scale >= 1.0:
        return rgb
    return rgb.resize(
        (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
        Image.LANCZOS,
    )


def _compute_luma(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"), dtype=np.float32) / 255.0


def _compute_foreground_score(
    luma: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    blurred_luma = _ndi.gaussian_filter(luma, BACKGROUND_SIGMA, mode="reflect")
    star_small = _ndi.gaussian_filter(luma, STAR_SMALL_SIGMA, mode="reflect")
    star_medium = _ndi.gaussian_filter(luma, STAR_MEDIUM_SIGMA, mode="reflect")
    star_feature = _ndi.gaussian_filter(
        np.clip(star_small - star_medium, 0.0, None),
        STAR_SMOOTH_SIGMA,
        mode="reflect",
    )

    seed_band = _seed_band_height(luma.shape[0])
    top_star = float(np.median(star_feature[:seed_band, :]))
    top_brightness = float(np.median(blurred_luma[:seed_band, :]))

    star_scale = max(
        float(np.percentile(star_feature, 90.0) - np.percentile(star_feature, 20.0)),
        1e-4,
    )
    brightness_scale = max(
        float(np.percentile(blurred_luma, 80.0) - np.percentile(blurred_luma, 20.0)),
        1e-4,
    )

    star_deficit = np.clip((top_star - star_feature) / star_scale, 0.0, 1.0)
    dark_deficit = np.clip((top_brightness - blurred_luma) / brightness_scale, 0.0, 1.0)
    raw_score = (
        FOREGROUND_STAR_WEIGHT * star_deficit
        + FOREGROUND_DARK_WEIGHT * dark_deficit
    )

    low_percentile, high_percentile = np.percentile(raw_score, [20.0, 90.0])
    normalized = np.clip(
        (raw_score - float(low_percentile))
        / max(float(high_percentile - low_percentile), 1e-4),
        0.0,
        1.0,
    )
    return blurred_luma, normalized


def _compute_edge_strength(blurred_luma: np.ndarray) -> np.ndarray:
    edge_strength = np.abs(_ndi.sobel(blurred_luma, axis=0, mode="reflect"))
    scale = max(float(np.percentile(edge_strength, 95.0)), 1e-6)
    return np.clip(edge_strength / scale, 0.0, 1.0)


def _trace_sky_boundary(
    foreground_score: np.ndarray,
    edge_strength: np.ndarray,
) -> tuple[np.ndarray, float]:
    height, width = foreground_score.shape
    diff = (1.0 - foreground_score) - foreground_score + edge_strength * EDGE_BONUS_WEIGHT
    cumulative = np.cumsum(diff, axis=0)
    cumulative += (
        np.arange(height, dtype=np.float32)[:, None] / max(height - 1, 1)
    ) * LOWER_BOUNDARY_PRIOR

    max_step = max(4, int(round(height / BOUNDARY_STEP_DIVISOR)))
    dp = cumulative[:, 0].astype(np.float32, copy=True)
    backtrack = np.zeros((width, height), dtype=np.int16)
    row_indices = np.arange(height, dtype=np.int16)

    for column in range(1, width):
        new_scores = np.full(height, -1e9, dtype=np.float32)
        previous_rows = np.zeros(height, dtype=np.int16)
        for delta in range(-max_step, max_step + 1):
            penalty = BOUNDARY_STEP_PENALTY * abs(delta)
            if delta < 0:
                candidate_scores = dp[-delta:] - penalty
                better = candidate_scores > new_scores[: height + delta]
                new_scores[: height + delta][better] = candidate_scores[better]
                previous_rows[: height + delta][better] = row_indices[-delta:][better]
            elif delta > 0:
                candidate_scores = dp[: height - delta] - penalty
                better = candidate_scores > new_scores[delta:]
                new_scores[delta:][better] = candidate_scores[better]
                previous_rows[delta:][better] = row_indices[: height - delta][better]
            else:
                better = dp > new_scores
                new_scores[better] = dp[better]
                previous_rows[better] = row_indices[better]
        dp = new_scores + cumulative[:, column]
        backtrack[column] = previous_rows

    row = int(np.argmax(dp))
    boundary = np.zeros(width, dtype=np.int16)
    boundary[-1] = row
    for column in range(width - 1, 0, -1):
        row = int(backtrack[column, row])
        boundary[column - 1] = row

    mean_edge = float(np.mean(edge_strength[boundary, np.arange(width)]))
    return boundary, mean_edge


def _mask_from_boundary(height: int, width: int, boundary: np.ndarray) -> np.ndarray:
    mask = np.zeros((height, width), dtype=np.uint8)
    for column, row in enumerate(boundary):
        mask[: int(row) + 1, column] = 1
    return mask


def _should_use_full_sky(mask: np.ndarray, mean_edge_score: float) -> bool:
    sky_area_ratio = float(mask.mean())
    return (
        sky_area_ratio >= PURE_SKY_MIN_AREA_RATIO
        and mean_edge_score <= PURE_SKY_MAX_EDGE_SCORE
    )


def _resize_mask(
    small_mask: np.ndarray,
    target_size: tuple[int, int],
) -> np.ndarray:
    mask_bool = small_mask.astype(bool)
    full = Image.fromarray((mask_bool.astype(np.uint8) * 255), "L").resize(
        target_size,
        Image.BILINEAR,
    )
    return (np.asarray(full, dtype=np.uint8) >= 128).astype(np.uint8)


def _build_heuristic_mask(image: Image.Image) -> np.ndarray | None:
    small = _downsample(image)
    luma = _compute_luma(small)
    if luma.ndim != 2 or luma.size == 0:
        return None

    blurred_luma, foreground_score = _compute_foreground_score(luma)
    edge_strength = _compute_edge_strength(blurred_luma)
    boundary, mean_edge_score = _trace_sky_boundary(foreground_score, edge_strength)
    small_mask = _mask_from_boundary(luma.shape[0], luma.shape[1], boundary)
    if _should_use_full_sky(small_mask, mean_edge_score):
        return np.ones(image.size[::-1], dtype=np.uint8)
    return _resize_mask(
        small_mask,
        image.size,
    )


def compute_sky_mask(image: Image.Image) -> np.ndarray | None:
    """
    Return a uint8 mask (0/1) the same size as `image`. 1 = sky.

    Returns None only if both the model path and the heuristic fallback fail.
    """
    if _load_model():
        score_map = _run_model(image)
        model_mask = _mask_from_model_score(score_map)
        if _should_bypass_to_full_sky(score_map, model_mask):
            return np.ones(image.size[::-1], dtype=np.uint8)
        if _model_mask_is_reasonable(model_mask):
            return _resize_mask(
                model_mask,
                image.size,
            )

    return _build_heuristic_mask(image)


def _in_sky(mask: np.ndarray, x: float, y: float) -> bool:
    height, width = mask.shape
    xi = max(0, min(width - 1, int(round(float(x)))))
    yi = max(0, min(height - 1, int(round(float(y)))))
    return bool(mask[yi, xi])


def mask_is_trustworthy(
    mask: np.ndarray,
    star_positions: list[tuple[float, float]],
    min_sky_ratio: float = 0.25,
    min_area_ratio: float = 0.08,
    min_top_coverage: float = 0.20,
) -> bool:
    """Sanity-check a mask against geometry plus plate-solved star positions."""
    area_ratio, top_coverage, _ = _mask_stats(mask)
    if area_ratio < min_area_ratio:
        return False
    if top_coverage < min_top_coverage:
        return False
    if not star_positions:
        return True
    in_sky = sum(1 for x, y in star_positions if _in_sky(mask, x, y))
    return (in_sky / len(star_positions)) >= min_sky_ratio


def filter_named_stars(
    named_stars: list[dict[str, Any]],
    mask: np.ndarray | None,
) -> list[dict[str, Any]]:
    if mask is None:
        return named_stars
    return [star for star in named_stars if _in_sky(mask, star["x"], star["y"])]


def filter_deep_sky_objects(
    objects: list[dict[str, Any]],
    mask: np.ndarray | None,
) -> list[dict[str, Any]]:
    if mask is None:
        return objects
    return [obj for obj in objects if _in_sky(mask, obj["x"], obj["y"])]


def _clip_segment_to_sky(
    segment: dict[str, Any],
    mask: np.ndarray,
) -> dict[str, Any] | None:
    start = segment["start"]
    end = segment["end"]
    start_in_sky = _in_sky(mask, start["x"], start["y"])
    end_in_sky = _in_sky(mask, end["x"], end["y"])
    if start_in_sky and end_in_sky:
        return segment
    if not start_in_sky and not end_in_sky:
        return None

    sky_point = start if start_in_sky else end
    ground_point = end if start_in_sky else start
    sx, sy = float(sky_point["x"]), float(sky_point["y"])
    gx, gy = float(ground_point["x"]), float(ground_point["y"])
    for _ in range(14):
        mx, my = (sx + gx) / 2.0, (sy + gy) / 2.0
        if _in_sky(mask, mx, my):
            sx, sy = mx, my
        else:
            gx, gy = mx, my

    boundary = {"x": sx, "y": sy}
    if start_in_sky:
        return {"start": start, "end": boundary}
    return {"start": boundary, "end": end}


def _relocate_label_to_sky(
    kept_segments: list[dict[str, Any]],
    mask: np.ndarray,
) -> tuple[float, float] | None:
    if not kept_segments:
        return None

    xs: list[float] = []
    ys: list[float] = []
    for segment in kept_segments:
        xs.extend([segment["start"]["x"], segment["end"]["x"]])
        ys.extend([segment["start"]["y"], segment["end"]["y"]])
    centroid_x = sum(xs) / len(xs)
    centroid_y = sum(ys) / len(ys)
    if _in_sky(mask, centroid_x, centroid_y):
        return centroid_x, centroid_y

    for segment in kept_segments:
        mid_x = (segment["start"]["x"] + segment["end"]["x"]) / 2.0
        mid_y = (segment["start"]["y"] + segment["end"]["y"]) / 2.0
        if _in_sky(mask, mid_x, mid_y):
            return mid_x, mid_y

    first = kept_segments[0]["start"]
    return float(first["x"]), float(first["y"])


def _label_is_near_border(
    label_x: float,
    label_y: float,
    mask: np.ndarray,
    pad_px: float = 8.0,
) -> bool:
    height, width = mask.shape
    return (
        label_x <= pad_px
        or label_x >= width - 1 - pad_px
        or label_y <= pad_px
        or label_y >= height - 1 - pad_px
    )


def filter_constellations(
    constellations: list[dict[str, Any]],
    mask: np.ndarray | None,
) -> list[dict[str, Any]]:
    if mask is None:
        return constellations

    filtered: list[dict[str, Any]] = []
    for constellation in constellations:
        kept_segments: list[dict[str, Any]] = []
        for segment in constellation.get("segments", []):
            clipped = _clip_segment_to_sky(segment, mask)
            if clipped is not None:
                kept_segments.append(clipped)
        if not kept_segments:
            continue

        new_entry = dict(constellation)
        new_entry["segments"] = kept_segments
        label_x = float(constellation.get("label_x", 0.0))
        label_y = float(constellation.get("label_y", 0.0))
        label_in_sky = _in_sky(mask, label_x, label_y)
        label_at_border = _label_is_near_border(label_x, label_y, mask)
        if not label_in_sky or label_at_border:
            relocated = _relocate_label_to_sky(kept_segments, mask)
            if relocated is not None:
                rx, ry = relocated
                if not _label_is_near_border(rx, ry, mask):
                    new_entry["label_x"] = float(rx)
                    new_entry["label_y"] = float(ry)
        filtered.append(new_entry)
    return filtered

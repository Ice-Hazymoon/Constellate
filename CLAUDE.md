# CLAUDE.md

This file provides guidance for coding agents working in this repository.

## Commands

Python environment lives at `.venv/`.

```bash
# First-time setup
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Run the server
.venv/bin/python -m star_server

# Run tests
.venv/bin/pytest -q

# End-to-end sample call
curl -s -F image=@samples/apod4.jpg http://127.0.0.1:3000/api/analyze | jq .

# Python-only end-to-end (bypasses FastAPI, useful for profiling a single stage)
.venv/bin/python python/annotate.py \
  --input samples/apod4.jpg --output-json /tmp/out.json \
  --index-dir data/astrometry --catalog data/catalog/minimal_hipparcos.csv \
  --constellations data/reference/stardroid-constellations.ascii \
  --constellations data/reference/modern_st.json \
  --star-names data/reference/common_star_names.fab \
  --dso-catalog data/reference/NGC.csv \
  --dso-catalog data/reference/stardroid-deep_sky_objects.csv

# Preload the ONNX sky-mask model into the local HF cache
.venv/bin/python -c "import sys; sys.path.insert(0, 'python'); import annotate_sky_mask as m; print(m.preload())"
```

`solve-field` (Astrometry.net) must be installed on the host. The Dockerfile installs it with `apt`.

## Architecture

### In-process runtime

```text
Browser ──HTTP──▶ FastAPI / uvicorn (star_server.app)
                   │
                   └── ThreadPoolExecutor job
                         └── annotate.annotate_image(...)
                               └── solve-field subprocess
```

- `star_server/` owns HTTP, upload validation, queueing, locale negotiation, preload, and error mapping.
- `python/annotate.py` and the rest of the annotation pipeline remain the business-logic core.
- Catalogs, Stardroid references, and the sky-mask ONNX model are preloaded once during app startup and then reused in-process.

This repo previously used a Bun HTTP server plus a Python worker subprocess. That split is gone. There is no stdin/stdout worker protocol and no CLI fallback path anymore.

### Annotation pipeline (`python/annotate.py:annotate_image`)

Sequential stages, each contributing to `timings_ms`:

1. `normalize` (`annotate_image_ops.py`) - decode image, set PIL `MAX_IMAGE_PIXELS`, filter FITS warnings.
2. `solve` (`annotate_solving.py`) - plate solve via `solve-field`. Tries a ladder of crop candidates and scale windows under a total `SOLVE_TIME_BUDGET_S` wall budget.
3. `scene` (`annotate_scene.py`) - project stars, constellation segments, and DSOs through the WCS.
4. `sky_mask` (`annotate_sky_mask.py`) - ONNX sky segmentation model with heuristic fallback.
5. `overlay_scene` (`annotate_scene.py:build_overlay_scene`) - assemble render-ready JSON.
6. `render` - optional, only when `render_mode=server`.

### Invariants

- **Astrometry.net is non-deterministic across runs.** Two solves on the same image can produce slightly different WCS and downstream visible-object lists.
- **Sky mask behavior is load-bearing.** The fallback heuristics and trust checks preserve the Orion treeline / all-sky behavior when the model is out of domain.
- **Business logic files are intentionally stable.** Avoid changing `python/annotate.py`, `annotate_solving.py`, `annotate_scene.py`, `annotate_sky_mask.py`, `annotate_image_ops.py`, `annotate_types.py`, and `annotate_geometry.py` unless the task explicitly requires it.

## Data layout

- `data/astrometry/` - large index files
- `data/catalog/minimal_hipparcos.csv` - filtered HIP star catalog
- `data/reference/` - Stellarium + Stardroid reference assets and localization XMLs
- `hf_cache/` - Hugging Face cache for the baked `skyseg.onnx` model

## Environment variables

Supported:

- `PORT`
- `IDLE_TIMEOUT_SECONDS`
- `MAX_UPLOAD_BYTES`
- `MAX_REQUEST_BODY_BYTES`
- `MAX_CONCURRENT_JOBS`
- `MAX_QUEUED_JOBS`
- `WORKER_JOB_TIMEOUT_MS`
- `LOG_REQUESTS`
- `CORS_ALLOWED_ORIGINS`
- `ANNOTATION_WORKER_ASSET_CACHE_SIZE`

Removed:

- `ALLOW_CLI_FALLBACK`
  Replacement: none. Annotation always runs in-process, so there is no subprocess worker failure mode to fall back from.
- `PRELOAD_WORKER_ON_STARTUP`
  Replacement: startup preload is unconditional. The app blocks readiness until preload completes.

## Docker build

The Dockerfile has three stages:

1. `python-deps` - creates `.venv`, installs requirements, preloads the ONNX sky-mask model.
2. `data-bootstrap` - downloads astrometry indexes into `data/astrometry`.
3. `runtime` - copies `.venv`, `hf_cache`, `data`, `python`, `star_server`, `public`, and `samples`, then runs `python -m star_server`.

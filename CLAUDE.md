# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Python environment lives at `.venv/`. The Bun server discovers it via `PYTHON_BIN_CANDIDATES` in `src/app-config.ts` (`.venv/bin/python` → `.venv/bin/python3` → system `python3`). When running Python scripts manually, use `.venv/bin/python3` so imports resolve against the pinned deps.

```bash
# First-time setup
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
bun install

# Fetch astrometry indexes (4107-4119), catalogs, reference data — required before first run
bun run bootstrap

# Dev / prod
bun run dev          # bootstrap + server
bun run start        # server only
bun run check        # typecheck + all tests

# Tests (bun test; file name ends in .test.ts)
bun test                                    # all
bun test src/job-limiter.test.ts            # single file
bun test -t "queues additional work"        # by test name

# Run built-in samples end-to-end (prints JSON to stdout)
bun run sample:apod4       # or sample:apod5, sample:orion

# Python-only end-to-end (bypasses worker; useful for profiling a single stage)
.venv/bin/python3 python/annotate.py \
  --input samples/apod4.jpg --output-json /tmp/out.json \
  --index-dir data/astrometry --catalog data/catalog/minimal_hipparcos.csv \
  --constellations data/reference/stardroid-constellations.ascii \
  --constellations data/reference/modern_st.json \
  --star-names data/reference/common_star_names.fab \
  --dso-catalog data/reference/NGC.csv \
  --dso-catalog data/reference/stardroid-deep_sky_objects.csv

# Preload the ONNX sky-mask model into the local HF cache
.venv/bin/python3 -c "import sys; sys.path.insert(0, 'python'); import annotate_sky_mask as m; print(m.preload())"
```

`solve-field` (Astrometry.net) must be installed on the host; the Dockerfile installs it via `apt`.

## Architecture

### Three-process runtime

```
Browser ──HTTP──▶ Bun server (src/server.ts)
                   │
                   │ JSON over stdin/stdout (newline-delimited)
                   ▼
                  Python worker (python/annotate_worker.py)
                   │
                   ├── imports annotate.py pipeline
                   └── shells out to `solve-field` (subprocess) for plate solving
```

- The **Bun server** handles HTTP, upload validation, queueing (`job-limiter.ts`), overlay option resolution, and locale negotiation. It has no astronomy logic.
- The **Python worker** is a long-lived subprocess. It preloads catalogs, Stardroid references, and the sky-mask ONNX model once at startup, then handles one job at a time via newline-delimited JSON on stdin/stdout. Actions: `ping`, `preload`, `annotate`.
- If the worker fails a request and `ALLOW_CLI_FALLBACK=true`, the server falls back to spawning `annotate.py` as a one-shot CLI (`runAnnotationViaCli` in `server.ts`). This path has no preloading — it's slower but works when the worker is wedged.

`src/api-types.ts` is the TypeScript source of truth for the request/response contract between server and client; `overlay-options.ts` owns the presets/layers schema mirrored in `python/annotate_options.py`.

### Annotation pipeline (in `python/annotate.py:annotate_image`)

Sequential stages — each contributes to `timings_ms` in the response:

1. **normalize** (`annotate_image_ops.py`) — decode image, set PIL `MAX_IMAGE_PIXELS`, filter FITS warnings.
2. **solve** (`annotate_solving.py`) — plate solve via `solve-field`. Tries a ladder of (crop candidate × scale window) attempts under a total `SOLVE_TIME_BUDGET_S` wall budget; cpulimits and wall timeouts sized empirically from sample profiling (see comment at top of the file). Each attempt's wall time is recorded in `attempts[].wall_ms` for diagnostics. **Astrometry.net is non-deterministic across runs** — two consecutive solves on the same image can produce slightly different WCS and thus different downstream visible-object lists.
3. **scene** (`annotate_scene.py`) — project catalog stars, constellation segments, and DSOs through the WCS. All three collectors use a single batched `project_points` + `skycoord_separation_degrees` call per catalog; per-object astropy calls in inner loops are a known perf trap (the DSO catalog alone is ~14k entries).
4. **sky_mask** (`annotate_sky_mask.py`) — `JianyuanWang/skyseg` ONNX model with heuristic fallback. See "Sky mask" below.
5. **overlay_scene** (`annotate_scene.py:build_overlay_scene`) — assemble render-ready JSON (dedup, label placement, leader lines).
6. **render** — optional; only runs when `output_image_path` is provided (render_mode=`server`).

### Sky mask (`python/annotate_sky_mask.py`)

Runtime: loads `skyseg.onnx` via `onnxruntime` from `$HF_HOME/skyseg.onnx` (Docker bakes this during build; local dev downloads on first preload if missing). The model input is fixed at `320×320`, uses ImageNet mean/std normalization, and averages all seven U-2-Net outputs before thresholding.

Night-sky images can still confuse the model. When the model mask is obviously bad (e.g. tiny sky area or weak top-edge coverage), the code falls back to the skyline heuristic in the same module. That fallback can also decide the whole frame is sky and return a full-frame mask, which avoids the "Milky Way cut out of a pure-sky shot" failure mode.

Load-bearing pieces here:

- `MODEL_INPUT_SIZE = 320` — matches the hosted ONNX graph.
- `_model_mask_is_reasonable(...)` — rejects tiny or inverted model masks before they affect solving/rendering.
- The heuristic fallback constants (`ANALYSIS_SIZE`, `BACKGROUND_SIGMA`, `BOUNDARY_STEP_*`, `PURE_SKY_*`) — these are what preserve the Orion treeline / all-sky behavior when the model is out of domain.

After the model/fallback: `mask_is_trustworthy` rejects masks that are geometrically implausible or place <25% of plate-solved stars in sky.

### Data layout

- `data/astrometry/` — large index files, not in Git. Populated by `bun run bootstrap` or the Docker data-bootstrap stage.
- `data/catalog/minimal_hipparcos.csv` — HIP star catalog, pre-filtered to stars referenced by constellations + named-star tables.
- `data/reference/` — Stellarium + Stardroid constellation lines, DSO catalogs, localization XMLs. Provenance in `docs/data-sources.md`.
- `hf_cache/` — HuggingFace cache for the baked `skyseg.onnx` model. Not in Git; Docker populates it during build.

### Docker build

Three stages: `python-deps` (installs Python requirements and preloads the ONNX sky-mask model), `data-bootstrap` (downloads astrometry indexes and reference data), and final runtime (copies `/app/.venv`, `/app/hf_cache`, and `/app/data`). The final image sets `HF_HUB_OFFLINE=1`, so the model cache must be populated during build.

# Star Annotator Demo

A lightweight star-field recognition service built as a single Python process with FastAPI, an in-process annotation pipeline, and Astrometry.net.

Given a night-sky photo captured from the ground, the service can:

1. Plate-solve the image
2. Query star and deep-sky catalogs
3. Project celestial coordinates back into image space through WCS
4. Draw constellation lines, constellation labels, star names, and deep-sky markers
5. Return structured JSON, with optional server-side rendered output

## Example

<table>
  <tr>
    <td align="center"><strong>Input</strong></td>
    <td align="center"><strong>Annotated Output</strong></td>
  </tr>
  <tr>
    <td><img src="./test/input.jpg" alt="Input sky photo" width="420" /></td>
    <td><img src="./test/refined-output.png" alt="Annotated star field" width="420" /></td>
  </tr>
</table>

## Highlights

- Upload `JPG`, `PNG`, or `WebP` sky photos
- Automatic plate solving with Astrometry.net
- Named stars, constellation lines, constellation labels, and deep-sky objects
- Multiple overlay presets and fine-grained layer switches
- Request-level locale selection with Stardroid-backed multilingual labels
- `server` render mode: returns a Base64-encoded annotated image
- `client` render mode: returns `overlay_scene` JSON for your own renderer
- No long-term file storage for uploads or generated results

## Stack

- FastAPI + uvicorn
- Python annotation pipeline
- [Astrometry.net](https://github.com/dstndstn/astrometry.net)
- Astropy / Skyfield / SEP / Pillow
- Stellarium sky-culture reference data
- Stardroid-derived supplemental constellation and deep-sky reference data
- Stardroid multilingual locale tables included for request-time label selection

## Project Layout

```text
.
├── data/
│   ├── astrometry/
│   ├── catalog/
│   └── reference/
├── public/
├── python/
├── samples/
├── star_server/
├── test/
├── tests/
├── Dockerfile
└── README.md
```

## How It Works

Runtime architecture:

```text
Browser ──HTTP──▶ FastAPI / uvicorn
                   │
                   └── in-process annotate.annotate_image(...)
                         └── shells out to solve-field when plate solving
```

The server preloads catalogs, Stardroid locale data, and the sky-mask ONNX model once during startup. Every request then runs annotation in a thread pool instead of talking to a long-lived subprocess over stdin/stdout.

Reference and localization provenance is documented in [docs/data-sources.md](./docs/data-sources.md).

## Quick Start

### 1. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

You also need `solve-field` installed locally.

### 2. Start the server

```bash
python -m star_server
```

Default URL:

- [http://localhost:3000](http://localhost:3000)

Cross-origin API access is enabled by default. To restrict it in production, set `CORS_ALLOWED_ORIGINS` to a comma-separated allowlist such as:

```bash
CORS_ALLOWED_ORIGINS=http://localhost:5173,https://your-app.example python -m star_server
```

### 3. Run tests

```bash
pytest -q
```

## Docker

The Docker build bootstraps missing astrometry indexes during image creation, so a fresh server can build from the repository without pre-populating `data/astrometry`. The first build requires network access and will take noticeably longer while it downloads the `4107-4119` index set.

Build:

```bash
docker build -t star-annotator:local .
```

Run:

```bash
docker run --rm -p 3000:3000 --name star-annotator star-annotator:local
```

Health checks:

```bash
curl http://127.0.0.1:3000/healthz
curl http://127.0.0.1:3000/readyz
```

## Render Modes

`render_mode` supports two values:

- `server`: the backend renders the annotated image and returns it as `annotatedImageBase64`
- `client`: the backend returns recognition data plus `overlay_scene`, leaving rendering to the client

Neither mode stores generated images on disk as durable output.

## Built-in Samples

The server ships with three sample images:

- `apod4` - wide Big Dipper / Ursa Major field
- `orion-over-pines` - real nightscape with foreground trees
- `apod5` - wide winter-sky stress sample

## API Overview

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/` | Minimal demo page |
| `GET` | `/app.js` | Frontend script |
| `GET` | `/samples/:filename` | Built-in sample images |
| `GET` | `/healthz` | Liveness probe |
| `GET` | `/readyz` | Readiness probe |
| `GET` | `/api/samples` | List built-in samples |
| `GET` | `/api/overlay-options` | Return default overlay options and presets |
| `POST` | `/api/analyze` | Upload and analyze an image |
| `POST` | `/api/analyze-sample` | Analyze a built-in sample |

## `GET /healthz`

Returns service health:

```json
{
  "ok": true,
  "uptimeMs": 2142,
  "activeJobs": 0,
  "queuedJobs": 0,
  "workerReady": true,
  "pendingWorkerRequests": 0,
  "config": {
    "maxUploadBytes": 26214400,
    "maxConcurrentJobs": 1,
    "maxQueuedJobs": 8
  }
}
```

## `GET /readyz`

Returns `503` until preload completes, then the same payload shape as `/healthz`.

## `POST /api/analyze`

Content type: `multipart/form-data`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `image` | `File` | Yes | Uploaded sky image |
| `render_mode` | `string` | No | `server` or `client` |
| `locale` | `string` | No | Preferred label locale such as `en`, `ja`, `zh-Hans`, `fr` |
| `options` | `string` | No | JSON string overriding overlay options |

Example:

```bash
curl -X POST http://127.0.0.1:3000/api/analyze \
  -F "image=@samples/apod4.jpg" \
  -F 'render_mode=server' \
  -F 'locale=ja' \
  -F 'options={"preset":"max"}'
```

## `POST /api/analyze-sample`

Content type: `application/json`

```bash
curl -X POST http://127.0.0.1:3000/api/analyze-sample \
  -H 'Content-Type: application/json' \
  -d '{"id":"orion-over-pines","render_mode":"client","locale":"ja","options":{"preset":"max"}}'
```

## Response Shape

Both `POST /api/analyze` and `POST /api/analyze-sample` return the same payload family. Key fields:

- `processingMs`
- `render_mode`
- `render_options`
- `available_renders`
- `inputImageUrl`
- `annotatedImageBase64`
- `annotatedImageMimeType`
- `overlay_scene`
- `solve`
- `solve_verification`
- `visible_named_stars`
- `visible_constellations`
- `visible_deep_sky_objects`
- `source_analysis`
- `timings_ms`

## Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `PORT` | `3000` | HTTP port |
| `IDLE_TIMEOUT_SECONDS` | `30` | Keep-alive timeout |
| `MAX_UPLOAD_BYTES` | `26214400` | Maximum upload size |
| `MAX_REQUEST_BODY_BYTES` | `31457280` | Maximum request body size |
| `MAX_CONCURRENT_JOBS` | `1` | Maximum in-flight annotation jobs |
| `MAX_QUEUED_JOBS` | `8` | Maximum queued jobs waiting for a slot |
| `WORKER_JOB_TIMEOUT_MS` | `120000` | Per-request wall-clock timeout |
| `LOG_REQUESTS` | `true` | Enable request logging |
| `CORS_ALLOWED_ORIGINS` | `*` | `*` or a comma-separated origin allowlist |
| `ANNOTATION_WORKER_ASSET_CACHE_SIZE` | `4` | Locale-specific asset cache size |

## Notes

- Uploads and generated images are not stored permanently
- Server rendering returns the image inline as Base64
- Client rendering uses `overlay_scene` on top of the original image
- Sample images remain available through `/samples/:filename`
- `data/astrometry/` is large and intentionally treated as runtime data
- Constellation and DSO display labels are loaded from included Stardroid XML resources, not hard-coded inside the pipeline
- When a locale is unavailable, the service falls back to the included English labels instead of hand-written translations

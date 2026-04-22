# Production Review Checklist

## Runtime prerequisites

- [ ] Python runtime available and pinned in deployment image
- [ ] `solve-field` installed and executable
- [ ] Astrometry indexes `4107-4119` present
- [ ] Required reference catalogs present
- [ ] Health endpoints wired into orchestrator probes

## API safety

- [ ] Request body size capped
- [ ] Upload size capped
- [ ] Unsupported file types rejected
- [ ] Busy server returns `429` instead of queueing unbounded work
- [ ] Annotation jobs have a hard timeout

## Process management

- [ ] Reference data and sky-mask model preload at startup
- [ ] Graceful shutdown stops HTTP server and in-process executor cleanly
- [ ] Request-scoped temp files are removed after each run

## Security and privacy

- [ ] Security headers applied to all responses
- [ ] Uploaded inputs are not exposed publicly unless explicitly enabled
- [ ] Static file serving prevents path traversal
- [ ] Runtime downloads are disabled in production deploy path

## Observability

- [ ] Request IDs added to API responses
- [ ] Startup failures are explicit and fail fast
- [ ] Health response includes preload/job state
- [ ] Error paths are logged without leaking stack traces to clients

## Verification

- [ ] `pytest -q`
- [ ] `python -m star_server`
- [ ] `curl -s -F image=@samples/orion-over-pines.jpg http://127.0.0.1:3000/api/analyze`
- [ ] Manual smoke check of `/healthz`, `/readyz`, `/api/analyze`

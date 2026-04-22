FROM python:3.12-slim AS python-deps

WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_CONFIG_FILE=/dev/null \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/hf_cache \
    HTTP_PROXY= \
    HTTPS_PROXY= \
    ALL_PROXY= \
    http_proxy= \
    https_proxy= \
    all_proxy= \
    NO_PROXY= \
    no_proxy=

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}"

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && find /app/.venv -name '*.pyc' -delete \
    && find /app/.venv -name '__pycache__' -type d -prune -exec rm -rf '{}' +

COPY python/annotate_sky_mask.py /tmp/annotate_sky_mask.py
RUN python -c "import sys; sys.path.insert(0, '/tmp'); import annotate_sky_mask as m; assert m.preload(), 'sky-mask model failed to load during build'" \
    && rm /tmp/annotate_sky_mask.py \
    && find /app/hf_cache -name '*.pyc' -delete 2>/dev/null || true

FROM python:3.12-slim AS data-bootstrap

WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive \
    HTTP_PROXY= \
    HTTPS_PROXY= \
    ALL_PROXY= \
    http_proxy= \
    https_proxy= \
    all_proxy= \
    NO_PROXY= \
    no_proxy=

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY data/catalog ./data/catalog
COPY data/reference ./data/reference
COPY samples ./samples

RUN mkdir -p /app/data/astrometry \
    && for index in 4107 4108 4109 4110 4111 4112 4113 4114 4115 4116 4117 4118 4119; do \
        echo "download index-${index}.fits"; \
        curl -fsSL --retry 3 --retry-delay 2 \
          "http://data.astrometry.net/4100/index-${index}.fits" \
          --output "/app/data/astrometry/index-${index}.fits"; \
      done

FROM python:3.12-slim AS runtime

WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/hf_cache \
    HF_HUB_OFFLINE=1 \
    HTTP_PROXY= \
    HTTPS_PROXY= \
    ALL_PROXY= \
    http_proxy= \
    https_proxy= \
    all_proxy= \
    NO_PROXY= \
    no_proxy= \
    PORT=3000

RUN apt-get update \
    && apt-get install -y --no-install-recommends astrometry.net ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=python-deps /app/.venv /app/.venv
COPY --from=python-deps /app/hf_cache /app/hf_cache
ENV PATH="/app/.venv/bin:${PATH}"

COPY --from=data-bootstrap /app/data /app/data
COPY --from=data-bootstrap /app/samples /app/samples

COPY public ./public
COPY python ./python
COPY star_server ./star_server
COPY requirements.txt README.md CLAUDE.md ./

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import os, sys, urllib.request; port = os.environ.get('PORT', '3000'); response = urllib.request.urlopen(f'http://127.0.0.1:{port}/readyz', timeout=4); sys.exit(0 if 200 <= response.status < 400 else 1)"]

CMD ["python", "-m", "star_server"]

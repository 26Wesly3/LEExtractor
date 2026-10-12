# syntax=docker/dockerfile:1
# LEExtractor 0.9.13 — one image, built for the reference host
# (Ubuntu 24.04, 2 vCPU, 2 GB RAM, 40 GB disk, Singapore).
#
# Two stages keep the runtime small: Node builds the Vue bundle, the Python
# stage never sees Node, npm or the toolchain. Nothing here depends on Windows,
# %TEMP%, %LOCALAPPDATA% or the build user's home.
#
# NOTE: this file has not been built on a machine with Docker yet. The devlog
# (docs/devlog/v0.9.13.md) tracks that gap; do not report it as verified.

FROM node:22-bookworm-slim AS web
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LEEXTRACTOR_HOST=0.0.0.0 \
    LEEXTRACTOR_PORT=8012 \
    LEEXTRACTOR_DATA_DIR=/data \
    LEEXTRACTOR_MODEL_DIR=/models \
    LEEXTRACTOR_CACHE_DIR=/cache \
    LEEXTRACTOR_LOG_DIR=/logs \
    LEEXTRACTOR_LOG_FORMAT=json \
    LEEXTRACTOR_MAX_ACTIVE_JOBS=1 \
    LEEXTRACTOR_EMBEDDING_THREADS=2 \
    LEEXTRACTOR_EMBEDDING_BATCH=8

WORKDIR /app

# curl is the health probe; nothing else is needed at runtime.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml requirements.txt constraints.txt README.md ./
COPY litsearch/ litsearch/
COPY scripts/ scripts/
COPY server.py app.py ./

# Editable install keeps /app/litsearch as the imported package, so the synced
# static assets below are the ones the API serves.
RUN python -m pip install --upgrade pip \
 && python -m pip install -e .

# Built web bundle -> the package's static directory.
COPY --from=web /build/web/dist/ web/dist/
RUN python scripts/sync_web_assets.py

# Non-root runtime user; the four persistent roots are the only writable paths.
RUN useradd --create-home --uid 10001 leextractor \
 && mkdir -p /data /models /cache /logs \
 && chown -R leextractor:leextractor /data /models /cache /logs /app
USER leextractor

EXPOSE 8012

# Liveness only: a provider outage must not make the container unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${LEEXTRACTOR_PORT}/api/health/live" || exit 1

ENTRYPOINT ["python", "-m", "litsearch.web"]

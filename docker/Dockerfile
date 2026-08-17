# Stage 1: Build browser assets
FROM node:20-slim AS frontend-builder
WORKDIR /build
COPY package.json package-lock.json tsconfig.json tsconfig.build.json ./
COPY frontend-src/ ./frontend-src/
# The whole app tree must be present: Tailwind only emits utilities it finds in
# scanned sources, and the dashboard builds most of the request-details markup in
# app/siftarr/static/js/. Copying just the templates silently dropped every
# JS-only class (e.g. the `gap-*` on season/episode rows, which then rendered
# with no spacing at all in the container while looking fine locally).
COPY app/ ./app/
RUN --mount=type=cache,target=/root/.npm \
    npm ci && \
    npm run build:frontend && \
    npm run build:css

# Stage 2: Python application
FROM python:3.14-slim

ARG SIFTARR_VERSION=0.0.0
LABEL org.opencontainers.image.title="siftarr"
LABEL org.opencontainers.image.version="${SIFTARR_VERSION}"
ENV SIFTARR_VERSION="${SIFTARR_VERSION}"

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set working directory
WORKDIR /app

# Create non-root user
RUN groupadd -g 1000 appgroup && useradd -u 1000 -g appgroup -m -s /bin/bash appuser

# Copy dependency metadata first for dependency-layer caching.
COPY pyproject.toml uv.lock* ./

# Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# Copy application code
COPY app/ ./app/

# Install the project after source is present. Set both hatch-vcs/setuptools-scm
# version override forms so builds without .git metadata (Docker context) work.
RUN --mount=type=cache,target=/root/.cache/uv \
    SETUPTOOLS_SCM_PRETEND_VERSION="${SIFTARR_VERSION#v}" \
    SETUPTOOLS_SCM_PRETEND_VERSION_FOR_SIFTARR="${SIFTARR_VERSION#v}" \
    uv sync --frozen

# Copy assets rebuilt from their canonical sources.
COPY --from=frontend-builder /build/app/siftarr/static/css/tailwind.css ./app/siftarr/static/css/tailwind.css
COPY --from=frontend-builder /build/app/siftarr/static/js/ ./app/siftarr/static/js/
COPY db/alembic/ ./alembic/
COPY db/alembic.ini ./

# Create data directories
RUN mkdir -p /data/db /data/staging && \
    chown -R appuser:appgroup /app /data

# Add LinuxServer-style entrypoint
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Run entrypoint as root so it can adjust ownership, then drop privileges
USER root

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD /app/.venv/bin/python -c "import httpx; httpx.get('http://localhost:8000/health')" || exit 1

# Run the application
ENTRYPOINT ["/entrypoint.sh"]
CMD ["/app/.venv/bin/python", "-m", "uvicorn", "app.siftarr.main:app", "--host", "0.0.0.0", "--port", "8000"]

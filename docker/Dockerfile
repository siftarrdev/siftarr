FROM python:3.12-slim

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

# Copy project files
COPY pyproject.toml uv.lock* ./

# Install dependencies
RUN SETUPTOOLS_SCM_PRETEND_VERSION_FOR_SIFTARR="${SIFTARR_VERSION#v}" uv sync --frozen

# Copy application code
COPY app/ ./app/
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

FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set working directory
WORKDIR /app

# Install dependencies
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-install-project

# Copy application code
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY ruff.toml ./
COPY ty.toml ./

# Create data directories
RUN mkdir -p /data/db /data/staging && \
    chown -R python:python /app /data

# Switch to non-root user
USER python

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health')" || exit 1

# Run the application
CMD ["uv", "run", "uvicorn", "arbitratarr.main:app", "--host", "0.0.0.0", "--port", "8000"]

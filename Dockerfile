FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set working directory
WORKDIR /app

# Create non-root user
RUN groupadd -g 568 appgroup && useradd -u 568 -g appgroup -m -s /bin/bash appuser

# Copy project files
COPY pyproject.toml uv.lock* ./

# Install dependencies
RUN uv sync --frozen

# Copy application code
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY ruff.toml ./
COPY ty.toml ./

# Create data directories
RUN mkdir -p /data/db /data/staging && \
    chown -R appuser:appgroup /app /data

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD /app/.venv/bin/python -c "import httpx; httpx.get('http://localhost:8000/health')" || exit 1

# Run the application
CMD ["/app/.venv/bin/python", "-m", "uvicorn", "app.arbitratarr.main:app", "--host", "0.0.0.0", "--port", "8000"]

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Arbitratarr",
        description="Media search and download decision middleware",
        version="0.1.0",
    )

    @app.get("/")
    async def root() -> JSONResponse:
        """Root endpoint returning service status."""
        return JSONResponse(content={"status": "ok"})

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Global exception handler for unhandled errors."""
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    return app


app = create_app()

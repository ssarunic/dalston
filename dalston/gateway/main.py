"""FastAPI Gateway application entry point."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from dalston.common.redis import close_redis
from dalston.common.s3 import ensure_bucket_exists
from dalston.config import get_settings
from dalston.db.session import engine, init_db
from dalston.gateway.api.v1 import router as v1_router
from dalston.gateway.api.console import router as console_router
from dalston.gateway.middleware import setup_exception_handlers
from dalston.session_router import SessionRouter

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Global session router instance (initialized in lifespan)
session_router: SessionRouter | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager.

    Startup:
    - Initialize database tables
    - Ensure default tenant exists
    - Ensure S3 bucket exists
    - Start Session Router (for real-time transcription)

    Shutdown:
    - Stop Session Router
    - Close Redis connections
    - Dispose database engine
    """
    global session_router

    logger.info("Starting Dalston Gateway...")

    # Initialize database
    logger.info("Initializing database...")
    await init_db()

    # Ensure S3 bucket exists
    logger.info("Ensuring S3 bucket exists...")
    try:
        await ensure_bucket_exists()
    except Exception as e:
        logger.warning("Could not ensure S3 bucket exists: %s", e)

    # Start Session Router for real-time transcription
    settings = get_settings()
    logger.info("Starting Session Router...")
    session_router = SessionRouter(redis_url=settings.redis_url)
    await session_router.start()

    logger.info("Dalston Gateway started successfully")

    yield

    # Shutdown
    logger.info("Shutting down Dalston Gateway...")

    # Stop Session Router
    if session_router:
        await session_router.stop()

    await close_redis()
    await engine.dispose()
    logger.info("Dalston Gateway shutdown complete")


# Create FastAPI application
app = FastAPI(
    title="Dalston",
    description="Modular, self-hosted audio transcription server",
    version="0.1.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup exception handlers
setup_exception_handlers(app)

# Mount API routes
app.include_router(v1_router)
app.include_router(console_router)


@app.get("/health", tags=["system"])
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/", tags=["system"])
async def root():
    """Root endpoint with API info."""
    return {
        "name": "Dalston",
        "version": "0.1.0",
        "docs": "/docs",
    }


# Web Console static file serving
# Look for web build in multiple locations (development vs Docker)
# __file__ = dalston/gateway/main.py -> .parent.parent.parent = project root
_console_paths = [
    Path(__file__).parent.parent.parent / "web" / "dist",  # Development: repo/web/dist
    Path("/app/web/dist"),  # Docker: /app/web/dist
]
_console_dir: Path | None = None
for p in _console_paths:
    if p.exists() and (p / "index.html").exists():
        _console_dir = p
        break

if _console_dir:
    logger.info("Serving web console from: %s", _console_dir)

    # Mount static assets (js, css, etc.) at /console/assets
    if (_console_dir / "assets").exists():
        app.mount(
            "/console/assets",
            StaticFiles(directory=_console_dir / "assets"),
            name="console-assets",
        )

    # Serve index.html for all /console routes (SPA fallback)
    @app.get("/console/{path:path}", include_in_schema=False)
    @app.get("/console", include_in_schema=False)
    async def serve_console(path: str = ""):
        """Serve the web console SPA."""
        # Try to serve static file first (for vite.svg, etc.)
        # Use resolve() and is_relative_to() to prevent path traversal attacks
        if path:
            static_file = (_console_dir / path).resolve()
            if static_file.is_relative_to(_console_dir) and static_file.is_file():
                return FileResponse(static_file)
        # Otherwise serve index.html for SPA routing
        return FileResponse(_console_dir / "index.html")
else:
    logger.warning("Web console not found. Run 'npm run build' in web/ directory.")

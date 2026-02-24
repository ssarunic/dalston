"""FastAPI Gateway application entry point."""

from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

import dalston.logging
import dalston.metrics
import dalston.telemetry
from dalston.common.redis import (
    LocalRedisProvider,
    get_redis,
    reset_provider,
    set_provider,
)
from dalston.common.s3 import ensure_bucket_exists
from dalston.config import get_settings
from dalston.db.session import DEFAULT_TENANT_ID, engine, init_db
from dalston.gateway.api.auth import router as auth_router
from dalston.gateway.api.console import router as console_router
from dalston.gateway.api.v1 import router as v1_router
from dalston.gateway.api.v2.router import router as v2_router
from dalston.gateway.middleware import setup_exception_handlers
from dalston.gateway.middleware.correlation import CorrelationIdMiddleware
from dalston.gateway.middleware.metrics import MetricsMiddleware
from dalston.gateway.services.auth import AuthService, Scope
from dalston.session_router import SessionRouter

# Configure structured logging
dalston.logging.configure("gateway")
logger = structlog.get_logger()

# Configure distributed tracing (M19)
dalston.telemetry.configure_tracing("dalston-gateway")

# Configure Prometheus metrics (M20)
dalston.metrics.configure_metrics("gateway")
# Gateway also hosts session router, so initialize those metrics too
dalston.metrics.init_session_router_metrics()

# Global session router instance (initialized in lifespan)
session_router: SessionRouter | None = None


async def _ensure_admin_key_exists() -> None:
    """Create an admin API key if none exist.

    This provides a seamless first-run experience by auto-creating
    an admin key and printing it to the console.
    """
    from dalston.db.session import async_session

    try:
        redis = await get_redis()
        async with async_session() as db:
            auth_service = AuthService(db, redis)

            # Check if any keys exist
            if await auth_service.has_any_api_keys():
                logger.info("API keys already exist, skipping auto-bootstrap")
                return

            # Create admin key
            raw_key, api_key = await auth_service.create_api_key(
                name="Auto-generated Admin Key",
                tenant_id=DEFAULT_TENANT_ID,
                scopes=[Scope.ADMIN],
                rate_limit=None,
            )

        # Print key prominently
        logger.info("")
        logger.info("=" * 70)
        logger.info("FIRST RUN: Admin API key auto-generated")
        logger.info("=" * 70)
        logger.info("")
        logger.info("API Key: %s", raw_key)
        logger.info("")
        logger.info("IMPORTANT: Store this key securely! It will not be shown again.")
        logger.info("")
        logger.info("Set as environment variable:")
        logger.info('  export DALSTON_API_KEY="%s"', raw_key)
        logger.info("")
        logger.info("Or use with curl:")
        logger.info('  curl -H "Authorization: Bearer %s" ...', raw_key)
        logger.info("")
        logger.info("=" * 70)

    except Exception as e:
        logger.warning("Could not auto-bootstrap admin key: %s", e)


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

    # Initialize Redis provider and store on app.state for DI
    settings = get_settings()
    redis_provider = LocalRedisProvider(settings)
    set_provider(redis_provider)
    app.state.redis_provider = redis_provider
    logger.info("Redis provider initialized")

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
    logger.info("Starting Session Router...")
    session_router = SessionRouter(redis_url=settings.redis_url)
    await session_router.start()

    # Auto-bootstrap admin key if no keys exist
    await _ensure_admin_key_exists()

    logger.info("Dalston Gateway started successfully")

    yield

    # Shutdown
    logger.info("Shutting down Dalston Gateway...")

    # Stop Session Router
    if session_router:
        await session_router.stop()

    # Close Redis provider
    await reset_provider()
    await engine.dispose()
    dalston.telemetry.shutdown_tracing()
    logger.info("Dalston Gateway shutdown complete")


# Create FastAPI application
app = FastAPI(
    title="Dalston",
    description="Modular, self-hosted audio transcription server",
    version="0.1.0",
    lifespan=lifespan,
)

# Add OpenTelemetry auto-instrumentation (M19)
if dalston.telemetry.is_tracing_enabled():
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except ImportError:
        logger.warning("opentelemetry-instrumentation-fastapi not installed, skipping")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add correlation ID middleware (generates request_id for every request)
app.add_middleware(CorrelationIdMiddleware)

# Add metrics middleware (M20) - records request counts and latencies
if dalston.metrics.is_metrics_enabled():
    app.add_middleware(MetricsMiddleware)

# Setup exception handlers
setup_exception_handlers(app)

# Mount API routes
app.include_router(v1_router)
app.include_router(v2_router)  # V2 API (artifact-centric retention)
app.include_router(auth_router)
app.include_router(console_router)


@app.get("/health", tags=["system"])
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/metrics", tags=["system"], include_in_schema=False)
async def metrics_endpoint():
    """Prometheus metrics endpoint."""
    if not dalston.metrics.is_metrics_enabled():
        return Response(content="Metrics disabled", status_code=404)

    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


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

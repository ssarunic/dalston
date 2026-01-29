"""FastAPI Gateway application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dalston.common.redis import close_redis
from dalston.common.s3 import ensure_bucket_exists
from dalston.config import get_settings
from dalston.db.session import engine, init_db
from dalston.gateway.api.v1 import router as v1_router
from dalston.gateway.middleware import setup_exception_handlers

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager.

    Startup:
    - Initialize database tables
    - Ensure default tenant exists
    - Ensure S3 bucket exists

    Shutdown:
    - Close Redis connections
    - Dispose database engine
    """
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

    logger.info("Dalston Gateway started successfully")

    yield

    # Shutdown
    logger.info("Shutting down Dalston Gateway...")
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

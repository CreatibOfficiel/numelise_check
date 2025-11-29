"""
Server entry point for ConsentCrawl API.

This module serves as the uvicorn entry point for Railway deployment.
It imports the FastAPI app and configures logging.

Usage:
    # Development (with auto-reload)
    uvicorn consentcrawl.server:app --reload

    # Production (Railway)
    uvicorn consentcrawl.server:app --host 0.0.0.0 --port $PORT

    # Direct execution
    python -m consentcrawl.server
"""

import os
import logging

# Import FastAPI app from api module
from consentcrawl.api import app

# Configure logging for production
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

# Export app for uvicorn
__all__ = ['app']


if __name__ == "__main__":
    """
    Direct execution entry point.

    Useful for local development and testing.
    In production, Railway will use: uvicorn consentcrawl.server:app
    """
    import uvicorn

    # Get port from environment (Railway sets $PORT)
    port = int(os.getenv("PORT", 8000))

    logging.info(f"Starting ConsentCrawl API server on port {port}...")

    uvicorn.run(
        "consentcrawl.server:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
        access_log=True
    )

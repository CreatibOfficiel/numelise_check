"""
FastAPI REST API for ConsentCrawl.

Exposes cookie consent audit functionality via HTTP endpoints for N8N integration.
Designed for Railway deployment with browser lifecycle management.
"""

import logging
import asyncio
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl, Field
from playwright.async_api import async_playwright

from consentcrawl.audit_crawl import audit_url
from consentcrawl.audit_schemas import AuditConfig

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Create FastAPI app
app = FastAPI(
    title="ConsentCrawl API",
    description="Cookie consent banner audit API for N8N integration",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Add CORS middleware for N8N integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify N8N domain
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Global browser instance (initialized on startup)
browser = None
playwright_instance = None


# Pydantic Models
class AuditRequest(BaseModel):
    """Request model for POST /audit endpoint."""
    url: HttpUrl = Field(..., description="URL to audit for cookie consent compliance")
    config: Optional[Dict[str, Any]] = Field(
        default={},
        description="Optional AuditConfig overrides (timeout_banner, timeout_modal, max_ui_depth, etc.)"
    )
    screenshot: Optional[bool] = Field(
        default=False,
        description="Whether to capture screenshots during audit"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "url": "https://www.example.com",
                "config": {
                    "timeout_banner": 15000,
                    "timeout_modal": 20000,
                    "max_ui_depth": 3
                },
                "screenshot": False
            }
        }


class AuditResponse(BaseModel):
    """Response model for POST /audit endpoint."""
    id: str
    url: str
    domain_name: str
    extraction_datetime: str
    banner_info: Optional[dict] = None
    categories: list
    vendors: list
    cookies: list
    actual_cookies: list
    tracking_domains: list
    third_party_domains: list
    ui_context: Optional[dict] = None
    screenshot_files: list
    status: str
    status_msg: str

    class Config:
        json_schema_extra = {
            "example": {
                "id": "ZXhhbXBsZS5jb20=",
                "url": "https://www.example.com",
                "domain_name": "example.com",
                "extraction_datetime": "2025-11-29T17:00:00.000000",
                "status": "success_detailed",
                "status_msg": "Full detailed extraction completed",
                "banner_info": {
                    "detected": True,
                    "cmp_type": "onetrust",
                    "cmp_brand": "OneTrust"
                },
                "categories": [],
                "vendors": [],
                "cookies": []
            }
        }


class HealthResponse(BaseModel):
    """Response model for GET /health endpoint."""
    status: str
    browser: str
    message: str


# Lifecycle Events
@app.on_event("startup")
async def startup_event():
    """
    Initialize browser on application startup.

    Railway will call this when the container starts.
    Uses headless mode with no-sandbox for containerized environments.
    """
    global browser, playwright_instance

    try:
        logging.info("Starting Playwright browser...")
        playwright_instance = await async_playwright().start()
        browser = await playwright_instance.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',  # Overcome limited resource problems
                '--disable-blink-features=AutomationControlled'
            ]
        )
        logging.info("✓ Browser launched successfully")
    except Exception as e:
        logging.error(f"Failed to launch browser: {e}")
        raise


@app.on_event("shutdown")
async def shutdown_event():
    """
    Cleanup browser on application shutdown.

    Ensures proper resource cleanup when Railway stops the container.
    """
    global browser, playwright_instance

    try:
        if browser:
            await browser.close()
            logging.info("Browser closed")

        if playwright_instance:
            await playwright_instance.stop()
            logging.info("Playwright stopped")
    except Exception as e:
        logging.error(f"Error during shutdown: {e}")


# API Endpoints
@app.get("/", response_model=Dict[str, Any])
async def root():
    """
    Root endpoint with API information.

    Returns:
        API metadata and available endpoints
    """
    return {
        "name": "ConsentCrawl API",
        "version": "1.0.0",
        "description": "Cookie consent banner audit API for N8N integration",
        "endpoints": {
            "POST /audit": "Audit a URL for cookie consent compliance",
            "GET /health": "Health check for Railway monitoring",
            "GET /docs": "Interactive API documentation (Swagger UI)",
            "GET /redoc": "Alternative API documentation (ReDoc)"
        },
        "documentation": "https://github.com/dumkydewilde/consentcrawl"
    }


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint for Railway monitoring.

    Railway uses this to verify the service is running correctly.

    Returns:
        Health status including browser state
    """
    browser_status = "ready" if browser else "not_ready"
    is_healthy = browser is not None

    return {
        "status": "healthy" if is_healthy else "degraded",
        "browser": browser_status,
        "message": "Service is operational" if is_healthy else "Browser not initialized"
    }


@app.post("/audit", response_model=AuditResponse)
async def audit_endpoint(request: AuditRequest):
    """
    Audit a single URL for cookie consent compliance.

    This endpoint synchronously waits for the audit to complete before returning.
    Typical execution time: 30-90 seconds depending on site complexity.

    Args:
        request: AuditRequest with URL and optional config

    Returns:
        Complete AuditResult with banner info, categories, vendors, and cookies

    Raises:
        HTTPException 503: Browser not initialized
        HTTPException 504: Audit timeout (>120s)
        HTTPException 500: Internal server error
    """
    # Check browser availability
    if not browser:
        logging.error("Audit requested but browser not initialized")
        raise HTTPException(
            status_code=503,
            detail="Browser not initialized. Service may be starting up."
        )

    try:
        # Parse config overrides
        config = AuditConfig(**request.config) if request.config else AuditConfig()

        logging.info(f"Starting audit for: {request.url}")

        # Run audit with 120s timeout
        try:
            result = await asyncio.wait_for(
                audit_url(
                    url=str(request.url),
                    browser=browser,
                    config=config,
                    screenshot=request.screenshot
                ),
                timeout=120  # 2 minutes max
            )
        except asyncio.TimeoutError:
            logging.error(f"Audit timeout for {request.url}")
            raise HTTPException(
                status_code=504,
                detail="Audit timeout after 120 seconds. Site may be too complex or slow."
            )

        # Convert AuditResult to dict
        result_dict = result.to_dict()

        logging.info(f"Audit completed for {request.url}: {result.status}")

        return result_dict

    except HTTPException:
        # Re-raise HTTP exceptions
        raise

    except Exception as e:
        # Log and return 500 for unexpected errors
        logging.error(f"Audit failed for {request.url}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


# Exception Handlers
@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    """Custom 404 handler."""
    return {
        "error": "Not found",
        "message": f"The endpoint {request.url.path} does not exist",
        "available_endpoints": ["/", "/health", "/audit", "/docs"]
    }


if __name__ == "__main__":
    import uvicorn
    import os
    
    port = int(os.getenv("PORT", 8000))
    logging.info(f"Starting ConsentCrawl API server on port {port}...")
    
    uvicorn.run(
        "consentcrawl.api:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
        access_log=True
    )

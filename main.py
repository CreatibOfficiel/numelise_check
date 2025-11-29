"""
Main entry point for Railway deployment with Railpack.

Railpack automatically detects FastAPI apps in main.py or app.py
and starts them with uvicorn.
"""

from consentcrawl.api import app

# Railpack will automatically run: uvicorn main:app

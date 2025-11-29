"""
Centralized logging configuration for ConsentCrawl.

This module provides a single point of configuration for all logging
across the application, ensuring consistency and easy customization.
"""

import logging
import sys


def setup_logging(level=logging.INFO, include_timestamps=True, quiet_libraries=True):
    """
    Configure logging for the entire application.
    
    Args:
        level: Logging level (default: INFO)
        include_timestamps: Whether to include timestamps in log format
        quiet_libraries: Whether to silence noisy third-party libraries
    """
    # Determine format string
    if include_timestamps:
        format_str = '%(asctime)s - %(levelname)s - %(message)s'
    else:
        format_str = '%(levelname)s - %(message)s'
    
    # Configure root logger
    logging.basicConfig(
        level=level,
        format=format_str,
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True
    )
    
    # Silence noisy third-party libraries
    if quiet_libraries:
        logging.getLogger('playwright').setLevel(logging.WARNING)
        logging.getLogger('urllib3').setLevel(logging.WARNING)
        logging.getLogger('httpx').setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for a specific module.
    
    Args:
        name: Module name (typically __name__)
        
    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)

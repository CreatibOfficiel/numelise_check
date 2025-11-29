"""
Constants and configuration values for ConsentCrawl.

This module centralizes all magic numbers and configuration values
to improve maintainability and make it easier to tune performance.
"""

# ============================================================================
# Page Load Timeouts (milliseconds)
# ============================================================================

# Maximum time to wait for page.goto() to complete
PAGE_LOAD_TIMEOUT = 15_000

# Wait time after page load for JavaScript execution
PAGE_READY_WAIT = 1_000

# ============================================================================
# UI Interaction Timeouts (milliseconds)
# ============================================================================

# Wait time for CSS animations to complete
ANIMATION_WAIT = 500

# Timeout for checking element visibility (aggressive optimization)
ELEMENT_VISIBILITY_CHECK = 100

# Wait time between modal detection retry attempts
MODAL_DETECTION_RETRY_WAIT = 500

# Timeout for clicking elements
CLICK_TIMEOUT = 5_000

# ============================================================================
# Resource Blocking
# ============================================================================

# Resource types to block for faster page loads
BLOCKED_RESOURCE_TYPES = ["image", "media", "font"]

# ============================================================================
# User Agents
# ============================================================================

# Default user agent strings for browser contexts
DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36 Edg/116.0.1938.81"
]

# ============================================================================
# Database Configuration
# ============================================================================

# Default database file name
DEFAULT_DB_FILE = "crawl_results.db"

# Default audit database file name
DEFAULT_AUDIT_DB_FILE = "audit_results.db"

# ============================================================================
# Retry Configuration
# ============================================================================

# Maximum number of retries for modal detection
MAX_MODAL_DETECTION_RETRIES = 1  # Optimized for speed

# Maximum number of click retries
MAX_CLICK_RETRIES = 3

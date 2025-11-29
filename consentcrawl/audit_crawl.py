"""
Audit crawl orchestration module.

This module orchestrates the complete audit pipeline:
1. Navigate to URL
2. Detect cookie banner
3. Extract banner information
4. Explore consent UI
5. Store results in SQLite and JSON files

Mirrors the structure of crawl.py but for audit mode.
"""

import os
import json
import logging
import datetime
import base64
import re
import sqlite3
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

from consentcrawl import utils
from consentcrawl.audit_schemas import AuditResult, AuditConfig, ConsentUIContext
from consentcrawl.banner_detector import detect_banner
from consentcrawl.ui_explorer import explore_consent_ui
from consentcrawl.crawl import get_consent_managers


DEFAULT_UA_STRINGS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36 Edg/116.0.1938.81"
]


async def audit_url(url: str, browser, config: AuditConfig, screenshot: bool = False) -> AuditResult:
    """
    Complete audit pipeline for a single URL.

    Steps:
    1. Create browser context
    2. Navigate to URL
    3. Wait for page load + banner timeout
    4. Detect banner
    5. Extract banner info
    6. If settings button found, explore UI
    7. Take screenshots if requested
    8. Build AuditResult
    9. Return result (never crash, always return)

    Args:
        url: URL to audit
        browser: Playwright Browser instance
        config: Audit configuration
        screenshot: Whether to capture screenshots

    Returns:
        AuditResult object with all extracted data
    """
    # Create audit result with default values
    domain_name = re.search(r"^(?:https?://)?(?:www\.)?([^/]+)", url).group(1) if url else "unknown"
    result_id = base64.b64encode(domain_name.encode()).decode("utf-8")

    audit_result = AuditResult(
        id=result_id,
        url=url,
        domain_name=domain_name,
        extraction_datetime=datetime.datetime.now().isoformat(),
        status="pending",
    )

    try:
        # Create browser context with random user agent
        context = await browser.new_context(
            user_agent=DEFAULT_UA_STRINGS[0],
            viewport={"width": 1366, "height": 768},
        )

        # Bypass webdriver detection
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        page = await context.new_page()

        # Navigate to URL
        logging.info(f"Auditing: {url}")
        try:
            await page.goto(url, wait_until="load", timeout=90000)
        except PlaywrightTimeoutError:
            logging.warning(f"Page load timeout for {url}")
            audit_result.status = "error"
            audit_result.status_msg = "Page load timeout"
            await context.close()
            return audit_result

        # Simulate mouse movement and scroll (helps trigger lazy-loaded CMPs)
        try:
            await page.mouse.move(100, 100)
            await page.mouse.move(200, 200)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 4)")
        except:
            pass

        # Wait for banner to appear
        await page.wait_for_timeout(config.timeout_banner)

        # Get CMP configurations
        cmp_configs = get_consent_managers()

        # Detect banner
        logging.debug("Detecting banner...")
        banner_info = await detect_banner(page, cmp_configs, config)

        if not banner_info or not banner_info.detected:
            logging.info(f"No banner detected on {url}")
            audit_result.banner_info = banner_info
            audit_result.status = "failed"
            audit_result.status_msg = "No banner detected"
            await context.close()
            return audit_result

        logging.info(f"Banner detected: {banner_info.cmp_type or 'unknown CMP'}")
        audit_result.banner_info = banner_info

        # Take screenshot if requested
        if screenshot:
            screenshot_dir = "screenshots"
            if not os.path.exists(screenshot_dir):
                os.makedirs(screenshot_dir)

            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_path = f"{screenshot_dir}/{domain_name}_{timestamp}_banner.png"
            await page.screenshot(path=screenshot_path, full_page=False)
            audit_result.screenshot_files.append(screenshot_path)
            logging.debug(f"Screenshot saved: {screenshot_path}")

        # HYBRID APPROACH: Passive extraction is guaranteed success
        # Banner detected + buttons extracted = success_basic (guaranteed)
        audit_result.status = "success_basic"
        audit_result.status_msg = "Banner extracted successfully"
        audit_result.ui_context = ConsentUIContext()

        # Attempt detailed extraction via UI navigation (best effort)
        settings_button = any(btn.role == "settings" for btn in banner_info.buttons)

        if settings_button or banner_info.cmp_type:
            logging.debug("Attempting detailed extraction via UI navigation...")
            try:
                from consentcrawl.ui_explorer import attempt_detailed_extraction

                detailed_data = await attempt_detailed_extraction(page, banner_info, config)

                if detailed_data:
                    # Detailed extraction succeeded
                    audit_result.categories = detailed_data.get("categories", [])
                    audit_result.vendors = detailed_data.get("vendors", [])
                    audit_result.cookies = detailed_data.get("cookies", [])

                    # Upgrade to success_detailed if we got meaningful data
                    if len(audit_result.categories) > 0 or len(audit_result.vendors) > 0:
                        audit_result.status = "success_detailed"
                        audit_result.status_msg = "Full detailed extraction completed"

                    logging.info(f"Detailed extraction succeeded: {len(audit_result.categories)} categories, "
                               f"{len(audit_result.vendors)} vendors, {len(audit_result.cookies)} cookies")

                    # Take screenshot after UI exploration
                    if screenshot:
                        screenshot_path = f"{screenshot_dir}/{domain_name}_{timestamp}_ui.png"
                        await page.screenshot(path=screenshot_path, full_page=False)
                        audit_result.screenshot_files.append(screenshot_path)
                else:
                    # Detailed extraction failed - keep success_basic
                    logging.debug("Detailed extraction returned None (acceptable in hybrid mode)")
                    audit_result.ui_context.errors.append("Detailed extraction skipped: Modal navigation failed")

            except Exception as e:
                # Exception during detailed extraction - keep success_basic
                logging.debug(f"Detailed extraction failed (acceptable in hybrid mode): {e}")
                audit_result.ui_context.errors.append(f"Detailed extraction failed: {str(e)[:100]}")
                # Status remains success_basic
        else:
            logging.debug("No settings button found, skipping detailed extraction")

        # Close context
        await context.close()

    except Exception as e:
        logging.error(f"Error auditing {url}: {e}")
        audit_result.status = "error"
        audit_result.status_msg = f"Error: {str(e)}"

    return audit_result


async def audit_batch(urls: list, batch_size: int = 10, config: AuditConfig = None,
                      headless: bool = True, results_db_file: str = "audit_results.db",
                      output_dir: str = "./audit_results", screenshot: bool = False) -> list:
    """
    Audit a batch of URLs in parallel.

    Uses the same batching pattern as crawl_batch().

    Args:
        urls: List of URLs to audit
        batch_size: Number of parallel browser windows
        config: Audit configuration
        headless: Run browser in headless mode
        results_db_file: SQLite database file path
        output_dir: Directory for JSON output files
        screenshot: Whether to capture screenshots

    Returns:
        List of audit result dictionaries
    """
    if config is None:
        config = AuditConfig()

    # Create output directory if it doesn't exist
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    all_results = []

    for url_batch in utils.batch(urls, batch_size):
        logging.info(f"Processing batch of {len(url_batch)} URLs")

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless
            )

            # Process URLs in parallel within the batch
            batch_results = await asyncio.gather(*[
                audit_url(url, browser, config, screenshot)
                for url in url_batch
            ])

            await browser.close()

            # Store results
            for result in batch_results:
                # Store in SQLite
                store_audit_results(result, results_db_file=results_db_file)

                # Store in JSON file
                store_json_result(result, output_dir)

                # Add to results list
                all_results.append(result.to_dict())

        logging.info(f"Batch complete. {len(batch_results)} URLs processed.")

    logging.info(f"All batches complete. Total: {len(all_results)} URLs audited.")
    return all_results


def store_audit_results(audit_result: AuditResult, results_db_file: str = "audit_results.db"):
    """
    Store audit result in SQLite database.

    Creates table if it doesn't exist. JSON-serializes complex fields.

    Args:
        audit_result: AuditResult object to store
        results_db_file: SQLite database file path
    """
    try:
        conn = sqlite3.connect(results_db_file)
        c = conn.cursor()

        # Create table if not exists
        from consentcrawl.audit_schemas import get_audit_schema

        schema = get_audit_schema()
        schema_sql = ", ".join([f"{k} {v}" for k, v in schema.items()])
        c.execute(f"CREATE TABLE IF NOT EXISTS audit_results ({schema_sql})")

        # Prepare data for insertion
        data = audit_result.to_dict()

        # JSON serialize complex types
        data_serialized = {}
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                data_serialized[k] = json.dumps(v)
            else:
                data_serialized[k] = v

        # Insert or replace
        columns = ", ".join(data_serialized.keys())
        placeholders = ", ".join(["?" for _ in data_serialized])
        sql = f"INSERT OR REPLACE INTO audit_results ({columns}) VALUES ({placeholders})"

        c.execute(sql, list(data_serialized.values()))
        conn.commit()
        conn.close()

        logging.debug(f"Stored audit result in database: {audit_result.url}")

    except Exception as e:
        logging.error(f"Error storing audit result in database: {e}")


def store_json_result(audit_result: AuditResult, output_dir: str):
    """
    Store audit result as individual JSON file.

    Args:
        audit_result: AuditResult object to store
        output_dir: Directory for JSON files
    """
    try:
        # Create safe filename from domain
        safe_domain = re.sub(r'[^a-zA-Z0-9_-]', '_', audit_result.domain_name)
        filename = f"{safe_domain}.json"
        filepath = os.path.join(output_dir, filename)

        # Write JSON file
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(audit_result.to_dict(), f, indent=2, ensure_ascii=False)

        logging.debug(f"Saved JSON result: {filepath}")

    except Exception as e:
        logging.error(f"Error saving JSON result: {e}")


def create_audit_result(url: str, status: str = "pending") -> AuditResult:
    """
    Factory function to create AuditResult with default values.

    Args:
        url: URL being audited
        status: Initial status

    Returns:
        AuditResult object
    """
    domain_name = re.search(r"^(?:https?://)?(?:www\.)?([^/]+)", url).group(1) if url else "unknown"
    result_id = base64.b64encode(domain_name.encode()).decode("utf-8")

    return AuditResult(
        id=result_id,
        url=url,
        domain_name=domain_name,
        extraction_datetime=datetime.datetime.now().isoformat(),
        status=status,
    )

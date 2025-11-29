"""
UI Explorer module for CMP audit mode.

This module handles deep exploration of consent UI preferences, extracting:
- Consent categories with descriptions and default states
- Vendor/partner lists with purposes and legal basis
- Individual cookie details from CMP interfaces

Supports 35+ CMP types with generic fallbacks.
"""

import logging
import os
import yaml
from typing import List, Optional, Dict, Any
from playwright.async_api import Page, Locator, TimeoutError as PlaywrightTimeoutError

from consentcrawl.audit_schemas import (
    BannerInfo, ButtonInfo, CategoryInfo, VendorInfo,
    CookieDetail, ConsentUIContext, AuditConfig,
    DiscoveredSection, SectionDiscoveryResult
)


MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIT_SELECTORS_FILE = f"{MODULE_DIR}/assets/audit_selectors.yml"


def load_audit_selectors():
    """Load audit selector patterns from YAML file."""
    with open(AUDIT_SELECTORS_FILE, "r") as f:
        return yaml.safe_load(f)


async def explore_consent_ui(page: Page, banner_info: BannerInfo, config: AuditConfig) -> Dict[str, Any]:
    """
    Complete UI exploration pipeline.

    Steps:
    1. Find and click settings/manage button
    2. Wait for modal to open
    3. Extract categories
    4. Extract vendors (if available)
    5. Extract cookies (if available)
    6. Return all extracted data

    Args:
        page: Playwright Page object
        banner_info: Detected banner information
        config: Audit configuration

    Returns:
        Dictionary with categories, vendors, cookies lists and context
    """
    context = ConsentUIContext()
    categories = []
    vendors = []
    cookies = []

    try:
        # Step 1: Find and click settings button
        modal_locator = await open_settings_modal(page, banner_info, config)
        if not modal_locator:
            logging.info("Settings modal could not be opened")
            context.errors.append("Settings modal not opened")
            return {
                "categories": categories,
                "vendors": vendors,
                "cookies": cookies,
                "context": context
            }

        # Step 2: Extract categories
        logging.debug("Extracting categories...")
        categories = await extract_categories(page, modal_locator, banner_info.cmp_type, config)
        logging.info(f"Extracted {len(categories)} categories")

        # Step 3: Extract vendors
        logging.debug("Extracting vendors...")
        vendors = await extract_vendors(page, modal_locator, banner_info.cmp_type, config, context)
        logging.info(f"Extracted {len(vendors)} vendors")

        # Step 4: Extract cookies
        logging.debug("Extracting cookies...")
        cookies = await extract_cookies(page, modal_locator, banner_info.cmp_type, config, context)
        logging.info(f"Extracted {len(cookies)} cookie details")

    except Exception as e:
        logging.error(f"Error during UI exploration: {e}")
        context.errors.append(f"UI exploration error: {str(e)}")

    return {
        "categories": categories,
        "vendors": vendors,
        "cookies": cookies,
        "context": context
    }


async def open_settings_modal(page: Page, banner_info: BannerInfo, config: AuditConfig) -> Optional[Locator]:
    """
    Ouvre la modal de settings avec stratégies de fallback multiples.

    Args:
        page: Playwright Page object
        banner_info: Banner information with buttons
        config: Audit configuration

    Returns:
        Locator for the opened modal, or None if failed
    """
    # 1. Trouver le bouton settings
    settings_button = None
    for button in banner_info.buttons:
        if button.role == "settings":
            settings_button = button
            break

    if not settings_button:
        logging.warning("No settings button found in banner")
        return None

    # 2. Tentatives de clic avec différentes stratégies
    click_strategies = [
        {
            'name': 'extracted_selector',
            'locator': lambda: page.locator(settings_button.selector).first,
            'enabled': bool(settings_button.selector)
        },
        {
            'name': 'aria_label',
            'locator': lambda: page.locator(f"[aria-label='{settings_button.aria_label}']").first,
            'enabled': bool(settings_button.aria_label)
        },
        {
            'name': 'button_text',
            'locator': lambda: page.locator(f"button:has-text('{settings_button.text}')").first,
            'enabled': bool(settings_button.text)
        },
        {
            'name': 'role_button_text',
            'locator': lambda: page.locator(f"[role='button']:has-text('{settings_button.text}')").first,
            'enabled': bool(settings_button.text)
        },
        {
            'name': 'any_clickable_text',
            'locator': lambda: page.locator(f":has-text('{settings_button.text}')").first,
            'enabled': bool(settings_button.text)
        }
    ]

    clicked = False
    click_errors = []

    for strategy in click_strategies:
        if not strategy['enabled']:
            continue

        try:
            logging.debug(f"Trying click strategy: {strategy['name']}")
            button_locator = strategy['locator']()

            # Clic avec retry - essayer d'abord sans attendre la visibilité
            for attempt in range(3):
                try:
                    # Tentative de clic direct (force=True pour ignorer les blocages)
                    await button_locator.click(timeout=config.timeout_click, force=True)
                    clicked = True
                    logging.info(f"✓ Settings button clicked using strategy: {strategy['name']}")
                    break
                except Exception as e:
                    if attempt < 2:
                        logging.debug(f"Click attempt {attempt+1} failed, retrying...")
                        await page.wait_for_timeout(1000)
                    else:
                        raise

            if clicked:
                break

        except Exception as e:
            error_msg = f"{strategy['name']}: {str(e)[:100]}"
            click_errors.append(error_msg)
            logging.debug(f"✗ Strategy failed: {error_msg}")
            continue

    if not clicked:
        logging.warning(f"Could not click settings button after {len([s for s in click_strategies if s['enabled']])} strategies")
        for error in click_errors:
            logging.debug(f"  - {error}")
        return None

    # 3. Attendre animation UI
    await page.wait_for_timeout(500)

    # 4. Détecter la modal avec retry (optimized to 2 for balance)
    modal_locator = await detect_modal_with_retry(page, banner_info.cmp_type, config, max_retries=2)

    if not modal_locator:
        logging.warning("Settings modal not detected after click")

    return modal_locator


async def detect_modal_with_retry(page: Page, cmp_type: Optional[str], config: AuditConfig, max_retries=2) -> Optional[Locator]:
    """
    Détecte la modal de settings avec retry et stratégies multiples.
    
    Enhanced with:
    - CMP-specific detection (Sourcepoint, OneTrust, Didomi)
    - Multi-frame search (all iframes)
    - Fallback to generic detection
    
    Args:
        page: Playwright Page object
        cmp_type: CMP type if known
        config: Audit configuration
        max_retries: Maximum retry attempts
        
    Returns:
        Locator for the modal, or None
    """
    from consentcrawl.cmp_detectors import (
        detect_sourcepoint_modal,
        detect_onetrust_modal,
        detect_didomi_modal,
        detect_modal_in_all_frames
    )
    
    for attempt in range(max_retries):
        try:
            logging.debug(f"[ModalDetection] Attempt {attempt+1}/{max_retries}")
            
            # Strategy 1: CMP-specific detection (highest precision)
            if cmp_type:
                normalized_cmp = cmp_type.replace("-cmp", "").replace("_", "-").lower()
                
                if "sourcepoint" in normalized_cmp:
                    logging.debug("Trying Sourcepoint-specific detection")
                    modal = await detect_sourcepoint_modal(page)
                    if modal:
                        logging.info("✓ Modal found via Sourcepoint detector")
                        return modal
                
                elif "onetrust" in normalized_cmp:
                    logging.debug("Trying OneTrust-specific detection")
                    modal = await detect_onetrust_modal(page)
                    if modal:
                        logging.info("✓ Modal found via OneTrust detector")
                        return modal
                
                elif "didomi" in normalized_cmp:
                    logging.debug("Trying Didomi-specific detection")
                    modal = await detect_didomi_modal(page)
                    if modal:
                        logging.info("✓ Modal found via Didomi detector")
                        return modal
            
            # Strategy 2: Generic detection (original logic)
            logging.debug("Trying generic detection")
            modal = await detect_modal(page, cmp_type, config)
            if modal:
                logging.info("✓ Modal found via generic detector")
                return modal
            
            # Strategy 3: Search all frames (fallback)
            if len(page.frames) > 1:
                logging.debug(f"Trying all-frames search ({len(page.frames)} frames)")
                modal = await detect_modal_in_all_frames(page, cmp_type, detect_modal)
                if modal:
                    logging.info("✓ Modal found via all-frames search")
                    return modal
            
            logging.warning(f"[ModalDetection] ✗ No modal found on attempt {attempt+1}/{max_retries}")
            
        except Exception as e:
            logging.debug(f"Modal detection attempt {attempt+1} failed: {e}")

        if attempt < max_retries - 1:
            await page.wait_for_timeout(500)  # Wait for CSS animations

    logging.warning(f"[ModalDetection] ✗✗ Modal detection failed after {max_retries} attempts")
    return None


async def detect_modal(page: Page, cmp_type: Optional[str], config: AuditConfig) -> Optional[Locator]:
    """
    Détecte la modal de settings ouverte.

    Args:
        page: Playwright Page object
        cmp_type: CMP type if known
        config: Audit configuration

    Returns:
        Locator for the modal, or None
    """
    audit_selectors = load_audit_selectors()
    modals_config = audit_selectors.get("modals", {})

    # Normaliser le type CMP (enlever suffixes, lowercase)
    normalized_cmp_type = None
    if cmp_type:
        normalized_cmp_type = cmp_type.replace("-cmp", "").replace("_", "-").lower()

    # Check if this CMP uses iframes
    iframe_contexts = [page]  # Always check main page
    if normalized_cmp_type in ["sourcepoint", "trustarc", "onetrust"]:
        # These CMPs use iframes, add iframe context
        iframe_selectors = {
            "sourcepoint": "[id*='sp_message_iframe']",
            "trustarc": "#truste_cm_frame",
            "onetrust": "#onetrust-consent-sdk iframe",
        }
        iframe_selector = iframe_selectors.get(normalized_cmp_type)
        if iframe_selector:
            try:
                iframe_count = await page.locator(iframe_selector).count()
                if iframe_count > 0:
                    logging.debug(f"Adding iframe context for {normalized_cmp_type}: {iframe_selector}")
                    iframe_contexts.insert(0, page.frame_locator(iframe_selector).first)  # Check iframe first
            except:
                pass

    # Stratégie 1 : CMP-specific patterns
    if normalized_cmp_type:
        cmp_selectors = modals_config.get(normalized_cmp_type, [])

        if cmp_selectors:
            logging.debug(f"Trying {len(cmp_selectors)} CMP-specific modal selectors for '{normalized_cmp_type}'")
            for page_context in iframe_contexts:
                context_name = "iframe" if page_context != page else "main page"
                for selector in cmp_selectors:
                    try:
                        locator = page_context.locator(selector).first
                        # Aggressive timeout optimization: 100ms per selector
                        # We rely on the retry loop in detect_modal_with_retry for waiting
                        if await locator.is_visible(timeout=100):
                            logging.info(f"✓ Modal detected with CMP selector in {context_name}: {selector}")
                            return locator
                    except:
                        continue
        else:
            logging.debug(f"No CMP-specific selectors found for '{normalized_cmp_type}'")

    # Stratégie 2 : Generic patterns
    generic_selectors = modals_config.get("general", [])
    logging.debug(f"Trying {len(generic_selectors)} generic modal selectors")

    for page_context in iframe_contexts:
        context_name = "iframe" if page_context != page else "main page"
        for selector in generic_selectors:
            try:
                locator = page_context.locator(selector).first
                if await locator.is_visible(timeout=100):
                    # Vérifier que c'est bien une modal (mots-clés)
                    text = await locator.inner_text(timeout=1000)
                    keywords = ["cookie", "consent", "preference", "vendor", "purpose", "category"]
                    if any(kw in text.lower() for kw in keywords):
                        logging.info(f"✓ Modal detected with generic selector in {context_name}: {selector}")
                        return locator
            except:
                continue

    logging.warning("No modal detected with any selector")
    return None


async def attempt_detailed_extraction(page: Page, banner_info: BannerInfo, config: AuditConfig) -> Optional[dict]:
    """
    Tentative best-effort d'extraction détaillée via navigation intelligente.

    Utilise NavigationStateMachine pour navigation modale robuste avec :
    - Timeouts adaptatifs selon CMP
    - Multi-stratégies de clic avec retry
    - Validation de readiness (pas juste visibility)
    - Rapport détaillé des tentatives

    Args:
        page: Playwright Page object
        banner_info: Information sur le banner détecté
        config: Configuration d'audit

    Returns:
        Dict avec 'categories', 'vendors', 'cookies' ou None si échec
    """
    # Import NavigationStateMachine
    from consentcrawl.navigation_state_machine import NavigationStateMachine

    # 1. Utiliser la state machine pour navigation intelligente
    state_machine = NavigationStateMachine(page, banner_info, config)
    modal, navigation_report = await state_machine.execute_navigation_flow()

    if not modal:
        logging.debug(
            f"Modal navigation failed after {len(navigation_report.attempts)} attempts: "
            f"{', '.join(navigation_report.errors)}"
        )
        return None

    logging.info(
        f"Modal navigation succeeded in {navigation_report.total_duration_ms}ms "
        f"using {len(navigation_report.attempts)} attempts"
    )

    # 2. Discover sections intelligently (Phase 2)
    from consentcrawl.section_discovery import SectionDiscoverer
    discoverer = SectionDiscoverer(page, modal, banner_info.cmp_type, config)
    discovery_result = await discoverer.discover_all_sections()

    logging.info(
        f"Section discovery: {len(discovery_result.sections)} sections found, "
        f"Categories={'✓' if discovery_result.categories_section else '✗'}, "
        f"Vendors={'✓' if discovery_result.vendors_section else '✗'}, "
        f"Cookies={'✓' if discovery_result.cookies_section else '✗'}"
    )

    if not discovery_result.sections:
        logging.warning("No sections discovered, extraction may return limited results")
        # Don't fail completely - try old extraction method as fallback

    # 3. Extraction détaillée depuis modal
    try:
        # Create UI context for tracking extraction state
        from consentcrawl.audit_schemas import ConsentUIContext
        context = ConsentUIContext()

        # Use discovery-based extraction if sections were found
        categories = await extract_categories_from_discovery(
            page, modal, discovery_result.categories_section, banner_info.cmp_type, config
        )
        vendors = await extract_vendors_from_discovery(
            page, modal, discovery_result.vendors_section, banner_info.cmp_type, config, context
        )
        cookies = await extract_cookies_from_discovery(
            page, modal, discovery_result.cookies_section, banner_info.cmp_type, config, context
        )

        logging.info(f"Detailed extraction succeeded: {len(categories)} categories, {len(vendors)} vendors, {len(cookies)} cookies")

        return {
            'categories': categories,
            'vendors': vendors,
            'cookies': cookies,
            'ui_context': context,
            'section_discovery': discovery_result
        }

    except Exception as e:
        logging.debug(f"Detailed data extraction failed: {e}")
        return None


async def extract_categories(page: Page, modal_locator: Locator, cmp_type: Optional[str], config: AuditConfig) -> List[CategoryInfo]:
    """
    Extract consent categories from the modal.

    Tries CMP-specific patterns first, then falls back to generic.

    Args:
        page: Playwright Page object
        modal_locator: Locator for the settings modal
        cmp_type: CMP type if known
        config: Audit configuration

    Returns:
        List of CategoryInfo objects
    """
    audit_selectors = load_audit_selectors()
    categories_config = audit_selectors.get("categories", {})

    categories = []

    # Try CMP-specific extraction
    if cmp_type:
        patterns = categories_config.get(cmp_type)
        if patterns:
            categories = await extract_categories_with_pattern(modal_locator, patterns, config)
            if categories:
                logging.debug(f"Extracted {len(categories)} categories using {cmp_type} pattern")
                return categories

    # Try generic extraction
    generic_patterns = categories_config.get("generic")
    if generic_patterns:
        categories = await extract_categories_with_pattern(modal_locator, generic_patterns, config)
        if categories:
            logging.debug(f"Extracted {len(categories)} categories using generic pattern")

    return categories


async def extract_categories_with_pattern(modal_locator: Locator, patterns: Dict[str, str], config: AuditConfig) -> List[CategoryInfo]:
    """
    Extract categories using a specific pattern configuration.

    Args:
        modal_locator: Locator for the settings modal
        patterns: Pattern configuration dict
        config: Audit configuration

    Returns:
        List of CategoryInfo objects
    """
    categories = []

    try:
        container_selector = patterns.get("container")
        item_selector = patterns.get("item")

        if not container_selector or not item_selector:
            return categories

        # Find category items
        items = await modal_locator.locator(item_selector).all()

        for item in items:
            try:
                # Extract name
                name = ""
                name_selector = patterns.get("name")
                if name_selector:
                    name_locator = item.locator(name_selector).first
                    name = await name_locator.inner_text(timeout=1000)
                    name = name.strip()
                elif patterns.get("name_in_button"):
                    # Name is in the button itself (Cookiebot pattern)
                    name = await item.inner_text(timeout=1000)
                    name = name.strip()

                if not name:
                    continue

                # Extract description
                description = None
                desc_selector = patterns.get("description")
                if desc_selector:
                    try:
                        # May need to expand accordion first
                        expand_btn_selector = patterns.get("expand_button")
                        if expand_btn_selector:
                            expand_btn = item.locator(expand_btn_selector).first
                            if await expand_btn.is_visible(timeout=500):
                                await expand_btn.click(timeout=1000)
                                await item.page.wait_for_timeout(500)

                        desc_locator = item.locator(desc_selector).first
                        description = await desc_locator.inner_text(timeout=1000)
                        description = description.strip()
                    except:
                        pass

                # Check if required/always active
                is_required = False
                required_marker_selector = patterns.get("required_marker")
                if required_marker_selector:
                    try:
                        required_marker = item.locator(required_marker_selector).first
                        is_required = await required_marker.is_visible(timeout=500)
                    except:
                        pass

                # Check for toggle and its state
                has_toggle = False
                default_enabled = None
                toggle_selector = patterns.get("toggle")
                if toggle_selector:
                    try:
                        toggle = item.locator(toggle_selector).first
                        if await toggle.is_visible(timeout=500):
                            has_toggle = True

                            # Check if checked (checkbox) or aria-checked (switch)
                            if "checkbox" in toggle_selector.lower():
                                default_enabled = await toggle.is_checked(timeout=500)
                            else:
                                aria_checked = await toggle.get_attribute("aria-checked", timeout=500)
                                if aria_checked:
                                    default_enabled = aria_checked.lower() == "true"
                    except:
                        pass

                categories.append(CategoryInfo(
                    name=name,
                    description=description,
                    default_enabled=default_enabled,
                    is_required=is_required,
                    has_toggle=has_toggle,
                ))

            except Exception as e:
                logging.debug(f"Error extracting category item: {e}")
                continue

    except Exception as e:
        logging.warning(f"Error extracting categories with pattern: {e}")

    return categories


async def extract_vendors(page: Page, modal_locator: Locator, cmp_type: Optional[str], config: AuditConfig, context: ConsentUIContext) -> List[VendorInfo]:
    """
    Extract vendor/partner information.

    May require clicking a "Show vendors" button first.

    Args:
        page: Playwright Page object
        modal_locator: Locator for the settings modal
        cmp_type: CMP type if known
        config: Audit configuration
        context: UI exploration context

    Returns:
        List of VendorInfo objects
    """
    audit_selectors = load_audit_selectors()
    vendors_config = audit_selectors.get("vendors", {})

    vendors = []

    # Try CMP-specific extraction
    if cmp_type:
        patterns = vendors_config.get(cmp_type)
        if patterns:
            vendors = await extract_vendors_with_pattern(page, modal_locator, patterns, config, context)
            if vendors:
                logging.debug(f"Extracted {len(vendors)} vendors using {cmp_type} pattern")
                return vendors

    # Try IAB TCF standard
    iab_patterns = vendors_config.get("iab_tcf")
    if iab_patterns:
        vendors = await extract_vendors_with_pattern(page, modal_locator, iab_patterns, config, context)
        if vendors:
            logging.debug(f"Extracted {len(vendors)} vendors using IAB TCF pattern")
            return vendors

    # Try generic extraction
    generic_patterns = vendors_config.get("generic")
    if generic_patterns:
        vendors = await extract_vendors_with_pattern(page, modal_locator, generic_patterns, config, context)
        if vendors:
            logging.debug(f"Extracted {len(vendors)} vendors using generic pattern")

    return vendors


async def extract_vendors_with_pattern(page: Page, modal_locator: Locator, patterns: Dict[str, str], config: AuditConfig, context: ConsentUIContext) -> List[VendorInfo]:
    """
    Extract vendors using a specific pattern configuration.

    Args:
        page: Playwright Page object
        modal_locator: Locator for the settings modal
        patterns: Pattern configuration dict
        config: Audit configuration
        context: UI exploration context

    Returns:
        List of VendorInfo objects
    """
    vendors = []

    try:
        # Check if we need to click a tab/button to show vendors
        tab_btn_selector = patterns.get("tab_button")
        if tab_btn_selector:
            try:
                tab_btn = modal_locator.locator(tab_btn_selector).first
                if await tab_btn.is_visible(timeout=1000):
                    await tab_btn.click(timeout=config.timeout_click)
                    await page.wait_for_timeout(1000)
            except:
                pass

        # Check for vendor link to click
        vendor_link_selector = patterns.get("vendor_link")
        if vendor_link_selector:
            try:
                vendor_link = modal_locator.locator(vendor_link_selector).first
                if await vendor_link.is_visible(timeout=1000):
                    await vendor_link.click(timeout=config.timeout_click)
                    await page.wait_for_timeout(1000)
            except:
                pass

        # Find vendor container
        container_selector = patterns.get("container")
        if not container_selector:
            return vendors

        container = modal_locator.locator(container_selector).first
        if not await container.is_visible(timeout=2000):
            return vendors

        # Handle lazy loading if needed
        item_selector = patterns.get("item")
        if item_selector:
            items = await handle_lazy_loading(container, item_selector, page)
        else:
            return vendors

        # Extract vendor info from each item
        for item in items[:100]:  # Limit to first 100 vendors
            try:
                # Extract name
                name = ""
                name_selector = patterns.get("name")
                if name_selector:
                    name_locator = item.locator(name_selector).first
                    name = await name_locator.inner_text(timeout=1000)
                    name = name.strip()

                if not name:
                    continue

                # Extract purposes
                purposes = []
                purposes_selector = patterns.get("purposes")
                if purposes_selector:
                    try:
                        purpose_elements = await item.locator(purposes_selector).all()
                        for elem in purpose_elements:
                            purpose_text = await elem.inner_text(timeout=500)
                            purposes.append(purpose_text.strip())
                    except:
                        pass

                # Extract legitimate interest purposes
                li_purposes = []
                li_selector = patterns.get("purposes_legitimate") or patterns.get("legitimate_interest")
                if li_selector:
                    try:
                        li_elements = await item.locator(li_selector).all()
                        for elem in li_elements:
                            li_text = await elem.inner_text(timeout=500)
                            li_purposes.append(li_text.strip())
                    except:
                        pass

                # Extract privacy policy link
                privacy_url = None
                link_selector = patterns.get("link") or patterns.get("privacy_link")
                if link_selector:
                    try:
                        link_elem = item.locator(link_selector).first
                        privacy_url = await link_elem.get_attribute("href", timeout=500)
                    except:
                        pass

                vendors.append(VendorInfo(
                    name=name,
                    purposes=purposes,
                    legitimate_interest_purposes=li_purposes,
                    privacy_policy_url=privacy_url,
                ))

            except Exception as e:
                logging.debug(f"Error extracting vendor item: {e}")
                continue

    except Exception as e:
        logging.warning(f"Error extracting vendors with pattern: {e}")

    return vendors


async def extract_cookies(page: Page, modal_locator: Locator, cmp_type: Optional[str], config: AuditConfig, context: ConsentUIContext) -> List[CookieDetail]:
    """
    Extract individual cookie details from the UI.

    Args:
        page: Playwright Page object
        modal_locator: Locator for the settings modal
        cmp_type: CMP type if known
        config: Audit configuration
        context: UI exploration context

    Returns:
        List of CookieDetail objects
    """
    audit_selectors = load_audit_selectors()
    cookies_config = audit_selectors.get("cookies", {})

    cookies = []

    # Try CMP-specific extraction
    if cmp_type:
        patterns = cookies_config.get(cmp_type)
        if patterns:
            cookies = await extract_cookies_with_pattern(page, modal_locator, patterns, config, context)
            if cookies:
                logging.debug(f"Extracted {len(cookies)} cookies using {cmp_type} pattern")
                return cookies

    # Try generic extraction
    generic_patterns = cookies_config.get("generic")
    if generic_patterns:
        cookies = await extract_cookies_with_pattern(page, modal_locator, generic_patterns, config, context)
        if cookies:
            logging.debug(f"Extracted {len(cookies)} cookies using generic pattern")

    return cookies


async def extract_cookies_with_pattern(page: Page, modal_locator: Locator, patterns: Dict[str, str], config: AuditConfig, context: ConsentUIContext) -> List[CookieDetail]:
    """
    Extract cookies using a specific pattern configuration.

    Args:
        page: Playwright Page object
        modal_locator: Locator for the settings modal
        patterns: Pattern configuration dict
        config: Audit configuration
        context: UI exploration context

    Returns:
        List of CookieDetail objects
    """
    cookies = []

    try:
        # Click tab/button to show cookies if needed
        tab_btn_selector = patterns.get("tab_button")
        if tab_btn_selector:
            try:
                tab_btn = modal_locator.locator(tab_btn_selector).first
                if await tab_btn.is_visible(timeout=1000):
                    await tab_btn.click(timeout=config.timeout_click)
                    await page.wait_for_timeout(1000)
            except:
                pass

        # Find cookie container
        container_selector = patterns.get("container")
        if not container_selector:
            return cookies

        container = modal_locator.locator(container_selector).first
        if not await container.is_visible(timeout=2000):
            return cookies

        # Find cookie items
        item_selector = patterns.get("item")
        if not item_selector:
            return cookies

        items = await container.locator(item_selector).all()

        # Extract cookie info from each item
        for item in items[:200]:  # Limit to first 200 cookies
            try:
                # Extract name
                name = ""
                name_selector = patterns.get("name")
                if name_selector:
                    name_locator = item.locator(name_selector).first
                    name = await name_locator.inner_text(timeout=500)
                    name = name.strip()

                if not name:
                    continue

                # Extract domain
                domain = None
                domain_selector = patterns.get("domain")
                if domain_selector:
                    try:
                        domain_locator = item.locator(domain_selector).first
                        domain = await domain_locator.inner_text(timeout=500)
                        domain = domain.strip()
                    except:
                        pass

                # Extract duration
                duration = None
                duration_selector = patterns.get("duration")
                if duration_selector:
                    try:
                        duration_locator = item.locator(duration_selector).first
                        duration = await duration_locator.inner_text(timeout=500)
                        duration = duration.strip()
                    except:
                        pass

                # Extract description/purpose
                purpose = None
                desc_selector = patterns.get("description") or patterns.get("details")
                if desc_selector:
                    try:
                        desc_locator = item.locator(desc_selector).first
                        purpose = await desc_locator.inner_text(timeout=500)
                        purpose = purpose.strip()
                    except:
                        pass

                cookies.append(CookieDetail(
                    name=name,
                    domain=domain,
                    duration=duration,
                    purpose=purpose,
                ))

            except Exception as e:
                logging.debug(f"Error extracting cookie item: {e}")
                continue

    except Exception as e:
        logging.warning(f"Error extracting cookies with pattern: {e}")

    return cookies


async def handle_lazy_loading(container: Locator, item_selector: str, page: Page, max_iterations: int = 10) -> List[Locator]:
    """
    Handle lazy loading for long lists (vendors, cookies).

    Scrolls container and waits for new items to load.

    Args:
        container: Container locator
        item_selector: Selector for list items
        page: Playwright Page object
        max_iterations: Maximum scroll iterations

    Returns:
        List of item locators
    """
    items = []
    last_count = 0
    iterations = 0

    while iterations < max_iterations:
        try:
            current_items = await container.locator(item_selector).all()
            current_count = len(current_items)

            if current_count == last_count:
                # No more items loading
                items = current_items
                break

            last_count = current_count
            items = current_items

            # Scroll to bottom to trigger lazy load
            await container.evaluate("el => el.scrollTop = el.scrollHeight")
            await page.wait_for_timeout(500)

            iterations += 1

        except Exception as e:
            logging.debug(f"Error during lazy loading: {e}")
            break

    return items


async def is_dangerous_button(button_locator: Locator) -> bool:
    """
    Check if a button is dangerous (should not be clicked during audit).

    Dangerous buttons: Save, Confirm, Accept, Apply, Submit

    Args:
        button_locator: Locator for the button

    Returns:
        True if dangerous, False otherwise
    """
    try:
        text = await button_locator.inner_text(timeout=500)
        text_lower = text.lower().strip() if text else ""

        aria_label = await button_locator.get_attribute("aria-label", timeout=500)
        aria_lower = aria_label.lower() if aria_label else ""

        data_action = await button_locator.get_attribute("data-action", timeout=500)
        data_action_lower = data_action.lower() if data_action else ""

        combined = f"{text_lower} {aria_lower} {data_action_lower}"

        dangerous_patterns = [
            "save", "confirm", "accept", "apply", "submit",
            "enregistrer", "confirmer", "accepter", "valider",
            "speichern", "bestätigen", "akzeptieren",
            "done", "terminé"
        ]

        return any(pattern in combined for pattern in dangerous_patterns)

    except:
        return False


# ============================================================================
# Phase 2 - Discovery-Based Extraction Functions
# ============================================================================

async def extract_categories_from_discovery(
    page: Page,
    modal_locator: Locator,
    section: Optional[DiscoveredSection],
    cmp_type: Optional[str],
    config: AuditConfig
) -> List[CategoryInfo]:
    """
    Extract categories using discovered section.

    Falls back to YAML-based extraction if section is None or contains no items.

    Args:
        page: Playwright Page object
        modal_locator: Locator for the settings modal
        section: Discovered categories section (or None)
        cmp_type: CMP type if known
        config: Audit configuration

    Returns:
        List of CategoryInfo objects
    """
    if not section or not section.contains_items:
        # Fallback to old YAML-based extraction
        logging.debug("No categories section discovered or section is empty, using YAML fallback")
        return await extract_categories(page, modal_locator, cmp_type, config)

    logging.info(f"Extracting categories from discovered section (confidence={section.confidence:.2f})")

    categories = []

    try:
        # Find container
        if section.locator:
            container = modal_locator.locator(section.locator).first
        else:
            container = modal_locator

        # Generic extraction: find all toggles with labels
        toggles = await container.locator('input[type="checkbox"], [role="switch"]').all()

        logging.debug(f"Found {len(toggles)} toggles in categories section")

        for idx, toggle in enumerate(toggles):
            try:
                # Get parent container (usually has name + description)
                parent = toggle.locator('xpath=..')

                # Extract name
                name = ""

                # Try associated label first
                toggle_id = await toggle.get_attribute("id")
                logging.debug(f"Toggle {idx}: id={toggle_id}")

                if toggle_id:
                    try:
                        label = await container.locator(f'label[for="{toggle_id}"]').first
                        name = await label.inner_text(timeout=1000)
                        name = name.strip()
                        logging.debug(f"Toggle {idx}: found label with text='{name}'")
                    except Exception as e:
                        logging.debug(f"Toggle {idx}: label search failed: {e}")

                # Fallback: get parent text
                if not name or len(name) < 2:
                    try:
                        parent_text = await parent.inner_text(timeout=1000)
                        logging.debug(f"Toggle {idx}: parent text='{parent_text[:100] if parent_text else '(empty)'}'")

                        # If parent is empty, try grandparent or nearby elements
                        if not parent_text or len(parent_text) < 2:
                            # Try grandparent
                            grandparent = parent.locator('xpath=..')
                            grandparent_text = await grandparent.inner_text(timeout=1000)
                            logging.debug(f"Toggle {idx}: grandparent text='{grandparent_text[:100] if grandparent_text else '(empty)'}'")
                            parent_text = grandparent_text

                        # Extract name from text
                        if parent_text:
                            lines = parent_text.split('\n')
                            for line in lines:
                                line = line.strip()
                                if len(line) > 2 and len(line) < 100:
                                    name = line
                                    break
                        logging.debug(f"Toggle {idx}: extracted name from parent='{name}'")
                    except Exception as e:
                        logging.debug(f"Toggle {idx}: parent text extraction failed: {e}")

                if not name or len(name) < 2:
                    logging.debug(f"Toggle {idx}: skipping - no valid name found")
                    continue

                logging.debug(f"Toggle {idx}: using name='{name}'")

                # Extract description (if available)
                description = None
                try:
                    # Look for description elements
                    desc_elements = await parent.locator('p, span[class*="desc"], [class*="detail"], small').all()
                    if desc_elements:
                        for elem in desc_elements:
                            desc_text = await elem.inner_text(timeout=500)
                            desc_text = desc_text.strip()
                            # Skip if it's the same as name
                            if desc_text and desc_text != name and len(desc_text) > len(name):
                                description = desc_text
                                break
                except:
                    pass

                # Check toggle state
                try:
                    default_enabled = await toggle.is_checked(timeout=500)
                except:
                    default_enabled = None

                # Check if required (disabled toggle)
                try:
                    is_required = await toggle.is_disabled(timeout=500)
                except:
                    is_required = False

                categories.append(CategoryInfo(
                    name=name,
                    description=description,
                    default_enabled=default_enabled,
                    is_required=is_required,
                    has_toggle=True
                ))

            except Exception as e:
                logging.debug(f"Failed to extract category from toggle: {e}")
                continue

        logging.info(f"Extracted {len(categories)} categories from discovered section")

    except Exception as e:
        logging.error(f"Category extraction from discovery failed: {e}")

    # If extraction failed or yielded no results, try YAML fallback
    if not categories:
        logging.debug("Discovery-based extraction yielded 0 categories, trying YAML fallback")
        return await extract_categories(page, modal_locator, cmp_type, config)

    return categories


async def extract_vendors_from_discovery(
    page: Page,
    modal_locator: Locator,
    section: Optional[DiscoveredSection],
    cmp_type: Optional[str],
    config: AuditConfig,
    context: ConsentUIContext
) -> List[VendorInfo]:
    """
    Extract vendors using discovered section.

    Falls back to YAML-based extraction if section is None or contains no items.

    Args:
        page: Playwright Page object
        modal_locator: Locator for the settings modal
        section: Discovered vendors section (or None)
        cmp_type: CMP type if known
        config: Audit configuration
        context: UI context for tracking state

    Returns:
        List of VendorInfo objects
    """
    if not section or not section.contains_items:
        # Fallback to old YAML-based extraction
        logging.debug("No vendors section discovered or section is empty, using YAML fallback")
        return await extract_vendors(page, modal_locator, cmp_type, config, context)

    logging.info(f"Extracting vendors from discovered section (confidence={section.confidence:.2f})")

    vendors = []

    try:
        # Find container
        if section.locator:
            container = modal_locator.locator(section.locator).first
        else:
            container = modal_locator

        # Check for lazy loading (use section metadata if available)
        has_lazy_loading = section.metadata.get("has_lazy_loading", False)

        # Generic extraction: find vendor items
        # Try multiple patterns
        vendor_items = []

        # Pattern 1: Look for vendor-specific classes
        try:
            items = await container.locator('[class*="vendor"], [class*="partner"]').all()
            if items:
                vendor_items = items
        except:
            pass

        # Pattern 2: Look for list items with privacy links
        if not vendor_items:
            try:
                items = await container.locator('li, div[role="listitem"], tr').all()
                if items:
                    vendor_items = items
            except:
                pass

        # Handle lazy loading if detected
        if has_lazy_loading and vendor_items:
            # Use existing handle_lazy_loading function
            item_selector = '[class*="vendor"], [class*="partner"], li'
            vendor_items = await handle_lazy_loading(container, item_selector, page)

        logging.debug(f"Found {len(vendor_items)} vendor items")

        for item in vendor_items[:500]:  # Limit to 500 vendors to avoid performance issues
            try:
                # Extract vendor name
                name = ""

                # Try to find name in heading, strong, or first meaningful text
                try:
                    name_candidates = await item.locator('h1, h2, h3, h4, h5, h6, strong, [class*="name"], [class*="title"]').all()
                    if name_candidates:
                        name = await name_candidates[0].inner_text(timeout=500)
                        name = name.strip()
                except:
                    pass

                # Fallback: use item text (first line)
                if not name or len(name) < 2:
                    item_text = await item.inner_text(timeout=1000)
                    lines = item_text.split('\n')
                    for line in lines:
                        line = line.strip()
                        if len(line) > 2 and len(line) < 100:
                            name = line
                            break

                if not name or len(name) < 2:
                    continue

                # Extract privacy policy URL
                privacy_policy_url = None
                try:
                    privacy_links = await item.locator('a[href*="privacy"], a[href*="politique"]').all()
                    if privacy_links:
                        privacy_policy_url = await privacy_links[0].get_attribute("href", timeout=500)
                except:
                    pass

                # Extract purposes (if available in text)
                purposes = []
                # This is simplified - full extraction would parse purpose text
                # For now, we'll leave purposes empty and let Phase 3 handle it

                vendors.append(VendorInfo(
                    name=name,
                    purposes=purposes,
                    privacy_policy_url=privacy_policy_url
                ))

            except Exception as e:
                logging.debug(f"Failed to extract vendor item: {e}")
                continue

        logging.info(f"Extracted {len(vendors)} vendors from discovered section")

    except Exception as e:
        logging.error(f"Vendor extraction from discovery failed: {e}")

    # If extraction failed or yielded no results, try YAML fallback
    if not vendors:
        logging.debug("Discovery-based extraction yielded 0 vendors, trying YAML fallback")
        return await extract_vendors(page, modal_locator, cmp_type, config, context)

    return vendors


async def extract_cookies_from_discovery(
    page: Page,
    modal_locator: Locator,
    section: Optional[DiscoveredSection],
    cmp_type: Optional[str],
    config: AuditConfig,
    context: ConsentUIContext
) -> List[CookieDetail]:
    """
    Extract cookies using discovered section.

    Falls back to YAML-based extraction if section is None or contains no items.

    Args:
        page: Playwright Page object
        modal_locator: Locator for the settings modal
        section: Discovered cookies section (or None)
        cmp_type: CMP type if known
        config: Audit configuration
        context: UI context for tracking state

    Returns:
        List of CookieDetail objects
    """
    if not section or not section.contains_items:
        # Fallback to old YAML-based extraction
        logging.debug("No cookies section discovered or section is empty, using YAML fallback")
        return await extract_cookies(page, modal_locator, cmp_type, config, context)

    logging.info(f"Extracting cookies from discovered section (confidence={section.confidence:.2f})")

    cookies = []

    try:
        # Find container
        if section.locator:
            container = modal_locator.locator(section.locator).first
        else:
            container = modal_locator

        # Generic extraction: find cookie items
        cookie_items = []

        # Pattern 1: Table rows (common for cookie lists)
        try:
            items = await container.locator('tr, [role="row"]').all()
            if len(items) > 1:  # More than just header
                cookie_items = items[1:]  # Skip header row
        except:
            pass

        # Pattern 2: List items
        if not cookie_items:
            try:
                items = await container.locator('li, div[class*="cookie"], [class*="cookie-item"]').all()
                if items:
                    cookie_items = items
            except:
                pass

        logging.debug(f"Found {len(cookie_items)} cookie items")

        for item in cookie_items[:1000]:  # Limit to 1000 cookies
            try:
                # Extract cookie details
                item_text = await item.inner_text(timeout=1000)

                # Extract cookie name
                name = ""

                # If it's a table row, try to get first cell
                try:
                    cells = await item.locator('td, th').all()
                    if cells:
                        name = await cells[0].inner_text(timeout=500)
                        name = name.strip()
                except:
                    pass

                # Fallback: use heading or first strong element
                if not name or len(name) < 2:
                    try:
                        name_elem = await item.locator('strong, [class*="name"], [class*="title"]').first
                        name = await name_elem.inner_text(timeout=500)
                        name = name.strip()
                    except:
                        pass

                # Last fallback: first line of text
                if not name or len(name) < 2:
                    lines = item_text.split('\n')
                    for line in lines:
                        line = line.strip()
                        if len(line) > 2 and len(line) < 100:
                            name = line
                            break

                if not name or len(name) < 2:
                    continue

                # Extract domain (look for domain patterns in text)
                domain = None
                domain_match = re.search(r'([a-zA-Z0-9.-]+\.(com|fr|eu|org|net|io|co\.uk))', item_text)
                if domain_match:
                    domain = domain_match.group(1)

                # Extract duration (look for duration keywords)
                duration = None
                duration_patterns = [
                    (r'(\d+)\s*(day|days|jour|jours)', 'days'),
                    (r'(\d+)\s*(month|months|mois)', 'months'),
                    (r'(\d+)\s*(year|years|an|année|années)', 'years'),
                    (r'(session|sessione)', 'session'),
                ]

                for pattern, unit in duration_patterns:
                    match = re.search(pattern, item_text, re.IGNORECASE)
                    if match:
                        if unit == 'session':
                            duration = 'session'
                        else:
                            duration = f"{match.group(1)} {unit}"
                        break

                # Extract purpose (simplified - take remaining text)
                purpose = None
                # Would need better parsing in Phase 3

                cookies.append(CookieDetail(
                    name=name,
                    domain=domain,
                    duration=duration,
                    purpose=purpose
                ))

            except Exception as e:
                logging.debug(f"Failed to extract cookie item: {e}")
                continue

        logging.info(f"Extracted {len(cookies)} cookies from discovered section")

    except Exception as e:
        logging.error(f"Cookie extraction from discovery failed: {e}")

    # If extraction failed or yielded no results, try YAML fallback
    if not cookies:
        logging.debug("Discovery-based extraction yielded 0 cookies, trying YAML fallback")
        return await extract_cookies(page, modal_locator, cmp_type, config, context)

    return cookies

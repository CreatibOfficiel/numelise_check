"""
Enhanced CMP-specific section discovery for better GDPR audit coverage.

This module provides specialized section discovery logic for major CMPs
that have complex or non-standard modal structures.
"""

import logging
from typing import List, Optional, Dict
from playwright.async_api import Page, Locator

from consentcrawl.audit_schemas import DiscoveredSection, SectionType, ContentType, DiscoveryMethod


async def discover_didomi_sections(modal_locator: Locator, page: Page) -> List[DiscoveredSection]:
    """
    Didomi-specific section discovery.
    
    Didomi uses TWO modals:
    1. didomi-popup-notice (initial banner)
    2. didomi-consent-popup-preferences (preferences modal with sections)
    
    We need to look inside the preferences modal specifically.
    
    Args:
        modal_locator: Locator for the modal (might be notice, not preferences)
        page: Playwright Page object
        
    Returns:
        List of discovered sections
    """
    sections = []
    
    try:
        # CRITICAL: Find the preferences modal, not the notice modal
        preferences_modal = None
        
        # Try to find preferences modal
        pref_selectors = [
            ".didomi-consent-popup-preferences",
            "[class*='preferences']",
            ".didomi-popup-preferences"
        ]
        
        for selector in pref_selectors:
            try:
                locator = page.locator(selector).first
                if await locator.is_visible(timeout=1000):
                    logging.info(f"✓ Didomi preferences modal found: {selector}")
                    preferences_modal = locator
                    break
            except:
                continue
        
        if not preferences_modal:
            logging.warning("Didomi preferences modal not found, using provided modal_locator")
            preferences_modal = modal_locator
        
        # Strategy 1: Look for purpose/vendor elements (Didomi uses these class names)
        logging.debug(f"Didomi: Searching for purposes/vendors in {preferences_modal}")
        purpose_elements = await preferences_modal.locator("[class*='purpose']").all()
        vendor_elements = await preferences_modal.locator("[class*='vendor']").all()
        
        logging.debug(f"Didomi: Found {len(purpose_elements)} purpose elements, {len(vendor_elements)} vendor elements")
        
        if len(purpose_elements) > 0:
            try:
                # Found purposes - create section
                first_purpose = preferences_modal.locator("[class*='purpose']").first
                sections.append(DiscoveredSection(
                    section_type=SectionType.LIST,
                    content_type=ContentType.CATEGORIES,
                    locator="[class*='purpose']",
                    activation_required=False,
                    activation_locator=None,
                    discovery_method=DiscoveryMethod.CMP_SPECIFIC,
                    confidence=0.9
                ))
                logging.info("✓ Didomi purposes section discovered")
            except Exception as e:
                logging.error(f"Error creating Didomi purposes section: {e}")
        
        if len(vendor_elements) > 0:
            try:
                # Found vendors - create section
                first_vendor = preferences_modal.locator("[class*='vendor']").first
                sections.append(DiscoveredSection(
                    section_type=SectionType.LIST,
                    content_type=ContentType.VENDORS,
                    locator="[class*='vendor']",
                    activation_required=False,
                    activation_locator=None,
                    discovery_method=DiscoveryMethod.CMP_SPECIFIC,
                    confidence=0.9
                ))
                logging.info("✓ Didomi vendors section discovered")
            except Exception as e:
                logging.error(f"Error creating Didomi vendors section: {e}")
        
        # Strategy 2: Look for accordion-style sections (fallback)
        if not sections:
            accordion_selector = ".didomi-components-accordion"
            accordions = await preferences_modal.locator(accordion_selector).all()
            
            for accordion in accordions[:5]:  # Limit to 5
                try:
                    text = await accordion.inner_text(timeout=500)
                    text_lower = text.lower()
                    
                    if any(kw in text_lower for kw in ['finalité', 'purpose', 'catégorie']):
                        sections.append(DiscoveredSection(
                            section_type=SectionType.CATEGORIES,
                            content_type=ContentType.PURPOSES,
                            locator=accordion,
                            selector=accordion_selector,
                            discovery_method=DiscoveryMethod.VISUAL,
                            confidence=0.7,
                            requires_activation=True
                        ))
                    elif any(kw in text_lower for kw in ['partenaire', 'vendor', 'partner']):
                        sections.append(DiscoveredSection(
                            section_type=SectionType.VENDORS,
                            content_type=ContentType.VENDORS,
                            locator=accordion,
                            selector=accordion_selector,
                            discovery_method=DiscoveryMethod.VISUAL,
                            confidence=0.7,
                            requires_activation=True
                        ))
                except:
                    continue
                    
    except Exception as e:
        logging.debug(f"Didomi section discovery failed: {e}")
    
    return sections


async def discover_sourcepoint_sections(modal: Locator) -> List[DiscoveredSection]:
    """
    Discover sections in Sourcepoint CMP.
    Sourcepoint often uses 'stacks' (accordions) for purposes.
    """
    sections = []
    logging.debug("  [Sourcepoint] Starting section discovery")
    
    # 1. Stacks (accordion style)
    # Look for the stack container or the row
    stack_locator = modal.locator(".message-component.stack-row, .tcfv2-stack")
    try:
        # Wait for at least one stack to appear (rendering delay)
        if await stack_locator.first.is_visible(timeout=5000):
            count = await stack_locator.count()
            logging.debug(f"  [Sourcepoint] Found {count} stack elements")
        else:
            count = 0
            logging.debug("  [Sourcepoint] Stacks not visible after wait")
    except:
        count = 0
        logging.debug("  [Sourcepoint] Error counting stack elements")

    if count > 0:
        logging.info(f"✓ Sourcepoint stacks found: {count}")
        # If stacks are found, they are likely the categories themselves or the container for them.
        # We can treat the stack container as a list of categories.
        
        # Add the stack container as a discovered section
        sections.append(DiscoveredSection(
            section_type=SectionType.ACCORDION,
            content_type=ContentType.CATEGORIES,
            locator=".message-component.stack-row, .tcfv2-stack",
            activation_required=True,
            activation_locator=".message-component.stack-row, .tcfv2-stack", # Click the stack to expand
            discovery_method=DiscoveryMethod.CMP_SPECIFIC,
            confidence=0.9
        ))
        
        # Also look for tabs just in case (hybrid approach)
        tab_selectors = {
            "categories": [
                "button:has-text('Purposes')",
                "button:has-text('Categories')",
                "[class*='message-component']:has-text('Purposes')",
                ".sp_choice_type_12",  # Sourcepoint-specific class
            ],
            "vendors": [
                "button:has-text('Vendors')",
                "button:has-text('Partners')",
                "[class*='message-component']:has-text('Vendors')",
                "[class*='message-component'][class*='vendors']",
            ]
        }
        
        for section_type, selectors in tab_selectors.items():
            for selector in selectors:
                try:
                    locator = modal.locator(selector).first
                    if await locator.is_visible(timeout=1000):
                        logging.info(f"✓ Sourcepoint {section_type} section found: {selector}")
                        
                        sections.append(DiscoveredSection(
                            section_type=SectionType.TAB,
                            content_type=ContentType.CATEGORIES if section_type == "categories" else ContentType.VENDORS,
                            locator=selector,
                            activation_required=True,
                            activation_locator=selector,
                            discovery_method=DiscoveryMethod.CMP_SPECIFIC,
                            confidence=0.9
                        ))
                        break
                except:
                    continue
                    

    
    return sections


async def discover_orejime_sections(modal: Locator) -> List[DiscoveredSection]:
    """
    Discover sections in Orejime CMP.
    
    Orejime uses a simple list structure with .orejime-AppList-item elements.
    Each item contains a title, description, and toggle switch.
    """
    sections = []
    logging.debug("  [Orejime] Starting section discovery")
    
    try:
        # Orejime has a simple list structure
        app_list_locator = modal.locator(".orejime-AppList")
        
        # Wait for the list to appear
        if await app_list_locator.first.is_visible(timeout=3000):
            # Count items
            item_count = await modal.locator(".orejime-AppList-item").count()
            logging.info(f"✓ Orejime app list found: {item_count} items")
            
            # Add the app list as a discovered section
            sections.append(DiscoveredSection(
                section_type=SectionType.LIST,
                content_type=ContentType.CATEGORIES,
                locator=".orejime-AppList-item",
                activation_required=False,  # Items are already visible
                discovery_method=DiscoveryMethod.CMP_SPECIFIC,
                confidence=0.95
            ))
        else:
            logging.debug("  [Orejime] App list not visible")
    except Exception as e:
        logging.debug(f"  [Orejime] Section discovery failed: {e}")
    
    return sections


async def discover_sfbx_sections(modal: Locator) -> List[DiscoveredSection]:
    """
    Discover sections in SFBX CMP.
    
    SFBX uses an iframe and requires clicking 'Set up' or 'Partners'.
    """
    sections = []
    logging.debug("  [SFBX] Starting section discovery")
    
    try:
        # The modal locator passed here is likely the body of the iframe (from detect_sfbx_modal)
        
        # Check if we are already in the vendor list (e.g. after clicking "Partners" in banner)
        vendor_items = modal.locator(".consentableItem")
        if await vendor_items.count() > 0:
            logging.info("  [SFBX] Already in vendor list")
            sections.append(DiscoveredSection(
                section_type=SectionType.LIST,
                content_type=ContentType.VENDORS,
                locator=".consentableItem",
                activation_required=False,
                activation_locator=None,
                discovery_method=DiscoveryMethod.CMP_SPECIFIC,
                confidence=1.0
            ))
            return sections

        # Try to find and click "Partners" button first
        partners_btn = modal.locator("button:has-text('partners'), button:has-text('partenaires')").first
        if await partners_btn.count() > 0:
            logging.info("  [SFBX] Clicking Partners button")
            await partners_btn.evaluate("e => e.click()")
            await modal.page.wait_for_timeout(2000)
            
            sections.append(DiscoveredSection(
                section_type=SectionType.LIST,
                content_type=ContentType.VENDORS,
                locator=".consentableItem",
                activation_required=False,
                activation_locator=None,
                discovery_method=DiscoveryMethod.CMP_SPECIFIC,
                confidence=1.0
            ))
            
        else:
            # Try "Set up" button
            setup_btn = modal.locator(".button__openPrivacyCenter").first
            if await setup_btn.count() > 0:
                logging.info("  [SFBX] Clicking Set up button")
                await setup_btn.evaluate("e => e.click()")
                await modal.page.wait_for_timeout(2000)
                
                # After set up, we might see purposes
                sections.append(DiscoveredSection(
                    section_type=SectionType.LIST,
                    content_type=ContentType.CATEGORIES,
                    locator="button[title]", # Purposes often have titles in buttons as seen in dump
                    activation_required=False,
                    discovery_method=DiscoveryMethod.CMP_SPECIFIC,
                    confidence=0.8
                ))
                
    except Exception as e:
        logging.debug(f"  [SFBX] Section discovery failed: {e}")
    
    return sections


async def discover_trust_commander_sections(modal: Locator) -> List[DiscoveredSection]:
    """
    Discover sections in Trust Commander CMP.
    
    Trust Commander uses .vendor class for vendors and has purpose/category sections.
    These are often inside an iframe (Privacy Center).
    """
    sections = []
    logging.debug("  [TrustCommander] Starting section discovery")
    
    try:
        # Determine the search context (modal or iframe)
        search_context = modal
        
        # Check if we need to switch to an iframe
        # If the modal is the main page wrapper (e.g. #footer_tc_privacy), the content is likely in an iframe
        page = modal.page
        frames = page.frames
        pc_frame = next((f for f in frames if "privacy-center" in f.url or "privacy-iframe" in f.name), None)
        
        if pc_frame:
            logging.info(f"  [TrustCommander] Found Privacy Center iframe: {pc_frame.url}")
            # Use the body of the iframe as the search context
            search_context = pc_frame.locator("body")
        
        # Trust Commander vendors are in .vendor elements
        vendor_locator = search_context.locator(".vendor")
        
        try:
            # Wait a bit for iframe content to render
            if await vendor_locator.first.is_visible(timeout=5000):
                count = await vendor_locator.count()
                logging.info(f"✓ Trust Commander vendors found: {count}")
                
                sections.append(DiscoveredSection(
                    section_type=SectionType.LIST,
                    content_type=ContentType.VENDORS,
                    locator=".vendor",
                    activation_required=False,
                    discovery_method=DiscoveryMethod.CMP_SPECIFIC,
                    confidence=0.95,
                    iframe_url=pc_frame.url if pc_frame else None
                ))
        except:
            logging.debug("  [TrustCommander] Vendors not visible in current context")
        
        # Look for purpose/category sections
        purpose_selectors = [
            ".purpose",
            ".category",
            "[class*='purpose']",
            "[class*='category']"
        ]
        
        for selector in purpose_selectors:
            try:
                locator = search_context.locator(selector).first
                if await locator.is_visible(timeout=1000):
                    count = await search_context.locator(selector).count()
                    if count > 0:
                        logging.info(f"✓ Trust Commander categories found: {count} with {selector}")
                        sections.append(DiscoveredSection(
                            section_type=SectionType.LIST,
                            content_type=ContentType.CATEGORIES,
                            locator=selector,
                            activation_required=False,
                            discovery_method=DiscoveryMethod.CMP_SPECIFIC,
                            confidence=0.9,
                            iframe_url=pc_frame.url if pc_frame else None
                        ))
                        break
            except:
                continue
                
    except Exception as e:
        logging.debug(f"  [TrustCommander] Section discovery failed: {e}")
    
    return sections


async def discover_onetrust_sections(modal_locator: Locator, page: Page) -> List[DiscoveredSection]:
    """
    OneTrust-specific section discovery.
    
    OneTrust uses specific IDs and classes like #onetrust-pc-sdk.
    
    Args:
        modal_locator: Locator for the modal
        page: Playwright Page object
        
    Returns:
        List of discovered sections
    """
    sections = []
    
    try:
        # OneTrust tab selectors
        tab_selectors = {
            "categories": [
                ".category-menu-switch-handler",
                "#onetrust-pc-sdk .ot-tab-desc:has-text('Cookie')",
                "button:has-text('Cookie Categories')",
            ],
            "vendors": [
                ".ot-link-btn.category-vendors-list-handler",
                "#onetrust-pc-sdk .ot-tab-desc:has-text('Vendor')",
                ".ot-ven-link",
            ],
            "cookies": [
                ".ot-link-btn.category-host-list-handler",
                "#onetrust-pc-sdk .ot-tab-desc:has-text('Cookie')",
            ]
        }
        
        for section_type, selectors in tab_selectors.items():
            for selector in selectors:
                try:
                    # Find all candidates
                    candidates = await modal_locator.locator(selector).all()
                    logging.debug(f"OneTrust selector {selector} found {len(candidates)} candidates")
                    
                    for i, candidate in enumerate(candidates):
                        # If visible, great
                        if await candidate.is_visible():
                            logging.info(f"✓ OneTrust {section_type} section found (visible): {selector}")
                            sections.append(DiscoveredSection(
                                section_type=SectionType.TAB,
                                content_type=ContentType.CATEGORIES if section_type == "categories" else ContentType.VENDORS,
                                locator=selector,
                                activation_required=True,
                                activation_locator=selector,
                                discovery_method=DiscoveryMethod.CMP_SPECIFIC,
                                confidence=0.9
                            ))
                            break
                        
                        # If not visible, check if it's inside a collapsed accordion
                        logging.debug(f"Candidate {i} not visible, checking for accordion")
                        try:
                            # Try to find a parent accordion trigger
                            container = candidate.locator("xpath=ancestor::div[contains(@class, 'ot-cat-item') or contains(@class, 'category-item')]").first
                            if await container.count() > 0:
                                logging.debug(f"Found container for candidate {i}")
                                trigger = container.locator("button[aria-expanded='false']").first
                                if await trigger.count() > 0:
                                    logging.debug(f"Found trigger for candidate {i}, visible={await trigger.is_visible()}")
                                    if await trigger.is_visible():
                                        logging.info(f"Expanding OneTrust accordion to reveal {section_type}")
                                        await trigger.click()
                                        await page.wait_for_timeout(1000) # Wait for animation
                                        
                                        if await candidate.is_visible():
                                            # Try to make locator more specific to ensure we click the visible one
                                            parent_id = await candidate.get_attribute("data-parent-id")
                                            specific_locator = selector
                                            if parent_id:
                                                specific_locator = f"{selector}[data-parent-id='{parent_id}']"
                                            
                                            logging.info(f"✓ OneTrust {section_type} section revealed: {specific_locator}")
                                            sections.append(DiscoveredSection(
                                                section_type=SectionType.TAB,
                                                content_type=ContentType.CATEGORIES if section_type == "categories" else ContentType.VENDORS,
                                                locator="", # Content is in the modal, not inside the button
                                                activation_required=True,
                                                activation_locator=specific_locator,
                                                discovery_method=DiscoveryMethod.CMP_SPECIFIC,
                                                confidence=0.9
                                            ))
                                            break
                                else:
                                    logging.debug(f"No trigger found for candidate {i}")
                            else:
                                logging.debug(f"No container found for candidate {i}")
                        except Exception as e:
                            logging.debug(f"Failed to expand accordion: {e}")
                            continue
                    
                    if sections and sections[-1].content_type == (ContentType.CATEGORIES if section_type == "categories" else ContentType.VENDORS):
                        break

                except Exception as e:
                    logging.debug(f"Error checking selector {selector}: {e}")
                    continue
                    
    except Exception as e:
        logging.debug(f"OneTrust section discovery failed: {e}")
    
    return sections


async def discover_cmp_specific_sections(
    modal_locator: Locator,
    page: Page,
    cmp_type: Optional[str]
) -> List[DiscoveredSection]:
    """
    Main entry point for CMP-specific section discovery.
    
    Routes to the appropriate CMP-specific function based on CMP type.
    
    Args:
        modal_locator: Locator for the modal
        page: Playwright Page object
        cmp_type: CMP type (e.g., 'didomi', 'sourcepoint-cmp', 'onetrust')
        
    Returns:
        List of discovered sections
    """
    if not cmp_type:
        return []
    
    normalized_cmp = cmp_type.replace("-cmp", "").replace("_", "-").lower()
    
    logging.info(f"🔍 Attempting CMP-specific section discovery for: {normalized_cmp}")
    
    if "didomi" in normalized_cmp:
        return await discover_didomi_sections(modal_locator, page)
    elif "sourcepoint" in normalized_cmp:
        return await discover_sourcepoint_sections(modal_locator)
    elif "onetrust" in normalized_cmp:
        return await discover_onetrust_sections(modal_locator, page)
    elif "orejime" in normalized_cmp:
        return await discover_orejime_sections(modal_locator)
    elif "sfbx" in normalized_cmp:
        return await discover_sfbx_sections(modal_locator)
    elif "trust" in normalized_cmp or "commander" in normalized_cmp:
        return await discover_trust_commander_sections(modal_locator)
    else:
        logging.debug(f"No CMP-specific section discovery for: {normalized_cmp}")
        return []

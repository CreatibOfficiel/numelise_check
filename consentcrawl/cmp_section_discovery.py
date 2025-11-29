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
        purpose_elements = await preferences_modal.locator("[class*='purpose']").all()
        vendor_elements = await preferences_modal.locator("[class*='vendor']").all()
        
        logging.info(f"Didomi: Found {len(purpose_elements)} purpose elements, {len(vendor_elements)} vendor elements")
        
        if len(purpose_elements) > 0:
            # Found purposes - create section
            first_purpose = preferences_modal.locator("[class*='purpose']").first
            sections.append(DiscoveredSection(
                section_type=SectionType.CATEGORIES,
                content_type=ContentType.PURPOSES,
                locator=first_purpose,
                selector="[class*='purpose']",
                discovery_method=DiscoveryMethod.CMP_SPECIFIC,
                confidence=0.9,
                requires_activation=False  # Already visible
            ))
            logging.info("✓ Didomi purposes section discovered")
        
        if len(vendor_elements) > 0:
            # Found vendors - create section
            first_vendor = preferences_modal.locator("[class*='vendor']").first
            sections.append(DiscoveredSection(
                section_type=SectionType.VENDORS,
                content_type=ContentType.VENDORS,
                locator=first_vendor,
                selector="[class*='vendor']",
                discovery_method=DiscoveryMethod.CMP_SPECIFIC,
                confidence=0.9,
                requires_activation=False  # Already visible
            ))
            logging.info("✓ Didomi vendors section discovered")
        
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


async def discover_sourcepoint_sections(modal_locator: Locator, page: Page) -> List[DiscoveredSection]:
    """
    Sourcepoint-specific section discovery.
    
    Sourcepoint often uses message-component classes and has sections
    that may be in nested iframes or shadow DOM.
    
    Args:
        modal_locator: Locator for the modal
        page: Playwright Page object
        
    Returns:
        List of discovered sections
    """
    sections = []
    
    try:
        # Sourcepoint tab selectors
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
                    locator = modal_locator.locator(selector).first
                    if await locator.is_visible(timeout=1000):
                        logging.info(f"✓ Sourcepoint {section_type} section found: {selector}")
                        
                        sections.append(DiscoveredSection(
                            section_type=SectionType.CATEGORIES if section_type == "categories" else SectionType.VENDORS,
                            content_type=ContentType.PURPOSES if section_type == "categories" else ContentType.VENDORS,
                            locator=locator,
                            selector=selector,
                            discovery_method=DiscoveryMethod.CMP_SPECIFIC,
                            confidence=0.9,
                            requires_activation=True
                        ))
                        break
                except:
                    continue
                    
    except Exception as e:
        logging.debug(f"Sourcepoint section discovery failed: {e}")
    
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
                "#onetrust-pc-sdk .ot-tab-desc:has-text('Cookie')",
                "#onetrust-pc-sdk .category-menu-switch-handler",
                "button:has-text('Cookie Categories')",
            ],
            "vendors": [
                "#onetrust-pc-sdk .ot-tab-desc:has-text('Vendor')",
                "#onetrust-pc-sdk .ot-tab-desc:has-text('IAB Vendors')",
                ".ot-ven-link",
                "#ot-ven-lst-lnk",
            ]
        }
        
        for section_type, selectors in tab_selectors.items():
            for selector in selectors:
                try:
                    locator = modal_locator.locator(selector).first
                    if await locator.is_visible(timeout=1000):
                        logging.info(f"✓ OneTrust {section_type} section found: {selector}")
                        
                        sections.append(DiscoveredSection(
                            section_type=SectionType.CATEGORIES if section_type == "categories" else SectionType.VENDORS,
                            content_type=ContentType.PURPOSES if section_type == "categories" else ContentType.VENDORS,
                            locator=locator,
                            selector=selector,
                            discovery_method=DiscoveryMethod.CMP_SPECIFIC,
                            confidence=0.9,
                            requires_activation=True
                        ))
                        break
                except:
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
        return await discover_sourcepoint_sections(modal_locator, page)
    elif "onetrust" in normalized_cmp:
        return await discover_onetrust_sections(modal_locator, page)
    else:
        logging.debug(f"No CMP-specific section discovery for: {normalized_cmp}")
        return []

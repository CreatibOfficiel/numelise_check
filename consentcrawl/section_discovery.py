"""
Section Discovery Module for Phase 2 - Intelligent Section Discovery.

This module implements a 3-tier discovery strategy to find consent sections
(Categories, Vendors, Cookies) in CMP modals:

Tier 1: ARIA Semantic Discovery (Confidence: 0.8-1.0)
  - role="tab", role="tabpanel", role="tablist"
  - aria-expanded, aria-controls
  - Button text matching with multi-language support

Tier 2: Visual Pattern Analysis (Confidence: 0.5-0.75)
  - Horizontal button groupings (tabs)
  - Chevron icons + text (accordions)
  - Repeated structures with similar children
  - Toggle/checkbox groupings

Tier 3: YAML Fallback (Confidence: 0.4-0.7)
  - Uses existing audit_selectors.yml patterns
  - CMP-specific > Generic patterns

All tiers run and results are merged with deduplication.
Sections are auto-activated during discovery to validate content.
"""

import logging
import os
import time
import re
import yaml
from typing import List, Optional, Dict, Any
from playwright.async_api import Page, Locator, TimeoutError as PlaywrightTimeoutError

from consentcrawl.audit_schemas import (
    SectionType, ContentType, DiscoveryMethod,
    DiscoveredSection, SectionDiscoveryResult,
    AuditConfig
)


MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIT_SELECTORS_FILE = f"{MODULE_DIR}/assets/audit_selectors.yml"


def load_audit_selectors():
    """Load audit selector patterns from YAML file."""
    with open(AUDIT_SELECTORS_FILE, "r") as f:
        return yaml.safe_load(f)


# ============================================================================
# Helper Functions
# ============================================================================

def classify_from_keywords(text: str) -> ContentType:
    """
    Classify section content type from text using multi-language keywords.

    Supports: English, French, German, Spanish, Italian

    Args:
        text: Text to classify (button label, tab text, etc.)

    Returns:
        ContentType enum value
    """
    if not text:
        return ContentType.UNKNOWN

    text_lower = text.lower().strip()

    # Categories/Purposes patterns
    category_keywords = [
        "categor", "purpose", "finalit", "zweck", "categor",
        "objective", "objectif", "cat\u00e9gorie"  # catégorie with accent
    ]
    if any(kw in text_lower for kw in category_keywords):
        return ContentType.CATEGORIES

    # Vendors/Partners patterns
    vendor_keywords = [
        "vendor", "partner", "partenaire", "fournisseur",
        "iab", "anbieter", "socio", "fornitori", "providers"
    ]
    if any(kw in text_lower for kw in vendor_keywords):
        return ContentType.VENDORS

    # Cookies patterns
    cookie_keywords = ["cookie", "t\u00e9moin", "keks"]  # témoin with accent
    if any(kw in text_lower for kw in cookie_keywords):
        return ContentType.COOKIES

    # Purposes (distinct from categories)
    purpose_keywords = ["purpose", "objectif", "objective", "finalidad"]
    if any(kw in text_lower for kw in purpose_keywords):
        return ContentType.PURPOSES

    return ContentType.UNKNOWN


async def get_selector_for_locator(locator: Locator) -> Optional[str]:
    """
    Extract a CSS selector from a Playwright Locator.

    Args:
        locator: Playwright Locator object

    Returns:
        CSS selector string or None
    """
    try:
        # Try to get ID first
        element_id = await locator.get_attribute("id")
        if element_id:
            return f"#{element_id}"

        # Try to get unique class combination
        classes = await locator.get_attribute("class")
        if classes:
            class_list = classes.split()[:3]  # Use first 3 classes
            if class_list:
                return "." + ".".join(class_list)

        # Try data attributes
        for attr in ["data-tab", "data-action", "data-id", "data-type"]:
            value = await locator.get_attribute(attr)
            if value:
                return f"[{attr}='{value}']"

        # Try aria-label
        aria_label = await locator.get_attribute("aria-label")
        if aria_label and len(aria_label) < 50:
            # Use text-based selector as last resort
            tag = await locator.evaluate("el => el.tagName.toLowerCase()")
            return f"{tag}[aria-label='{aria_label}']"

        # Very last fallback: use tag with text content (if button/a)
        tag = await locator.evaluate("el => el.tagName.toLowerCase()")
        if tag in ["button", "a"]:
            text = await locator.inner_text(timeout=500)
            text = text.strip()[:30]  # Limit to 30 chars
            if text:
                return f"{tag}:has-text('{text}')"

        # Absolute fallback: just tag (will likely fail, but better than None)
        return tag

    except Exception as e:
        logging.debug(f"Could not extract selector from locator: {e}")
        return None


async def are_horizontally_aligned(buttons: List[Locator]) -> bool:
    """
    Check if buttons are horizontally aligned (tab pattern).

    Args:
        buttons: List of button locators

    Returns:
        True if buttons are in horizontal alignment
    """
    if len(buttons) < 2:
        return False

    try:
        # Get bounding boxes for first 3 buttons
        boxes = []
        for btn in buttons[:3]:
            box = await btn.bounding_box()
            if box:
                boxes.append(box)

        if len(boxes) < 2:
            return False

        # Check if Y coordinates are similar (within 10px tolerance)
        y_coords = [box['y'] for box in boxes]
        y_diff = max(y_coords) - min(y_coords)

        return y_diff < 10

    except Exception as e:
        logging.debug(f"Horizontal alignment check failed: {e}")
        return False


async def are_similar_elements(elements: List[Locator]) -> bool:
    """
    Check if elements have similar structure (repeated pattern).

    Compares tag names and class patterns to detect lists.

    Args:
        elements: List of element locators

    Returns:
        True if elements appear to be similar/repeated
    """
    if len(elements) < 2:
        return False

    try:
        # Get tag names and class lists
        tags = []
        class_patterns = []

        for elem in elements[:5]:  # Sample first 5
            tag = await elem.evaluate("el => el.tagName.toLowerCase()")
            classes = await elem.get_attribute("class") or ""
            tags.append(tag)
            class_patterns.append(set(classes.split()))

        # All same tag?
        if len(set(tags)) > 1:
            return False

        # At least 2 classes in common?
        if len(class_patterns) >= 2:
            common = class_patterns[0].intersection(*class_patterns[1:])
            return len(common) >= 2

        return True

    except Exception as e:
        logging.debug(f"Element similarity check failed: {e}")
        return False


async def has_cookie_indicators(element: Locator) -> bool:
    """
    Check if element contains cookie-specific indicators.

    Looks for domain names, duration patterns, etc.

    Args:
        element: Element locator to check

    Returns:
        True if element appears to contain cookie info
    """
    try:
        text = await element.inner_text(timeout=1000)
        text_lower = text.lower()

        # Cookie indicators: domain names, duration patterns
        domain_pattern = r'\.(com|fr|eu|org|net|io|co\.uk)'
        duration_patterns = [
            'day', 'month', 'year', 'session',
            'jour', 'mois', 'an', 'année',
            'tag', 'monat', 'jahr'
        ]

        has_domain = bool(re.search(domain_pattern, text))
        has_duration = any(p in text_lower for p in duration_patterns)

        return has_domain or has_duration

    except Exception as e:
        logging.debug(f"Cookie indicator check failed: {e}")
        return False


# ============================================================================
# Main SectionDiscoverer Class
# ============================================================================

class SectionDiscoverer:
    """
    Main class for intelligent section discovery in consent modals.

    Implements 3-tier discovery strategy (ARIA → Visual → YAML) with
    auto-activation and validation.
    """

    def __init__(
        self,
        page: Page,
        modal_locator: Locator,
        cmp_type: Optional[str],
        config: AuditConfig
    ):
        """
        Initialize Section Discoverer.

        Args:
            page: Playwright Page object
            modal_locator: Locator for the opened modal
            cmp_type: CMP type if known (e.g., "didomi", "onetrust")
            config: Audit configuration
        """
        self.page = page
        self.modal_locator = modal_locator
        self.cmp_type = cmp_type
        self.config = config
        self.yaml_patterns = load_audit_selectors()

    async def discover_all_sections(self) -> SectionDiscoveryResult:
        """
        Execute complete section discovery pipeline.

        Runs all 3 tiers, merges results, activates sections, and validates content.

        Returns:
            SectionDiscoveryResult with discovered sections
        """
        start_time = time.time()
        result = SectionDiscoveryResult(sections=[])

        try:
            logging.info("[SectionDiscovery] Starting 3-tier discovery")

            # Step 1: Run all 3 discovery tiers
            tier1_sections = await self.discover_tier1_aria()
            logging.info(f"[Tier1-ARIA] Found {len(tier1_sections)} sections")
            for section in tier1_sections:
                logging.debug(
                    f"  - {section.content_type.value}: {section.section_type.value}, "
                    f"confidence={section.confidence:.2f}"
                )

            tier2_sections = await self.discover_tier2_visual()
            logging.info(f"[Tier2-Visual] Found {len(tier2_sections)} sections")
            for section in tier2_sections:
                logging.debug(
                    f"  - {section.content_type.value}: {section.section_type.value}, "
                    f"confidence={section.confidence:.2f}"
                )

            tier3_sections = await self.discover_tier3_yaml()
            logging.info(f"[Tier3-YAML] Found {len(tier3_sections)} sections")
            for section in tier3_sections:
                logging.debug(
                    f"  - {section.content_type.value}: {section.section_type.value}, "
                    f"confidence={section.confidence:.2f}"
                )

            # Step 2: Merge and deduplicate
            merged_sections = await self.merge_discoveries(tier1_sections, tier2_sections, tier3_sections)
            logging.info(f"[Merge] After deduplication: {len(merged_sections)} unique sections")

            # Step 3: Activate and validate each section
            logging.info("[Activation] Starting section activation and validation")
            for section in merged_sections:
                try:
                    if section.activation_required:
                        success = await self.activate_and_validate_section(section)
                        if not success:
                            logging.warning(
                                f"Section {section.content_type.value} activation/validation failed"
                            )
                            continue
                    else:
                        # Just validate (already visible)
                        await self.validate_section_has_content(section)

                    # Check for lazy loading if section has items
                    if section.contains_items and section.content_type in [
                        ContentType.VENDORS, ContentType.COOKIES
                    ]:
                        await self.handle_section_lazy_loading(section)

                    # Log results
                    if section.contains_items:
                        logging.info(
                            f"  ✓ {section.content_type.value}: "
                            f"{section.item_count_after_activation} items found"
                        )
                    else:
                        logging.warning(f"  ✗ {section.content_type.value}: no items found")

                except Exception as e:
                    logging.error(f"Failed to activate/validate {section.content_type.value}: {e}")
                    result.errors.append(f"{section.content_type.value}: {str(e)}")
                    continue

            # Step 4: Organize by content type
            for section in merged_sections:
                if section.contains_items:  # Only keep sections with content
                    if section.content_type == ContentType.CATEGORIES and not result.categories_section:
                        result.categories_section = section
                    elif section.content_type == ContentType.VENDORS and not result.vendors_section:
                        result.vendors_section = section
                    elif section.content_type == ContentType.COOKIES and not result.cookies_section:
                        result.cookies_section = section
                    elif section.content_type == ContentType.PURPOSES and not result.purposes_section:
                        result.purposes_section = section

            result.sections = [s for s in merged_sections if s.contains_items]

            # Final summary
            logging.info(
                f"[SectionDiscovery] Complete: "
                f"Categories={'✓' if result.categories_section else '✗'} "
                f"({result.categories_section.item_count_after_activation if result.categories_section else 0}), "
                f"Vendors={'✓' if result.vendors_section else '✗'} "
                f"({result.vendors_section.item_count_after_activation if result.vendors_section else 0}), "
                f"Cookies={'✓' if result.cookies_section else '✗'} "
                f"({result.cookies_section.item_count_after_activation if result.cookies_section else 0})"
            )

        except Exception as e:
            logging.error(f"Section discovery pipeline failed: {e}")
            result.errors.append(f"Discovery pipeline error: {str(e)}")

        finally:
            result.discovery_duration_ms = int((time.time() - start_time) * 1000)
            logging.info(f"[SectionDiscovery] Completed in {result.discovery_duration_ms}ms")

        return result

    async def discover_tier1_aria(self) -> List[DiscoveredSection]:
        """
        Tier 1: ARIA Semantic Discovery.

        Searches for standard ARIA roles and patterns:
        - [role="tablist"] > [role="tab"]
        - [aria-expanded]
        - Buttons with semantic text matching

        Returns:
            List of discovered sections
        """
        discovered = []

        try:
            # 1. Find tab structures [role="tablist"] > [role="tab"]
            tablists = await self.modal_locator.locator('[role="tablist"]').all()
            for tablist in tablists:
                tabs = await tablist.locator('[role="tab"]').all()

                for tab in tabs:
                    try:
                        # Get tab text
                        tab_text = await tab.inner_text(timeout=1000)
                        tab_text = tab_text.strip()

                        if len(tab_text) < 2:
                            continue

                        # Classify content type from text
                        content_type = classify_from_keywords(tab_text)

                        if content_type == ContentType.UNKNOWN:
                            continue

                        # Find associated tabpanel
                        aria_controls = await tab.get_attribute("aria-controls")
                        tabpanel_locator = None
                        confidence = 0.85

                        if aria_controls:
                            tabpanel_locator = f'[role="tabpanel"][id="{aria_controls}"]'
                            confidence = 1.0  # Perfect ARIA structure

                        activation_selector = await get_selector_for_locator(tab)

                        section = DiscoveredSection(
                            section_type=SectionType.TAB,
                            content_type=content_type,
                            locator=tabpanel_locator,
                            activation_required=True,
                            activation_locator=activation_selector,
                            discovery_method=DiscoveryMethod.ARIA_SEMANTIC,
                            confidence=confidence,
                            metadata={"tab_text": tab_text, "aria_controls": aria_controls}
                        )
                        discovered.append(section)

                    except Exception as e:
                        logging.debug(f"Error processing tab: {e}")
                        continue

            # 2. Find accordion structures [aria-expanded]
            accordions = await self.modal_locator.locator('[aria-expanded]').all()
            for accordion in accordions:
                try:
                    is_expanded = await accordion.get_attribute("aria-expanded")
                    button_text = await accordion.inner_text(timeout=1000)
                    button_text = button_text.strip()

                    if len(button_text) < 3:
                        continue

                    content_type = classify_from_keywords(button_text)

                    if content_type == ContentType.UNKNOWN:
                        continue

                    # Find content panel (aria-controls or next sibling)
                    aria_controls = await accordion.get_attribute("aria-controls")
                    content_locator = None
                    confidence = 0.75

                    if aria_controls:
                        content_locator = f"#{aria_controls}"
                        confidence = 0.9

                    activation_selector = await get_selector_for_locator(accordion)

                    section = DiscoveredSection(
                        section_type=SectionType.ACCORDION,
                        content_type=content_type,
                        locator=content_locator,
                        activation_required=(is_expanded == "false"),
                        activation_locator=activation_selector,
                        discovery_method=DiscoveryMethod.ARIA_SEMANTIC,
                        confidence=confidence,
                        metadata={"button_text": button_text, "currently_expanded": is_expanded}
                    )
                    discovered.append(section)

                except Exception as e:
                    logging.debug(f"Error processing accordion: {e}")
                    continue

            # 3. Find buttons with specific text patterns
            keyword_patterns = {
                ContentType.CATEGORIES: [
                    "Categories", "Catégories", "Finalités", "Purposes",
                    "Categorias", "Kategorien", "Objectifs"
                ],
                ContentType.VENDORS: [
                    "Vendors", "Partenaires", "Partners", "Fournisseurs",
                    "IAB", "Providers", "Anbieter"
                ],
                ContentType.COOKIES: [
                    "Cookies", "Cookie List", "Liste des cookies",
                    "Cookie-Liste"
                ],
                ContentType.PURPOSES: [
                    "Purposes", "Finalités", "Objectives", "Objectifs"
                ]
            }

            for content_type, keywords in keyword_patterns.items():
                for keyword in keywords:
                    try:
                        # Search for buttons with this keyword (case-insensitive)
                        buttons = await self.modal_locator.locator(
                            f'button:has-text("{keyword}")'
                        ).all()

                        # Also search role="button"
                        role_buttons = await self.modal_locator.locator(
                            f'[role="button"]:has-text("{keyword}")'
                        ).all()

                        all_buttons = buttons + role_buttons

                        for button in all_buttons:
                            # Check if already discovered
                            button_text = await button.inner_text(timeout=1000)
                            if any(
                                s.metadata.get("button_text", "").lower() == button_text.lower()
                                for s in discovered
                            ):
                                continue

                            activation_selector = await get_selector_for_locator(button)

                            section = DiscoveredSection(
                                section_type=SectionType.TAB,
                                content_type=content_type,
                                locator=None,  # Will be determined after activation
                                activation_required=True,
                                activation_locator=activation_selector,
                                discovery_method=DiscoveryMethod.ARIA_SEMANTIC,
                                confidence=0.8,
                                metadata={"button_text": button_text}
                            )
                            discovered.append(section)

                    except Exception as e:
                        logging.debug(f"Error searching for keyword '{keyword}': {e}")
                        continue

        except Exception as e:
            logging.error(f"Tier 1 ARIA discovery failed: {e}")

        return discovered

    async def discover_tier2_visual(self) -> List[DiscoveredSection]:
        """
        Tier 2: Visual Pattern Analysis.

        Detects UI patterns through structure analysis:
        - Horizontal button groupings (tabs)
        - Chevron icons + text (accordions)
        - Repeated structures (lists)
        - Toggle/checkbox groupings

        Returns:
            List of discovered sections
        """
        discovered = []

        try:
            # 1. Detect horizontal tab navigation
            nav_containers = await self.modal_locator.locator(
                'nav, [class*="tab"], [class*="navigation"], [role="navigation"]'
            ).all()

            for nav in nav_containers:
                try:
                    buttons = await nav.locator('button, a, [role="button"]').all()

                    if len(buttons) >= 2:
                        # Check if horizontally aligned
                        if await are_horizontally_aligned(buttons):
                            for button in buttons:
                                text = await button.inner_text(timeout=1000)
                                text = text.strip()

                                if len(text) < 2:
                                    continue

                                content_type = classify_from_keywords(text)

                                if content_type == ContentType.UNKNOWN:
                                    continue

                                activation_selector = await get_selector_for_locator(button)

                                section = DiscoveredSection(
                                    section_type=SectionType.TAB,
                                    content_type=content_type,
                                    locator=None,
                                    activation_required=True,
                                    activation_locator=activation_selector,
                                    discovery_method=DiscoveryMethod.VISUAL_PATTERN,
                                    confidence=0.7,
                                    metadata={"button_text": text, "nav_container": True}
                                )
                                discovered.append(section)

                except Exception as e:
                    logging.debug(f"Error processing navigation container: {e}")
                    continue

            # 2. Detect accordion pattern: chevron icon + text header
            potential_accordions = await self.modal_locator.locator(
                '[class*="chevron"], [class*="arrow"], [class*="expand"], '
                '[class*="accordion"], svg[class*="icon"]'
            ).all()

            for element in potential_accordions:
                try:
                    # Get parent (usually the button)
                    parent = element.locator('xpath=..')
                    text = await parent.inner_text(timeout=1000)
                    text = text.strip()

                    if len(text) > 5:  # Has meaningful text
                        content_type = classify_from_keywords(text)

                        if content_type == ContentType.UNKNOWN:
                            continue

                        activation_selector = await get_selector_for_locator(parent)

                        section = DiscoveredSection(
                            section_type=SectionType.ACCORDION,
                            content_type=content_type,
                            locator=None,
                            activation_required=True,
                            activation_locator=activation_selector,
                            discovery_method=DiscoveryMethod.VISUAL_PATTERN,
                            confidence=0.6,
                            metadata={"button_text": text, "has_chevron": True}
                        )
                        discovered.append(section)

                except Exception as e:
                    logging.debug(f"Error processing accordion element: {e}")
                    continue

            # 3. Detect repeated structures (vendor/cookie lists)
            containers = await self.modal_locator.locator(
                'ul, ol, div[class*="list"], [role="list"]'
            ).all()

            for container in containers[:10]:  # Limit to first 10 to avoid performance issues
                try:
                    # Get direct children
                    children = await container.locator('> *').all()

                    if len(children) >= 5:  # Threshold for "list"
                        # Check similarity
                        if await are_similar_elements(children):
                            # Sample first element to determine content type
                            sample_text = await children[0].inner_text(timeout=1000)
                            content_type = classify_from_keywords(sample_text)

                            # If still unknown, check for indicators
                            if content_type == ContentType.UNKNOWN:
                                # Check for toggles (vendors/categories)
                                has_toggles = await container.locator(
                                    'input[type="checkbox"], [role="switch"]'
                                ).count() > 0

                                # Check for privacy links (vendors)
                                has_privacy_links = await container.locator(
                                    'a[href*="privacy"]'
                                ).count() > 0

                                if has_toggles and has_privacy_links:
                                    content_type = ContentType.VENDORS
                                elif has_toggles:
                                    content_type = ContentType.CATEGORIES
                                else:
                                    # Check for cookie indicators
                                    if await has_cookie_indicators(children[0]):
                                        content_type = ContentType.COOKIES

                            if content_type != ContentType.UNKNOWN:
                                container_selector = await get_selector_for_locator(container)

                                section = DiscoveredSection(
                                    section_type=SectionType.LIST,
                                    content_type=content_type,
                                    locator=container_selector,
                                    activation_required=False,  # Already visible
                                    activation_locator=None,
                                    discovery_method=DiscoveryMethod.VISUAL_PATTERN,
                                    confidence=0.65,
                                    metadata={"item_count": len(children), "has_toggles": has_toggles}
                                )
                                discovered.append(section)

                except Exception as e:
                    logging.debug(f"Error processing container: {e}")
                    continue

            # 4. Detect toggle groupings (categories without clear sections)
            toggle_groups = await self.modal_locator.locator(
                'fieldset, [role="group"]'
            ).all()

            for group in toggle_groups:
                try:
                    toggles = await group.locator(
                        'input[type="checkbox"], [role="switch"]'
                    ).all()

                    if len(toggles) >= 2:  # At least 2 toggles = likely categories
                        group_selector = await get_selector_for_locator(group)

                        section = DiscoveredSection(
                            section_type=SectionType.LIST,
                            content_type=ContentType.CATEGORIES,
                            locator=group_selector,
                            activation_required=False,
                            activation_locator=None,
                            discovery_method=DiscoveryMethod.VISUAL_PATTERN,
                            confidence=0.7,
                            metadata={"toggle_count": len(toggles)}
                        )
                        discovered.append(section)

                except Exception as e:
                    logging.debug(f"Error processing toggle group: {e}")
                    continue

        except Exception as e:
            logging.error(f"Tier 2 Visual discovery failed: {e}")

        return discovered

    async def discover_tier3_yaml(self) -> List[DiscoveredSection]:
        """
        Tier 3: YAML Fallback.

        Uses existing audit_selectors.yml patterns as fallback.

        Returns:
            List of discovered sections
        """
        discovered = []

        try:
            # Determine confidence based on CMP type
            if not self.cmp_type:
                confidence_base = 0.4
            else:
                confidence_base = 0.6

            # Try categories
            categories_config = self.yaml_patterns.get("categories", {})
            if self.cmp_type:
                patterns = categories_config.get(self.cmp_type) or categories_config.get("generic", {})
            else:
                patterns = categories_config.get("generic", {})

            if patterns:
                container_selector = patterns.get("container")
                if container_selector:
                    try:
                        # Try to find container within modal
                        container = self.modal_locator.locator(container_selector).first
                        is_visible = await container.is_visible(timeout=1000)

                        # If not found, check if modal itself matches the selector
                        if not is_visible:
                            # Check if modal is the container
                            modal_classes = await self.modal_locator.get_attribute("class") or ""
                            # Simple check if container selector might match the modal
                            if "axeptio" in container_selector and "axeptio" in modal_classes.lower():
                                is_visible = True
                                container_selector = None  # Use modal directly

                        if is_visible:
                            section = DiscoveredSection(
                                section_type=SectionType.LIST,
                                content_type=ContentType.CATEGORIES,
                                locator=container_selector,  # None means use modal directly
                                activation_required=False,
                                activation_locator=None,
                                discovery_method=DiscoveryMethod.YAML_FALLBACK,
                                confidence=confidence_base,
                                metadata={"yaml_pattern": "categories", "cmp_type": self.cmp_type}
                            )
                            discovered.append(section)
                            logging.debug(f"[Tier3-YAML] Found categories container (locator={container_selector})")
                    except Exception as e:
                        logging.debug(f"[Tier3-YAML] Categories discovery error: {e}")

            # Try vendors
            vendors_config = self.yaml_patterns.get("vendors", {})
            if self.cmp_type:
                patterns = vendors_config.get(self.cmp_type) or vendors_config.get("generic", {})
            else:
                patterns = vendors_config.get("generic", {})

            if patterns:
                # Check for tab button first
                tab_button = patterns.get("tab_button")
                container_selector = patterns.get("container")

                if tab_button:
                    # Vendors in a tab
                    section = DiscoveredSection(
                        section_type=SectionType.TAB,
                        content_type=ContentType.VENDORS,
                        locator=container_selector,
                        activation_required=True,
                        activation_locator=tab_button,
                        discovery_method=DiscoveryMethod.YAML_FALLBACK,
                        confidence=confidence_base,
                        metadata={"yaml_pattern": "vendors", "cmp_type": self.cmp_type}
                    )
                    discovered.append(section)
                elif container_selector:
                    # Vendors directly visible
                    try:
                        container = self.modal_locator.locator(container_selector).first
                        if await container.is_visible(timeout=1000):
                            section = DiscoveredSection(
                                section_type=SectionType.LIST,
                                content_type=ContentType.VENDORS,
                                locator=container_selector,
                                activation_required=False,
                                activation_locator=None,
                                discovery_method=DiscoveryMethod.YAML_FALLBACK,
                                confidence=confidence_base,
                                metadata={"yaml_pattern": "vendors", "cmp_type": self.cmp_type}
                            )
                            discovered.append(section)
                    except Exception:
                        pass

            # Try cookies
            cookies_config = self.yaml_patterns.get("cookies", {})
            if self.cmp_type:
                patterns = cookies_config.get(self.cmp_type) or cookies_config.get("generic", {})
            else:
                patterns = cookies_config.get("generic", {})

            if patterns:
                container_selector = patterns.get("container")
                if container_selector:
                    try:
                        container = self.modal_locator.locator(container_selector).first
                        if await container.is_visible(timeout=1000):
                            section = DiscoveredSection(
                                section_type=SectionType.LIST,
                                content_type=ContentType.COOKIES,
                                locator=container_selector,
                                activation_required=False,
                                activation_locator=None,
                                discovery_method=DiscoveryMethod.YAML_FALLBACK,
                                confidence=confidence_base,
                                metadata={"yaml_pattern": "cookies", "cmp_type": self.cmp_type}
                            )
                            discovered.append(section)
                    except Exception:
                        pass

        except Exception as e:
            logging.error(f"Tier 3 YAML discovery failed: {e}")

        return discovered

    async def merge_discoveries(
        self,
        tier1: List[DiscoveredSection],
        tier2: List[DiscoveredSection],
        tier3: List[DiscoveredSection]
    ) -> List[DiscoveredSection]:
        """
        Merge results from all tiers, removing duplicates.

        Priority: ARIA > Visual > YAML

        Args:
            tier1: ARIA semantic discoveries
            tier2: Visual pattern discoveries
            tier3: YAML fallback discoveries

        Returns:
            Merged and deduplicated list of sections
        """
        all_sections = tier1 + tier2 + tier3

        # Group by content_type
        by_content = {}
        for section in all_sections:
            content_key = section.content_type
            if content_key not in by_content:
                by_content[content_key] = []
            by_content[content_key].append(section)

        # Deduplicate within each content type
        merged = []
        for content_type, sections in by_content.items():
            if len(sections) == 1:
                merged.append(sections[0])
            else:
                # Group overlapping sections
                groups = []
                for section in sections:
                    # Check if overlaps with existing group
                    added = False
                    for group in groups:
                        if await self._sections_overlap(section, group[0]):
                            group.append(section)
                            added = True
                            break

                    if not added:
                        groups.append([section])

                # From each group, keep highest confidence
                method_priority = {
                    DiscoveryMethod.ARIA_SEMANTIC: 3,
                    DiscoveryMethod.VISUAL_PATTERN: 2,
                    DiscoveryMethod.YAML_FALLBACK: 1
                }

                for group in groups:
                    # Sort by confidence, then by method priority
                    best = max(
                        group,
                        key=lambda s: (s.confidence, method_priority[s.discovery_method])
                    )

                    # Mark as hybrid if merged from multiple methods
                    if len(group) > 1:
                        best.discovery_method = DiscoveryMethod.HYBRID
                        best.metadata["merged_from"] = [s.discovery_method.value for s in group]

                    merged.append(best)

        return merged

    async def _sections_overlap(
        self,
        s1: DiscoveredSection,
        s2: DiscoveredSection
    ) -> bool:
        """
        Check if two sections refer to same DOM element.

        Args:
            s1: First section
            s2: Second section

        Returns:
            True if sections overlap
        """
        # If both have activation_locator, compare those
        if s1.activation_locator and s2.activation_locator:
            # Normalize selectors for comparison
            if s1.activation_locator == s2.activation_locator:
                return True

        # If both have content locator, compare those
        if s1.locator and s2.locator:
            if s1.locator == s2.locator:
                return True

        # If metadata has button text, compare
        text1 = s1.metadata.get("button_text", "").lower().strip()
        text2 = s2.metadata.get("button_text", "").lower().strip()
        if text1 and text2 and text1 == text2:
            return True

        return False

    async def activate_and_validate_section(self, section: DiscoveredSection) -> bool:
        """
        Click tab/accordion and validate content appears.

        Args:
            section: Section to activate

        Returns:
            True if activation succeeded and content validated
        """
        if not section.activation_locator:
            logging.warning(
                f"Section {section.content_type.value} requires activation but no locator provided"
            )
            # Try to validate anyway - section might not actually need activation
            return await self.validate_section_has_content(section)

        try:
            # Find activation button
            button = self.modal_locator.locator(section.activation_locator).first

            # Check if already active
            is_active = await self.is_section_active(button)
            if is_active:
                logging.debug(f"Section {section.content_type.value} already active")
                section.was_activated = False
                return await self.validate_section_has_content(section)

            # Click to activate
            logging.info(
                f"Activating section: {section.content_type.value} via {section.activation_locator}"
            )
            await button.click(timeout=3000)
            section.was_activated = True

            # Wait for content to appear (adaptive based on CMP)
            wait_ms = 1000 if self.cmp_type in ["onetrust", "cookiebot"] else 1500
            await self.page.wait_for_timeout(wait_ms)

            # Wait for animations
            await self.page.wait_for_timeout(500)

            # Validate content appeared
            has_content = await self.validate_section_has_content(section)

            if has_content:
                logging.info(f"✓ Section {section.content_type.value} activated successfully")
            else:
                logging.warning(f"✗ Section {section.content_type.value} activated but no content found")

            return has_content

        except Exception as e:
            logging.error(f"Failed to activate section {section.content_type.value}: {e}")
            section.metadata["activation_error"] = str(e)

            # Even if activation fails, try to validate - the section might already be visible
            logging.debug(f"Attempting validation despite activation failure for {section.content_type.value}")
            try:
                has_content = await self.validate_section_has_content(section)
                if has_content:
                    logging.info(f"✓ Section {section.content_type.value} validated despite activation failure")
                    section.activation_required = False  # Update since it didn't need activation
                    return True
            except Exception as val_error:
                logging.debug(f"Validation also failed: {val_error}")

            return False

    async def is_section_active(self, button: Locator) -> bool:
        """
        Check if tab/accordion is already active.

        Args:
            button: Button locator to check

        Returns:
            True if section is active
        """
        try:
            # Check aria-selected (tabs)
            aria_selected = await button.get_attribute("aria-selected")
            if aria_selected == "true":
                return True

            # Check aria-expanded (accordions)
            aria_expanded = await button.get_attribute("aria-expanded")
            if aria_expanded == "true":
                return True

            # Check class patterns
            classes = await button.get_attribute("class") or ""
            if any(c in classes for c in ["active", "selected", "current"]):
                return True

            return False
        except Exception:
            return False

    async def validate_section_has_content(self, section: DiscoveredSection) -> bool:
        """
        Validate that section contains extractable items.

        Updates section.item_count_after_activation and section.contains_items

        Args:
            section: Section to validate

        Returns:
            True if section has items
        """
        try:
            # Determine where to look for items
            if section.locator:
                search_locator = self.modal_locator.locator(section.locator).first
            else:
                search_locator = self.modal_locator

            # Content-specific validation
            items = []

            if section.content_type == ContentType.CATEGORIES:
                # Look for category indicators: toggles, labels
                items = await search_locator.locator(
                    'input[type="checkbox"], [role="switch"], '
                    '[class*="category"], [class*="purpose"]'
                ).all()

            elif section.content_type == ContentType.VENDORS:
                # Look for vendor indicators
                items = await search_locator.locator(
                    '[class*="vendor"], [class*="partner"], '
                    'a[href*="privacy"], [data-vendor-id]'
                ).all()

            elif section.content_type == ContentType.COOKIES:
                # Look for cookie indicators
                items = await search_locator.locator(
                    '[class*="cookie"], td, tr, [data-cookie-name]'
                ).all()

            else:
                # Generic: any list items, checkboxes, or repeated structures
                items = await search_locator.locator(
                    'li, tr, [role="listitem"], input[type="checkbox"]'
                ).all()

            section.item_count_after_activation = len(items)
            section.contains_items = len(items) > 0

            logging.debug(
                f"Section {section.content_type.value} validation: "
                f"found {len(items)} items, contains_items={section.contains_items}"
            )

            return section.contains_items

        except Exception as e:
            logging.warning(f"Section validation failed for {section.content_type.value}: {e}")
            section.item_count_after_activation = 0
            section.contains_items = False
            return False

    async def handle_section_lazy_loading(self, section: DiscoveredSection) -> bool:
        """
        Handle lazy loading for sections (quick validation only).

        Scrolls once to trigger initial load and validates items appear.
        Full lazy loading happens during extraction.

        Args:
            section: Section to check for lazy loading

        Returns:
            True if lazy loading detected
        """
        if not section.locator:
            return False

        try:
            container = self.modal_locator.locator(section.locator).first

            # Get initial item count
            initial_count = section.item_count_after_activation

            # Scroll to bottom once
            await container.evaluate("el => el.scrollTop = el.scrollHeight")
            await self.page.wait_for_timeout(1000)

            # Re-validate
            await self.validate_section_has_content(section)

            # Did items increase?
            if section.item_count_after_activation > initial_count:
                logging.debug(
                    f"Lazy loading detected in {section.content_type.value}: "
                    f"{initial_count} -> {section.item_count_after_activation} items"
                )
                section.metadata["has_lazy_loading"] = True
                return True

            return False

        except Exception as e:
            logging.debug(f"Lazy loading check failed for {section.content_type.value}: {e}")
            return False

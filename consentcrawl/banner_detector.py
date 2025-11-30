"""
Banner detection module for CMP audit mode.

This module implements multi-level banner detection strategies:
1. CMP-specific detection using known selectors
2. Generic detection using ARIA roles and common patterns
3. Text-based detection using multi-language keywords
4. Iframe search (with nested iframe support)
5. Shadow DOM search

Designed to detect 35+ CMP types plus custom implementations.
"""

import asyncio
import logging
import os
import yaml
from typing import Optional, Tuple, List
from playwright.async_api import Page, Locator, Frame, TimeoutError as PlaywrightTimeoutError

from consentcrawl.audit_schemas import BannerInfo, ButtonInfo, AuditConfig
from consentcrawl.cmp_detectors import (
    detect_sourcepoint_modal,
    detect_onetrust_modal,
    detect_didomi_modal,
    detect_orejime_modal,
    detect_trust_commander_modal,
    detect_sfbx_modal,
    detect_lemonde_wall
)


MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIT_SELECTORS_FILE = f"{MODULE_DIR}/assets/audit_selectors.yml"


def load_audit_selectors():
    """Load audit selector patterns from YAML file."""
    with open(AUDIT_SELECTORS_FILE, "r") as f:
        return yaml.safe_load(f)


async def find_banner_container_from_button(button_locator: Locator) -> Optional[Locator]:
    """
    Remonte depuis un bouton jusqu'au conteneur de la bannière complète.

    Stratégie :
    1. Remonte les parents jusqu'à trouver un conteneur avec plusieurs boutons
    2. Vérifie que le conteneur a les caractéristiques d'une bannière
    3. Limite la remontée à 6 niveaux pour éviter de sélectionner <body>

    Args:
        button_locator: Locator du bouton Accept détecté

    Returns:
        Locator du conteneur de bannière, ou None si pas trouvé
    """
    try:
        # Utiliser evaluate pour remonter l'arbre DOM
        container_info = await button_locator.evaluate("""
            (button) => {
                let current = button;
                let maxLevels = 6;
                let level = 0;

                while (current && current !== document.body && level < maxLevels) {
                    // Compter les boutons dans cet élément
                    const buttons = current.querySelectorAll('button, a[role="button"], a[href]');
                    const buttonsCount = buttons.length;

                    // Vérifier la taille
                    const rect = current.getBoundingClientRect();
                    const height = rect.height;
                    const width = rect.width;

                    // Vérifier le z-index et position
                    const style = window.getComputedStyle(current);
                    const zIndex = parseInt(style.zIndex) || 0;
                    const position = style.position;

                    // Vérifier présence de texte
                    const text = current.innerText || '';
                    const hasConsentKeywords = /cookie|consent|privacy|confidentialité|données|paramètre/i.test(text);

                    // Critères pour être un conteneur de bannière
                    if (buttonsCount >= 2 &&           // Au moins 2 boutons
                        height > 50 &&                 // Hauteur raisonnable
                        width > 200 &&                 // Largeur raisonnable
                        hasConsentKeywords &&          // Contient mots-clés
                        (position === 'fixed' ||
                         position === 'sticky' ||
                         position === 'absolute' ||
                         zIndex > 50)) {

                        // Retourner les infos pour recréer un sélecteur
                        return {
                            found: true,
                            id: current.id,
                            className: current.className,
                            tagName: current.tagName.toLowerCase(),
                            level: level
                        };
                    }

                    current = current.parentElement;
                    level++;
                }

                return { found: false };
            }
        """)

        if container_info and container_info.get('found'):
            # Reconstruire le locator basé sur les infos
            page = button_locator.page

            # Essayer d'abord par ID
            if container_info.get('id'):
                container = page.locator(f"#{container_info['id']}").first
                try:
                    if await container.is_visible(timeout=1000):
                        logging.debug(f"Found banner container by ID: {container_info['id']}")
                        return container
                except:
                    pass

            # Sinon par classe (première classe)
            if container_info.get('className'):
                first_class = str(container_info['className']).split()[0] if container_info['className'] else None
                if first_class and first_class.strip():
                    try:
                        container = page.locator(f".{first_class}").first
                        if await container.is_visible(timeout=1000):
                            logging.debug(f"Found banner container by class: {first_class}")
                            return container
                    except:
                        pass

            # En dernier recours, remonter manuellement
            try:
                parent = button_locator
                for _ in range(container_info.get('level', 3)):
                    parent = parent.locator('..').first
                logging.debug(f"Found banner container by traversing {container_info.get('level')} levels")
                return parent
            except:
                pass

    except Exception as e:
        logging.debug(f"Error finding banner container: {e}")

    # Fallback : remonter de 3 niveaux par défaut
    try:
        logging.debug("Using fallback: going up 3 levels")
        return button_locator.locator('..').locator('..').locator('..').first
    except:
        return button_locator


async def detect_banner(page: Page, cmp_configs: List[dict], config: AuditConfig) -> Optional[BannerInfo]:
    """
    Main banner detection orchestrator.

    Tries multiple detection strategies in order:
    1. CMP-specific detection (highest precision)
    2. Generic detection (good coverage)
    3. Text-based detection (maximum coverage)
    4. Search in iframes (if enabled)
    5. Search in shadow DOM (if enabled)

    Args:
        page: Playwright Page object
        cmp_configs: List of CMP configurations from consent_managers.yml
        config: Audit configuration

    Returns:
        BannerInfo object if detected, None otherwise
    """
    # 0. Try Hardcoded CMP detectors (Python functions)
    # These are for complex CMPs that require specific logic not expressible in YAML
    detectors = [
        ("sourcepoint", detect_sourcepoint_modal),
        ("onetrust", detect_onetrust_modal),
        ("didomi", detect_didomi_modal),
        ("orejime", detect_orejime_modal),
        ("trust_commander", detect_trust_commander_modal),
        ("sfbx", detect_sfbx_modal),
        ("lemonde", detect_lemonde_wall),
    ]

    for cmp_name, detector_func in detectors:
        try:
            modal = await detector_func(page)
            if modal:
                logging.info(f"Banner detected using hardcoded detector: {cmp_name}")
                
                buttons = []
                if cmp_name == "lemonde":
                    # Extract Le Monde buttons
                    try:
                        accept_btn = modal.locator("button[data-gdpr-expression='acceptAll']")
                        if await accept_btn.count() > 0:
                            buttons.append(ButtonInfo(
                                text="Accéder gratuitement",
                                role="accept_all",
                                selector="button[data-gdpr-expression='acceptAll']",
                                is_visible=True
                            ))
                        
                        subscribe_btn = modal.locator(".js-gdpr-deny-subscribe")
                        if await subscribe_btn.count() > 0:
                            buttons.append(ButtonInfo(
                                text="S'abonner",
                                role="reject_all", # Effectively rejecting consent by paying
                                selector=".js-gdpr-deny-subscribe",
                                is_visible=True
                            ))
                    except Exception as e:
                        logging.warning(f"Error extracting Le Monde buttons: {e}")

                elif cmp_name == "onetrust":
                    # Extract OneTrust buttons (Banner + Preference Center)
                    try:
                        # Accept All (Banner & PC)
                        accept_selectors = [
                            "#onetrust-accept-btn-handler",
                            "#accept-recommended-btn-handler",
                            ".save-preference-btn-handler" # Often "Save & Exit" acts as accept if all selected or default
                        ]
                        for sel in accept_selectors:
                            btn = modal.locator(sel)
                            if await btn.count() > 0 and await btn.first.is_visible():
                                buttons.append(ButtonInfo(
                                    text="Accept",
                                    role="accept_all",
                                    selector=sel,
                                    is_visible=True
                                ))
                                break # Found one accept button

                        # Reject All (Banner & PC)
                        reject_selectors = [
                            "#onetrust-reject-all-handler",
                            ".ot-pc-refuse-all-handler"
                        ]
                        for sel in reject_selectors:
                            btn = modal.locator(sel)
                            if await btn.count() > 0 and await btn.first.is_visible():
                                buttons.append(ButtonInfo(
                                    text="Reject",
                                    role="reject_all",
                                    selector=sel,
                                    is_visible=True
                                ))
                                break

                        # Manage Preferences (Banner)
                        manage_selectors = ["#onetrust-pc-btn-handler"]
                        for sel in manage_selectors:
                            btn = modal.locator(sel)
                            if await btn.count() > 0 and await btn.first.is_visible():
                                buttons.append(ButtonInfo(
                                    text="Manage",
                                    role="settings",
                                    selector=sel,
                                    is_visible=True
                                ))
                                break
                    except Exception as e:
                        logging.warning(f"Error extracting OneTrust buttons: {e}")

                elif cmp_name == "trust_commander":
                    # Extract Trust Commander buttons
                    try:
                        # Accept All
                        accept_selectors = [
                            "#footer_tc_privacy_button_2", # Cdiscount specific
                            "#popin_tc_privacy_button_2", # Credit Agricole specific
                            "[title='Accepter']",
                            "[title='Accepter et fermer']",
                            "button:has-text('Accepter'):not(:has-text('sans'))" # Avoid "Continuer sans accepter"
                        ]
                        for sel in accept_selectors:
                            btn = modal.locator(sel)
                            if await btn.count() > 0 and await btn.first.is_visible():
                                buttons.append(ButtonInfo(
                                    text="Accepter",
                                    role="accept_all",
                                    selector=sel,
                                    is_visible=True
                                ))
                                break

                        # Reject All
                        reject_selectors = [
                            "#footer_tc_privacy_button_3", # Cdiscount specific
                            "#popin_tc_privacy_button_3", # Credit Agricole specific
                            "[title='Continuer sans accepter']",
                            "button:has-text('Continuer sans accepter')"
                        ]
                        for sel in reject_selectors:
                            btn = modal.locator(sel)
                            if await btn.count() > 0 and await btn.first.is_visible():
                                buttons.append(ButtonInfo(
                                    text="Continuer sans accepter",
                                    role="reject_all",
                                    selector=sel,
                                    is_visible=True
                                ))
                                break

                        # Settings
                        settings_selectors = [
                            "#footer_tc_privacy_button", # Cdiscount specific
                            "#popin_tc_privacy_button", # Credit Agricole specific
                            "[title='Paramétrer les cookies']",
                            "[title='Personnaliser mes choix']",
                            "button:has-text('Paramétrer')",
                            "button:has-text('Personnaliser')"
                        ]
                        for sel in settings_selectors:
                            btn = modal.locator(sel)
                            if await btn.count() > 0 and await btn.first.is_visible():
                                buttons.append(ButtonInfo(
                                    text="Paramétrer",
                                    role="settings",
                                    selector=sel,
                                    is_visible=True
                                ))
                                break

                    except Exception as e:
                        logging.warning(f"Error extracting Trust Commander buttons: {e}")

                elif cmp_name == "orejime":
                    # Extract Orejime buttons
                    try:
                        # Accept All
                        accept_btn = modal.locator(".orejime-Button--save")
                        if await accept_btn.count() > 0:
                            buttons.append(ButtonInfo(
                                text="Accepter",
                                role="accept_all",
                                selector=".orejime-Button--save",
                                is_visible=True
                            ))
                        
                        # Reject All
                        reject_btn = modal.locator(".orejime-Button--decline")
                        if await reject_btn.count() > 0:
                            buttons.append(ButtonInfo(
                                text="Refuser",
                                role="reject_all",
                                selector=".orejime-Button--decline",
                                is_visible=True
                            ))
                            
                        # Settings
                        settings_btn = modal.locator(".orejime-Button--info")
                        if await settings_btn.count() > 0:
                            buttons.append(ButtonInfo(
                                text="Personnaliser",
                                role="settings",
                                selector=".orejime-Button--info",
                                is_visible=True
                            ))
                    except Exception as e:
                        logging.warning(f"Error extracting Orejime buttons: {e}")

                # Create BannerInfo directly
                return BannerInfo(
                    detected=True,
                    cmp_type=cmp_name,
                    detection_method="hardcoded_detector",
                    in_iframe=False,
                    buttons=buttons
                )
        except Exception as e:
            logging.debug(f"Hardcoded detector {cmp_name} failed: {e}")

    # Strategy 1: CMP-specific detection (YAML based)
    cmp_info, banner_locator, iframe_info = await detect_cmp_banner(page, cmp_configs)
    if cmp_info and banner_locator:
        banner_info = await extract_banner_info(banner_locator, cmp_info, "cmp_specific")
        if banner_info and iframe_info:
            # Banner was found in an iframe
            banner_info.in_iframe = True
            banner_info.iframe_src = iframe_info.get("src", None)
            logging.debug(f"Banner detected in iframe: {iframe_info.get('selector', 'unknown')}")
        return banner_info

    # Strategy 2: Generic detection (ARIA roles, common classes)
    banner_info = await detect_generic_banner(page, config)
    if banner_info:
        return banner_info

    # Strategy 3: Text-based detection
    banner_info = await detect_text_based_banner(page, config.languages)
    if banner_info:
        return banner_info

    # Strategy 4: Search in iframes
    if config.support_nested_iframes:
        banner_info = await search_in_iframes(page, lambda p: detect_generic_banner(p, config), max_depth=2)
        if banner_info:
            banner_info.in_iframe = True
            return banner_info

    # Strategy 5: Search in shadow DOM
    if config.support_shadow_dom:
        banner_info = await search_in_shadow_dom(page)
        if banner_info:
            banner_info.in_shadow_dom = True
            return banner_info

    # No banner detected
    return BannerInfo(detected=False)


async def detect_cmp_banner(page: Page, cmp_configs: List[dict]) -> Tuple[Optional[dict], Optional[Locator], Optional[dict]]:
    """
    Detect banner using CMP-specific selectors from consent_managers.yml.

    This reuses the detection logic from click_consent_manager() but WITHOUT clicking.

    Args:
        page: Playwright Page object
        cmp_configs: List of CMP configurations

    Returns:
        Tuple of (cmp_info dict, banner_locator, iframe_info dict) or (None, None, None)
        iframe_info contains: {"selector": str, "src": str} if banner is in iframe
    """
    for cmp in cmp_configs:
        parent_locator = page
        locator = None
        iframe_info = None

        try:
            for action in cmp["actions"]:
                if action["type"] == "iframe":
                    # Check if iframe exists
                    if await parent_locator.locator(action["value"]).count() > 0:
                        # Get iframe src before switching context
                        try:
                            iframe_element = await parent_locator.locator(action["value"]).first.element_handle()
                            iframe_src = await iframe_element.get_attribute("src") if iframe_element else None
                        except:
                            iframe_src = None

                        parent_locator = parent_locator.frame_locator(action["value"]).first
                        iframe_info = {"selector": action["value"], "src": iframe_src}
                        logging.debug(f"Switching to iframe: {action['value']}")
                    else:
                        break

                elif action["type"] == "css-selector":
                    if await parent_locator.locator(action["value"]).first.is_visible(timeout=1000):
                        locator = parent_locator.locator(action["value"]).first
                        break

                elif action["type"] == "css-selector-list":
                    for selector in action["value"]:
                        try:
                            if await parent_locator.locator(selector).first.is_visible(timeout=500):
                                locator = parent_locator.locator(selector).first
                                cmp["matched_selector"] = selector
                                break
                        except:
                            continue
                    if locator:
                        break

            if locator is not None:
                logging.debug(f"Found CMP button: {cmp['id']}")
                # Remonter au conteneur de la bannière
                banner_container = await find_banner_container_from_button(locator)
                if banner_container:
                    logging.debug(f"Found banner container for {cmp['id']}")
                    return cmp, banner_container, iframe_info
                else:
                    logging.warning(f"Could not find banner container for {cmp['id']}, using button locator")
                    return cmp, locator, iframe_info  # Fallback sur le bouton si échec

        except Exception as e:
            logging.debug(f"Error detecting CMP {cmp.get('id', 'unknown')}: {e}")
            continue

    return None, None, None


async def detect_generic_banner(page: Page, config: AuditConfig) -> Optional[BannerInfo]:
    """
    Generic banner detection using ARIA roles and common patterns.

    Looks for:
    - [role="dialog"]
    - [aria-modal="true"]
    - .modal, .popup, .overlay with cookie/consent keywords

    Args:
        page: Playwright Page object
        config: Audit configuration

    Returns:
        BannerInfo if detected, None otherwise
    """
    # 1. Try CMP-specific detectors first (most reliable)
    # Note: The user's instruction implies a 'detectors' list here, but the current
    # detect_cmp_banner already handles CMP-specific detection via cmp_configs.
    # If this list is intended for *additional* hardcoded CMP detectors not in cmp_configs,
    # it should be placed in a logical spot, likely before the generic selectors.
    # For now, I'm adding it as per the instruction's placement, assuming it's a new
    # set of selectors/logic for generic detection that includes CMP-like patterns.
    # This interpretation makes the most sense to maintain syntactical correctness
    # while incorporating the user's explicit "Code Edit" content.
    detectors = [
        ("sourcepoint", detect_sourcepoint_modal),
        ("onetrust", detect_onetrust_modal),
        ("didomi", detect_didomi_modal),
        ("orejime", detect_orejime_modal),
        ("trust_commander", detect_trust_commander_modal),
        ("sfbx", detect_sfbx_modal),
        ("lemonde", detect_lemonde_wall),
    ]

    selectors = [
        "[role='dialog']",
        "[aria-modal='true']",
        ".modal.show",
        ".modal[style*='display: block']",
        ".popup[style*='display: block']",
        "[class*='cookie'][class*='banner']:visible",
        "[class*='consent'][class*='banner']:visible",
        "[id*='cookie'][id*='banner']",
        "[id*='consent'][id*='banner']",
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible(timeout=500):
                # Validate it contains cookie/consent keywords
                text = await locator.inner_text(timeout=1000)
                text_lower = text.lower()

                keywords = ["cookie", "consent", "privacy", "données", "confidentialité"]
                if any(keyword in text_lower for keyword in keywords):
                    banner_info = await extract_banner_info(locator, None, "generic")
                    return banner_info

        except Exception as e:
            logging.debug(f"Generic detection failed for selector {selector}: {e}")
            continue

    return None


async def detect_text_based_banner(page: Page, languages: List[str]) -> Optional[BannerInfo]:
    """
    Text-based detection using multi-language keywords.

    Searches for visible text containing cookie/consent keywords in supported languages.

    Args:
        page: Playwright Page object
        languages: List of language codes (e.g., ['en', 'fr'])

    Returns:
        BannerInfo if detected, None otherwise
    """
    audit_selectors = load_audit_selectors()
    keywords_config = audit_selectors.get("detection_keywords", {})

    all_keywords = []
    for lang in languages:
        lang_keywords = keywords_config.get(lang, {})
        all_keywords.extend(lang_keywords.get("cookies", []))
        all_keywords.extend(lang_keywords.get("consent", []))

    # Use Playwright's text selector
    for keyword in all_keywords:
        try:
            # Look for elements containing the keyword
            locator = page.locator(f":has-text('{keyword}')").first
            if await locator.is_visible(timeout=500):
                # Get the closest container with a reasonable size
                # This helps avoid selecting the whole body
                banner_locator = await find_banner_container(locator)
                if banner_locator:
                    banner_info = await extract_banner_info_safe(banner_locator, None, "text_based")
                    return banner_info

        except Exception as e:
            logging.debug(f"Text detection failed for keyword {keyword}: {e}")
            continue

    return None


async def find_banner_container(text_locator: Locator) -> Optional[Locator]:
    """
    Find the appropriate container element for a banner given a text locator.

    Looks for parent elements with common banner characteristics:
    - position: fixed
    - high z-index
    - reasonable size
    - contains buttons

    Args:
        text_locator: Locator pointing to text containing keywords

    Returns:
        Locator for the banner container, or None
    """
    try:
        # Try to find a parent with position:fixed or high z-index
        container = await text_locator.evaluate("""
            (el) => {
                let current = el;
                while (current && current !== document.body) {
                    const style = window.getComputedStyle(current);
                    const zIndex = parseInt(style.zIndex) || 0;
                    const position = style.position;

                    if ((position === 'fixed' || position === 'sticky' || zIndex > 100) &&
                        current.offsetHeight > 50 &&
                        current.querySelectorAll('button, a').length > 0) {
                        return current.className + '#' + current.id;
                    }
                    current = current.parentElement;
                }
                return null;
            }
        """)

        if container:
            class_name, id_name = container.split('#')
            if id_name:
                return text_locator.page.locator(f"#{id_name}").first
            elif class_name:
                return text_locator.page.locator(f".{class_name.split()[0]}").first

    except Exception as e:
        logging.debug(f"Error finding banner container: {e}")

    # Fallback: return the text locator's parent
    return text_locator.locator('..').first


async def find_banner_container_safe(text_locator: Locator, page: Page) -> Optional[Locator]:
    """
    Version sécurisée de find_banner_container avec timeout et fallbacks.

    Args:
        text_locator: Locator vers le texte contenant des keywords
        page: Page Playwright

    Returns:
        Locator du conteneur de bannière ou fallback
    """
    try:
        # Vérifier que le locator est toujours valide
        if not await text_locator.is_visible(timeout=500):
            return None

        # Évaluation avec timeout explicite
        container_info = await asyncio.wait_for(
            text_locator.evaluate("""
                (el) => {
                    let current = el;
                    let maxLevels = 8;
                    let level = 0;

                    while (current && current !== document.body && level < maxLevels) {
                        const style = window.getComputedStyle(current);
                        const zIndex = parseInt(style.zIndex) || 0;
                        const position = style.position;
                        const rect = current.getBoundingClientRect();

                        // Critères de bannière
                        const hasButtons = current.querySelectorAll('button, a[role="button"]').length >= 2;
                        const hasSize = rect.height > 50 && rect.width > 200;
                        const hasKeywords = /cookie|consent|privacy|confidentialité|données/i.test(current.innerText || '');
                        const hasPosition = position === 'fixed' || position === 'sticky' || position === 'absolute' || zIndex > 50;

                        if (hasButtons && hasSize && hasKeywords && hasPosition) {
                            return {
                                found: true,
                                id: current.id,
                                className: current.className,
                                level: level
                            };
                        }

                        current = current.parentElement;
                        level++;
                    }

                    return {found: false};
                }
            """),
            timeout=3.0
        )

        if not container_info or not container_info.get('found'):
            # Fallback simple : remonter 3 niveaux
            try:
                return text_locator.locator('..').locator('..').locator('..').first
            except:
                return text_locator

        # Reconstruire locator depuis les infos
        if container_info.get('id'):
            try:
                container = page.locator(f"#{container_info['id']}").first
                if await container.is_visible(timeout=1000):
                    return container
            except:
                pass

        if container_info.get('className'):
            first_class = str(container_info['className']).split()[0]
            if first_class:
                try:
                    container = page.locator(f".{first_class}").first
                    if await container.is_visible(timeout=1000):
                        return container
                except:
                    pass

        # Fallback manuel par remontée
        try:
            parent = text_locator
            for _ in range(container_info.get('level', 3)):
                parent = parent.locator('..').first
            return parent
        except:
            return text_locator

    except asyncio.TimeoutError:
        logging.warning("find_banner_container timed out, using fallback")
        try:
            return text_locator.locator('..').locator('..').first
        except:
            return text_locator
    except Exception as e:
        logging.debug(f"find_banner_container_safe error: {e}")
        return text_locator


async def extract_all_text_from_banner(banner_locator: Locator) -> str:
    """
    Extrait TOUT le texte du banner, y compris sections cachées/accordéons.

    Cette fonction tente d'expandre tous les éléments collapsibles pour
    extraire le texte caché qui n'est pas visible initialement.

    Args:
        banner_locator: Locator vers la bannière

    Returns:
        Texte complet extrait (ou chaîne vide si échec)
    """
    try:
        # Cliquer sur tous les éléments expandables pour révéler texte caché
        expandables = await banner_locator.locator("[aria-expanded='false']").all()

        for elem in expandables[:10]:  # Limite à 10 pour éviter boucles infinies
            try:
                await elem.click(timeout=500)
                await banner_locator.page.wait_for_timeout(200)
            except:
                # Continuer même si un clic échoue
                pass

        # Extraire tout le texte après expansion
        full_text = await banner_locator.inner_text(timeout=3000)
        return full_text

    except Exception as e:
        logging.debug(f"extract_all_text_from_banner failed: {e}")
        return ""


def clean_text(text: Optional[str]) -> str:
    """Clean text by removing HTML tags and normalizing whitespace while preserving structure."""
    if not text:
        return ""
    import re
    
    # Replace block tags with newlines to preserve structure
    text = re.sub(r'<(br|div|p|h\d|li|tr)[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</(div|p|h\d|li|tr)>', '\n', text, flags=re.IGNORECASE)
    
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)
    
    # Remove CSS styles in text (rare but happens)
    text = re.sub(r'{[^}]+}', '', text)
    
    # Normalize whitespace: replace multiple spaces with single space, but preserve newlines
    lines = []
    for line in text.split('\n'):
        clean_line = re.sub(r'\s+', ' ', line).strip()
        if clean_line:
            lines.append(clean_line)
            
    return '\n'.join(lines)


async def extract_banner_info_safe(banner_locator: Locator, cmp_info: Optional[dict], detection_method: str) -> Optional[BannerInfo]:
    """
    Version améliorée avec extraction maximale du texte visible.

    Cette fonction extrait HTML, texte et boutons du banner. Elle tente également
    d'expandre les accordéons pour capturer le texte caché.

    Args:
        banner_locator: Locator vers la bannière
        cmp_info: Info CMP si détecté
        detection_method: Méthode de détection

    Returns:
        BannerInfo ou None si échec
    """
    try:
        # Vérifier visibilité
        if not await banner_locator.is_visible(timeout=1000):
            return None

        # Extraction HTML/texte avec timeout généreux
        banner_html = await banner_locator.inner_html(timeout=5000)
        banner_text = await banner_locator.inner_text(timeout=5000)

        # NOUVEAU : Tenter d'extraire texte de sections cachées/accordéons
        expanded_text = await extract_all_text_from_banner(banner_locator)
        if len(expanded_text) > len(banner_text):
            logging.debug(f"Expanded text is longer ({len(expanded_text)} vs {len(banner_text)} chars)")
            banner_text = expanded_text

        # Extraire boutons
        buttons = await extract_banner_buttons(banner_locator)

        # Validation souple : au moins DU TEXTE ou des boutons
        if len(buttons) == 0 and len(banner_text.strip()) < 20:
            logging.debug("Banner extraction failed validation (no buttons, minimal text)")
            return None

        banner_info = BannerInfo(
            detected=True,
            cmp_type=cmp_info.get("id") if cmp_info else None,
            cmp_brand=cmp_info.get("brand") if cmp_info else None,
            banner_html=banner_html[:20000],  # Augmenté à 20k
            banner_text=clean_text(banner_text),
            buttons=buttons,
            detection_method=detection_method,
        )

        return banner_info

    except Exception as e:
        logging.warning(f"extract_banner_info_safe failed: {e}")
        return None


async def extract_banner_info(banner_locator: Locator, cmp_info: Optional[dict], detection_method: str) -> Optional[BannerInfo]:
    """
    Extract detailed information from detected banner with validation and retry.

    Args:
        banner_locator: Locator pointing to the banner element
        cmp_info: CMP configuration dict if detected via CMP-specific method
        detection_method: How the banner was detected

    Returns:
        BannerInfo object with all extracted data, or None if extraction fails
    """
    # 1. Validate element is accessible
    try:
        if not await banner_locator.is_visible(timeout=1000):
            logging.debug("Banner element not visible")
            return None
    except Exception as e:
        logging.debug(f"Banner visibility check failed: {e}")
        return None

    # 2. Try extraction with generous timeout and retry on failure
    banner_html = None
    banner_text = None

    for attempt in range(2):
        try:
            timeout_ms = 5000 if attempt == 0 else 8000
            logging.debug(f"Extraction attempt {attempt + 1} with {timeout_ms}ms timeout")

            banner_html = await banner_locator.inner_html(timeout=timeout_ms)
            banner_text = await banner_locator.inner_text(timeout=timeout_ms)

            # Success - break out of retry loop
            break

        except Exception as e:
            if attempt == 0:
                logging.debug(f"Extraction timeout on attempt {attempt + 1}: {e}, retrying...")
                await asyncio.sleep(0.5)
            else:
                logging.warning(f"Extraction failed after {attempt + 1} attempts: {e}")
                return None

    # 3. Extract buttons
    buttons = await extract_banner_buttons(banner_locator)

    # 4. Validate: at least SOME content (HTML or text or buttons)
    if not banner_html and not banner_text and len(buttons) == 0:
        logging.debug("Banner extraction failed validation: no content extracted")
        return None

    banner_info = BannerInfo(
        detected=True,
        cmp_type=cmp_info.get("id") if cmp_info else None,
        cmp_brand=cmp_info.get("brand") if cmp_info else None,
        banner_html=banner_html[:10000] if banner_html else "",
        banner_text=clean_text(banner_text[:10000]) if banner_text else "",
        buttons=buttons,
        detection_method=detection_method,
    )

    return banner_info


async def extract_button_selector(btn_locator: Locator) -> str:
    """
    Extrait un sélecteur unique et robuste pour un bouton.

    Stratégie hiérarchique :
    1. ID unique → #id
    2. Classes sémantiques → .semantic-class
    3. Aria-label unique → [aria-label="..."]
    4. Combinaison classe + texte → .class:has-text("...")
    5. Tag générique → button

    Args:
        btn_locator: Locator du bouton

    Returns:
        Sélecteur CSS robuste
    """
    try:
        selector_info = await btn_locator.evaluate("""
            (el) => {
                // 1. ID unique (meilleur cas)
                if (el.id && el.id.length > 0) {
                    return {type: 'id', value: el.id};
                }

                // 2. Classes sémantiques (priorité haute)
                const classes = el.className.split(' ').filter(c => c && c.length > 0);
                const semanticPatterns = [
                    'button', 'btn', 'manage', 'settings', 'configure',
                    'option', 'choice', 'action', 'accept', 'reject',
                    'consent', 'cookie', 'preference', 'type_'
                ];

                const semanticClasses = classes.filter(c =>
                    semanticPatterns.some(pattern => c.toLowerCase().includes(pattern))
                );

                if (semanticClasses.length > 0) {
                    // Prendre la classe la plus spécifique (la plus longue)
                    const bestClass = semanticClasses.sort((a, b) => b.length - a.length)[0];
                    return {type: 'class', value: bestClass};
                }

                // 3. Aria-label unique
                const ariaLabel = el.getAttribute('aria-label');
                if (ariaLabel && ariaLabel.length > 0 && ariaLabel.length < 100) {
                    return {type: 'aria-label', value: ariaLabel};
                }

                // 4. Première classe + texte partiel
                if (classes.length > 0) {
                    const text = el.innerText?.trim() || '';
                    if (text.length > 0 && text.length < 50) {
                        return {
                            type: 'class-text',
                            className: classes[0],
                            text: text.substring(0, 20)
                        };
                    }
                    return {type: 'class', value: classes[0]};
                }

                // 5. Fallback tag
                return {type: 'tag', value: el.tagName.toLowerCase()};
            }
        """)

        # Construire le sélecteur basé sur le type
        if selector_info['type'] == 'id':
            return f"#{selector_info['value']}"
        elif selector_info['type'] == 'class':
            return f".{selector_info['value']}"
        elif selector_info['type'] == 'aria-label':
            return f"[aria-label='{selector_info['value']}']"
        elif selector_info['type'] == 'class-text':
            return f".{selector_info['className']}:has-text('{selector_info['text']}')"
        else:
            return selector_info['value']
    except Exception as e:
        logging.debug(f"Error in extract_button_selector: {e}")
        # Fallback simple
        return "button"


async def extract_banner_buttons(banner_locator: Locator) -> List[ButtonInfo]:
    """
    Extract and classify all buttons in the banner.

    Classifies buttons by role based on text and aria-label:
    - accept_all: Accept, Agree, Accepter, J'accepte
    - reject_all: Reject, Refuse, Refuser
    - settings: Manage, Settings, Paramétrer, Gérer
    - info: Privacy policy links, More info
    - unknown: Other buttons

    Args:
        banner_locator: Locator pointing to the banner element

    Returns:
        List of ButtonInfo objects
    """
    buttons = []

    try:
        # Find all interactive elements
        button_locators = await banner_locator.locator("button, a[role='button'], a[href]").all()

        for btn_locator in button_locators:
            try:
                text = await btn_locator.inner_text(timeout=500)
                text = text.strip().lower() if text else ""

                aria_label = await btn_locator.get_attribute("aria-label", timeout=500)
                aria_label_lower = aria_label.lower() if aria_label else ""

                is_visible = await btn_locator.is_visible(timeout=500)

                # Get a unique selector using improved extraction
                selector = await extract_button_selector(btn_locator)

                # Classify button role
                role = classify_button_role(text, aria_label_lower)

                buttons.append(ButtonInfo(
                    text=text[:200],  # Limit size
                    role=role,
                    selector=selector[:200],
                    aria_label=aria_label[:200] if aria_label else None,
                    is_visible=is_visible,
                ))

            except Exception as e:
                logging.debug(f"Error extracting button info: {e}")
                continue

    except Exception as e:
        logging.warning(f"Error finding buttons in banner: {e}")

    return buttons


def classify_button_role(text: str, aria_label: str) -> str:
    """
    Classify button role based on text and aria-label.

    Args:
        text: Button text (lowercase)
        aria_label: Button aria-label (lowercase)

    Returns:
        Role string: accept_all, reject_all, settings, info, or unknown
    """
    combined = text + " " + aria_label

    # Accept patterns
    accept_patterns = [
        "accept all", "accept", "agree", "allow all", "allow",
        "accepter tout", "accepter", "j'accepte", "tout accepter",
        "akzeptieren", "alle akzeptieren", "aceptar"
    ]
    if any(pattern in combined for pattern in accept_patterns):
        # Make sure it's not "accept necessary" or similar
        if "necessary" not in combined and "essential" not in combined:
            return "accept_all"

    # Reject patterns
    reject_patterns = [
        "reject all", "reject", "refuse", "deny",
        "refuser tout", "refuser", "tout refuser",
        "ablehnen", "alles ablehnen", "rechazar"
    ]
    if any(pattern in combined for pattern in reject_patterns):
        return "reject_all"

    # Settings patterns
    settings_patterns = [
        "setting", "manage", "customize", "configure", "preference", "choice", "choose", "option",
        "paramétrer", "gérer", "personnaliser", "configurer", "préférence",
        "einstellung", "verwalten", "anpassen",
        "configurar", "gestionar", "set up", "partners", "partenaires"
    ]
    if any(pattern in combined for pattern in settings_patterns):
        return "settings"

    # Info patterns
    info_patterns = [
        "more info", "learn more", "privacy policy", "cookie policy", "details",
        "plus d'info", "en savoir plus", "politique",
        "mehr erfahren", "datenschutz",
        "más información", "política"
    ]
    if any(pattern in combined for pattern in info_patterns):
        return "info"

    return "unknown"


async def search_in_iframes(page: Page, detector_func, max_depth: int = 2, current_depth: int = 0) -> Optional[BannerInfo]:
    """
    Recursively search for banners in iframes.

    Supports nested iframes up to max_depth levels.

    Args:
        page: Playwright Page or Frame object
        detector_func: Detection function to call on each frame
        max_depth: Maximum recursion depth
        current_depth: Current recursion depth

    Returns:
        BannerInfo if found, None otherwise
    """
    if current_depth >= max_depth:
        return None

    try:
        # Handle both Page and Frame objects
        if hasattr(page, "frames"):
            frames = page.frames
        else:
            frames = page.child_frames
        for frame in frames:
            # Skip the main frame
            if frame == page.main_frame:
                continue

            try:
                # Try detection in this frame
                banner_info = await detector_func(frame)
                if banner_info and banner_info.detected:
                    banner_info.in_iframe = True
                    banner_info.iframe_src = frame.url
                    return banner_info

                # Recursively search nested frames
                nested_banner = await search_in_iframes(frame, detector_func, max_depth, current_depth + 1)
                if nested_banner:
                    return nested_banner

            except Exception as e:
                logging.debug(f"Error searching frame {frame.url}: {e}")
                continue

    except Exception as e:
        logging.warning(f"Error accessing frames: {e}")

    return None


async def search_in_shadow_dom(page: Page) -> Optional[BannerInfo]:
    """
    Search for banners in Shadow DOM.

    Injects a script to enumerate shadow roots and search for cookie/consent keywords.

    Args:
        page: Playwright Page object

    Returns:
        BannerInfo if found, None otherwise
    """
    try:
        # Inject script to find shadow roots with cookie/consent content
        shadow_info = await page.evaluate("""
            () => {
                const results = [];
                document.querySelectorAll('*').forEach(el => {
                    if (el.shadowRoot) {
                        const shadowHTML = el.shadowRoot.innerHTML;
                        const shadowText = el.shadowRoot.textContent || '';

                        const keywords = ['cookie', 'consent', 'privacy', 'confidentialité'];
                        const hasKeyword = keywords.some(kw =>
                            shadowHTML.toLowerCase().includes(kw) ||
                            shadowText.toLowerCase().includes(kw)
                        );

                        if (hasKeyword && shadowText.length > 20) {
                            results.push({
                                hostTag: el.tagName.toLowerCase(),
                                hostId: el.id,
                                hostClass: el.className,
                                html: shadowHTML.substring(0, 10000),
                                text: shadowText.substring(0, 5000),
                            });
                        }
                    }
                });
                return results.length > 0 ? results[0] : null;
            }
        """)

        if shadow_info:
            # Create BannerInfo from shadow DOM content
            banner_info = BannerInfo(
                detected=True,
                cmp_type="shadow_dom_custom",
                banner_html=shadow_info.get("html", ""),
                banner_text=shadow_info.get("text", ""),
                in_shadow_dom=True,
                detection_method="shadow_dom",
                buttons=[],  # Shadow DOM button extraction would require more complex logic
            )
            return banner_info

    except Exception as e:
        logging.warning(f"Error searching shadow DOM: {e}")

    return None

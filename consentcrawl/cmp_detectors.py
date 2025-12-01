"""
CMP-specific modal detection helpers for ui_explorer.py

These functions handle iframe-based and shadow DOM CMPs that require
special detection logic beyond generic selectors.
"""

import logging
from typing import Optional
from playwright.async_api import Page, Locator, Frame


async def detect_sourcepoint_modal(page: Page) -> Optional[Locator]:
    """
    Sourcepoint-specific modal detection.
    
    Sourcepoint typically uses iframes with IDs like 'sp_message_iframe_xxx'.
    The modal content is usually the entire iframe body.
    
    Args:
        page: Playwright Page object
        
    Returns:
        Modal locator if found, None otherwise
    """
    try:
        # Find Sourcepoint iframes
        sp_frames = [
            f for f in page.frames 
            if 'sp_message_iframe' in f.name or 
               'sourcepoint' in f.url.lower() or
               'sp-prod.net' in f.url.lower() or
               'privacy-mgmt' in f.url.lower()
        ]
        
        logging.debug(f"Found {len(sp_frames)} potential Sourcepoint iframes")
        
        # Prioritize iframes that look like Privacy Manager
        pm_frames = [f for f in sp_frames if "privacy-manager" in f.url]
        other_frames = [f for f in sp_frames if "privacy-manager" not in f.url]
        
        # Check PM frames first
        for frame in pm_frames + other_frames:
            try:
                # Sourcepoint modal selectors
                selectors = [
                    ".message-container",
                    "#sp-message-container",
                    "div[class*='message-stack']",
                    "div[class*='sp_choice']",
                    "div[id*='notice']",
                    ".message-overlay", # Move to end as it might be just a backdrop
                    "body > div",  # Fallback
                ]
                
                for selector in selectors:
                    locator = frame.locator(selector).first
                    if await locator.is_visible(timeout=100):
                        # Verify it's not just the banner (check for PM specific elements)
                        # PM usually has stacks, tabs, or "Privacy Manager" title
                        pm_indicators = [
                            ".tcfv2-stack",
                            ".message-component.stack-row",
                            ".pm-sub-p",
                            "button:has-text('Purposes')",
                            "button:has-text('Vendors')"
                        ]
                        
                        is_pm = False
                        for indicator in pm_indicators:
                            if await frame.locator(indicator).count() > 0:
                                is_pm = True
                                break
                        
                        if is_pm or "privacy-manager" in frame.url:
                            logging.info(f"✓ Sourcepoint modal found in iframe {frame.url} with selector: {selector}")
                            return locator
                        else:
                             logging.debug(f"  Ignored potential Sourcepoint modal in {frame.url} (not PM-like)")
                            
            except Exception as e:
                logging.debug(f"Error checking Sourcepoint iframe: {e}")
                continue
                            
            except Exception as e:
                logging.debug(f"Error checking Sourcepoint iframe: {e}")
                continue
                
    except Exception as e:
        logging.debug(f"Sourcepoint detection failed: {e}")
    
    return None


async def detect_onetrust_modal(page: Page) -> Optional[Locator]:
    """
    OneTrust-specific modal detection.
    
    OneTrust can use iframes or direct DOM insertion.
    Often creates iframe dynamically after button click.
    
    Args:
        page: Playwright Page object
        
    Returns:
        Modal locator if found, None otherwise
    """
    try:
        # Strategy 1: Check main page first (OneTrust often injects directly)
        main_selectors = [
            "#onetrust-pc-sdk",
            "#ot-pc-content",
            ".ot-sdk-container",
            "[class*='onetrust']",
        ]
        
        for selector in main_selectors:
            try:
                locator = page.locator(selector).first
                if await locator.is_visible(timeout=100):
                    logging.info(f"✓ OneTrust modal found in main page: {selector}")
                    return locator
            except:
                continue
        
        # Strategy 2: Check for OneTrust iframes
        ot_frames = [
            f for f in page.frames
            if 'onetrust' in f.url.lower() or 
               'cookielaw' in f.url.lower() or
               'ot-' in f.name.lower()
        ]
        
        logging.debug(f"Found {len(ot_frames)} potential OneTrust iframes")
        
        for frame in ot_frames:
            try:
                iframe_selectors = [
                    "#onetrust-pc-sdk",
                    ".ot-pc-content",
                    "div[role='dialog']",
                ]
                
                for selector in iframe_selectors:
                    locator = frame.locator(selector).first
                    if await locator.is_visible(timeout=100):
                        logging.info(f"✓ OneTrust modal found in iframe: {selector}")
                        return locator
                        
            except Exception as e:
                logging.debug(f"Error checking OneTrust iframe: {e}")
                continue
                
    except Exception as e:
        logging.debug(f"OneTrust detection failed: {e}")
    
    return None


async def detect_didomi_modal(page: Page) -> Optional[Locator]:
    """
    Didomi-specific modal detection.
    
    Didomi often uses shadow DOM (#didomi-host) or iframes.
    For settings/preferences, it uses .didomi-consent-popup-preferences.
    
    Args:
        page: Playwright Page object
        
    Returns:
        Modal locator if found, None otherwise
    """
    try:
        # Strategy 0: Check for preferences modal (Priority for UI exploration)
        preferences_selectors = [
            ".didomi-consent-popup-preferences",
            ".didomi-popup-preferences"
        ]
        
        for selector in preferences_selectors:
            try:
                locator = page.locator(selector).first
                if await locator.is_visible(timeout=100):
                    logging.info(f"✓ Didomi preferences modal found: {selector}")
                    return locator
            except:
                continue

        # Strategy 1: Check shadow DOM
        try:
            # Didomi uses #didomi-host with shadow root
            shadow_modal = page.locator("#didomi-host").locator("div[role='dialog']").first
            if await shadow_modal.is_visible(timeout=100):
                logging.info("✓ Didomi modal found in shadow DOM")
                return shadow_modal
        except:
            pass
        
        # Strategy 2: Check main page (Notice modal)
        main_selectors = [
            "#didomi-notice",
            ".didomi-popup",
            "[class*='didomi']",
        ]
        
        for selector in main_selectors:
            try:
                locator = page.locator(selector).first
                if await locator.is_visible(timeout=100):
                    logging.info(f"✓ Didomi modal found in main page: {selector}")
                    return locator
            except:
                continue
        
        # Strategy 3: Check iframes
        frames = getattr(page, 'frames', getattr(page, 'child_frames', []))
        didomi_frames = [
            f for f in frames
            if 'didomi' in f.url.lower() or 'didomi' in f.name.lower()
        ]
        
        logging.debug(f"Found {len(didomi_frames)} potential Didomi iframes")
        
        for frame in didomi_frames:
            try:
                locator = frame.locator("div[role='dialog'], .didomi-popup").first
                if await locator.is_visible(timeout=100):
                    logging.info("✓ Didomi modal found in iframe")
                    return locator
            except Exception as e:
                logging.debug(f"Error checking Didomi iframe: {e}")
                continue
                
    except Exception as e:
        logging.debug(f"Didomi detection failed: {e}")
    
    return None


async def detect_trust_commander_modal(page: Page) -> Optional[Locator]:
    """
    Trust Commander-specific modal detection.
    
    Trust Commander uses an iframe (#privacy-iframe) for the privacy center.
    
    Args:
        page: Playwright Page object
        
    Returns:
        Modal locator if found, None otherwise
    """
    try:
        # Strategy 1: Check for Privacy Center iframe FIRST (after clicking settings)
        frames = getattr(page, 'frames', getattr(page, 'child_frames', []))
        
        for frame in frames:
            try:
                # Check if this is the Trust Commander privacy center iframe
                if "privacy-center" in frame.url or "privacy-iframe" in frame.name:
                    # Look for modal content inside iframe
                    selectors = [
                        ".modal-content",
                        ".modal-body",
                        "[role='dialog']",
                        "body"  # Fallback to iframe body
                    ]
                    
                    for selector in selectors:
                        try:
                            locator = frame.locator(selector).first
                            count = await frame.locator(selector).count()
                            if count > 0:
                                logging.info(f"✓ Trust Commander Privacy Center found in iframe: {frame.url}")
                                return locator
                        except:
                            continue
            except:
                continue
        
        # Strategy 2: Fall back to main page banner (initial detection)
        main_selectors = [
            "#footer_tc_privacy",
            "#tc-privacy-wrapper",
            ".tc-privacy-banner",
            "#popin_tc_privacy"
        ]
        
        for selector in main_selectors:
            if await page.locator(selector).count() > 0:
                locator = page.locator(selector).first
                if await locator.is_visible():
                    logging.info(f"✓ Trust Commander banner found in main page: {selector}")
                    return locator
                
    except Exception as e:
        logging.debug(f"Trust Commander detection failed: {e}")
    
    return None


async def detect_sfbx_modal(page: Page) -> Optional[Locator]:
    """
    SFBX-specific modal detection.
    
    SFBX uses an iframe (#appconsent > iframe) with srcdoc.
    
    Args:
        page: Playwright Page object
        
    Returns:
        Modal locator if found, None otherwise
    """
    try:
        # SFBX iframe selector
        iframe_selector = "#appconsent > iframe"
        
        # Check if iframe exists
        if await page.locator(iframe_selector).count() > 0:
            # Get element handle to access content frame
            element_handle = await page.locator(iframe_selector).element_handle()
            if element_handle:
                frame = await element_handle.content_frame()
                if frame:
                    # Look for key elements inside iframe
                    selectors = [
                        ".modal__container",
                        ".page__content",
                        ".button__openPrivacyCenter",
                        "body"
                    ]
                    
                    for selector in selectors:
                        try:
                            candidates = await frame.locator(selector).all()
                            for candidate in candidates:
                                if await candidate.is_visible():
                                    logging.info(f"✓ SFBX modal found and visible in iframe with selector: {selector}")
                                    return candidate
                            # If no visible candidate found, but candidates exist, return the first one (fallback)
                            if candidates:
                                logging.info(f"✓ SFBX modal found (hidden) in iframe with selector: {selector}")
                                return candidates[0]
                        except Exception as e:
                            logging.debug(f"Error checking SFBX selector {selector}: {e}")
                            
    except Exception as e:
        logging.debug(f"SFBX detection failed: {e}")
    
    return None


async def detect_lemonde_wall(page: Page) -> Optional[Locator]:
    """
    Le Monde custom CMP detection.
    
    Args:
        page: Playwright Page object
        
    Returns:
        Modal locator if found, None otherwise
    """
    try:
        # Le Monde wall selector
        wall_selector = ".gdpr-lmd-wall"
        
        if await page.locator(wall_selector).count() > 0:
            wall = page.locator(wall_selector).first
            if await wall.is_visible():
                logging.info("✓ Le Monde wall found and visible")
                return wall
                
    except Exception as e:
        logging.debug(f"Le Monde detection failed: {e}")
    
    return None



async def detect_orejime_modal(page: Page) -> Optional[Locator]:
    """
    Orejime-specific modal detection.
    
    Orejime modal uses .orejime-Modal class.
    
    Args:
        page: Playwright Page object
        
    Returns:
        Modal locator if found, None otherwise
    """
    try:
        # Priority 1: Settings modal (after clicking "Personnaliser")
        settings_selectors = [
            ".orejime-Modal",  # The settings modal
            ".orejime-AppList",  # The app list inside settings
        ]
        
        for selector in settings_selectors:
            if await page.locator(selector).count() > 0:
                element = page.locator(selector).first
                if await element.is_visible():
                    logging.info(f"✓ Orejime settings modal found with selector: {selector}")
                    return element
        
        # Priority 2: Initial notice (before clicking settings)
        notice_selectors = [
            ".orejime-Notice",  # The visible notice/modal
            ".orejime-Modal-form",
            "[class*='orejime'][class*='Modal']",
        ]
        
        for selector in notice_selectors:
            if await page.locator(selector).count() > 0:
                # Check if it has content (buttons)
                element = page.locator(selector).first
                if await element.is_visible():
                    logging.info(f"✓ Orejime notice found with selector: {selector}")
                    return element
                elif await element.locator("button").count() > 0:
                     # Even if not visible, if it has buttons, it's likely the right container
                    logging.info(f"✓ Orejime notice found (hidden but has buttons) with selector: {selector}")
                    return element
                    
    except Exception as e:
        logging.debug(f"Orejime detection failed: {e}")
    
    return None


async def detect_modal_in_all_frames(page: Page, cmp_type: Optional[str], detect_func) -> Optional[Locator]:
    """
    Search for modal in ALL frames (main page + all iframes).
    
    This is a fallback when CMP-specific detection fails.
    Searches up to 10 frames to avoid performance issues.
    
    Args:
        page: Playwright Page object
        cmp_type: CMP type if known
        detect_func: Detection function to call on each frame
        
    Returns:
        Modal locator if found, None otherwise
    """
    try:
        all_frames = page.frames[:10]  # Limit to 10 frames for performance
        logging.debug(f"Searching for modal in {len(all_frames)} frames")
        
        for i, frame in enumerate(all_frames):
            try:
                # Try to detect modal in this frame
                modal = await detect_func(frame, cmp_type, None)  # Pass None for config
                if modal:
                    frame_info = f"frame {i}: {frame.url[:50] if frame.url else 'about:blank'}"
                    logging.info(f"✓ Modal found in {frame_info}")
                    return modal
            except Exception as e:
                logging.debug(f"Error checking frame {i}: {e}")
                continue
                
    except Exception as e:
        logging.debug(f"All-frames detection failed: {e}")
    
    return None

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
               'sp-prod.net' in f.url.lower()
        ]
        
        logging.debug(f"Found {len(sp_frames)} potential Sourcepoint iframes")
        
        for frame in sp_frames:
            try:
                # Sourcepoint modal selectors
                selectors = [
                    "div[class*='message-stack']",
                    "div[class*='sp_choice']",
                    "div[id*='notice']",
                    "body > div",  # Sometimes the entire body is the modal
                ]
                
                for selector in selectors:
                    locator = frame.locator(selector).first
                    if await locator.is_visible(timeout=100):
                        logging.info(f"✓ Sourcepoint modal found in iframe with selector: {selector}")
                        return locator
                        
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
    
    Args:
        page: Playwright Page object
        
    Returns:
        Modal locator if found, None otherwise
    """
    try:
        # Strategy 1: Check shadow DOM
        try:
            # Didomi uses #didomi-host with shadow root
            shadow_modal = page.locator("#didomi-host").locator("div[role='dialog']").first
            if await shadow_modal.is_visible(timeout=100):
                logging.info("✓ Didomi modal found in shadow DOM")
                return shadow_modal
        except:
            pass
        
        # Strategy 2: Check main page
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
        didomi_frames = [
            f for f in page.frames
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

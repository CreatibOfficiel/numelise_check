"""
Navigation State Machine for Cookie Consent Modal Navigation.

This module implements an intelligent state machine for navigating cookie consent modals
with adaptive timeouts, multi-strategy retry, and validation at each transition.

Architecture:
    Phase 1: BANNER_DETECTED - Initial state with detected banner
    Phase 2: MODAL_OPENING - Attempting to open settings modal
    Phase 3: MODAL_READY - Modal open and ready for extraction
    Phase 4: EXTRACTION_COMPLETE - Data extraction finished
    FAILED - Terminal state for failures

The state machine uses adaptive timeouts based on CMP type and validates
readiness (not just visibility) before proceeding.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Tuple

from playwright.async_api import Page, Locator, TimeoutError as PlaywrightTimeoutError

from consentcrawl.audit_schemas import BannerInfo, AuditConfig


class NavigationState(Enum):
    """States in the modal navigation flow."""
    BANNER_DETECTED = "banner_detected"
    MODAL_OPENING = "modal_opening"
    MODAL_READY = "modal_ready"
    EXTRACTION_COMPLETE = "extraction_complete"
    FAILED = "failed"


@dataclass
class NavigationAttempt:
    """Record of a single navigation attempt."""
    strategy: str  # "direct_selector", "aria_label", "text_based", etc.
    success: bool
    error_msg: Optional[str] = None
    duration_ms: int = 0


@dataclass
class NavigationReport:
    """Detailed report of navigation process."""
    initial_state: NavigationState
    final_state: NavigationState
    attempts: List[NavigationAttempt] = field(default_factory=list)
    total_duration_ms: int = 0
    transitions: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def add_transition(self, from_state: NavigationState, to_state: NavigationState):
        """Record a state transition."""
        self.transitions.append(f"{from_state.value} -> {to_state.value}")

    def add_attempt(self, strategy: str, success: bool, error_msg: Optional[str] = None, duration_ms: int = 0):
        """Record a navigation attempt."""
        self.attempts.append(NavigationAttempt(strategy, success, error_msg, duration_ms))

    def add_error(self, error: str):
        """Record an error."""
        self.errors.append(error)


class AdaptiveWaiter:
    """Intelligent timeout management based on CMP type and operation."""

    # CMP-specific timeout configurations (in milliseconds)
    CMP_TIMEOUTS = {
        "didomi": {"modal_open": 2000, "animation": 1500, "lazy_load": 1000},
        "onetrust": {"modal_open": 1000, "animation": 800, "lazy_load": 800},
        "cookiebot": {"modal_open": 1500, "animation": 1000, "lazy_load": 1000},
        "axeptio": {"modal_open": 2500, "animation": 2000, "lazy_load": 1500},
        "sourcepoint": {"modal_open": 1500, "animation": 1200, "lazy_load": 1000},
        "quantcast": {"modal_open": 1500, "animation": 1000, "lazy_load": 1000},
        "usercentrics": {"modal_open": 1800, "animation": 1200, "lazy_load": 1000},
        "sfbx": {"modal_open": 3000, "animation": 3000, "lazy_load": 2000},
        "sfbx-io": {"modal_open": 3000, "animation": 3000, "lazy_load": 2000},
    }

    DEFAULT_TIMEOUTS = {"modal_open": 3000, "animation": 1500, "lazy_load": 1000}

    def __init__(self, cmp_type: Optional[str] = None):
        """
        Initialize adaptive waiter.

        Args:
            cmp_type: CMP type (e.g., "didomi", "onetrust")
        """
        # Normalize CMP type (remove "-cmp" suffix)
        if cmp_type:
            self.cmp_type = cmp_type.replace("-cmp", "").lower()
        else:
            self.cmp_type = None

        # Get timeouts for this CMP or use defaults
        self.timeouts = self.CMP_TIMEOUTS.get(self.cmp_type, self.DEFAULT_TIMEOUTS)

        logging.debug(f"AdaptiveWaiter initialized for CMP: {self.cmp_type}, timeouts: {self.timeouts}")

    async def wait_for_modal_open(self, page: Page):
        """
        Wait for modal opening with CMP-specific timeout.

        Args:
            page: Playwright page
        """
        timeout_ms = self.timeouts["modal_open"]
        logging.debug(f"Waiting {timeout_ms}ms for modal to open")
        await page.wait_for_timeout(timeout_ms)

    async def wait_for_animation_complete(self, page: Page):
        """
        Wait for CSS animations to complete.

        Args:
            page: Playwright page
        """
        timeout_ms = self.timeouts["animation"]
        logging.debug(f"Waiting {timeout_ms}ms for animations to complete")
        await page.wait_for_timeout(timeout_ms)

    async def wait_for_lazy_load(self, page: Page):
        """
        Wait for lazy-loaded content.

        Args:
            page: Playwright page
        """
        timeout_ms = self.timeouts["lazy_load"]
        logging.debug(f"Waiting {timeout_ms}ms for lazy loading")
        await page.wait_for_timeout(timeout_ms)

    def get_modal_detection_timeout(self) -> int:
        """Get timeout for modal detection in milliseconds."""
        return self.timeouts["modal_open"]

    def get_readiness_timeout(self) -> int:
        """Get timeout for readiness validation in milliseconds."""
        return self.timeouts["animation"]


class NavigationStateMachine:
    """
    State machine for intelligent cookie consent modal navigation.

    Orchestrates the 4-phase navigation process:
    1. BANNER_DETECTED - Starting point
    2. MODAL_OPENING - Multi-strategy modal opening attempts
    3. MODAL_READY - Validation that modal is ready
    4. EXTRACTION_COMPLETE - Terminal success state

    Features:
    - Adaptive timeouts based on CMP type
    - Multi-strategy retry with exponential backoff
    - Readiness validation (not just visibility)
    - Detailed navigation reporting
    """

    def __init__(self, page: Page, banner_info: BannerInfo, config: AuditConfig):
        """
        Initialize state machine.

        Args:
            page: Playwright page
            banner_info: Detected banner information
            config: Audit configuration
        """
        self.page = page
        self.banner_info = banner_info
        self.config = config
        self.current_state = NavigationState.BANNER_DETECTED
        self.waiter = AdaptiveWaiter(banner_info.cmp_type)
        self.report = NavigationReport(
            initial_state=NavigationState.BANNER_DETECTED,
            final_state=NavigationState.BANNER_DETECTED
        )
        self.start_time = time.time()

        # Handle iframe context if banner is in iframe
        self.page_context = page
        # Check if banner is in iframe (either by src or by flag)
        if banner_info.in_iframe:
            logging.info(f"Banner is in iframe, will search within iframe context")
            # Note: We'll need to get the frame_locator when clicking
            self.iframe_selector = self._guess_iframe_selector(banner_info)
            if not self.iframe_selector and banner_info.iframe_src:
                # Fallback to src if no selector guessed
                logging.debug("No iframe selector guessed, relying on src if needed")
        else:
            self.iframe_selector = None

    def _guess_iframe_selector(self, banner_info: BannerInfo) -> Optional[str]:
        """Guess iframe selector from CMP type."""
        iframe_selectors = {
            "sourcepoint-cmp": "[id*='sp_message_iframe']",
            "sourcepoint": "[id*='sp_message_iframe']",
            "trustarc": "#truste_cm_frame",
            "onetrust": "#onetrust-consent-sdk iframe",
            "sfbx": "#appconsent > iframe",
            "sfbx-io": "#appconsent > iframe",
        }
        cmp_key = banner_info.cmp_type.replace("-cmp", "") if banner_info.cmp_type else None
        return iframe_selectors.get(cmp_key) or iframe_selectors.get(banner_info.cmp_type)

    async def execute_navigation_flow(self) -> Tuple[Optional[Locator], NavigationReport]:
        """
        Execute complete navigation flow.

        Returns:
            Tuple of (modal_locator, navigation_report)
            - modal_locator: Playwright Locator for modal (None if failed)
            - navigation_report: Detailed navigation report
        """
        logging.info(f"Starting navigation flow from state: {self.current_state.value}")

        try:
            # Phase 2: Attempt modal opening
            modal_locator = await self._attempt_modal_opening()

            if not modal_locator:
                self._transition_to(NavigationState.FAILED)
                self.report.add_error("Modal opening failed after all strategies")
                return None, self._finalize_report()

            # Phase 3: Validate modal readiness
            is_ready = await self._validate_modal_ready(modal_locator)

            if not is_ready:
                self._transition_to(NavigationState.FAILED)
                self.report.add_error("Modal not ready after opening")
                return None, self._finalize_report()

            # Success!
            self._transition_to(NavigationState.MODAL_READY)
            logging.info(f"Navigation successful: modal ready for extraction")

            return modal_locator, self._finalize_report()

        except Exception as e:
            self._transition_to(NavigationState.FAILED)
            self.report.add_error(f"Unexpected error in navigation flow: {str(e)}")
            logging.error(f"Navigation flow failed with exception: {e}")
            return None, self._finalize_report()

    async def _attempt_modal_opening(self) -> Optional[Locator]:
        """
        Attempt to open settings modal using multiple strategies.

        Returns:
            Modal locator if successful, None otherwise
        """
        self._transition_to(NavigationState.MODAL_OPENING)

        # Find settings button
        settings_button = None
        for btn in self.banner_info.buttons:
            if btn.role == "settings":
                settings_button = btn
                break
        
        # Fallback: check for 'info' buttons that might be settings (common in Sourcepoint)
        if not settings_button:
            for btn in self.banner_info.buttons:
                if btn.role == "info":
                    logging.info(f"Using 'info' button as fallback for settings: {btn.text}")
                    settings_button = btn
                    break

        if not settings_button:
            self.report.add_error("No settings button found in banner")
            logging.debug("No settings button detected, skipping modal opening")
            return None

        logging.info(f"Found settings button: selector={settings_button.selector}")

        # Strategy 1: Direct selector from button detection
        modal = await self._try_click_strategy(
            "direct_selector",
            settings_button.selector
        )
        if modal:
            return modal

        # Strategy 2: Aria-label matching (if available)
        if settings_button.aria_label:
            modal = await self._try_click_strategy(
                "aria_label",
                f"[aria-label='{settings_button.aria_label}']"
            )
            if modal:
                return modal

        # Strategy 3: Text-based with button tag
        if settings_button.text:
            modal = await self._try_click_strategy(
                "button_text",
                f"button:has-text('{settings_button.text}')"
            )
            if modal:
                return modal

        # Strategy 4: Text-based with role=button
        if settings_button.text:
            modal = await self._try_click_strategy(
                "role_button_text",
                f"[role='button']:has-text('{settings_button.text}')"
            )
            if modal:
                return modal

        # Strategy 5: Generic text-based (broadest)
        if settings_button.text:
            modal = await self._try_click_strategy(
                "generic_text",
                f":has-text('{settings_button.text}')"
            )
            if modal:
                return modal

        # Strategy 6: Fallback - Try common settings button patterns
        # (for cases where banner has been removed/changed since detection)
        logging.info("[fallback] Trying common settings button patterns as last resort")
        common_patterns = [
            "button:has-text('manage options'):visible",
            "button:has-text('settings'):visible",
            "button:has-text('customize'):visible",
            "[role='button']:has-text('manage'):visible",
            "[aria-label*='manage' i]:visible",
            "[aria-label*='settings' i]:visible",
            "[aria-label*='option' i]:visible",
            ".sp_choice_type_12:visible",  # Sourcepoint specific
        ]

        for pattern in common_patterns:
            try:
                count = await self.page.locator(pattern).count()
                if count > 0:
                    logging.info(f"[fallback] Found {count} elements with pattern: {pattern}")
                    modal = await self._try_click_strategy(
                        "fallback_pattern",
                        pattern,
                        max_retries=1  # Only try once for fallback patterns
                    )
                    if modal:
                        return modal
            except:
                pass

        logging.warning("All click strategies failed (including fallback patterns)")
        return None

    async def _try_click_strategy(self, strategy_name: str, selector: str, max_retries: int = 3) -> Optional[Locator]:
        """
        Try clicking using a specific selector strategy.

        Args:
            strategy_name: Name of the strategy for logging
            selector: CSS selector to try
            max_retries: Maximum click attempts

        Returns:
            Modal locator if successful, None otherwise
        """
        logging.info(f"[{strategy_name}] Starting strategy with selector: {selector}")
        start_time = time.time()

        for attempt in range(max_retries):
            try:
                # Get the correct page context (iframe or main page)
                if self.iframe_selector:
                    # Banner is in iframe, search within iframe
                    try:
                        iframe_count = await self.page.locator(self.iframe_selector).count()
                        logging.debug(f"[{strategy_name}] Found {iframe_count} iframes matching {self.iframe_selector}")
                        if iframe_count > 0:
                            page_context = self.page.frame_locator(self.iframe_selector).first
                            button_locator = page_context.locator(selector).first
                        else:
                            logging.warning(f"[{strategy_name}] Iframe not found, falling back to main page")
                            button_locator = self.page.locator(selector).first
                    except Exception as e:
                        logging.warning(f"[{strategy_name}] Error accessing iframe: {e}, using main page")
                        button_locator = self.page.locator(selector).first
                else:
                    # Banner is in main page
                    button_locator = self.page.locator(selector).first

                # DEBUG: Count matching elements in the correct context
                try:
                    if self.iframe_selector and iframe_count > 0:
                        # Count within iframe - need to use page context for counting
                        # Note: frame_locator doesn't support .count(), so we check visibility instead
                        logging.debug(f"[{strategy_name}] Checking element in iframe context")
                    else:
                        count = await self.page.locator(selector).count()
                        logging.info(f"[{strategy_name}] Found {count} elements matching selector")
                except:
                    pass

                # Wait briefly for button to be actionable
                logging.debug(f"[{strategy_name}] Waiting for button visibility (attempt {attempt + 1}/{max_retries})")
                await button_locator.wait_for(state="visible", timeout=2000)
                logging.info(f"[{strategy_name}] Button is visible, proceeding to click")

                # DEBUG: Get button properties
                try:
                    is_enabled = await button_locator.is_enabled()
                    is_visible = await button_locator.is_visible()
                    logging.debug(f"[{strategy_name}] Button state: enabled={is_enabled}, visible={is_visible}")
                except:
                    pass

                # CMP-specific handling (Axeptio example)
                if self.banner_info.cmp_type == "axeptio":
                    logging.debug(f"[{strategy_name}] Axeptio: scrolling button into view")
                    try:
                        await button_locator.scroll_into_view_if_needed()
                    except:
                        pass
                    await self.page.wait_for_timeout(500)

                # Click button
                logging.info(f"[{strategy_name}] Attempting click...")
                await button_locator.click(timeout=3000, force=True)
                logging.info(f"[{strategy_name}] ✓ Click succeeded!")

                # Wait for modal to open (CMP-specific timeout)
                modal_timeout = self.waiter.get_modal_detection_timeout()
                logging.debug(f"[{strategy_name}] Waiting {modal_timeout}ms for modal to open")
                await self.waiter.wait_for_modal_open(self.page)

                # Try to detect modal
                logging.debug(f"[{strategy_name}] Attempting modal detection...")
                modal = await self._detect_modal()

                if modal:
                    duration_ms = int((time.time() - start_time) * 1000)
                    self.report.add_attempt(strategy_name, True, None, duration_ms)
                    logging.info(f"[{strategy_name}] ✓✓ SUCCESS! Modal detected in {duration_ms}ms")
                    return modal

                # Modal not detected, retry
                logging.warning(f"[{strategy_name}] ✗ Modal not detected after click")
                if attempt < max_retries - 1:
                    logging.debug(f"[{strategy_name}] Retrying in 1s (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(1)

            except PlaywrightTimeoutError as e:
                if attempt < max_retries - 1:
                    logging.debug(f"Click timeout on attempt {attempt + 1}, retrying")
                    await asyncio.sleep(1)
                else:
                    duration_ms = int((time.time() - start_time) * 1000)
                    self.report.add_attempt(strategy_name, False, f"Timeout: {str(e)}", duration_ms)
                    logging.debug(f"Strategy '{strategy_name}' failed after {max_retries} attempts")
            except Exception as e:
                duration_ms = int((time.time() - start_time) * 1000)
                self.report.add_attempt(strategy_name, False, str(e), duration_ms)
                logging.debug(f"Strategy '{strategy_name}' failed with exception: {e}")
                break

        return None

    async def _detect_modal(self) -> Optional[Locator]:
        """
        Detect opened modal with retry.

        Returns:
            Modal locator if detected, None otherwise
        """
        # Import here to avoid circular dependency
        from consentcrawl.ui_explorer import detect_modal_with_retry

        logging.info(f"[ModalDetection] Starting modal detection for CMP: {self.banner_info.cmp_type}")

        max_retries = 2
        for attempt in range(max_retries):
            try:
                logging.debug(f"[ModalDetection] Attempt {attempt + 1}/{max_retries} - calling detect_modal_with_retry()")
                # Use enhanced detection with CMP-specific logic
                modal = await detect_modal_with_retry(self.page, self.banner_info.cmp_type, self.config, max_retries=1)

                if modal:
                    logging.info(f"[ModalDetection] ✓ Modal detected on attempt {attempt + 1}/{max_retries}")

                    # DEBUG: Get modal info
                    try:
                        is_visible = await modal.is_visible()
                        logging.debug(f"[ModalDetection] Modal is visible: {is_visible}")
                    except:
                        pass

                    return modal

                # Modal not found, log and retry
                logging.warning(f"[ModalDetection] ✗ No modal found on attempt {attempt + 1}/{max_retries}")

                # Wait before retry
                if attempt < max_retries - 1:
                    animation_timeout = self.waiter.get_readiness_timeout()
                    logging.debug(f"[ModalDetection] Waiting {animation_timeout}ms before retry...")
                    await self.waiter.wait_for_animation_complete(self.page)

            except Exception as e:
                logging.warning(f"[ModalDetection] ✗ Attempt {attempt + 1}/{max_retries} failed with exception: {e}")
                if attempt < max_retries - 1:
                    logging.debug(f"[ModalDetection] Sleeping 1s before retry...")
                    await asyncio.sleep(1)

        logging.warning(f"[ModalDetection] ✗✗ Modal detection failed after {max_retries} attempts")
        return None

    async def _validate_modal_ready(self, modal_locator: Locator) -> bool:
        """
        Validate that modal is ready for extraction (not just visible).

        Args:
            modal_locator: Locator for detected modal

        Returns:
            True if modal is ready, False otherwise
        """
        logging.debug("Validating modal readiness")

        try:
            # Check 1: Modal is visible
            is_visible = await modal_locator.is_visible(timeout=self.waiter.get_readiness_timeout())
            if not is_visible:
                logging.debug("Modal readiness check failed: not visible")
                return False

            # Check 2: Modal has content (not empty)
            try:
                text_content = await modal_locator.inner_text(timeout=2000)
                if len(text_content.strip()) < 20:
                    logging.debug(f"Modal readiness check failed: minimal content ({len(text_content)} chars)")
                    return False
            except:
                logging.debug("Modal readiness check failed: could not extract text")
                return False

            # Check 3: Wait for final animations to complete
            await self.waiter.wait_for_animation_complete(self.page)

            # Check 4: Re-validate visibility after animations
            is_still_visible = await modal_locator.is_visible(timeout=self.waiter.get_readiness_timeout())
            if not is_still_visible:
                logging.debug("Modal readiness check failed: disappeared after animation wait")
                return False

            logging.info("Modal readiness validation passed")
            return True

        except Exception as e:
            logging.debug(f"Modal readiness validation failed with exception: {e}")
            return False

    def _transition_to(self, new_state: NavigationState):
        """
        Transition to a new state.

        Args:
            new_state: Target state
        """
        old_state = self.current_state
        self.current_state = new_state
        self.report.add_transition(old_state, new_state)
        logging.debug(f"State transition: {old_state.value} -> {new_state.value}")

    def _finalize_report(self) -> NavigationReport:
        """
        Finalize and return navigation report.

        Returns:
            Complete navigation report
        """
        self.report.final_state = self.current_state
        self.report.total_duration_ms = int((time.time() - self.start_time) * 1000)

        logging.info(
            f"Navigation flow completed: {self.report.initial_state.value} -> {self.report.final_state.value} "
            f"in {self.report.total_duration_ms}ms with {len(self.report.attempts)} attempts"
        )

        return self.report

"""
Data structures and schemas for CMP audit mode.

This module defines dataclasses for storing audit results, including banner information,
consent categories, vendors, and individual cookie details.
"""

import json
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum


@dataclass
class AuditConfig:
    """Configuration for audit mode behavior."""
    max_ui_depth: int = 3
    timeout_banner: int = 5000   # milliseconds (was 10000)
    timeout_modal: int = 8000    # milliseconds (was 15000)
    timeout_click: int = 5000    # milliseconds
    languages: List[str] = field(default_factory=lambda: ['en', 'fr'])
    support_shadow_dom: bool = True
    support_nested_iframes: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            'max_ui_depth': self.max_ui_depth,
            'timeout_banner': self.timeout_banner,
            'timeout_modal': self.timeout_modal,
            'timeout_click': self.timeout_click,
            'languages': self.languages,
            'support_shadow_dom': self.support_shadow_dom,
            'support_nested_iframes': self.support_nested_iframes,
        }


@dataclass
class ButtonInfo:
    """Information about a button in the consent UI."""
    text: str
    role: str  # accept_all, reject_all, settings, info, unknown
    selector: str
    aria_label: Optional[str] = None
    is_visible: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            'text': self.text,
            'role': self.role,
            'selector': self.selector,
            'aria_label': self.aria_label,
            'is_visible': self.is_visible,
        }


@dataclass
class BannerInfo:
    """Information about detected consent banner."""
    detected: bool
    cmp_type: Optional[str] = None  # onetrust, didomi, cookiebot, etc.
    cmp_brand: Optional[str] = None
    banner_html: Optional[str] = None
    banner_text: Optional[str] = None
    buttons: List[ButtonInfo] = field(default_factory=list)
    in_iframe: bool = False
    in_shadow_dom: bool = False
    iframe_src: Optional[str] = None
    detection_method: str = "none"  # cmp_specific, generic, text_based

    def to_dict(self) -> Dict[str, Any]:
        return {
            'detected': self.detected,
            'cmp_type': self.cmp_type,
            'cmp_brand': self.cmp_brand,
            'banner_html': self.banner_html,
            'banner_text': self.banner_text,
            'buttons': [b.to_dict() for b in self.buttons],
            'in_iframe': self.in_iframe,
            'in_shadow_dom': self.in_shadow_dom,
            'iframe_src': self.iframe_src,
            'detection_method': self.detection_method,
        }


@dataclass
class CategoryInfo:
    """Consent category information."""
    name: str
    description: Optional[str] = None
    default_enabled: Optional[bool] = None
    is_required: bool = False
    has_toggle: bool = False
    purposes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'description': self.description,
            'default_enabled': self.default_enabled,
            'is_required': self.is_required,
            'has_toggle': self.has_toggle,
            'purposes': self.purposes,
        }


@dataclass
class VendorInfo:
    """Vendor/partner information."""
    name: str
    purposes: List[str] = field(default_factory=list)
    legitimate_interest_purposes: List[str] = field(default_factory=list)
    special_purposes: List[str] = field(default_factory=list)
    features: List[str] = field(default_factory=list)
    privacy_policy_url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'purposes': self.purposes,
            'legitimate_interest_purposes': self.legitimate_interest_purposes,
            'special_purposes': self.special_purposes,
            'features': self.features,
            'privacy_policy_url': self.privacy_policy_url,
        }


@dataclass
class CookieDetail:
    """Individual cookie details from UI."""
    name: str
    domain: Optional[str] = None
    duration: Optional[str] = None
    purpose: Optional[str] = None
    category: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'domain': self.domain,
            'duration': self.duration,
            'purpose': self.purpose,
            'category': self.category,
        }


# Enums for Phase 2 - Section Discovery

class SectionType(Enum):
    """Types of UI section structures."""
    TAB = "tab"
    ACCORDION = "accordion"
    LIST = "list"
    NESTED_TAB = "nested_tab"
    MODAL_SWITCH = "modal_switch"
    UNKNOWN = "unknown"


class ContentType(Enum):
    """Content that sections may contain."""
    CATEGORIES = "categories"
    VENDORS = "vendors"
    COOKIES = "cookies"
    PURPOSES = "purposes"
    PARTNERS = "partners"
    UNKNOWN = "unknown"


class DiscoveryMethod(Enum):
    """How a section was discovered."""
    ARIA_SEMANTIC = "aria_semantic"
    VISUAL_PATTERN = "visual_pattern"
    YAML_FALLBACK = "yaml_fallback"
    HYBRID = "hybrid"


# Dataclasses for Phase 2 - Section Discovery

@dataclass
class DiscoveredSection:
    """Discovered UI section (tab, accordion, list)."""
    section_type: SectionType
    content_type: ContentType
    locator: Optional[str]
    activation_required: bool
    activation_locator: Optional[str]
    discovery_method: DiscoveryMethod
    confidence: float
    was_activated: bool = False
    item_count_after_activation: int = 0
    contains_items: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'section_type': self.section_type.value,
            'content_type': self.content_type.value,
            'locator': self.locator,
            'activation_required': self.activation_required,
            'activation_locator': self.activation_locator,
            'discovery_method': self.discovery_method.value,
            'confidence': self.confidence,
            'was_activated': self.was_activated,
            'item_count_after_activation': self.item_count_after_activation,
            'contains_items': self.contains_items,
            'metadata': self.metadata,
        }


@dataclass
class SectionDiscoveryResult:
    """Result of section discovery process."""
    sections: List[DiscoveredSection]
    categories_section: Optional[DiscoveredSection] = None
    vendors_section: Optional[DiscoveredSection] = None
    cookies_section: Optional[DiscoveredSection] = None
    purposes_section: Optional[DiscoveredSection] = None
    discovery_duration_ms: int = 0
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'sections': [s.to_dict() for s in self.sections],
            'categories_section': self.categories_section.to_dict() if self.categories_section else None,
            'vendors_section': self.vendors_section.to_dict() if self.vendors_section else None,
            'cookies_section': self.cookies_section.to_dict() if self.cookies_section else None,
            'purposes_section': self.purposes_section.to_dict() if self.purposes_section else None,
            'discovery_duration_ms': self.discovery_duration_ms,
            'errors': self.errors,
        }


@dataclass
class ConsentUIContext:
    """State tracking during UI exploration."""
    current_depth: int = 0
    visited_selectors: set = field(default_factory=set)
    modal_stack: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'current_depth': self.current_depth,
            'visited_selectors': list(self.visited_selectors),
            'modal_stack': self.modal_stack,
            'errors': self.errors,
        }


@dataclass
class AuditResult:
    """Complete audit result for one URL."""
    id: str
    url: str
    domain_name: str
    extraction_datetime: str
    banner_info: Optional[BannerInfo] = None
    categories: List[CategoryInfo] = field(default_factory=list)
    vendors: List[VendorInfo] = field(default_factory=list)
    cookies: List[CookieDetail] = field(default_factory=list)
    ui_context: Optional[ConsentUIContext] = None
    screenshot_files: List[str] = field(default_factory=list)
    # Status granularity:
    # - success_detailed: Banner + buttons + modal + categories/vendors/cookies
    # - success_basic: Banner + buttons (no modal navigation)
    # - partial: Banner detected but incomplete extraction
    # - failed: No banner detected
    status: str = "pending"
    status_msg: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'url': self.url,
            'domain_name': self.domain_name,
            'extraction_datetime': self.extraction_datetime,
            'banner_info': self.banner_info.to_dict() if self.banner_info else None,
            'categories': [c.to_dict() for c in self.categories],
            'vendors': [v.to_dict() for v in self.vendors],
            'cookies': [c.to_dict() for c in self.cookies],
            'ui_context': self.ui_context.to_dict() if self.ui_context else None,
            'screenshot_files': self.screenshot_files,
            'status': self.status,
            'status_msg': self.status_msg,
        }


def get_audit_schema() -> Dict[str, str]:
    """
    Returns the database schema for audit results.

    All complex types (dicts, lists) are stored as JSON-serialized TEXT fields.
    """
    return {
        "id": "STRING",
        "url": "STRING",
        "domain_name": "STRING",
        "extraction_datetime": "STRING",
        "banner_info": "TEXT",  # JSON
        "categories": "TEXT",    # JSON array
        "vendors": "TEXT",       # JSON array
        "cookies": "TEXT",       # JSON array
        "ui_context": "TEXT",    # JSON
        "screenshot_files": "TEXT",  # JSON array
        "status": "STRING",
        "status_msg": "STRING",
    }

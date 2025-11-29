
[![PyPI](https://img.shields.io/pypi/v/consentcrawl.svg?maxAge=3600)](https://pypi.python.org/pypi/consentcrawl)
[![PyPI](https://img.shields.io/pypi/pyversions/consentcrawl.svg?maxAge=3600)](https://pypi.python.org/pypi/consentcrawl)

# ConsentCrawl - CMP Audit Tool

ConsentCrawl is a comprehensive tool for auditing Cookie Consent Management Platforms (CMPs). It can detect, analyze, and extract detailed information from 35+ CMP providers.

## Features

**Audit Mode (Default):**
- Deep exploration of consent UIs to extract complete CMP configurations
- Detect 35+ consent management platforms automatically
- Extract consent categories with descriptions and default states
- Extract vendor/partner lists with purposes and legal basis
- Extract individual cookie details from CMP interfaces
- Support for Shadow DOM and nested iframes
- Multi-language detection (English, French)
- Detailed JSON output per URL

**Crawl Mode (Legacy):**
- Measure impact of consent on tracking (before/after accept)
- Detect unconsented third-party domains and cookies
- Classify tracking domains based on 7 commonly used ad blocking lists
- Keep screenshots before and after consent
- Capture JSON-LD and meta tags

## CLI Arguments

```sh
consentcrawl URL [OPTIONS]
```

### General Arguments

| Argument | Description |
|----------|-------------|
| `url` | **(Required)** URL, comma-separated URLs, or .txt file with URLs |
| `--mode` | Mode to run: `audit` (default) or `crawl` for legacy behavior |
| `--debug` | Enable debug logging |
| `--headless` | Run browser in headless mode (yes/no, default: yes) |
| `--screenshot` | Take screenshots before/after |
| `--batch_size`, `-b` | Number of parallel browser windows (default: 15) |
| `--show_output`, `-o` | Show output in terminal (max 25 results) |
| `--db_file`, `-db` | SQLite database path (default: crawl_results.db) |

### Audit Mode Arguments

| Argument | Description |
|----------|-------------|
| `--output_dir` | Directory for audit JSON output (default: ./audit_results) |
| `--max_ui_depth` | Maximum UI exploration depth (default: 3) |
| `--timeout_banner` | Banner detection timeout in ms (default: 10000) |
| `--timeout_modal` | Modal operation timeout in ms (default: 15000) |

### Crawl Mode Arguments (Legacy)

| Argument | Description |
|----------|-------------|
| `--bootstrap` | Force refresh of blocklists |
| `--blocklists`, `-bf` | Path to custom blocklists file (YAML) |

## Installation

```bash
pip install consentcrawl
```

The [Playwright (headless) browsers](https://playwright.dev/python/docs/browsers) are not automatically installed so run:
```bash
playwright install
```

Or specify a specific browser: `playwright install chromium`

## Usage Examples

### Audit Mode (Default)

Audit a single site:
```bash
consentcrawl https://example.com
```

Audit multiple sites from file:
```bash
consentcrawl sites.txt --output_dir ./audit_results
```

Deep exploration with screenshots:
```bash
consentcrawl example.com --max_ui_depth 5 --screenshot
```

Debug mode (visible browser):
```bash
consentcrawl example.com --headless no --debug
```

### Crawl Mode (Legacy)

```bash
consentcrawl example.com --mode crawl --screenshot
```

With jq to get tracking domains without consent:
```bash
consentcrawl leboncoin.fr,marktplaats.nl,ebay.com --mode crawl -o | jq '.[] | .tracking_domains_no_consent'
```

## Audit Mode Output

### JSON Structure

Each URL generates a JSON file in `--output_dir`:

```json
{
  "url": "https://example.com",
  "domain_name": "example.com",
  "extraction_datetime": "2025-01-15T10:30:00Z",
  "banner_info": {
    "detected": true,
    "cmp_type": "onetrust",
    "cmp_brand": "OneTrust",
    "banner_html": "...",
    "banner_text": "We use cookies...",
    "buttons": [
      {
        "text": "Accept All",
        "role": "accept_all",
        "selector": "#onetrust-accept-btn-handler",
        "is_visible": true
      },
      {
        "text": "Cookie Settings",
        "role": "settings",
        "selector": "#onetrust-pc-btn-handler",
        "is_visible": true
      }
    ],
    "in_iframe": false,
    "in_shadow_dom": false,
    "detection_method": "cmp_specific"
  },
  "categories": [
    {
      "name": "Strictly Necessary",
      "description": "These cookies are essential...",
      "default_enabled": true,
      "is_required": true,
      "has_toggle": false
    },
    {
      "name": "Performance Cookies",
      "description": "Help us understand...",
      "default_enabled": false,
      "is_required": false,
      "has_toggle": true,
      "purposes": ["Analytics", "Performance Measurement"]
    }
  ],
  "vendors": [
    {
      "name": "Google Analytics",
      "purposes": ["Analytics", "Measurement"],
      "legitimate_interest_purposes": [],
      "privacy_policy_url": "https://policies.google.com/privacy"
    }
  ],
  "cookies": [
    {
      "name": "_ga",
      "domain": ".example.com",
      "duration": "2 years",
      "purpose": "Used to distinguish users",
      "category": "Performance"
    }
  ],
  "status": "success"
}
```

### SQLite Database

Results are also stored in SQLite database (default: `audit_results.db`):
- Table: `audit_results`
- All fields JSON-serialized for complex types
- Queryable with standard SQL

## Advanced Features

### Shadow DOM Support
Audit mode automatically detects and explores CMPs using Shadow DOM.

### Nested Iframe Support
Handles complex iframe structures (e.g., Sourcepoint, TrustArc) up to 2 levels deep.

### Multi-Language Detection
Detects banners in English and French automatically. Extensible to other languages via configuration.

### Safe Exploration
- Never clicks "Save" or "Accept" buttons during exploration
- Depth limiting to prevent infinite loops
- Timeout protection on all operations
- Graceful error handling - returns partial results if something fails

## Python API

Or if you want to import into an existing Python script:
```python
import asyncio
from consentcrawl import crawl

results = asyncio.run(crawl.crawl_single("dumky.net"))
```

The playwright browser runs asynchronously which is great for running multiple
URLs in parallel, but for running a single URL you'll need to use asyncio.run()
to run the asynchronous function.

## Troubleshooting

### Banner not detected
- Try increasing `--timeout_banner` (default: 10s)
- Run with `--headless no --debug` to see what's happening
- Check if site uses Shadow DOM or nested iframes (automatically handled)

### Modal doesn't open
- Increase `--timeout_modal` (default: 15s)
- Some CMPs have non-standard "settings" buttons - logged in debug mode

### Incomplete data extraction
- Increase `--max_ui_depth` for deeper exploration
- Some CMPs don't expose all data in UI (limitations logged)

### Performance
- Decrease `--batch_size` if system overloaded
- Each URL typically takes 15-30 seconds in audit mode

## How it works

### Audit Mode
Audit mode performs deep exploration of CMP interfaces:
1. Navigate to URL and wait for banner to appear
2. Detect banner using CMP-specific selectors, generic patterns, or text-based detection
3. Extract banner information (HTML, text, all buttons)
4. Click "Settings/Manage" button to open preferences modal
5. Navigate through categories, vendors, and cookie lists
6. Extract all available information without saving consent
7. Store results in SQLite database and JSON files

Supports detection in iframes and Shadow DOM, with multi-language keyword matching.

### Crawl Mode (Legacy)
Crawl mode measures consent impact on tracking:
1. Load URL and capture initial state (cookies, domains)
2. Detect consent manager
3. Click "Accept All" button
4. Capture post-consent state
5. Compare before/after to identify tracking changes

Uses blocklists to classify tracking domains.

## Available Consent Managers:
- OneTrust
- Optanon
- CookieLaw (CookiePro)
- Drupal EU Cookie Compliance
- JoomlaShaper SP Cookie Consent Extension
- FastCMP
- Google Funding Choices
- Klaro
- Ensighten
- GX Software
- EZ Cookie
- CookieBot
- CookieHub
- TYPO3 Wacon Cookie Management Extension
- TYPO3 Cookie Consent Extension
- Commanders Act - Trust Commander
- CookieFirst
- Osano
- Orejime
- Axceptio
- Civic UK Cookie Control
- UserCentrics
- CookieYes
- Secure Privacy
- Quantcast
- Didomi
- MediaVine CMP
- CookieLaw
- ConsentManager.net
- HubSpot Cookie Banner
- LiveRamp PrivacyManager.io
- TrustArc Truste
- SFBX AppConsent
- Piwik PRO GDPR Consent Manager
- Finsweet Cookie Consent for Webflow
- Non-specific / Custom (looks for general CSS selectors like "#acceptCookies" or ".cookie-accept")

Are you missing a consent manager? Have a look at [the full list](consentcrawl/assets/consent_managers.yml) and feel free to open an issue or pull request!

## Examples
The examples folder shows examples to run ConsentCrawl:
- as a Github Action
- on Google Cloud Run with a simple FastAPI server that responds with the ConsentCrawl results on a POST request to a `/consentcrawl` endpoint.

## To Do
- [ ] Detect consent managers with cookies instead of just CSS selectors
- [ ] Show progress when using CLI

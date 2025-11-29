# ConsentCrawl API - Usage Guide

## Overview

ConsentCrawl API provides a REST endpoint to audit websites for cookie consent compliance. This API is designed for integration with N8N workflows and deployment on Railway.

## Base URL

**Development**: `http://localhost:8000`
**Production**: `https://your-app.railway.app`

---

## Endpoints

### 1. POST /audit

Audit a single URL for cookie consent compliance.

**URL**: `/audit`
**Method**: `POST`
**Content-Type**: `application/json`

#### Request Body

```json
{
  "url": "https://www.example.com",
  "config": {
    "timeout_banner": 15000,
    "timeout_modal": 20000,
    "max_ui_depth": 3
  },
  "screenshot": false
}
```

#### Parameters

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `url` | string | ✅ Yes | - | URL to audit (must be valid HTTP/HTTPS URL) |
| `config` | object | ❌ No | `{}` | Optional configuration overrides |
| `config.timeout_banner` | integer | ❌ No | `10000` | Timeout for banner detection (milliseconds) |
| `config.timeout_modal` | integer | ❌ No | `15000` | Timeout for modal operations (milliseconds) |
| `config.max_ui_depth` | integer | ❌ No | `3` | Maximum depth for UI exploration |
| `screenshot` | boolean | ❌ No | `false` | Whether to capture screenshots |

#### Response (200 OK)

```json
{
  "id": "ZXhhbXBsZS5jb20=",
  "url": "https://www.example.com",
  "domain_name": "example.com",
  "extraction_datetime": "2025-11-29T17:00:00.000000",
  "status": "success_detailed",
  "status_msg": "Full detailed extraction completed",
  "banner_info": {
    "detected": true,
    "cmp_type": "onetrust",
    "cmp_brand": "OneTrust",
    "banner_text": "We use cookies...",
    "buttons": [
      {
        "text": "accept all",
        "role": "accept_all",
        "selector": "#onetrust-accept-btn-handler",
        "is_visible": true
      }
    ],
    "in_iframe": false,
    "detection_method": "cmp_specific"
  },
  "categories": [
    {
      "name": "Strictly Necessary Cookies",
      "description": "These cookies are essential...",
      "default_enabled": true,
      "is_required": true,
      "has_toggle": false
    }
  ],
  "vendors": [],
  "cookies": [],
  "ui_context": {
    "current_depth": 0,
    "visited_selectors": [],
    "errors": []
  },
  "screenshot_files": []
}
```

#### Status Codes

- `200 OK`: Audit completed successfully (check `status` field in response)
- `400 Bad Request`: Invalid URL or request parameters
- `500 Internal Server Error`: Server error during audit
- `503 Service Unavailable`: Browser not initialized (service starting up)
- `504 Gateway Timeout`: Audit took longer than 120 seconds

#### Status Field Values

| Status | Description |
|--------|-------------|
| `success_detailed` | Banner detected + categories/vendors extracted |
| `success_basic` | Banner detected but detailed extraction failed |
| `failed` | No banner detected on the page |
| `error` | Error occurred during audit |

---

### 2. GET /health

Health check endpoint for monitoring.

**URL**: `/health`
**Method**: `GET`

#### Response (200 OK)

```json
{
  "status": "healthy",
  "browser": "ready",
  "message": "Service is operational"
}
```

---

### 3. GET /

API information endpoint.

**URL**: `/`
**Method**: `GET`

#### Response (200 OK)

```json
{
  "name": "ConsentCrawl API",
  "version": "1.0.0",
  "description": "Cookie consent banner audit API for N8N integration",
  "endpoints": {
    "POST /audit": "Audit a URL for cookie consent compliance",
    "GET /health": "Health check for Railway monitoring",
    "GET /docs": "Interactive API documentation (Swagger UI)",
    "GET /redoc": "Alternative API documentation (ReDoc)"
  }
}
```

---

### 4. GET /docs

Interactive Swagger UI documentation.

**URL**: `/docs`
**Method**: `GET`

Opens an interactive API explorer where you can test endpoints directly from your browser.

---

## N8N Integration

### Setup HTTP Request Node

1. **Add HTTP Request node** to your N8N workflow
2. **Configure the node**:
   - **Method**: `POST`
   - **URL**: `https://your-app.railway.app/audit`
   - **Authentication**: None
   - **Send Body**: Yes
   - **Body Content Type**: JSON
   - **Timeout**: `120000` (120 seconds)

3. **Set Body Parameters**:

```json
{
  "url": "{{ $json.website_url }}"
}
```

Or with config overrides:

```json
{
  "url": "{{ $json.website_url }}",
  "config": {
    "timeout_banner": 15000,
    "timeout_modal": 20000
  }
}
```

### Example N8N Workflow

```json
{
  "nodes": [
    {
      "name": "Webhook",
      "type": "n8n-nodes-base.webhook",
      "parameters": {
        "path": "audit-cookie"
      }
    },
    {
      "name": "ConsentCrawl API",
      "type": "n8n-nodes-base.httpRequest",
      "parameters": {
        "method": "POST",
        "url": "https://your-app.railway.app/audit",
        "jsonParameters": true,
        "options": {
          "timeout": 120000
        },
        "bodyParametersJson": "={{ { \"url\": $json.website_url } }}"
      }
    },
    {
      "name": "Process Results",
      "type": "n8n-nodes-base.set",
      "parameters": {
        "values": [
          {
            "name": "domain",
            "value": "={{ $json.domain_name }}"
          },
          {
            "name": "cmp_detected",
            "value": "={{ $json.banner_info.detected }}"
          },
          {
            "name": "cmp_type",
            "value": "={{ $json.banner_info.cmp_type }}"
          },
          {
            "name": "categories_count",
            "value": "={{ $json.categories.length }}"
          },
          {
            "name": "status",
            "value": "={{ $json.status }}"
          }
        ]
      }
    }
  ]
}
```

### Accessing Response Data in N8N

Use these expressions to extract data from the response:

| Data | N8N Expression |
|------|----------------|
| Domain name | `{{ $json.domain_name }}` |
| Banner detected | `{{ $json.banner_info.detected }}` |
| CMP type | `{{ $json.banner_info.cmp_type }}` |
| CMP brand | `{{ $json.banner_info.cmp_brand }}` |
| Number of categories | `{{ $json.categories.length }}` |
| Number of vendors | `{{ $json.vendors.length }}` |
| Audit status | `{{ $json.status }}` |
| Status message | `{{ $json.status_msg }}` |
| First category name | `{{ $json.categories[0].name }}` |
| All button texts | `{{ $json.banner_info.buttons.map(b => b.text) }}` |

---

## Railway Deployment

### Prerequisites

- GitHub account
- Railway account (connect with GitHub)
- Your code pushed to a GitHub repository

### Deployment Steps

1. **Connect Railway to GitHub**
   - Go to [railway.app](https://railway.app)
   - Click "New Project" → "Deploy from GitHub repo"
   - Select your repository

2. **Railway Auto-Detection**
   - Railway will detect Python and install dependencies from `requirements.txt`
   - Railway will use the `Procfile` to start the application

3. **Set Environment Variables** (optional)
   - `PYTHONUNBUFFERED=1` (recommended for real-time logs)
   - `LOG_LEVEL=INFO` (optional)
   - `PORT` is automatically set by Railway

4. **Deploy**
   - Railway will automatically deploy on every push to your main branch
   - First deployment takes 5-10 minutes (Playwright browser installation)

5. **Get Your API URL**
   - Railway provides a public URL: `https://your-app.railway.app`
   - Use this URL in your N8N workflows

### Monitoring

- **Health Check**: Visit `https://your-app.railway.app/health`
- **API Docs**: Visit `https://your-app.railway.app/docs`
- **Logs**: View real-time logs in Railway dashboard

---

## Local Development

### Installation

```bash
# Clone repository
git clone https://github.com/your-username/consentcrawl.git
cd consentcrawl

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium
```

### Running Locally

```bash
# Start server with auto-reload
uvicorn consentcrawl.server:app --reload

# Or run directly
python -m consentcrawl.server
```

Server will start at `http://localhost:8000`

### Testing

```bash
# Health check
curl http://localhost:8000/health

# Test audit
curl -X POST http://localhost:8000/audit \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.axeptio.eu",
    "screenshot": false
  }'

# View interactive docs
open http://localhost:8000/docs
```

---

## Performance Notes

- **Typical Execution Time**: 30-90 seconds per URL
- **Timeout**: Maximum 120 seconds per audit
- **Concurrency**: One audit at a time per instance (browser limitation)
- **Scaling**: Deploy multiple Railway instances for parallel processing

---

## Error Handling

### Common Errors

**503 Service Unavailable - Browser not initialized**
- The service is still starting up
- Wait 30 seconds and retry

**504 Gateway Timeout - Audit timeout**
- The target website took too long to respond
- Site may be slow or have complex consent UI
- Try increasing `timeout_banner` and `timeout_modal` in config

**500 Internal Server Error**
- Check Railway logs for detailed error message
- Ensure the target URL is accessible

---

## Support

- **GitHub Issues**: [github.com/dumkydewilde/consentcrawl/issues](https://github.com/dumkydewilde/consentcrawl/issues)
- **Documentation**: [github.com/dumkydewilde/consentcrawl](https://github.com/dumkydewilde/consentcrawl)
- **Railway Logs**: Available in Railway dashboard

---

## Example cURL Commands

### Basic Audit
```bash
curl -X POST https://your-app.railway.app/audit \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.bbc.com"}'
```

### Audit with Custom Config
```bash
curl -X POST https://your-app.railway.app/audit \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.example.com",
    "config": {
      "timeout_banner": 15000,
      "timeout_modal": 20000,
      "max_ui_depth": 3
    }
  }'
```

### Audit with Screenshots
```bash
curl -X POST https://your-app.railway.app/audit \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.example.com",
    "screenshot": true
  }'
```

---

## Supported CMPs

ConsentCrawl API supports 35+ consent management platforms including:

- **OneTrust**
- **Cookiebot**
- **Didomi**
- **Sourcepoint**
- **Axeptio**
- **TrustArc**
- **Quantcast**
- **And many more...**

Full list: See `consentcrawl/assets/audit_selectors.yml`

#!/bin/bash
# Railway build hook - runs after pip install

echo "Installing Playwright browsers..."
playwright install chromium
playwright install-deps chromium
echo "✓ Playwright installation complete"

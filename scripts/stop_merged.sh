#!/usr/bin/env bash
# Best-effort stop for merged dev processes (macOS / Linux).
set -euo pipefail

echo "Stopping uvicorn (api.app)..."
pkill -f "uvicorn api.app:app" 2>/dev/null || true

echo "Stopping COA web_api..."
pkill -f "python.*web_api.py" 2>/dev/null || true

echo "Stopping Vite (council-web)..."
pkill -f "vite.*web" 2>/dev/null || true
pkill -f "node.*vite" 2>/dev/null || true

echo "Done."

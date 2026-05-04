#!/bin/bash
set -e

echo "========================================="
echo "  Stealth Spotify Bot - Startup"
echo "========================================="

# ---- Option 1: Docker (recommended) ----
if command -v docker &> /dev/null && [ -f "docker-compose.yml" ]; then
    if [ -z "$CLOUDFLARE_TUNNEL_TOKEN" ] && [ -f ".env" ]; then
        export $(grep -v '^#' .env | xargs)
    fi

    if [ -n "$CLOUDFLARE_TUNNEL_TOKEN" ]; then
        echo "Starting with Docker + Cloudflare Tunnel..."
        docker compose up -d --build
        echo ""
        echo "Bot is running! Access it via your Cloudflare domain."
        echo "Logs: docker compose logs -f bot"
    else
        echo "Starting bot only (no Cloudflare Tunnel)..."
        docker compose up -d --build bot
        echo ""
        echo "Bot is running at: http://localhost:${PORT:-8000}"
        echo ""
        echo "To add Cloudflare Tunnel:"
        echo "  1. Set CLOUDFLARE_TUNNEL_TOKEN in .env"
        echo "  2. Run: docker compose up -d"
    fi
    exit 0
fi

# ---- Option 2: Direct Python ----
echo "Docker not found. Running with Python directly..."

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate
pip install -q -r requirements.txt

echo ""
echo "Starting server on port ${PORT:-8000}..."
echo "Dashboard: http://localhost:${PORT:-8000}"
echo ""

python main.py

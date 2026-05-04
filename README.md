# Stealth Spotify Bot

Multi-threaded Spotify streaming bot with a web dashboard. Uses Selenium for browser automation with stealth fingerprinting.

## Features

- Web-based dashboard (dark Spotify-themed UI)
- Multi-threaded playback with configurable thread count
- Stealth browser fingerprinting (mobile emulation, random user agents)
- Cookie-based authentication (import Spotify cookies)
- Proxy support (manual + SmartProxy preset)
- Real-time activity logs
- Health check endpoint at `/health`

## Project Structure

```
├── bot_engine.py       # Core bot logic (Selenium automation)
├── main.py             # FastAPI server + API endpoints
├── static/
│   └── index.html      # Self-contained dashboard UI
├── requirements.txt    # Python dependencies
├── Dockerfile          # Container with Chrome pre-installed
├── docker-compose.yml  # Bot + Cloudflare Tunnel
├── .env.example        # Environment variable template
└── start.sh            # Quick start script
```

## Deployment

### Option 1: Docker + Cloudflare Tunnel (Recommended)

This exposes your bot via a Cloudflare domain with DDoS protection, SSL, and CDN caching — all for free.

#### 1. Create a Cloudflare Tunnel

1. Go to [Cloudflare Dashboard](https://dash.cloudflare.com/) → **Networking** → **Tunnels**
2. Click **Create Tunnel** → name it (e.g. `spotify-bot`)
3. Skip the install step (Docker handles it)
4. Under **Routes**, add a **Published Application**:
   - **Subdomain**: `bot` (or whatever you want)
   - **Domain**: select your Cloudflare domain
   - **Service URL**: `http://bot:8000`
5. Copy the **tunnel token** from the install command

#### 2. Configure and Run

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/spotify-bot-cloudflare.git
cd spotify-bot-cloudflare

# Set up environment
cp .env.example .env
# Edit .env and paste your CLOUDFLARE_TUNNEL_TOKEN

# Start everything
docker compose up -d --build

# View logs
docker compose logs -f bot
```

Your dashboard is now live at `https://bot.yourdomain.com`

#### Useful Commands

```bash
docker compose down          # Stop everything
docker compose restart bot   # Restart the bot
docker compose logs -f       # Follow all logs
```

---

### Option 2: Quick Tunnel (No domain needed)

For testing — generates a random `trycloudflare.com` URL:

```bash
# Start the bot
docker compose up -d --build bot

# In another terminal, create a quick tunnel
docker run --rm --network spotify-bot-cloudflare_bot-network \
  cloudflare/cloudflared:latest tunnel --url http://bot:8000
```

The URL printed in the terminal is your public dashboard link.

---

### Option 3: VPS without Docker

```bash
# Install Chrome
wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install -y /tmp/chrome.deb

# Clone and setup
git clone https://github.com/YOUR_USERNAME/spotify-bot-cloudflare.git
cd spotify-bot-cloudflare
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run
python main.py
# Bot runs on http://localhost:8000

# (Optional) Expose with cloudflared
cloudflared tunnel --url http://localhost:8000
```

---

### Option 4: Google Colab

Use the `Spotify_Bot_Colab_Full.ipynb` notebook (see releases).

---

## Getting Spotify Cookies

1. Install a browser extension like [Cookie-Editor](https://cookie-editor.cgagnier.ca/)
2. Log in to [open.spotify.com](https://open.spotify.com)
3. Export cookies as JSON
4. Upload the JSON file via the dashboard's **Import Cookies** button

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Dashboard UI |
| `GET` | `/health` | Health check |
| `GET` | `/api/bot` | Get bot state |
| `POST` | `/api/bot` | Send action/settings |
| `POST` | `/api/bot/upload-cookies` | Upload cookie file |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8000` | Server port |
| `CLOUDFLARE_TUNNEL_TOKEN` | — | Cloudflare Tunnel token |

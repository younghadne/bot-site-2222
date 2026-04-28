# Instagram Bot

A web-based Instagram automation bot with a real-time dashboard.

## Features
- Auto Follow from target accounts
- Welcome DM to every followed user
- Auto Unfollow
- Auto Like Feed
- Mass Story View
- Auto DM
- Auto Comment
- Approve Follow Requests

## Deploy to Railway

1. Push this repo to GitHub
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Select this repo
4. Railway auto-detects Python and uses `Procfile`
5. Add environment variable: `FLASK_SECRET_KEY` = any random string
6. Your app will be live at the Railway URL

## Custom Domain via Cloudflare

1. In Railway → Settings → Networking → Custom Domain → add your domain
2. In Cloudflare DNS → add a CNAME record pointing to the Railway URL
3. Set Cloudflare SSL to "Full"

## Local Development

```bash
pip install -r requirements.txt
python web_app.py
```
Open http://localhost:5000

## Sessions
Sessions are stored in the `sessions/` folder (gitignored). On Railway, sessions persist within a deployment but reset on redeploy. For persistent sessions, use Railway Volumes.

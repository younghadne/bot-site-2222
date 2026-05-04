from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import uvicorn
import os

from bot_engine import BotEngine

app = FastAPI(title="Spotify Bot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

bot = BotEngine()

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def serve_dashboard():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "isRunning": bot.is_running,
        "totalPlays": bot.total_plays,
        "activeThreads": bot.active_threads,
    }


class ActionRequest(BaseModel):
    action: Optional[str] = None
    payload: Optional[dict] = None
    settings: Optional[dict] = None


@app.get("/api/bot")
def get_state():
    return bot.get_state()


@app.post("/api/bot")
def post_action(req: ActionRequest):
    if req.settings:
        bot.update_settings(req.settings)

    if req.action:
        action = req.action
        payload = req.payload or {}

        if action == "start":
            if not bot.is_running:
                bot.start_bot()
        elif action == "stop":
            bot.stop_bot()
        elif action == "addProxy":
            proxy = payload.get("proxy", "")
            if proxy:
                bot.add_proxy(proxy)
        elif action == "removeProxy":
            idx = payload.get("index")
            if idx is not None:
                bot.remove_proxy_by_index(int(idx))
        elif action == "clearProxies":
            bot.clear_proxies()
        elif action == "testProxy":
            bot.test_proxy()
        elif action == "clearProfiles":
            bot.clear_all_profiles()
        elif action == "resetPlays":
            bot.reset_plays()

    return bot.get_state()


@app.post("/api/bot/upload-cookies")
async def upload_cookies(file: UploadFile = File(...)):
    data = await file.read()
    filename = file.filename or "cookies.json"
    success = bot.import_cookie_data(filename, data)
    return {"success": success, **bot.get_state()}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

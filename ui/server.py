"""
ui/server.py — Local dashboard server for Navigator AI.

Runs on port 5000 in a daemon thread alongside the voice loop.
Endpoints:
  GET /           → ui/index.html
  GET /status     → connector status + user name JSON
  GET /transcript → current session log JSON
"""

import json
import logging
import os
import socket
import threading
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

try:
    from ara_sdk import fastapi_endpoint
except ImportError:
    def fastapi_endpoint(**kwargs):
        def decorator(fn):
            return fn
        return decorator

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

app = FastAPI(title="Navigator AI", docs_url=None, redoc_url=None)

_UI_DIR   = Path(__file__).parent
_ROOT_DIR = _UI_DIR.parent


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/")
@fastapi_endpoint(method="GET", path="/", auth="none")
async def index() -> HTMLResponse:
    """Serve the Navigator AI dashboard."""
    html = (_UI_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


@app.get("/status")
@fastapi_endpoint(method="GET", path="/status", auth="none")
async def status() -> JSONResponse:
    """Return connector status and user name."""
    from utils.connectors import check_connector
    from utils.profile import load_profile

    profile = load_profile() or {}
    connectors = ["Gmail", "Google Drive", "Google Calendar", "Messages", "Phone"]
    return JSONResponse({
        "name":       profile.get("name", ""),
        "connectors": {name: check_connector(name) for name in connectors},
    })


@app.get("/transcript")
@fastapi_endpoint(method="GET", path="/transcript", auth="none")
async def transcript() -> JSONResponse:
    """Return the current session transcript from session_log.json."""
    from utils.voice import SESSION_LOG_PATH
    entries = []
    try:
        for line in SESSION_LOG_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    except Exception:
        pass
    return JSONResponse({
        "log": [{"role": e["speaker"], "content": e["message"]} for e in entries]
    })


# ── Server lifecycle ──────────────────────────────────────────────────────────

def _get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def start_ui_server(port: int = 5000) -> None:
    """Start the FastAPI server in a background daemon thread."""
    ip = _get_local_ip()
    print(f"\nNavigator AI running. Open http://{ip}:{port} to view the demo\n")

    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=port,
        log_level="error",
    )
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()

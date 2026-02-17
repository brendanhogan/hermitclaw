"""FastAPI web server — API + WebSocket + serves frontend."""

import asyncio
import hashlib
import json
import logging
import os
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from hermitclaw.brain import Brain
from hermitclaw.config import config
from hermitclaw.identity import _derive_traits

logger = logging.getLogger("hermitclaw.server")

app = FastAPI(title="HermitClaw")
brains: dict[str, Brain] = {}  # crab_id -> Brain


def create_app(all_brains: dict[str, Brain]) -> FastAPI:
    """Initialize the app with brains dict. Called from main.py."""
    global brains
    brains = all_brains
    return app


def _get_brain(request: Request) -> Brain:
    """Look up brain by ?crab=ID query param, or default to first."""
    crab_id = request.query_params.get("crab")
    if crab_id and crab_id in brains:
        return brains[crab_id]
    return next(iter(brains.values()))


# CORS for development (Vite dev server)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- WebSocket ---


@app.websocket("/ws/{crab_id}")
async def websocket_endpoint(ws: WebSocket, crab_id: str):
    brain = brains.get(crab_id)
    if not brain:
        await ws.close(code=4004)
        return
    await ws.accept()
    brain.add_ws_client(ws)
    logger.info(f"WebSocket client connected to {crab_id}")
    try:
        while True:
            await ws.receive_text()  # keep connection alive
    except WebSocketDisconnect:
        brain.remove_ws_client(ws)
        logger.info(f"WebSocket client disconnected from {crab_id}")


@app.websocket("/ws")
async def websocket_default(ws: WebSocket):
    """Backwards-compatible /ws — connects to the first brain."""
    if not brains:
        await ws.close(code=4004)
        return
    brain = next(iter(brains.values()))
    await ws.accept()
    brain.add_ws_client(ws)
    logger.info("WebSocket client connected (default)")
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        brain.remove_ws_client(ws)
        logger.info("WebSocket client disconnected (default)")


# --- REST API ---


@app.get("/api/crabs")
async def get_crabs():
    """List all running crabs."""
    return [
        {
            "id": crab_id,
            "name": brain.identity["name"],
            "state": brain.state,
            "thought_count": brain.thought_count,
        }
        for crab_id, brain in brains.items()
    ]


@app.post("/api/crabs")
async def create_crab(request: Request):
    """Create a new crab at runtime."""
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        return {"ok": False, "error": "name is required"}

    crab_id = name.lower()
    if crab_id in brains:
        return {"ok": False, "error": f"crab '{crab_id}' already exists"}

    # Create box directory
    project_root = os.path.dirname(os.path.dirname(__file__))
    box_path = os.path.join(project_root, f"{crab_id}_box")
    os.makedirs(box_path, exist_ok=True)

    # Generate identity with random entropy (no interactive keyboard mashing)
    seed_bytes = hashlib.sha256(
        f"{name}{time.time_ns()}{os.urandom(32).hex()}".encode()
    ).digest()
    genome_hex = seed_bytes.hex()
    traits = _derive_traits(seed_bytes)

    identity = {
        "name": name,
        "genome": genome_hex,
        "traits": traits,
        "born": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    with open(os.path.join(box_path, "identity.json"), "w") as f:
        json.dump(identity, f, indent=2)

    # Start the brain
    brain = Brain(identity, box_path)
    brains[crab_id] = brain
    asyncio.create_task(brain.run())
    logger.info(f"Created and started new crab: {name} ({crab_id})")

    return {"ok": True, "id": crab_id, "name": name}


@app.get("/api/identity")
async def get_identity(request: Request):
    """Get the crab's identity."""
    brain = _get_brain(request)
    return brain.identity


@app.get("/api/events")
async def get_events(request: Request, limit: int = 100):
    brain = _get_brain(request)
    return brain.events[-limit:]


@app.get("/api/raw")
async def get_raw(request: Request, limit: int = 20):
    """Get raw API call history."""
    brain = _get_brain(request)
    return brain.api_calls[-limit:]


@app.get("/api/status")
async def get_status(request: Request):
    brain = _get_brain(request)
    return {
        "state": brain.state,
        "thought_count": brain.thought_count,
        "importance_sum": brain.stream.importance_sum if brain.stream else 0,
        "reflection_threshold": config["reflection_threshold"],
        "memory_count": len(brain.stream.memories) if brain.stream else 0,
        "model": config["model"],
        "name": brain.identity["name"],
        "position": brain.position,
        "focus_mode": brain._focus_mode,
    }


@app.post("/api/focus-mode")
async def post_focus_mode(request: Request):
    """Toggle focus mode on or off."""
    brain = _get_brain(request)
    body = await request.json()
    enabled = bool(body.get("enabled", False))
    await brain.set_focus_mode(enabled)
    return {"ok": True, "focus_mode": enabled}


@app.post("/api/message")
async def post_message(request: Request):
    """Receive a message from the user (voice from outside the room)."""
    brain = _get_brain(request)
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        return {"ok": False, "error": "empty message"}
    if brain._waiting_for_reply:
        brain.receive_conversation_reply(text)
    else:
        brain.receive_user_message(text)
    return {"ok": True}


@app.post("/api/snapshot")
async def post_snapshot(request: Request):
    """Receive a canvas snapshot from the frontend."""
    brain = _get_brain(request)
    body = await request.json()
    brain.latest_snapshot = body.get("image")
    return {"ok": True}


@app.get("/api/files")
async def get_files(request: Request):
    brain = _get_brain(request)
    env_root = os.path.realpath(brain.env_path)
    files = []
    for dirpath, _, filenames in os.walk(env_root):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, env_root)
            if not rel.startswith("."):
                files.append(rel)
    files.sort()
    return {"files": files}


@app.get("/api/files/{path:path}")
async def get_file(request: Request, path: str):
    brain = _get_brain(request)
    env_root = os.path.realpath(brain.env_path)
    full = os.path.realpath(os.path.join(env_root, path))
    if not full.startswith(env_root):
        return {"path": path, "content": "Blocked: path outside environment."}
    try:
        with open(full, "r") as f:
            return {"path": path, "content": f.read()}
    except Exception as e:
        return {"path": path, "content": f"Error: {e}"}


# --- Static frontend ---

frontend_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(frontend_dist):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(frontend_dist, "assets")),
        name="assets",
    )

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        file_path = os.path.join(frontend_dist, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(frontend_dist, "index.html"))


# --- Startup ---


@app.on_event("startup")
async def startup():
    async def _start_brains():
        # Small delay so the server finishes binding the port first
        await asyncio.sleep(0.5)
        for crab_id, brain in brains.items():
            asyncio.create_task(brain.run())
            logger.info(f"{brain.identity['name']} ({crab_id}) starting...")

    asyncio.create_task(_start_brains())

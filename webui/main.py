"""FastAPI main entry point — serves API + static frontend."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from webui.config import WebUIConfig
from webui.api.strategies import router as strategies_router
from webui.api.metrics import router as metrics_router

logger = logging.getLogger(__name__)

# Ensure the logger outputs somewhere — uvicorn doesn't configure module loggers
if not logger.handlers and not logging.getLogger().handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)

config = WebUIConfig()

# Track WebSocket clients for live metrics
ws_clients: set[WebSocket] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background task for pushing metrics to WebSocket clients."""
    task = asyncio.create_task(_metrics_broadcaster())
    task.add_done_callback(lambda t: logger.error("Broadcaster crashed: %s", t.exception() if t.exception() else "unknown"))
    yield
    task.cancel()


app = FastAPI(
    title="Crypto Screener Web UI",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routers
app.include_router(strategies_router, prefix="/api")
app.include_router(metrics_router, prefix="/api")


# ── WebSocket: real-time metrics ──

@app.websocket("/api/ws/metrics")
async def metrics_websocket(websocket: WebSocket):
    await websocket.accept()
    ws_clients.add(websocket)
    try:
        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients.discard(websocket)


async def _metrics_broadcaster():
    """Push metric updates every 3 seconds to all WebSocket clients."""
    import aiohttp
    from webui.api.metrics import fetch_prometheus

    logger.warning("Metrics broadcaster started")
    while True:
        await asyncio.sleep(3)
        logger.debug("Broadcaster tick: %d clients", len(ws_clients))
        if not ws_clients:
            continue
        try:
            data = await fetch_prometheus()
            sb_count = len(data.get("signals_blocked", []))
            logger.info("Broadcasting %d keys (blocked=%d)", len(data), sb_count)
            payload = json.dumps({"type": "metrics", "data": data})
            dead = set()
            for ws in ws_clients:
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.add(ws)
            ws_clients.difference_update(dead)
        except Exception:
            logger.exception("Metrics broadcaster error")


# ── Static frontend ──

@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "webui"}


# Serve React SPA for all non-API routes
if config.static_dir and __import__("pathlib").Path(config.static_dir).exists():
    app.mount("/assets", StaticFiles(directory=f"{config.static_dir}/assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        if full_path.startswith("api/"):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        index = f"{config.static_dir}/index.html"
        return FileResponse(index)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    uvicorn.run(
        "webui.main:app",
        host=config.host,
        port=config.port,
        reload=config.debug,
    )


if __name__ == "__main__":
    main()

"""
MetricsServer — лёгкий HTTP-сервер для /metrics и /health.

Не требует FastAPI/Flask — использует встроенный asyncio HTTP server.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from core.monitoring.registry import get_metrics_registry
from core.monitoring.health import get_healthcheck

logger = logging.getLogger(__name__)


class MetricsServer:
    """Лёгкий HTTP сервер на asyncio.

    Endpoints:
    - GET /metrics → Prometheus text format
    - GET /health → JSON healthcheck
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9119):
        self.host = host
        self.port = port
        self._server: asyncio.AbstractServer | None = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5)
            if not request_line:
                writer.close()
                return

            path = request_line.decode("utf-8", errors="replace").strip().split(" ")[1]
            response_body = self._route(path)
            response = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/plain; charset=utf-8\r\n"
                f"Content-Length: {len(response_body)}\r\n"
                "Connection: close\r\n"
                "\r\n"
                f"{response_body}"
            )
            writer.write(response.encode("utf-8"))
            await writer.drain()
        except Exception:
            logger.exception("[metrics] Request handler error")
            writer.close()
        finally:
            writer.close()

    def _route(self, path: str) -> str:
        if path == "/metrics":
            return get_metrics_registry().prometheus_text()
        elif path == "/health":
            data = get_healthcheck().to_dict()
            return json.dumps(data, indent=2, ensure_ascii=False)
        elif path == "/":
            return json.dumps({
                "service": "crypto-screener-v2",
                "version": "v3",
                "endpoints": ["/metrics", "/health"],
            }, indent=2)
        else:
            return json.dumps({"error": "not found"}, indent=2)

    async def start(self):
        self._server = await asyncio.start_server(
            self._handle, host=self.host, port=self.port,
        )
        logger.info("[metrics] MetricsServer started on http://%s:%s", self.host, self.port)

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info("[metrics] MetricsServer stopped")

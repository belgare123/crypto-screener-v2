"""WebUI configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class WebUIConfig:
    host: str = os.getenv("WEBUI_HOST", "0.0.0.0")
    port: int = int(os.getenv("WEBUI_PORT", "8000"))
    debug: bool = os.getenv("WEBUI_DEBUG", "false").lower() == "true"

    # Prometheus (for metric queries)
    prometheus_url: str = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")

    # Frontend static files
    static_dir: str = os.getenv("WEBUI_STATIC_DIR", "/app/webui/frontend/dist")

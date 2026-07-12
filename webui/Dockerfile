FROM python:3.11-slim AS backend

WORKDIR /app

# Backend deps
COPY webui/requirements.txt /app/webui/requirements.txt
RUN pip install --no-cache-dir -r /app/webui/requirements.txt

# Copy project code (needed for signal registry)
COPY . .

# ── Frontend build (requires Node) ──
FROM node:20-alpine AS frontend

WORKDIR /app
COPY webui/frontend/package.json /app/package.json
COPY webui/frontend/ /app/
RUN npm install && npm run build

# ── Final image ──
FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn[standard] aiohttp websockets

COPY --from=backend /app /app
COPY --from=frontend /app/dist /app/webui/frontend/dist

# Unprivileged user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["python", "-m", "webui.main"]

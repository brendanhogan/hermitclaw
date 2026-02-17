# Stage 1: Build frontend
FROM node:20-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json ./
RUN npm install --quiet
COPY frontend/ ./
RUN npm run build

# Stage 2: Python runtime
FROM python:3.12-slim
WORKDIR /app

# System deps (pymupdf needs libmupdf; slim base needs it compiled in wheel)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[all]" 2>/dev/null \
    || pip install --no-cache-dir \
        "fastapi>=0.104.0" \
        "uvicorn>=0.24.0" \
        "websockets>=12.0" \
        "openai>=1.50.0" \
        "pyyaml>=6.0" \
        "pydantic>=2.0" \
        "pymupdf>=1.24.0"

# Copy backend source
COPY hermitclaw/ ./hermitclaw/
COPY config.yaml ./

# Copy built frontend from stage 1
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Box data lives at /data/boxes (host-mounted volume)
ENV BOX_ROOT=/data/boxes
ENV PORT=8000
ENV HERMITCLAW_HEADLESS=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["python", "-m", "hermitclaw.main"]

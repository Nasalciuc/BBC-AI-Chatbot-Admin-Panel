# ── BBC Chatbot API — Production Dockerfile ──────────────────
# Multi-stage: deps → production
# Usage: docker build -t bbc-api . && docker run --env-file .env -p 8000:8000 bbc-api

# ── Stage 1: Install dependencies ────────────────────────────
FROM python:3.11-slim AS deps
WORKDIR /app
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Stage 2: Production image ────────────────────────────────
FROM deps AS production
RUN useradd -m -s /bin/bash appuser
COPY app/ app/
COPY config/ config/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

USER appuser
EXPOSE 8000
CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1

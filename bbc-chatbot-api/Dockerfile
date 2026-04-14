# ── BBC Chatbot API — Production Dockerfile ──────────────────
# Multi-stage: deps → production
# Usage: docker build -t bbc-api . && docker run --env-file .env -p 8000:8000 bbc-api

# ── Stage 1: Install dependencies ────────────────────────────
FROM python:3.11-slim-bookworm AS deps
WORKDIR /app
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends ca-certificates && \
    apt-get clean && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── Stage 2: Production image ────────────────────────────────
FROM python:3.11-slim-bookworm AS production
WORKDIR /app
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends ca-certificates && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    useradd -m -s /bin/bash appuser
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin
COPY app/ /app/app/
COPY config/ /app/config/

ENV PYTHONPATH=/app:/app/app
ENV PYTHONUNBUFFERED=1

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)" || exit 1

USER appuser
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]

# syntax=docker/dockerfile:1
# One image for amd64 and arm64: python slim plus a uv-built virtualenv.
# The CollectorVision models ship in the wheel; its catalogue and the RapidOCR models are
# fetched on first use into /data, so the image itself stays free of downloads at runtime
# apart from those.

FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM python:3.13-slim
# libglib2 for OpenCV (headless build still links gthread); libgomp for onnxruntime.
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    DATA_DIR=/data \
    TZ=Australia/Sydney \
    COLLECTORVISION_CACHE=/data/cv-cache \
    OMP_NUM_THREADS=2
RUN useradd --uid 1000 --create-home app && mkdir -p /data && chown app:app /data
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
USER app
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1
CMD ["uvicorn", "app.asgi:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

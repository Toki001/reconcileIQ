FROM ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b AS uv
FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_CONCURRENT_DOWNLOADS=4 UV_HTTP_TIMEOUT=30 UV_HTTP_RETRIES=2
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY services/api/pyproject.toml services/api/pyproject.toml
COPY services/worker/pyproject.toml services/worker/pyproject.toml
COPY packages/database/pyproject.toml packages/database/pyproject.toml
COPY packages/reconciliation/pyproject.toml packages/reconciliation/pyproject.toml
COPY packages/ai/pyproject.toml packages/ai/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --all-packages --no-dev --no-install-workspace
COPY services/ services/
COPY packages/ packages/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --all-packages --no-dev

FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS common
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng poppler-utils \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
WORKDIR /app
COPY --from=build /app /app
COPY alembic.ini ./
COPY scripts/ scripts/
COPY tests/fixtures/financial/manifest.json tests/fixtures/financial/manifest.json
USER app
STOPSIGNAL SIGTERM

FROM common AS worker
HEALTHCHECK --interval=20s --timeout=10s --start-period=30s --retries=3 CMD ["python", "scripts/container_health.py", "worker"]
CMD ["celery", "-A", "reconcileiq_worker.app:celery_app", "worker", "--loglevel=WARNING"]

# API is the final/default target to preserve the development Compose interface.
FROM common AS api
EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=10s --start-period=20s --retries=3 CMD ["python", "scripts/container_health.py", "api"]
CMD ["uvicorn", "reconcileiq_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

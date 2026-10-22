FROM ghcr.io/astral-sh/uv:0.13.4 AS uv
FROM python:3.12-slim AS build
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

FROM python:3.12-slim AS runtime
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng poppler-utils \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
WORKDIR /app
COPY --from=build --chown=app:app /app /app
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app scripts/ scripts/
COPY --chown=app:app tests/fixtures/financial/manifest.json tests/fixtures/financial/manifest.json
USER app
EXPOSE 8000
CMD ["uvicorn", "reconcileiq_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

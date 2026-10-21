FROM ghcr.io/astral-sh/uv:0.13.4 AS uv
FROM python:3.12-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY services/ services/
COPY packages/ packages/
RUN uv sync --frozen --all-packages --no-dev

FROM python:3.12-slim AS runtime
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
WORKDIR /app
COPY --from=build --chown=app:app /app /app
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app scripts/ scripts/
USER app
EXPOSE 8000
CMD ["uvicorn", "reconcileiq_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

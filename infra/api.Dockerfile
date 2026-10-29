FROM ghcr.io/astral-sh/uv:0.8.13 AS uv
FROM python:3.12.11-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY apps/api/src ./apps/api/src
COPY apps/api/migrations ./apps/api/migrations
COPY apps/api/alembic.ini ./apps/api/alembic.ini
RUN uv sync --frozen --no-dev --no-editable --no-cache
USER 10001:10001
ENV PYTHONUNBUFFERED=1 DOCTA_DEV_MANAGED=1
CMD ["/app/.venv/bin/python", "-m", "uvicorn", "docta_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

FROM ghcr.io/astral-sh/uv:0.8.13 AS uv
FROM python:3.12.11-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY apps/api/src ./apps/api/src
RUN uv sync --frozen --no-dev --no-editable --no-cache
USER 10001:10001
ENV PYTHONUNBUFFERED=1
CMD ["/app/.venv/bin/python", "-m", "docta_api.worker"]

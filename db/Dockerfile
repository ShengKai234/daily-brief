FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY brief_db/ ./brief_db/
ENTRYPOINT ["/app/.venv/bin/python", "-m", "brief_db"]

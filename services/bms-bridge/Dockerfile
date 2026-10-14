# Build context is the repository root (the shared bms-health package lives in packages/).
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
WORKDIR /app/services/bms-bridge
COPY packages/bms-health /app/packages/bms-health
COPY services/bms-bridge/pyproject.toml services/bms-bridge/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY services/bms-bridge/ .
ENV PATH="/app/services/bms-bridge/.venv/bin:$PATH" PYTHONUNBUFFERED=1
EXPOSE 8001
CMD ["python", "main.py"]

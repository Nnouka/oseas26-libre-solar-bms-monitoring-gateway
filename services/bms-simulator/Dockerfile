FROM python:3.12-slim

# uv is only needed at build time, to install exactly what uv.lock says
RUN pip install --no-cache-dir uv

WORKDIR /app

# 1) dependencies first (cached unless pyproject.toml or uv.lock change)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) then the code
COPY *.py ./

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

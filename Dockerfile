FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . .
RUN pip install --no-cache-dir .

RUN playwright install --with-deps chromium

EXPOSE ${PORT:-8000}

CMD ["python", "-m", "docs_mcp.api"]

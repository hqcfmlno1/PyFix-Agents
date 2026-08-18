FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYFIX_MCP_SERVER_HOST=127.0.0.1 \
    PYFIX_MCP_SERVER_PORT=8000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY . .
RUN chmod +x /app/docker-entrypoint.sh /app/bug_arena_adapter.sh

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["main"]

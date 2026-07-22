FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers

# docker CLI for sandbox `docker exec` (Phase C); curl for healthchecks;
# gcc/libpq-dev for psycopg2 source build (mem0 1.x graph deps)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates gnupg gcc python3-dev libpq-dev \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && echo "deb [signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Chromium for the enrichment agent (About/Contact page navigation)
RUN playwright install --with-deps chromium

# Node.js (NodeSource 20 LTS) + Browser MCP agent (@playwright/mcp) for the Google Search
# connector — navigates the date-filtered SERP and works through consent/cookie/selection
# dialogs. Installs the Node-side Chromium into the shared PLAYWRIGHT_BROWSERS_PATH.
RUN curl -fsSL https://deb.nodesource.com/setup_20.x -o /tmp/ns.sh && bash /tmp/ns.sh \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @playwright/mcp@latest playwright \
    && npx playwright install chromium \
    && npm cache clean --force \
    && rm -rf /var/lib/apt/lists/* /tmp/ns.sh

COPY . .

EXPOSE 8002
# Migrate then serve on $PORT (Render/Fly set it). docker-compose overrides `command`.
CMD ["sh", "docker/start.sh"]

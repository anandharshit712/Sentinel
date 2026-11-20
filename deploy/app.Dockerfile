# Sentinel application image: the Delivery Gateway (with the built dashboard, single origin) and the
# neuro-san agent server. One image, two commands (see deploy/compose.yaml). Multi-arch: every base
# image publishes amd64 + arm64 and every Python dependency ships wheels for both.
#
#   docker build -f deploy/app.Dockerfile --build-arg SENTINEL_VERSION=$(git rev-parse --short HEAD) -t sentinel-app .
#
# Build context is the repo root; deploy/app.Dockerfile.dockerignore keeps .env, .venv,
# node_modules and the vendored neuro-san-studio clone out of it.

FROM node:20-bookworm-slim AS spa
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=spa /src/frontend/dist frontend/dist

ARG SENTINEL_VERSION=dev
# Runs never execute repository code in this image (SANDBOX_MODE=docker hands that to sandboxd),
# so it runs unprivileged. /workspaces is created here, owned by that user, so the named volume
# mounted over it starts out writable.
RUN useradd --uid 10001 --create-home sentinel \
 && mkdir -p /workspaces /app/logs/thinking_dir /app/.cache \
 && chown -R sentinel:sentinel /workspaces /app/logs /app/.cache
ENV SENTINEL_VERSION=${SENTINEL_VERSION} PYTHONPATH=/app PYTHONUNBUFFERED=1 \
    WORKSPACE_ROOT=/workspaces SANDBOX_MODE=docker \
    AGENT_MANIFEST_FILE=registries/manifest.hocon AGENT_TOOL_PATH=coded_tools \
    AGENT_LLM_INFO_FILE=config/custom_llm_info.hocon AGENT_HTTP_PORT=8080 \
    AGENT_SERVICE_LOG_JSON=logging.hocon THINKING_DIR=logs/thinking_dir
USER sentinel
EXPOSE 8000 8080
# Default: the Gateway. Migrations run first so a fresh volume comes up ready.
CMD ["sh", "-c", "cd db && python -m alembic upgrade head && cd .. && python scripts/run_gateway.py"]

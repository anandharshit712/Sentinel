# Per-run sandbox image: where repository code (installs, tests, mutants, oracle probes) runs.
# Built and run only through sandboxd (sandboxd/server.py). Multi-arch: both base images publish
# amd64 + arm64, and nothing here downloads an arch-specific binary.
#
#   docker build -t sentinel-sandbox:latest sandbox/
FROM node:20-bookworm-slim AS node

FROM python:3.12-slim-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && apt-get update && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir "pytest>=8.0" "pytest-cov>=5.0" \
 && mkdir -p /work /scratch /cache && chmod 1777 /cache
# sandboxd runs this with --user <uid> and a read-only root: HOME and caches must live on tmpfs
# or the mounted per-repo cache volume.
ENV HOME=/tmp PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1 NPM_CONFIG_UPDATE_NOTIFIER=false
WORKDIR /work
CMD ["sleep", "infinity"]

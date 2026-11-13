# syntax=docker/dockerfile:1.7
# The RepoAtlas worker: background jobs, which are snapshot builds (DEP-1). Build from the
# repository root:
#   docker build -f infra/docker/worker.Dockerfile -t repoatlas-worker .
#
# Same structure as api.Dockerfile (multi-stage, uv from the lockfile, a user without
# privileges, base images pinned by digest), plus git, which builds use to fetch repositories.
#
# The worker embeds every chunk of a snapshot, so the embedding model is in the image: it is
# downloaded while the image is built, and a cold start downloads nothing. Build with
# --build-arg EMBEDDING_MODEL=hash-256 for an image without it (smoke checks only).
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.3@sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
# Dependencies first, so this layer is rebuilt only when the lockfile changes.
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY backend/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM build AS model
ARG EMBEDDING_MODEL=jinaai/jina-embeddings-v2-base-code
ENV REPOATLAS_EMBEDDING_MODEL=${EMBEDDING_MODEL} \
    REPOATLAS_MODEL_CACHE_DIR=/opt/models
RUN mkdir -p /opt/models && /opt/venv/bin/repoatlas models fetch

FROM ${PYTHON_IMAGE} AS runtime
ARG EMBEDDING_MODEL=jinaai/jina-embeddings-v2-base-code
LABEL org.opencontainers.image.title="repoatlas-worker" \
      org.opencontainers.image.source="https://github.com/rajatsxngh/RepoAtlas" \
      org.opencontainers.image.licenses="MIT"
RUN apt-get update \
    && apt-get install --yes --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 repoatlas \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /data \
       --shell /usr/sbin/nologin repoatlas \
    && mkdir -p /data \
    && chown 10001:10001 /data
COPY --from=build /opt/venv /opt/venv
COPY --from=model --chown=10001:10001 /opt/models /opt/models
# HF_HUB_OFFLINE: the model is already in the image; never look for a newer one at start-up.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1 \
    REPOATLAS_EMBEDDING_MODEL=${EMBEDDING_MODEL} \
    REPOATLAS_MODEL_CACHE_DIR=/opt/models \
    REPOATLAS_DATA_DIR=/data \
    REPOATLAS_LOG_FORMAT=json
WORKDIR /data
USER 10001:10001
# SIGTERM stops the worker gracefully: the current job goes back to the queue.
STOPSIGNAL SIGTERM
CMD ["repoatlas", "worker"]

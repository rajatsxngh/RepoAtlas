# Sandbox base image for Python benchmark repos (decision 0012). Every input is pinned:
# upstream images by digest, Python and uv by version, scip-python by package-lock.json.
# Built from our own files only; repo code is added later by the image builder.
FROM ghcr.io/astral-sh/uv:0.12.3@sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc AS uv

FROM docker.io/library/node:22-bookworm-slim@sha256:18052396bf1d0c6e0a8c58181b5c1457da19409efbd5032a3f4d86974e74d13c
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_NO_CACHE=1 \
    UV_LINK_MODE=copy
RUN uv python install 3.12.13 \
 && uv venv --seed --python 3.12.13 /opt/venv \
 && groupadd --gid 10001 sandbox \
 && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/sandbox --shell /bin/sh sandbox \
 && mkdir -p /src /tmp/out \
 && chown -R 10001:10001 /src /opt/venv
COPY scip-python/package.json scip-python/package-lock.json /opt/scip/
RUN cd /opt/scip && npm ci --ignore-scripts --omit=dev --no-audit --no-fund \
 && ln -s /opt/scip/node_modules/.bin/scip-python /usr/local/bin/scip-python \
 && npm cache clean --force
ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    HOME=/home/sandbox \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
USER 10001:10001
WORKDIR /src

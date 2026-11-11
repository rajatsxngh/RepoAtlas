# Sandbox base image for TypeScript benchmark repos (decision 0012). Every input is pinned:
# the upstream image by digest, scip-typescript by package-lock.json, pnpm/yarn through corepack
# (each repo's packageManager field pins its version). Repo code is added later by the builder.
FROM docker.io/library/node:22-bookworm-slim@sha256:18052396bf1d0c6e0a8c58181b5c1457da19409efbd5032a3f4d86974e74d13c
RUN groupadd --gid 10001 sandbox \
 && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/sandbox --shell /bin/sh sandbox \
 && mkdir -p /src /tmp/out \
 && chown -R 10001:10001 /src \
 && corepack enable
COPY scip-typescript/package.json scip-typescript/package-lock.json /opt/scip/
RUN cd /opt/scip && npm ci --ignore-scripts --omit=dev --no-audit --no-fund \
 && ln -s /opt/scip/node_modules/.bin/scip-typescript /usr/local/bin/scip-typescript \
 && npm cache clean --force
ENV HOME=/home/sandbox \
    COREPACK_HOME=/home/sandbox/.corepack \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_AUDIT=false
USER 10001:10001
WORKDIR /src

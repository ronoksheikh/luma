# syntax=docker/dockerfile:1.7
# Luma Studio — single container, single port.
#   docker compose up --build
# Multi-arch: linux/amd64 and linux/arm64 (all Python wheels, incl. skia-python, ship
# manylinux_2_28 builds for x86_64 and aarch64).

ARG NODE_IMAGE=node:22.23.3-bookworm-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c
ARG BASE_IMAGE=python:3.11.16-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b

# ---------------------------------------------------------------- frontend build
FROM ${NODE_IMAGE} AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi; \
    npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- runtime
FROM ${BASE_IMAGE} AS runtime
ARG INTER_VERSION=4.1
ARG INTER_SHA256=9883fdd4a49d4fb66bd8177ba6625ef9a64aa45899767dde3d36aa425756b11e
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LANG=C.UTF-8

COPY docker/install-system.sh /tmp/install-system.sh
RUN --mount=type=secret,id=extra_ca,required=false \
    INTER_VERSION=${INTER_VERSION} INTER_SHA256=${INTER_SHA256} bash /tmp/install-system.sh && rm /tmp/install-system.sh

# Node.js for the agent (JS tooling in the sandbox): the same pinned build as the frontend stage
COPY --from=frontend /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && node --version && npm --version

# Python environment (fully pinned lock)
COPY docker/requirements.lock /tmp/requirements.lock
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export PIP_CERT=/etc/ssl/certs/ca-certificates.crt; fi; \
    python3.11 -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip==25.2 \
 && /opt/venv/bin/pip install --no-deps -r /tmp/requirements.lock \
 && /opt/venv/bin/pip check \
 && rm /tmp/requirements.lock

# Users: `studio` runs the backend and owns the secrets; `luma` is the agent's sandbox
# user (passwordless sudo INSIDE the container). Both share the `lumawork` group.
RUN if getent passwd 1000 >/dev/null; then userdel -r "$(getent passwd 1000 | cut -d: -f1)" 2>/dev/null || true; fi \
 && groupadd -g 1500 lumawork \
 && useradd -m -u 1000 -s /bin/bash -G lumawork luma \
 && useradd -m -u 1001 -s /usr/sbin/nologin -G lumawork studio \
 && groupadd -g 1501 lumanonet \
 && echo "luma ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/luma-sandbox \
 && chmod 440 /etc/sudoers.d/luma-sandbox \
 && mkdir -p /data && chown studio:lumawork /data && chmod 2775 /data
COPY --chmod=440 docker/sudoers-studio /etc/sudoers.d/studio
COPY --chmod=755 docker/luma-netctl /usr/local/sbin/luma-netctl
COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN visudo -c

COPY engine /opt/luma/engine
RUN /opt/venv/bin/pip install --no-deps --no-build-isolation /opt/luma/engine \
 && /opt/venv/bin/python -c "import luma_engine, skia; print('luma_engine', luma_engine.__version__, 'skia', skia.__version__)"
COPY backend /opt/luma/backend
COPY prompts /opt/luma/prompts
COPY samples /opt/luma/samples
COPY toolbox_seed /opt/luma/toolbox_seed
COPY --from=frontend /src/dist /opt/luma/frontend/dist

# The sandbox user's pip/npm installs go to its home; the venv is on its PATH.
RUN printf '%s\n' 'export PATH=/opt/venv/bin:$HOME/.local/bin:$PATH' >> /home/luma/.profile \
 && chown luma:luma /home/luma/.profile

ENV PATH=/opt/venv/bin:$PATH \
    LUMA_DATA_DIR=/data \
    LUMA_STATIC_DIR=/opt/luma/frontend/dist \
    LUMA_PROMPTS_DIR=/opt/luma/prompts \
    LUMA_SAMPLES_DIR=/opt/luma/samples \
    LUMA_HOST=0.0.0.0 \
    LUMA_PORT=8080 \
    LUMA_SANDBOX_USER=luma \
    LUMA_SANDBOX_GROUP=lumawork \
    LUMA_SANDBOX_PYTHON=/opt/venv/bin/python \
    LUMA_SANDBOX_PATH=/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LUMA_NETCTL="sudo -n /usr/local/sbin/luma-netctl" \
    LUMA_TOOLBOX_SEED=/opt/luma/toolbox_seed \
    LUMA_NONET_GROUP=lumanonet

EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]

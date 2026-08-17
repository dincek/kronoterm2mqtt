# syntax=docker/dockerfile:1

# Hardened, least-privilege image for kronoterm2mqtt.
#
#  * multi-stage: compilers and the package manager stay in the builder
#  * base image pinned by digest so builds are reproducible
#  * runs as an unprivileged user (UID/GID 65532, the "nonroot" convention)
#  * no runtime bootstrap: the venv is fully built at image build time, so the
#    container needs neither a writable /app nor network access to PyPI
#
# Rebuild the digest with:
#   docker pull python:3.14-slim && docker image inspect python:3.14-slim \
#     --format '{{index .RepoDigests 0}}'
ARG PYTHON_IMAGE=python@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4
ARG UV_VERSION=0.12.5
ARG APP_UID=65532
ARG APP_GID=65532


# --------------------------------------------------------------------------
# Builder: creates /opt/venv with all runtime dependencies
# --------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder

ARG UV_VERSION

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

# Build dependencies for source-only wheels. They never reach the final image.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libc6-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "uv==${UV_VERSION}"

WORKDIR /app

# Project metadata first, so the dependency layer is cached independently
COPY uv.lock pyproject.toml README.md ./

# Submodule that kronoterm2mqtt/pyetera_uart_bridge symlinks into
COPY etera-uart-bridge/pyetera-uart-bridge/pyetera-uart-bridge/ \
     etera-uart-bridge/pyetera-uart-bridge/pyetera-uart-bridge/

# Source code plus the symlink that git tracks but the build context flattens
COPY kronoterm2mqtt/ kronoterm2mqtt/
RUN rm -rf kronoterm2mqtt/pyetera_uart_bridge && \
    ln -s ../etera-uart-bridge/pyetera-uart-bridge/pyetera-uart-bridge \
          kronoterm2mqtt/pyetera_uart_bridge

# Locked, reproducible install of runtime deps + the project itself
RUN uv sync --frozen --no-dev


# --------------------------------------------------------------------------
# Runtime: no compilers, no package manager, no root
# --------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

ARG APP_UID
ARG APP_GID

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    HOME=/home/nonroot

# Latest OS security patches, an unprivileged account, and the config directory
# the settings loader expects (~/.config/kronoterm2mqtt).
RUN apt-get update && \
    apt-get upgrade -y && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd --gid "${APP_GID}" nonroot && \
    useradd --uid "${APP_UID}" --gid "${APP_GID}" \
            --home-dir /home/nonroot --create-home \
            --shell /usr/sbin/nologin nonroot && \
    mkdir -p /home/nonroot/.config/kronoterm2mqtt && \
    chown -R "${APP_UID}:${APP_GID}" /home/nonroot && \
    # Strip setuid/setgid bits: nothing in here needs privilege escalation
    find / -xdev -perm /6000 -type f -exec chmod a-s {} + || true && \
    # Remove package managers so an attacker cannot install tooling. The dpkg
    # database stays, so image scanners (Trivy, Grype, docker scout) still work.
    rm -rf /usr/local/lib/python*/site-packages/pip* /usr/local/bin/pip* \
           /usr/bin/apt /usr/bin/apt-get /usr/bin/apt-cache /usr/bin/apt-config \
           /usr/bin/apt-key /usr/bin/apt-mark /usr/bin/dpkg /usr/bin/dpkg-deb \
           /usr/bin/dpkg-query /usr/bin/dpkg-split /usr/bin/dpkg-trigger

# Application and venv stay root-owned and read-only for the app user, so the
# process cannot rewrite its own code.
COPY --from=builder --chown=root:root /opt/venv /opt/venv
COPY --from=builder --chown=root:root /app /app

WORKDIR /app
USER ${APP_UID}:${APP_GID}

ENTRYPOINT ["/opt/venv/bin/kronoterm2mqtt_app"]
# -v sets the log level to WARNING so Modbus retries and reconnects show up in
# the container logs. Override with `command:` in compose for more/less output.
CMD ["publish-loop", "-v"]

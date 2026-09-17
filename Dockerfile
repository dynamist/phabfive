# Scratch image holding phabfive and a uv-managed Python under /opt/phabfive
# (python/ is the interpreter, venv/ the environment, bin/ only phabfive).
# It is not runnable on its own, it is meant to be copied into other images:
#
#   COPY --from=ghcr.io/dynamist/phabfive:latest /opt/phabfive /opt/phabfive
#   ENV PATH=/opt/phabfive/bin:$PATH
#
# The tree contains absolute paths, so it must be copied to /opt/phabfive.
# Build with LIBC=musl for Alpine based images.

ARG LIBC=gnu

FROM debian:trixie-slim AS base-gnu
FROM alpine:3.24 AS base-musl

FROM base-${LIBC} AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /bin/uv

ENV UV_PYTHON_INSTALL_DIR=/opt/phabfive/python \
    UV_PROJECT_ENVIRONMENT=/opt/phabfive/venv \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_PYTHON=3.14 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /src

# Install dependencies first for better layer caching
COPY pyproject.toml uv.lock /src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --no-install-project

COPY README.md /src/
COPY phabfive/ /src/phabfive/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable \
    && rm -rf /opt/phabfive/python/.lock /opt/phabfive/python/.temp /opt/phabfive/python/.cache \
    && mkdir /opt/phabfive/bin \
    && ln -s ../venv/bin/phabfive /opt/phabfive/bin/phabfive

# Smoke test the tree in a clean image, without uv or the sources. The same
# script gates the wheel and the standalone executables, so all three
# artifacts are held to one standard. Run with the venv's interpreter: the
# base images have no Python of their own.
FROM base-${LIBC} AS test
COPY --from=builder /opt/phabfive /opt/phabfive
COPY scripts/smoke.py /tmp/smoke.py
RUN /opt/phabfive/venv/bin/python /tmp/smoke.py --executable /opt/phabfive/bin/phabfive

FROM scratch
LABEL org.opencontainers.image.source="https://github.com/dynamist/phabfive" \
      org.opencontainers.image.description="phabfive CLI for Phabricator and Phorge, to be copied from into other images" \
      org.opencontainers.image.licenses="Apache-2.0"
# Copy from the test stage so the smoke test always runs
COPY --from=test /opt/phabfive /opt/phabfive

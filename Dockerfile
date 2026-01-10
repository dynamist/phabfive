FROM alpine:3.22

# Install terminfo database for proper terminal support
RUN apk add --no-cache ncurses-terminfo-base

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set uv environment variables
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Copy dependency files first for better layer caching
COPY pyproject.toml uv.lock /app/

# Install dependencies into virtual environment with cache mount
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Copy the rest of the application
COPY README.md /app/
COPY phabfive/ /app/phabfive/

# Install the project itself with cache mount
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

ENTRYPOINT ["/app/.venv/bin/phabfive"]
CMD []

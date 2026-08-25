# Multi-stage build for Python 3.12 with uv (pinned exact version)
FROM python:3.12-slim AS builder

# Install pinned uv for deterministic dependency resolution
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /uvx /bin/

WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Copy dependency specifications
COPY pyproject.toml uv.lock ./

# Install production dependencies into /app/.venv
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Copy source code and install project
COPY src/ ./src/
COPY README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Final runtime image
FROM python:3.12-slim AS runner

# Create non-root system user
RUN groupadd -r nadirabot && useradd -r -g nadirabot -d /app -s /sbin/nologin nadirabot

WORKDIR /app

# Copy virtual environment and application from builder
COPY --from=builder --chown=nadirabot:nadirabot /app/.venv /app/.venv
COPY --from=builder --chown=nadirabot:nadirabot /app/src /app/src

# Set environment paths
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

USER nadirabot

# Run Nadira Discord Music Bot
CMD ["python", "-m", "nadira_bot"]

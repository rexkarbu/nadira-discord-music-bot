# Multi-stage build for Python 3.12 with uv
FROM python:3.12-slim AS builder

# Install uv for fast, deterministic dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

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

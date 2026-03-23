FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Install dependencies first (cached layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy source
COPY src/ ./src/

# Non-root user for security
RUN useradd -m -u 1000 hyrax && chown -R hyrax:hyrax /app
USER hyrax

# Persistent memory volume
VOLUME ["/memory"]
ENV MEMORY_DIR=/memory

ENTRYPOINT ["uv", "run", "python", "-m", "hyrax.main"]

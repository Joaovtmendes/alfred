FROM python:3.12-slim

# Security: non-root user
RUN groupadd --gid 1001 alfred && \
    useradd --uid 1001 --gid alfred --shell /bin/bash --create-home alfred

WORKDIR /app
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first (cache layer)
COPY pyproject.toml ./
# Create minimal src so pip can resolve the package metadata
RUN mkdir -p src/alfred && touch src/alfred/__init__.py
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -e ".[prod]"

# Copy actual source (overwrites the placeholder)
COPY --chown=alfred:alfred src/ ./src/
COPY --chown=alfred:alfred alembic/ ./alembic/
COPY --chown=alfred:alfred alembic.ini ./
COPY --chown=alfred:alfred start.sh .
COPY --chown=alfred:alfred scripts/ ./scripts/
RUN chmod +x /app/scripts/*.py
RUN chmod +x /app/start.sh

USER alfred

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["bash", "/app/start.sh"]

# rebuild 20260925

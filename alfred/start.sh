#!/bin/bash
set -e
exec 1>&2  # redirect stdout to stderr so Railway captures all output

echo "=== Alfred startup ==="
echo "Working dir: $(pwd)"
echo "Python: $(python --version 2>&1)"
echo "PORT: ${PORT:-8000}"

echo "--- Running alembic migrations ---"
alembic upgrade head
echo "--- Alembic done ---"

echo "--- Starting uvicorn ---"
exec uvicorn alfred.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --log-level info

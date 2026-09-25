#!/bin/bash
set -e

echo "=== Alfred startup ==="
echo "Working dir: $(pwd)"
echo "Python: $(python --version 2>&1)"

echo "--- Running alembic migrations ---"
alembic upgrade head
echo "--- Alembic done ---"

echo "--- Testing Python imports ---"
python - << 'PYEOF'
import sys
try:
    import alfred.main
    print("IMPORT_OK: alfred.main loaded")
except Exception as e:
    print(f"IMPORT_ERROR: {type(e).__name__}: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)
PYEOF

echo "--- Starting uvicorn on port ${PORT:-8000} ---"
exec uvicorn alfred.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --log-level info

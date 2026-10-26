#!/bin/sh
set -eu

python -m alembic upgrade head
exec python -m uvicorn src.main:app \
    --host 0.0.0.0 --port 8000 --workers 1 \
    --no-proxy-headers --no-server-header

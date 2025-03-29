#!/bin/sh
set -e
PORT=${PORT:-8000}
echo "Starting Gunicorn..."
echo "Listening on: 0.0.0.0:${PORT}"
echo "Worker Temp Dir: /dev/shm"
exec gunicorn app:app \
    --worker-tmp-dir /dev/shm \
    --workers=3 \
    --timeout=120 \
    --bind=0.0.0.0:${PORT} \
    --log-level=info
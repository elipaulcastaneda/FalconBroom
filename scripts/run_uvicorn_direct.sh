#!/usr/bin/env bash
HOST=${1:-127.0.0.1}
PORT=${2:-3009}
LIFESPAN_OFF=${3:-0}

# prefer venv python if available
VENV_PYTHON=".venv/bin/python"
if [ -x "$VENV_PYTHON" ]; then
  PYTHON="$VENV_PYTHON"
else
  PYTHON="python3"
fi

LIFESPAN_ARG=""
if [ "$LIFESPAN_OFF" = "1" ] || [ "$LIFESPAN_OFF" = "true" ]; then
  LIFESPAN_ARG="--lifespan off"
fi

case "$HOST" in
  127.0.0.1|::1|localhost)
    export ENV=development
    export FALCONBROOM_LOCAL_MODE=1
    ;;
  *)
    export ENV=production
    export FALCONBROOM_LOCAL_MODE=0
    ;;
esac

exec "$PYTHON" -m uvicorn fbroom.main:app --host "$HOST" --port "$PORT" --log-level debug $LIFESPAN_ARG

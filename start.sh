#!/bin/sh
BASE=$(cd "$(dirname "$0")" && pwd)
cd "$BASE" || exit 1

export PYTHONPATH="$BASE/../litellm:$PYTHONPATH"

CMD="/usr/bin/python3 $BASE/app.py"
ESCAPED=$(printf '%s' "$CMD" | sed 's/[][\.*^$/]/\\&/g')

PID=$(pgrep -f "^$ESCAPED\$")

if [ "$1" = "status" ]; then
    if [ -n "$PID" ]; then
        echo "running: $CMD (pid $PID)"
    else
        echo "not running: $CMD"
    fi
    echo -n "health: "
    curl -s -m 3 "http://localhost:${PORT:-4000}/health/liveliness" || echo "unreachable"
    echo
    exit 0
fi

if [ -n "$PID" ]; then
    echo "stopping: $CMD (pid $PID)"
    pkill -f "^$ESCAPED\$"
    while pgrep -f "^$ESCAPED\$" >/dev/null 2>&1; do
        sleep 0.2
    done
fi

if [ "$1" = "kill" ]; then
    exit 0
fi

if [ "$1" = "debug" ]; then
    export LITELLM_DEBUG=true
else
    unset LITELLM_DEBUG
fi

echo "starting: $CMD (debug=${LITELLM_DEBUG:-false})"
rm -f "$BASE/litellm-minimal.log"
nohup $CMD >>"$BASE/litellm-minimal.log" 2>&1 &
echo "pid $!"


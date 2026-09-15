#!/bin/sh
# deepseek-gateway (Termux / Linux / macOS)
DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$DIR/gateway.py" "$@"
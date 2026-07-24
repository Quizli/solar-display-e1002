#!/bin/sh
set -eu

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

cd "$PROJECT_DIR"

sudo docker run --rm \
  --user "$(id -u):$(id -g)" \
  -v "$PROJECT_DIR:/app" \
  -w /app \
  python:3.12-slim \
  python renderer/src/render.py

echo
echo "Rendered SVG:"
echo "$PROJECT_DIR/renderer/output/dashboard.svg"

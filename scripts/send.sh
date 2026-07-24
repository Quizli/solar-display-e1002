#!/bin/sh
set -eu

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
OUTPUT_DIR="$PROJECT_DIR/publish"
SOURCE_FILE="$PROJECT_DIR/renderer/output/dashboard.svg"
TARGET_FILE="$OUTPUT_DIR/dashboard.svg"

mkdir -p "$OUTPUT_DIR"

"$PROJECT_DIR/scripts/render.sh"

cp "$SOURCE_FILE" "$TARGET_FILE"

echo
echo "Prepared for sending:"
echo "$TARGET_FILE"
echo
echo "Current mode: render + publish/stage only."
echo "Final direct E1002 upload step will be added once the target interface is fixed."

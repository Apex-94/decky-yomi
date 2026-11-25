#!/bin/bash
# ──────────────────────────────────────────────────────────────
# Decky Yomi — Install Python dependencies for vendoring
# Run this on a development machine (or in Desktop Mode on Deck)
# to populate py_modules/ before deploying the plugin.
# ──────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(dirname "$SCRIPT_DIR")"
PY_MODULES="$PLUGIN_DIR/py_modules"
REQUIREMENTS="$SCRIPT_DIR/requirements.txt"

echo "═══════════════════════════════════════════"
echo "  Decky Yomi — Dependency Installer"
echo "═══════════════════════════════════════════"
echo ""
echo "Plugin dir:  $PLUGIN_DIR"
echo "Target:      $PY_MODULES"
echo "Requirements: $REQUIREMENTS"
echo ""

# Clean previous install
if [ -d "$PY_MODULES" ]; then
    echo "🗑  Removing old py_modules/..."
    rm -rf "$PY_MODULES"
fi
mkdir -p "$PY_MODULES"

mkdir -p "$PLUGIN_DIR/installer-tmp"
export TMPDIR="$PLUGIN_DIR/installer-tmp"

# Bootstrap pip privately; never modify the system Python installation.
python3 -I -m venv "$PLUGIN_DIR/installer-venv"
PIP_PYTHON="$PLUGIN_DIR/installer-venv/bin/python"

echo "📦 Installing dependencies into $PY_MODULES ..."
echo ""

"$PIP_PYTHON" -I -m pip install \
    --target="$PY_MODULES" \
    --no-cache-dir \
    --upgrade \
    --require-hashes \
    -r "$REQUIREMENTS"

INPUT_REQUIREMENTS="$SCRIPT_DIR/requirements-input.txt"
if [ -f "$INPUT_REQUIREMENTS" ] && { command -v gcc >/dev/null 2>&1 || command -v cc >/dev/null 2>&1; } && [ -f /usr/include/linux/input.h ]; then
    "$PIP_PYTHON" -I -m pip install \
        --target="$PY_MODULES" \
        --no-cache-dir \
        --upgrade \
        --require-hashes \
        -r "$INPUT_REQUIREMENTS"
fi

echo ""
echo "═══════════════════════════════════════════"
echo "✅ Dependencies installed successfully!"
echo "📦 Size: $(du -sh "$PY_MODULES" | cut -f1)"
echo ""
echo "Contents:"
ls -1 "$PY_MODULES/" | head -20
echo "═══════════════════════════════════════════"

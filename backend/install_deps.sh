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
VENV_DIR="$PLUGIN_DIR/installer-venv"
PIP_PYTHON="$VENV_DIR/bin/python"
if ! "$PIP_PYTHON" -I -c "import pip" >/dev/null 2>&1; then
    if ! python3 -I -m venv --clear "$VENV_DIR"; then
        echo "ensurepip unavailable; bootstrapping pip into private venv..."
        python3 -I -m venv --clear --without-pip "$VENV_DIR"
        GET_PIP="$PLUGIN_DIR/installer-tmp/get-pip.py"
        GET_PIP_URL="https://raw.githubusercontent.com/pypa/get-pip/8c427468071b5d15717839ffd76c53a96df8b703/public/get-pip.py"
        GET_PIP_SHA256="356e0d0de21cdc348c3394084d90939b85e4246c4d14c76aee6ca4f46646ea98"
        curl -fsSL "$GET_PIP_URL" -o "$GET_PIP"
        if ! echo "$GET_PIP_SHA256  $GET_PIP" | sha256sum -c - >/dev/null 2>&1; then
            rm -f "$GET_PIP"
            echo "ERROR: get-pip.py SHA-256 verification failed" >&2
            exit 1
        fi
        "$PIP_PYTHON" -I "$GET_PIP" --no-cache-dir
    fi
fi

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

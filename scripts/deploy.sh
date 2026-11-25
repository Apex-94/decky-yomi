#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_DIR="/home/deck/homebrew/plugins/decky-yomi"

cd "$ROOT_DIR"

echo "==> Building frontend..."
if command -v pnpm >/dev/null 2>&1; then
  pnpm run build
else
  npm run build
fi
node scripts/check-bundle.mjs

echo "==> Syncing runtime files to $PLUGIN_DIR..."
cp -r dist/* "$PLUGIN_DIR/dist/"
cp main.py "$PLUGIN_DIR/"
cp backend/*.py backend/*.sh backend/requirements.txt "$PLUGIN_DIR/backend/"
cp -r defaults/* "$PLUGIN_DIR/defaults/"
cp -r assets/* "$PLUGIN_DIR/assets/"
for f in plugin.json package.json LICENSE README.md; do
  if [ -w "$PLUGIN_DIR/$f" ]; then
    cp "$f" "$PLUGIN_DIR/$f"
  fi
done

echo "==> Triggering Decky plugin reload..."
if curl -sf "http://127.0.0.1:1337/plugins/reload" >/dev/null 2>&1; then
  echo "Reloaded frontend via Decky API (port 1337)."
else
  echo "Decky reload endpoint did not respond; use Developer -> Reload in Decky UI."
fi
echo "Note: If Python backend files (main.py / backend/*.py) changed, restart plugin_loader:"
echo "  sudo systemctl restart plugin_loader"

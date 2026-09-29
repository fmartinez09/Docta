#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
git config --global --add safe.directory "$PWD"
# Named volumes keep Windows dependencies out of the Linux environment.
sudo chown "$(id -u):$(id -g)" .venv node_modules apps/web/node_modules apps/web/.next
uv sync --frozen
npm ci
npx playwright install --with-deps chromium

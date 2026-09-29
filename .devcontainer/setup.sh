#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
git config --global --add safe.directory "$PWD"
if ! git rev-parse --git-common-dir >/dev/null 2>&1; then
  echo 'Git metadata is unavailable. For linked worktrees, use relative Git paths and'
  echo 'devcontainer up --workspace-folder <path> --mount-git-worktree-common-dir.'
  exit 1
fi
if ! git config user.name >/dev/null || ! git config user.email >/dev/null; then
  echo 'Git commit identity is not configured in this container; configure user.name and user.email before committing.'
fi
sudo chown "$(id -u):$(id -g)" "${DOCTA_DEV_HOME:?Missing shared development state mount}"
chmod 700 "$DOCTA_DEV_HOME"
# Named volumes keep Windows dependencies out of the Linux environment.
sudo chown "$(id -u):$(id -g)" .venv node_modules apps/web/node_modules apps/web/.next
uv sync --frozen
npm ci
npx playwright install --with-deps chromium

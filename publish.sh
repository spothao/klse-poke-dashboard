#!/usr/bin/env bash
# Rebuild the e-ink dashboard and push index.html if it changed.
# Does not commit credentials or ledger files.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

python3 "$ROOT/build.py"

if [[ ! -d "$ROOT/.git" ]]; then
  echo "Built index.html, but $ROOT is not a git repository, so nothing was pushed."
  exit 0
fi

git add -- "index.html"

if git diff --cached --quiet; then
  echo "No dashboard changes to publish."
  exit 0
fi

STAMP="$(TZ=Asia/Kuala_Lumpur date '+%Y-%m-%d %H:%M MYT')"
git commit -m "Update KLSE e-ink dashboard (${STAMP})"

if ! git push; then
  echo "git push failed. Authenticate the gh CLI (gh auth login) or set up a git remote with credentials, then re-run." >&2
  exit 1
fi

echo "Published dashboard changes."

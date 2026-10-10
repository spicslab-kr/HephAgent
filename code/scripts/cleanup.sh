#!/usr/bin/env sh
# Remove everything that setup or a local run created, returning the tree to
# what is committed in git. Safe to run before `git add` / pushing.
# Run from the repository root:  sh scripts/cleanup.sh
cd "$(dirname "$0")/.."
rm -rf .venv .pytest_cache .mypy_cache .ruff_cache build dist .sdlc
find . -type d -name "__pycache__" -prune -exec rm -rf {} +
find . -type d -name "*.egg-info" -prune -exec rm -rf {} +
echo "Cleanup complete. Re-create the environment with scripts/setup.sh"

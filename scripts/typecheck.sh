#!/usr/bin/env bash
set -euo pipefail

uv run python -m mypy

for entrypoint in examples/*/run.py; do
  uv run python -m mypy --no-warn-unused-configs "$entrypoint"
done

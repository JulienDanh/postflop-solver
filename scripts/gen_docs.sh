#!/usr/bin/env bash
# Regenerate Python API docs using pdoc.
# Requires: pip install pdoc
# Requires: the postflop_solver package must be importable (maturin develop).
set -e
pdoc postflop_solver -o docs/python
echo "Docs generated at docs/python/index.html"

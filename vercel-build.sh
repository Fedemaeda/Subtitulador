#!/bin/bash
set -e
echo "=== Installing CPU-only torch ==="
pip install torch --index-url https://download.pytorch.org/whl/cpu --no-deps --upgrade -q
echo "=== Installing remaining deps ==="
pip install -r requirements-vercel.txt -q
echo "=== Build complete ==="

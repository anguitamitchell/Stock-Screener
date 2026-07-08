#!/bin/bash
set -e
cd "$(dirname "$0")/.."
export PATH="$PWD/.venv/bin:$PATH"
exec vercel dev --listen 3000 --yes

#!/bin/bash
# Finder-friendly entry point; implementation lives with the other scripts.
set -euo pipefail
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec /bin/bash "$REPO_ROOT/scripts/install.sh" "$@"

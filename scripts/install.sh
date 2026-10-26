#!/bin/bash
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

pause_terminal() {
  if [[ -t 0 ]]; then read -r -p 'Press Enter to close…' || true; fi
}
installation_failed() {
  echo 'Installation stopped. See the error above.' >&2
  pause_terminal
}
trap installation_failed ERR

if [[ "$(uname -s)" != Darwin ]]; then
  echo 'Video Queue requires macOS and Apple Photos.' >&2
  exit 1
fi
if ! command -v brew >/dev/null; then
  echo 'Install Homebrew from https://brew.sh, then run this installer again.' >&2
  pause_terminal
  exit 1
fi

brew install python@3.13 python-tk@3.13 ffmpeg exiftool
PYTHON="$(brew --prefix python@3.13)/bin/python3.13"
"$PYTHON" "$REPO_ROOT/scripts/install_macos.py"
open "$HOME/Applications/Video Queue.app"
echo "Installed: $HOME/Applications/Video Queue.app"
pause_terminal

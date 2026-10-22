#!/bin/bash
set -euo pipefail
trap 'echo "Installation stopped. See the error above."; read -r -p "Press Enter to close…"' ERR
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")"
if ! command -v brew >/dev/null; then
  echo 'Homebrew is required. Install it from https://brew.sh then run this installer again.'
  open https://brew.sh
  read -r -p 'Press Enter to close…'
  exit 1
fi
brew install python@3.13 python-tk@3.13 ffmpeg exiftool
APP_DEST="$HOME/Applications/Video Queue.app"
DATA_DEST="$HOME/Library/Application Support/Video Queue"
mkdir -p "$DATA_DEST" "$APP_DEST/Contents/MacOS" "$APP_DEST/Contents/Resources"
"$(brew --prefix python@3.13)/bin/python3.13" -m venv "$DATA_DEST/venv"
"$DATA_DEST/venv/bin/python" -m pip install 'tkinterdnd2==0.6.3' 'osxphotos==0.77.2' pyobjc-framework-Photos
cp app.py engine.py photos_catalog.py photos_browser.py photos_previews.py photos_gallery.py quicktime_tracks.py photos_download.py photos_actions.py photos_resource.py jobs.py state_store.py storage_cleanup.py photos_service.py photos_navigation.py workflow_logic.py "$DATA_DEST/"
"$DATA_DEST/venv/bin/python" install_photos_service.py "$HOME/Library/Services/Convert video to 1080p.workflow"
/System/Library/CoreServices/pbs -update
cat > "$APP_DEST/Contents/MacOS/VideoQueue" <<'LAUNCH'
#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
APP_DATA="$HOME/Library/Application Support/Video Queue"
exec "$APP_DATA/venv/bin/python" "$APP_DATA/app.py" "$@" >> "$APP_DATA/launch.log" 2>&1
LAUNCH
chmod +x "$APP_DEST/Contents/MacOS/VideoQueue"
cat > "$APP_DEST/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>VideoQueue</string>
<key>CFBundleIdentifier</key><string>local.silvio.videoqueue</string>
<key>CFBundleName</key><string>Video Queue</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>3.0</string>
<key>NSHighResolutionCapable</key><true/>
<key>NSPhotosLibraryUsageDescription</key><string>Download selected videos, add reviewed smaller copies, and move originals to Recently Deleted only after you approve replacement.</string>
<key>NSAppleEventsUsageDescription</key><string>Show selected items in Photos and read selected videos for 1080p conversion.</string>
</dict></plist>
PLIST
open "$APP_DEST"
echo "Installed: $APP_DEST"
read -r -p 'Press Enter to close…'

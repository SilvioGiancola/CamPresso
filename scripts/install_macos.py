"""Install the package and macOS app bundle, preserving shared workflow data."""

import fcntl
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def atomic_write(path, content, mode=0o644):
    """Publish a complete bundle file without exposing a partial write."""
    descriptor, temporary = tempfile.mkstemp(prefix=".videoqueue-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def install():
    if sys.platform != "darwin":
        raise RuntimeError("Video Queue requires macOS and Apple Photos.")
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError("Run the installer with Homebrew Python 3.13.")

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    data = Path.home() / "Library" / "Application Support" / "Video Queue"
    bundle = Path.home() / "Applications" / "Video Queue.app" / "Contents"
    data.mkdir(parents=True, exist_ok=True)

    # Hold the same lock as the GUI for the entire runtime update.
    with (data / ".app.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Quit Video Queue before installing or updating.") from error

        environment = data / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True)
        python = str(environment / "bin" / "python")
        # Build away from the checkout so installation leaves it clean.
        with tempfile.TemporaryDirectory(prefix="videoqueue-install-") as temporary:
            source = Path(temporary) / "source"
            shutil.copytree(
                ROOT,
                source,
                ignore=shutil.ignore_patterns(
                    ".git", ".venv", "venv", "__pycache__", "*.egg-info",
                    "build", "dist", ".local", ".DS_Store", "*.mp4", "*.mov", "*.m4v",
                ),
            )
            subprocess.run([python, "-m", "pip", "install", "--upgrade", str(source)], check=True)
        subprocess.run(
            [python, str(ROOT / "scripts" / "install_photos_service.py")], check=True
        )

        (bundle / "MacOS").mkdir(parents=True, exist_ok=True)
        (bundle / "Resources").mkdir(exist_ok=True)
        info = {
            "CFBundleExecutable": "VideoQueue",
            "CFBundleIdentifier": "local.silvio.videoqueue",
            "CFBundleName": "Video Queue",
            "CFBundlePackageType": "APPL",
            "CFBundleVersion": project["version"],
            "NSHighResolutionCapable": True,
            "NSPhotosLibraryUsageDescription": (
                "Download selected videos, add reviewed smaller copies, and move "
                "originals to Recently Deleted only after you approve replacement."
            ),
            "NSAppleEventsUsageDescription": (
                "Show selected items in Photos and read selected videos for 1080p conversion."
            ),
        }
        launcher = (
            '#!/bin/bash\n'
            'export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"\n'
            'APP_DATA="$HOME/Library/Application Support/Video Queue"\n'
            'exec "$APP_DATA/venv/bin/python" -m video_queue "$@" '
            '>> "$APP_DATA/launch.log" 2>&1\n'
        )
        atomic_write(bundle / "Info.plist", plistlib.dumps(info))
        atomic_write(bundle / "MacOS" / "VideoQueue", launcher.encode(), mode=0o755)
        subprocess.run(["/System/Library/CoreServices/pbs", "-update"], check=True)


if __name__ == "__main__":
    try:
        install()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Installation failed: {error}", file=sys.stderr)
        raise SystemExit(1)

"""Install the package and macOS app bundle, preserving shared workflow data."""

import fcntl
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import uuid

from build_macos import build


ROOT = Path(__file__).resolve().parents[1]



def install():
    if sys.platform != "darwin":
        raise RuntimeError("Video Queue requires macOS and Apple Photos.")
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError("Run the installer with Homebrew Python 3.13.")

    if int(platform.mac_ver()[0].split(".")[0]) < 26:
        raise RuntimeError("Video Queue requires macOS 26 or later.")
    data = Path.home() / "Library" / "Application Support" / "Video Queue"
    bundle = Path.home() / "Applications" / "Video Queue.app"
    data.mkdir(parents=True, exist_ok=True)

    # Hold the same lock as the GUI for the entire runtime update.
    with (data / ".app.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Quit Video Queue before installing or updating.") from error

        # Build before changing the installed Python runtime or bundle.
        native_build = tempfile.TemporaryDirectory(prefix="videoqueue-native-install-")
        with native_build as native_temporary:
            native_bundle = build(Path(native_temporary)/"Video Queue.app")
            install_runtime(data, bundle, native_bundle)


def install_runtime(data, bundle, native_bundle):
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
                "build", "dist", ".build", ".swiftpm", ".local", ".DS_Store", "*.mp4", "*.mov", "*.m4v",
            ),
        )
        subprocess.run([python, "-m", "pip", "install", "--upgrade", str(source)], check=True)
    subprocess.run(
        [python, str(ROOT / "scripts" / "install_photos_service.py")], check=True
    )

    bundle.parent.mkdir(parents=True, exist_ok=True)
    staging = bundle.with_name("Video Queue.staging-" + uuid.uuid4().hex + ".app")
    previous = bundle.with_name("Video Queue.previous-" + uuid.uuid4().hex + ".app")
    shutil.copytree(native_bundle, staging)
    had_previous = bundle.exists()
    try:
        if had_previous:
            os.replace(bundle, previous)
        os.replace(staging, bundle)
    except OSError:
        if had_previous and previous.exists() and not bundle.exists():
            os.replace(previous, bundle)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    if previous.exists():
        shutil.rmtree(previous)
    subprocess.run(["/System/Library/CoreServices/pbs", "-update"], check=True)


if __name__ == "__main__":
    try:
        install()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Installation failed: {error}", file=sys.stderr)
        raise SystemExit(1)

"""Build and sign a native app bundle without installing it or opening Photos."""

import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def build(destination, disable_swift_sandbox=False):
    destination = Path(destination).expanduser().absolute()
    if destination.suffix != '.app' or destination.exists():
        raise RuntimeError('Choose a new .app destination; existing bundles are never overwritten by the build command.')
    version = tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
    with tempfile.TemporaryDirectory(prefix='campresso-build-') as temporary:
        temporary = Path(temporary)
        # All compiler/cache output stays outside the source checkout.
        environment = dict(os.environ, CLANG_MODULE_CACHE_PATH=str(temporary/'modules'),
                           SWIFTPM_MODULECACHE_OVERRIDE=str(temporary/'modules'))
        command = ['xcrun', 'swift', 'build', '--package-path', str(ROOT/'macos'),
                   '--scratch-path', str(temporary/'build'), '--cache-path', str(temporary/'cache'),
                   '--config-path', str(temporary/'config'), '--security-path', str(temporary/'security'),
                   '-c', 'release', '-debug-info-format', 'none']
        if disable_swift_sandbox:
            command.append('--disable-sandbox')
        subprocess.run(command, env=environment, check=True)
        binary_path = subprocess.check_output(command + ['--show-bin-path'], env=environment, text=True).strip()
        contents = temporary/'CamPresso.app'/'Contents'
        (contents/'MacOS').mkdir(parents=True)
        (contents/'Resources').mkdir()
        shutil.copy2(Path(binary_path)/'CamPresso', contents/'MacOS'/'CamPresso')
        for filename in ('CamPresso.icns', 'campresso-mark.png', 'campresso-mark-dark.png'):
            shutil.copy2(ROOT/'assets'/'brand'/filename, contents/'Resources'/filename)
        info = {
            'CFBundleExecutable': 'CamPresso', 'CFBundleIdentifier': 'local.silvio.campresso',
            'CFBundleName': 'CamPresso', 'CFBundlePackageType': 'APPL',
            'CFBundleDisplayName': 'CamPresso', 'CFBundleIconFile': 'CamPresso.icns',
            'CFBundleShortVersionString': version, 'CFBundleVersion': version,
            'LSMinimumSystemVersion': '26.0', 'NSHighResolutionCapable': True,
            'NSPhotoLibraryUsageDescription': 'Download videos and add smaller copies for comparison. Remove only the version you approve with Keep Compressed or Revert.',
            'NSPhotoLibraryAddUsageDescription': 'Add verified smaller videos to Photos for review alongside their originals.',
            'NSAppleEventsUsageDescription': 'Show selected videos in Apple Photos.',
        }
        (contents/'Info.plist').write_bytes(plistlib.dumps(info))
        entitlements = temporary/'entitlements.plist'
        entitlements.write_bytes(plistlib.dumps({
            'com.apple.security.personal-information.photos-library': True,
            'com.apple.security.automation.apple-events': True,
        }))
        subprocess.run(['codesign', '--force', '--sign', '-', '--entitlements', str(entitlements),
                        str(contents.parent)], check=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(contents.parent, destination)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--disable-swift-sandbox', action='store_true',
                        help='For already sandboxed build hosts that cannot nest the SwiftPM compiler sandbox.')
    options = parser.parse_args()
    print(build(options.destination, options.disable_swift_sandbox))

"""Install CamPresso with fresh first-run settings and protected legacy recovery."""

from contextlib import ExitStack
import fcntl
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import uuid

from build_macos import build

ROOT = Path(__file__).resolve().parents[1]
# The checkout is the installer source of truth, not an older installed package.
sys.path.insert(0, str(ROOT/'src'))
from campresso.history import (LEGACY_NAME, LEGACY_BUNDLE_ID, LEGACY_SERVICE_ID,
                               capture_previous, clear_previous_history)
from campresso.native_store import read_strict
from campresso.state_store import StateFile


def locked(stack, data):
    handle = stack.enter_context((data/'.app.lock').open('a+'))
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError('Quit CamPresso and the previous app before installing or cleaning history.') from error


def publish_bundle(source, bundle):
    bundle.parent.mkdir(parents=True, exist_ok=True)
    if bundle.exists():
        info = plistlib.loads((bundle/'Contents/Info.plist').read_bytes())
        if bundle.is_symlink() or info.get('CFBundleIdentifier') != 'local.silvio.campresso':
            raise RuntimeError('An unrelated CamPresso.app already exists; it was not overwritten.')
    staging = bundle.with_name('CamPresso.staging-'+uuid.uuid4().hex+'.app')
    previous = bundle.with_name('CamPresso.previous-'+uuid.uuid4().hex+'.app')
    shutil.copytree(source, staging)
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


def retire_owned_bundle(path, bundle_id):
    if not path.exists():
        return
    if path.is_symlink():
        raise RuntimeError('Previous installation is a symbolic link; it was retained: '+str(path))
    try:
        info = plistlib.loads((path/'Contents/Info.plist').read_bytes())
    except (OSError, ValueError) as error:
        raise RuntimeError('Cannot verify the previous app/Service; it was retained: '+str(path)) from error
    if info.get('CFBundleIdentifier') != bundle_id:
        raise RuntimeError('Unexpected previous app/Service identifier; it was retained: '+str(path))
    shutil.rmtree(path)


def install():
    if sys.platform != 'darwin' or int(platform.mac_ver()[0].split('.')[0]) < 26:
        raise RuntimeError('CamPresso requires macOS 26 or later.')
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError('Run the installer with Homebrew Python 3.13.')
    support = Path.home()/'Library/Application Support'
    data, legacy = support/'CamPresso', support/LEGACY_NAME
    bundle = Path.home()/'Applications/CamPresso.app'
    if data.is_symlink() or legacy.is_symlink():
        raise RuntimeError('Application data contains a symbolic link. No previous data was cleaned.')
    marker = StateFile(data/'installation.json')
    state = read_strict(marker.path)
    if data.exists() and not state and any(path.name != '.app.lock' for path in data.iterdir()):
        raise RuntimeError('Unrecognized CamPresso data already exists. It will not be overwritten; choose a backup/recovery location first.')
    if state and state.get('app_id') != 'local.silvio.campresso':
        raise RuntimeError('The existing data belongs to another installation. Nothing was overwritten.')
    data.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        locked(stack, data)
        if legacy.is_dir():
            locked(stack, legacy)
        if not state:
            state = dict(app_id='local.silvio.campresso', phase='preparing', legacy_captured=False)
            marker.write(state)
        with tempfile.TemporaryDirectory(prefix='campresso-install-') as temporary:
            native_bundle = build(Path(temporary)/'CamPresso.app')
            environment = data/'venv'
            subprocess.run([sys.executable, '-m', 'venv', str(environment)], check=True)
            python = str(environment/'bin/python')
            source = Path(temporary)/'source'
            shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(
                '.git', '.venv', 'venv', '__pycache__', '*.egg-info', 'build', 'dist',
                '.build', '.swiftpm', '.local', '.DS_Store', '*.mp4', '*.mov', '*.m4v'))
            subprocess.run([python, '-m', 'pip', 'install', '--upgrade', str(source)], check=True)
            if not state['legacy_captured']:
                if legacy.is_dir():
                    capture_previous(data, legacy)
                # No library, preferences, history or approvals are imported.
                StateFile(data/'workflow.json').write(dict(version=4, jobs=[]))
                StateFile(data/'preferences.json').write({})
                state['legacy_captured'] = True
                marker.write(state)
            publish_bundle(native_bundle, bundle)
            subprocess.run([python, str(ROOT/'scripts/install_photos_service.py')], check=True)
        if legacy.is_dir():
            # Recapturing is intentionally skipped on future updates. The old
            # workflow is read only to finish/retry its already-planned cleanup.
            old = read_strict(legacy/'workflow.json')
            if old and old.get('version') not in (3, 4):
                raise RuntimeError('The previous workflow version is unsupported; it was not cleaned.')
            jobs = old.get('jobs', [])
            if old.get('version') == 3:
                for job in jobs:
                    if job.get('original_removed') and job.get('verified') and job.get('new_uuid'):
                        job.update(state='Completed', decision='keep', replacement_complete=True)
            outcome = clear_previous_history(legacy, jobs)
            for failure in outcome['retained']:
                print('Retained previous file: '+failure, file=sys.stderr)
        retire_owned_bundle(Path.home()/'Applications'/(LEGACY_NAME+'.app'), LEGACY_BUNDLE_ID)
        retire_owned_bundle(Path.home()/'Library/Services/Convert video to 1080p.workflow', LEGACY_SERVICE_ID)
        subprocess.run(['/System/Library/CoreServices/pbs', '-update'], check=True)
        state['phase'] = 'ready'
        marker.write(state)
        print('CamPresso installed. Previous Photos videos and unfinished recovery work were preserved.')


if __name__ == '__main__':
    try:
        install()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print('Installation failed: '+str(error), file=sys.stderr)
        raise SystemExit(1)

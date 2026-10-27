"""Launch the native application. The headless backend has its own entry point."""

import argparse
import os
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description='Open the native macOS CamPresso app.')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--backend-python', type=Path)
    parser.add_argument('--native-app', type=Path,
                        default=Path.home()/'Applications'/'CamPresso.app')
    options = parser.parse_args(argv)
    executable = options.native_app.expanduser().resolve()
    if executable.suffix == '.app':
        executable = executable/'Contents'/'MacOS'/'CamPresso'
    try:
        magic = executable.open('rb').read(4)
    except OSError:
        magic = b''
    # Never recurse through a previous Python/shell bundle launcher.
    if magic not in (b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'):
        parser.error('Install the native app with Install.command first, or supply --native-app with its compiled executable.')
    args = [str(executable)]
    if options.data_dir:
        args += ['--data-dir', str(options.data_dir.expanduser().resolve())]
    args += ['--backend-python', str((options.backend_python or Path(sys.executable)).absolute())]
    os.execv(str(executable), args)


def __getattr__(name):
    # Historical test/helper imports stay available without loading Tk at launch.
    if name == 'App':
        from .legacy_app import App
        return App
    raise AttributeError(name)


if __name__ == '__main__':
    main()

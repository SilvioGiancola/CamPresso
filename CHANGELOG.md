# Changelog

## Unreleased

### Added

- Separate Settings window for the Photos library and conversion defaults, with Save and Cancel.
- Automatic loading of the saved library at launch, plus a Refresh action.
- Python package metadata, an editable development install and `video-queue` / `python -m video_queue` entry points.
- `--data-dir` for launching the GUI with separate application state.
- Repository static checks, GitHub Actions configuration and contributor documentation.

### Changed

- Runtime code lives under `src/video_queue/`, existing tests under `tests/`, and installation tools under `scripts/`.
- Conversion defaults are remembered across launches and apply to newly queued videos.
- The installer installs the package and bundled Service resource, and reads its version/dependencies from `pyproject.toml`.
- The installer holds the app's shared storage lock during the runtime update.
- Installation builds from a temporary source copy to keep generated packaging files out of the checkout.

### Fixed

- Replaced Tk's demonstration menus with Video Queue menus and native Settings/Quit handlers.
- Closing during downloads or conversion asks for confirmation, cancels work and waits for tracked workers before cleanup.
- Polling continues after callback errors so stale busy flags cannot keep shutdown waiting indefinitely.

## Earlier work

The initial app, Photos Service, Compress → Review → Replace workflow and separate user/developer guides predate this changelog. Their history is available in Git commits.

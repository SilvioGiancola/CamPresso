# Changelog

## Unreleased — 4.1.1

- Load AVFoundation asset/track properties asynchronously and use macOS 26 video-composition configurations while preserving Photos rendering instructions.
- Keep frame encoding and bounded watchdog waits on a dedicated worker; use scoped native locks and the asynchronous export API.
- Reuse installed Homebrew dependencies and install only missing tools, avoiding misleading already-installed warnings.
- Verification: Debug/Release compilation and static checks; no tests or live Photos operations.

## 4.1.0 — CamPresso

- Rebrand app, executable, Swift target, Python package/CLI, Service, documentation and GitHub links as CamPresso — Action, packed lighter.
- Add editable espresso/orange/cream vector logos, transparent PNGs and a bundled macOS icon, with deterministic export tooling.
- Start in a new owned data directory/venv with blank preferences and history; preserve unfinished previous reviews/recovery separately.
- Add Settings → Storage → Clear conversion history with preview, protected records, minimal asset links and retryable safe-file cleanup.
- Update existing test imports for the package rename. Static checks and native builds only; no tests or live Photos operations executed.

## Previous native work — 4.0.0

### Added

- macOS 26+ SwiftUI gallery, separate Settings and native menus.
- Library, Processing, Review and Completed filters with favorites, availability and phase/batch progress.
- AVKit comparison with synchronized playback, shared timeline, divider, zoom and audio selection.
- Hardware HEVC/H.264 media layer for Photos' current rendition, supported HLG/HDR10 and rendered edits/slow motion.
- Gentle, Standard, High and Maximum presets; Standard / 1080p HEVC default.
- Headless Python scheduler connected through asynchronous versioned JSON.
- Journalled automatic smaller-copy import followed by explicit Keep Compressed or Revert, including batch decisions.
- Persistent review pairs, exact identifier/version/generation approvals, serialized native changes and explicit reconciliation.
- Version-3 state/preferences backups and migration; explicit action for older local-only results.
- Native bundle build/signing command, updated installer/Photos Service and separate user/developer/verification guides.

### Changed

- Native app replaces the Tk entry point; legacy helpers remain available for compatibility and recovery.
- Temporary media is released after verified import; comparison retrieves Photos media again.
- Closing stops downloads/encoding after confirmation while allowing an active Photos transaction to finish safely.
- Shared/uncertain cases remain browse/play-only; the full workflow requires a confirmed personal System Photo Library.

### Verification

- Native compilation and repository static checks only. No tests, live Photos writes or media acceptance scenarios executed.

## Previous unreleased work (3.0.4)

### Added

- Separate Settings window for the Photos library and conversion defaults, with Save and Cancel.
- Automatic loading of the saved library at launch, plus a Refresh action.
- Python package metadata, an editable development install and `campresso` / `python -m campresso` entry points.
- `--data-dir` for launching the GUI with separate application state.
- Repository static checks, GitHub Actions configuration and contributor documentation.

### Changed

- Runtime code lives under `src/campresso/`, existing tests under `tests/`, and installation tools under `scripts/`.
- Conversion defaults are remembered across launches and apply to newly queued videos.
- The installer installs the package and bundled Service resource, and reads its version/dependencies from `pyproject.toml`.
- The installer holds the app's shared storage lock during the runtime update.
- Installation builds from a temporary source copy to keep generated packaging files out of the checkout.

### Fixed

- Replaced Tk's demonstration menus with CamPresso menus and native Settings/Quit handlers.
- Closing during downloads or conversion asks for confirmation, cancels work and waits for tracked workers before cleanup.
- Polling continues after callback errors so stale busy flags cannot keep shutdown waiting indefinitely.

## Earlier work

The initial app, Photos Service, Compress → Review → Replace workflow and separate user/developer guides predate this changelog. Their history is available in Git commits.

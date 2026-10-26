# Contributing

Read the [developer guide](docs/DEVELOPER_GUIDE.md) for setup, architecture and Photos recovery contracts. The [user guide](docs/USER_GUIDE.md) describes the supported workflow.

## Make a change

1. Create a focused branch from the current `main`, for example `fix/download-cancellation` or `chore/repository-layout`.
2. Install the package in a separate development environment with `python -m pip install -e .`.
3. Keep runtime changes under `src/video_queue/`, existing checks under `tests/`, installation tools under `scripts/`, and guides under `docs/`.
4. Update the relevant guide and the Unreleased section of [CHANGELOG.md](CHANGELOG.md) when behavior or setup changes.
5. Run the static checks below. Select relevant existing tests using their documented requirements and limitations, and report exactly which checks ran.
6. Review `git diff` and `git status`, commit related files together with a descriptive message, and open a pull request. Summarize the change, checks, known gaps and any Photos operations performed.

```bash
python scripts/check_repository.py
git diff --check
```

The GitHub Actions workflow runs static checks on pushes and pull requests. It does not exercise the GUI or the Photos workflow. See [test documentation](tests/README.md) for manual suite commands and existing limitations.

## Code conventions

- Use four-space indentation, descriptive names and small functions for new or substantially changed code. Follow the surrounding code when making a narrow fix; keep broad formatting changes separate from behavior changes.
- Use package-relative imports inside `video_queue`, and `video_queue.*` imports in external tools and tests. Launch helpers with `python -m video_queue.<module>`.
- Keep the package initializer free of GUI startup, native access and other side effects.
- Define dependency and release-version changes in `pyproject.toml`. Include bundled non-Python resources in its package-data configuration.
- Keep generated media, environments, credentials, logs and personal workflow data out of commits. Generate media fixtures in temporary directories; use `.local/` for disposable local development files.
- Preserve the supported Python 3.13/macOS setup unless a compatibility change is intentional and documented.

## Photos and storage changes

Preserve exact-asset approval, journal-before-write ordering and recovery identifiers. Ordinary compression must leave Photos unchanged. Uncertain imports or deletions must not be repeated automatically.

Use a separate app data directory for development. This isolates app state, not Photos: opening the GUI can load the remembered/System library, and native helpers target the selected System Photo Library. Mock native operations before any explicitly approved live-library work. Never remove external inputs or in-library files as part of cache cleanup.

## Releases

1. Review the current change set and documented verification limits.
2. Update the version in `pyproject.toml` and move completed Unreleased notes into a versioned changelog section.
3. Follow the developer guide's release steps and record actual check results.
4. Publish/tag only after the intended revision and release contents are approved.

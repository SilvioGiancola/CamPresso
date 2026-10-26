# Video Queue

**Compress → Review → Replace** videos in your Apple Photos library to reduce Photos and iCloud storage.

Browse video thumbnails, download and compress selected originals, compare the results, then approve replacing them in Photos. The default is **1080p HEVC / Balanced**. Original downloads and conversion can run at the same time.

## Documentation

| Guide | Contents |
| --- | --- |
| [User guide](docs/USER_GUIDE.md) | Installation, permissions, everyday workflow, video statuses, quality settings, Photos Service, storage cleanup and troubleshooting. |
| [Developer guide](docs/DEVELOPER_GUIDE.md) | Development setup, architecture, queues, internal interfaces, saved state, native journals, recovery, testing and release procedure. |
| [Contributing](CONTRIBUTING.md) | Repository layout, code conventions and change/review workflow. |
| [Changelog](CHANGELOG.md) | Unreleased changes and release history. |

## Get started

1. Download or clone this repository on your Mac. Quit any running Video Queue instance.
2. Install [Homebrew](https://brew.sh/) if needed, then double-click [Install.command](Install.command). From Terminal, you can run `bash Install.command` in the repository folder.
3. Open **Settings…**, choose your **System Photo Library**, and click **Save**. The library loads now and automatically on future launches.
4. Select videos and click **Compress**. Use **Play original** and **Play compressed** to review each result.
5. Select the results you want to keep and click **Replace in Photos…**. A verified smaller copy is added before its original moves to Recently Deleted.

You can also select videos in Photos and choose **Photos → Services → Convert video to 1080p**. The Service stops at review; replacement requires your approval.

## Before replacing videos

- Compression alone leaves Photos unchanged. Replacement creates a new Photos asset and affects devices using iCloud Photos.
- Dates, location, favorites, hidden status and writable regular albums are transferred. Faces, edit history and some other Photos metadata may not transfer.
- **Done** confirms replacement in the local Photos library. It does not confirm iCloud synchronization or immediately reclaimed disk space. Permanent deletion from Recently Deleted remains manual.
- Temporary videos are cleared on close after any required discard confirmation. Recovery copies for unresolved Photos operations are retained.
- The app currently handles **SDR video**. HDR and anamorphic videos are skipped; output must be smaller to qualify for replacement.

See the [user guide](docs/USER_GUIDE.md) for the full workflow, limitations and recovery steps. Source-level behavior and verification limitations are covered in the [developer guide](docs/DEVELOPER_GUIDE.md).

## Repository layout

```text
src/video_queue/   Application package and bundled Service resource
tests/             Existing unit, integration and GUI checks
scripts/           Installation and repository maintenance
docs/              User and developer guides
.github/workflows/ Automatic static checks
pyproject.toml     Package metadata, dependencies and version
Install.command    Double-click installer for macOS
```

For development, install with `python -m pip install -e .` in a Python 3.13 environment, then launch with `python -m video_queue`. See the developer guide for an isolated app data directory and the existing test-suite limitations.

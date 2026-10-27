# CamPresso

<img src="assets/brand/campresso-icon.png" width="112" alt="CamPresso press and video icon">

**Action, packed lighter**

A native **macOS 26+** app for reducing the size of videos in your personal Apple Photos library.

**Select videos → Compress → Review → Keep Compressed or Revert**

Browse a compact thumbnail gallery, compress local or iCloud videos, then compare the two versions with synchronized playback. The default is **Standard / 1080p HEVC**, using Apple's hardware encoder. The workflow follows [HEVCut's documented approach](https://hevcut.com/tools/video-compressor); HEVCut's performance has not been independently tested.

## Get started

1. Install [Homebrew](https://brew.sh/) and **Xcode 26+** with Swift 6.2+. Quit CamPresso and any running instance of the previous app.
2. Double-click [Install.command](Install.command), or run `bash Install.command` from the checkout.
3. Allow Photos access. In **Settings…**, select your **System Photo Library**, confirm it is a personal library without family Shared Photo Library configured, and read the import/review notice. The saved library opens automatically on future launches.
4. Select videos and click **Compress**. Cloud videos download automatically; one download can run while another video converts.
5. Open **Review**. Use the comparison divider, shared timeline and audio switch.
6. Choose **Keep Compressed** to move the matching original to Recently Deleted, or **Revert** to remove the generated copy. Selected results can be decided as a batch.

**Compression adds a verified smaller copy to Photos before review. Your original stays until you choose Keep Compressed.** Pending reviews survive closing the app. These changes synchronize through iCloud Photos when enabled.

## Documentation

| Guide | Contents |
| --- | --- |
| [User guide](docs/USER_GUIDE.md) | Installation, permissions, settings, statuses, comparison, Photos Service, storage and recovery. |
| [Developer guide](docs/DEVELOPER_GUIDE.md) | Swift/Python architecture, protocol, scheduling, transactions, migration and development builds. |
| [Contributing](CONTRIBUTING.md) | Repository conventions and change workflow. |
| [Verification checklist](docs/VERIFICATION.md) | Acceptance scenarios and current verification limits. |
| [Changelog](CHANGELOG.md) | Changes and release history. |

## Fresh start and history cleanup

The first CamPresso installation uses a new app identity and data folder, with blank library settings, default conversion settings and empty history. The installer keeps prior asset associations separately and preserves unfinished reviews in **Settings → Previous work / Recovery**. No Photos videos are removed by installation.

Use **Settings → Storage → Clear conversion history…** to preview finished records and safe temporary files before clearing them. Pending reviews, recovery journals, changed/external files and Photos videos are protected. Later CamPresso updates preserve its settings and current work.

See the [branding guide](docs/BRANDING.md) for editable logos and exports.

## Before you start

- Supported dates, location, favorites, hidden status, filenames and writable regular albums carry over. Faces, edit history and some Photos metadata do not.
- Supported HLG/HDR10 stays HDR. Unsupported media is skipped rather than silently downgraded. Dolby Vision dynamic metadata and editable Cinematic effects are not retained.
- Savings mean **library size reduction**, not immediately reclaimed disk space. Recently Deleted normally retains removed items for 30 days; permanent deletion is manual. See [Apple's deletion guide](https://support.apple.com/en-lamr/104967).
- Shared and uncertain cases are browse/play-only. The complete workflow requires the personal System Photo Library.
- Local temporary files are released when safe. Photos manages its own downloads/cache; CamPresso never deletes files inside the Photos library.

## Repository

```text
macos/             SwiftUI app, AVKit comparison, PhotoKit transactions, encoder
src/campresso/   Headless Python scheduler, catalog, persistence, legacy helpers
scripts/           Native build, installer, Photos Service and static checks
docs/              User, developer and verification guides
tests/             Existing Python tests, including legacy GUI checks
pyproject.toml     Python package metadata, dependencies and release version
```

This native implementation builds successfully, but its live Photos workflow and media acceptance scenarios have **not been executed**. Existing Python tests do not establish native app coverage. No tests or live Photos changes were performed for this implementation.

# User guide

[README](../README.md) · [Developer guide](DEVELOPER_GUIDE.md)

## Install and update

Requirements: **macOS 26+**, a Mac supporting hardware HEVC encoding, **Xcode 26+ / Swift 6.2+**, and [Homebrew](https://brew.sh/). The installer builds the native app locally, installs Python 3.13, FFmpeg/ffprobe and ExifTool, and creates the Photos Service.

Quit Video Queue, then double-click `Install.command` or run `bash Install.command` in the repository. The app is installed at `~/Applications/Video Queue.app`. Updates preserve existing application data, preferences, identifiers and journals. Because local builds use an ad hoc signature, macOS may ask for permissions again after an update. Distribution signing/notarization is not provided by this installer.

Open the app through Finder or Spotlight. The old Tk interface is no longer the application entry point.

## First launch and Settings

1. Click **Allow Photos Access** and allow full access. If denied, use **System Settings → Privacy & Security → Photos**.
2. Open **Video Queue → Settings…** (`⌘,`). Select your `.photoslibrary` bundle.
3. Check **Photos → Settings → General**: the selected library must be the **System Photo Library** to compress or make Photos changes. Other libraries can be browsed, but native playback accesses only the System library.
4. Check the Shared Library settings in Photos. Confirm in Video Queue that family **Shared Photo Library is not configured** for this library. Personal iCloud Photos is supported. Shared or uncertain cases remain browse/play-only.
5. Read and accept the notice explaining that compressed copies are added before review and which metadata can transfer. Save.

The saved library is scanned automatically on launch. If catalog reading fails, check the path and grant Video Queue Full Disk Access in **Privacy & Security → Full Disk Access**, then reopen it. Refresh (`⌘R`) reloads the catalog; the app does not edit Photos databases directly.

## Browse and select

The gallery shows thumbnails, favorites (♥), duration, original size, estimated size reduction, cloud/local availability at the last catalog refresh, and one current status.

- **Library:** all catalog entries, including tracked results.
- **Processing:** work in progress, paused/retryable work and errors.
- **Review:** saved compressed pairs awaiting a decision and items needing inspection.
- **Completed:** confirmed decisions, grouped into one entry per original/replacement pair.

Sort by most estimated savings, largest original, date or filename. Search filenames/identifiers and filter favorites or local items. Click cards to toggle multiple selection. With nothing selected, Compress targets eligible videos in the current view whose estimated reduction is at least 5%, plus videos without a reliable estimate. Explicit selection allows trying other videos; actual output must still be smaller.

Estimates are approximate, especially when frame rate or duration is unavailable. The original stored resource size is used for the final comparison, including for edited videos.

Right-click a card for **Play**, **Compare**, **Show in Photos**, compression and Details. Play may retrieve a cloud rendition. Show in Photos reveals the surviving version after a completed decision; it does not switch libraries.

## Compress

Click **Compress**. There are no separate download/upload/delete controls in the normal workflow.

- Photos retrieves its **current rendered version**, including supported edits, trims and slow motion.
- One video encodes at a time. At most one original is prefetched ahead.
- Photos changes are serialized independently of download and encoding.
- Download percentages appear when Photos supplies them; otherwise an activity indicator appears. Conversion shows frame progress. Overall batch completion is displayed separately.
- Offline/cloud failures leave those videos retryable while other videos continue. Use **Resume / Retry** or select them and Compress again.
- The app checks complete playback, timing, dimensions, audio tracks, HDR and measured size. A failed or larger result is not imported; its original remains unchanged.
- A successful smaller copy is automatically added to Photos and verified by its exact identifier. Both Photos versions then remain available for review.

**Pause** stops scheduling new conversions/downloads after current work finishes. A verified result can still finish saving to Photos. **Stop** cancels reads/encoding and clears queued decisions; an already active Photos transaction is allowed to finish safely.

### Presets

Settings apply to newly queued work. The defaults follow [HEVCut's published presets](https://hevcut.com/tools/video-compressor):

| Preset | Reference bitrate at 1080p / 30 fps | Resolution cap | Frame rate cap |
| --- | --- | --- | --- |
| Gentle | 7.5 Mbps | 1080p | Original |
| Standard (default) | 4.5 Mbps | 1080p | 60 fps |
| High | 3 Mbps | 1080p | 60 fps |
| Maximum | 1.5 Mbps | 720p | 30 fps |

Bitrate scales with output pixels and frame rate, within bounded limits. **Keep original resolution** lifts the resolution cap. Portrait orientation is preserved and smaller media is never upscaled. Advanced settings offer HEVC/H.264, MP4/MOV and a resolution override. H.264 is SDR-only.

Supported HLG/HDR10 uses 10-bit HEVC and must pass HDR verification. Supported Dolby Vision HLG base layers become HLG without Dolby Vision dynamic metadata. Unsupported profiles, Apple Log, spatial/immersive video, alpha, protected content and anamorphic media are skipped. Cinematic appearance is rendered without retaining editable depth/focus. Spatial Audio passes through only in supported unedited renditions/containers; edited audio is rendered to stereo AAC. Limitations are shown with the result.

## Review and decide

Open a **Ready to review** video to compare the two Photos assets. Both players share playback position and a timeline. Drag the divider; choose full Original or Compressed views; zoom; switch which audio is audible. Playback pauses if buffering would break alignment.

- **Keep Compressed:** confirms the exact reviewed identifiers/versions, preserves supported metadata on the copy, then moves only the matching original to Recently Deleted.
- **Revert:** moves only the generated copy to Recently Deleted and keeps the original.

Select multiple review cards to decide a batch. The confirmation displays the count and measured library size reduction for Keep Compressed. Decisions run sequentially. A changed asset invalidates approval and requires inspection and another review. There is no automatic deletion based on a filename or a size estimate.

Dates, location, favorite, hidden status, original filename stem and writable regular albums are preserved where PhotoKit supports them. Faces, edit history, smart/shared albums, some proprietary metadata and editability cannot be transferred. Review the result before keeping it.

**Completed** means the local Photos decision was verified. It does not prove iCloud synchronization. Photos normally retains removed videos in Recently Deleted for 30 days. Permanent deletion stays manual; see [Apple's guide](https://support.apple.com/en-lamr/104967). Revert is available before the decision; after Keep Compressed, recovery is through Photos' Recently Deleted.

## Status reference

| Status | Meaning / action |
| --- | --- |
| Ready | Select and Compress. |
| Queued | Waiting for retrieval or encoding; a prefetched original may already be prepared. |
| Downloading | Photos is retrieving/preparing the current version. Percentage if available. |
| Converting | Hardware encoding, with frame progress. |
| Verifying | Checking complete decoding, timing, dimensions, HDR and size. |
| Saving to Photos | Waiting for or performing a journalled import. Original stays. |
| Ready to review | Both Photos versions are verified. Compare and decide. |
| Applying decision | Serial Keep Compressed/Revert transaction. |
| Completed | Matching removal and surviving version were confirmed. |
| Paused / Cancelled | No automatic resume after interruption. Resume or select and Compress. |
| Waiting for connection / Waiting for space | Retry after fixing the cause. Other videos may continue. |
| Skipped | Unsupported media or result not smaller; see reason in Details. |
| Error | Ordinary failure; original unchanged. Retry after checking Details. |
| Needs attention | Changed asset, uncertain transaction or retained recovery file. Inspect first. |
| Awaiting import | Migrated local-only result. Explicit **Add existing result for review…** is required. |

## Photos Service

Installation adds **Photos → Services → Convert video to 1080p**. Select one or more videos in Photos' grid and run it. It queues the same native workflow with a 1080p override and stops at review with originals retained. It never approves Keep Compressed.

If missing, check **System Settings → Keyboard → Keyboard Shortcuts → Services**, restart Photos, or rerun the installer with Video Queue closed. A selection must belong to the loaded personal System Photo Library. Complete Video Queue setup first. Photos' Services menu is the integration point; an item-specific contextual extension is not installed.

## Close, storage and recovery

Closing during processing asks **Stop and Close** or **Keep Working**. Confirming stops downloads/encoding, preserves finished Photos reviews and waits for an active Photos transaction. An operating-system Photos confirmation may need a response before it can finish.

Safe app-managed media under `~/Library/Application Support/Video Queue/NativeWork/` is released after verified import and on clean close. Comparison media is requested again from Photos as needed. Photos itself manages its iCloud downloads and cache; Video Queue cannot promise to evict those bytes.

Unresolved operations retain recovery files. **Details** shows their size/reason, both identifiers, generation and operation history; **Open Logs and Recovery** opens the data directory. Preferences, identifiers, workflow records, migration backups and transaction journals remain. External inputs and files inside `.photoslibrary` bundles are never removed by cache cleanup.

For **Needs attention**, use **Inspect Photos status**. Inspection reads exact IDs and journals; it never retries an import or deletion. If both versions are verified, another review becomes available. If an import ID is unknown or a version is missing, the state remains unresolved: inspect Photos and the journal manually. Do not repeatedly enqueue the same source or delete recovery files to clear the warning.

On upgrade, version-3 state and preferences are backed up under `Migrations/` before migration. Completed pairs stay grouped. Older local-only results require explicit import; pending writes require reconciliation. The old interface refuses to open migrated state. Legacy external-file records are preserved as history but are not part of the native Photos gallery.

## Verification limits

The native app builds, but live Photos/media acceptance checks have not been run for this implementation. See [verification scenarios](VERIFICATION.md). Installing/building is separate from approving any live-library verification work.

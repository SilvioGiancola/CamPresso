# Native workflow verification

[Developer guide](DEVELOPER_GUIDE.md) · [User guide](USER_GUIDE.md)

## Current evidence

The native Swift executable compiles and repository static checks are available. **No automated tests, GUI session, live Photos operations or media acceptance scenarios were executed for this implementation.** HEVCut was studied through official documentation; its behavior/performance was not independently tested.

The existing Python suite primarily covers legacy helpers. It is not native acceptance coverage. Running tests, launching the app and modifying a live Photos library remain separate actions requiring an explicit request. A separate app-data folder alone does not isolate PhotoKit from the System Photo Library.

## Scenarios for an authorized acceptance session

Use disposable videos and a dedicated test **System Photo Library**, with a backup and controlled iCloud settings. Record exact asset IDs and which steps actually ran.

| Scenario | Required outcome |
| --- | --- |
| Mixed cloud/local batch | One encoder, at most one original prefetched; real download percentage or spinner; independent batch count. Local items continue after cloud failure. |
| Offline and retry | Cloud item waits for connection, original unchanged; explicit retry resumes without duplicate import. |
| SDR portrait and small input | Correct orientation, no upscaling, valid frame timing/audio, output smaller. |
| HLG, HDR10 and supported DV HLG | 10-bit/color/static metadata validated; compare on HDR display; DV dynamic metadata warning. No silent SDR downgrade. |
| Edited/trimmed/slow-motion | Current Photos appearance, crop, duration, speed and audio retained in rendered copy. |
| Spatial/alternate audio | No doubled audio; groups/fallback retained when passthrough is supported; clear warning/skip otherwise. |
| Unsupported/larger result | Clear reason, no Photos import or removal; batch continues. |
| Keep Compressed | Generated exact ID verified first; supported metadata/albums; only matching original moves to Recently Deleted. |
| Revert | Only generated ID removed; original and its metadata stay. |
| Batch decisions | Confirmation count and measured reduction; serialized writes; changed member invalidates approval. |
| Changed asset/output | Recheck versions/hash/generation; require another review; no write against stale approval. |
| Interrupt before/after import commit | Journal retains exact placeholder/returned ID; inspect confirms existing pair; never blindly import again. |
| Interrupt removal | Inspect target/survivor IDs; no automatic replay; uncertain operation blocks further writes. |
| Cancel and close | Confirmation works from close button/Quit; reads/encode stop; active Photos write finishes; reviews survive reopening. |
| Disk exhaustion / persistence failure | Original retained, recoverable state and files kept; no unrecorded continuation of writes. |
| Reopening | Saved library loads, pending reviews retrieved, completed pairs stay grouped and are not offered as fresh originals. |
| Cache cleanup | Safe app media released; unknown/changed/recovery files retained with size/reason; no external/library-file removal. |
| Version-3 migration | Backup state/preferences before changing them; preserve completed/pending pairs; local-only explicit import; uncertain journals reconciled first. |
| Permissions/shared library | Denied access fails visibly; non-System/shared/uncertain cases do not write. |
| Photos Service | Grid selection IDs enqueue 1080p workflow; no replacement approval; failed selection is visible/retryable. |

Inspect Recently Deleted and both supported metadata and unsupported metadata limitations. Successful local import/removal is not evidence that iCloud finished synchronizing or disk space was immediately reclaimed.


## Rebrand and cleanup acceptance scenarios

No test or live-library execution is implied by this checklist.

- Fresh installation has a blank library, default settings, empty Processing/Completed views and the branded icon/About/tagline; reopening after setup remembers the newly chosen library.
- Previous completed decisions lose detailed history but retain minimal associations; the pending prior review remains in Recovery and requires explicit read-only inspection before restoration.
- Cleanup preview/confirmation counts match, cancellation changes nothing, and repeated cleanup remains safe with no duplicate imports.
- Cleanup never removes Photos assets, external inputs, changed files, hard links, symlinks, pending reviews, paused/active work or uncertain transaction material.
- Interrupt between ledger publication, workflow removal and file unlinking; reopening preserves recognition/recovery and offers retry for remaining safe files.
- Run an old app while installing/cleaning: the storage lock blocks the operation. Conflicting unrecognized CamPresso data or app identity is retained with an actionable error.
- Inspect SVG/transparent PNG/16–1024-pixel ICNS representations; the mark contains exactly three motion lines and the bundle references its icon.
- Verify new repo URL, origin, main revision and all internal documentation links after publishing.

## Thumbnail and responsiveness acceptance scenarios

- On Retina and standard displays, compare local landscape/portrait posters with Photos: no fast/degraded image is retained as the final poster.
- Scroll repeatedly while encoding/downloading/verifying: cached images stay sharp, offscreen requests cancel and only active cards/status text update per progress tick.
- Search, select, change filters and open Settings during a long conversion; measure interaction latency separately from encode throughput on the target Mac.
- Refresh after editing a Photos asset; check that its poster changes. A cloud asset with no local poster must not download its full original just for browsing; retry the poster after download.
- Verify cache/concurrency bounds, correct batch counts, stable gallery ordering and prompt Stop/Close behavior under a mixed batch.

These interactive scenarios have not been executed as part of the build/static checks.

## Dark appearance acceptance scenarios

- With macOS in either Light or Dark mode, check the gallery, Settings, Details, About, native dialogs and comparison/playback window chrome use the app's dark appearance.
- Check white/cyan text and navy labels on orange actions, including focused, selected and disabled controls. Retain native status meanings and keyboard focus indicators.
- Check all three white motion lines and the white base remain visible on the navy icon and dark-background mark, including small icon sizes.
- Confirm the theme does not tint thumbnail or playback pixels; the comparison stage stays black and media color verification remains unchanged.

App builds and asset/site inspection do not constitute execution of these live application scenarios.

## Playback crash regression scenarios

The October 28 and October 31 crash reports from 4.1.5 on macOS 27.0.1 both terminate inside `_AVKit_SwiftUI` during `AppModel.play`. The 4.1.6 fix uses AppKit `AVPlayerView` instead; static symbol inspection should show no SwiftUI `VideoPlayer` references in the executable.

- Open original and compressed playback for Ready to review and Needs attention records with known IDs; use native play/pause, seek, volume and full screen, then close/reopen repeatedly.
- Try an empty/unrecorded compressed ID, removed Photos asset, revoked permission, offline cloud rendition and a rendition that fails decoding. Show an actionable error while keeping the app and workflow running.
- Close or quit while Photos is retrieving playback, and retry while an earlier request is pending. Requests cancel, stale callbacks do not affect the next attempt, and closed player windows/items are released.
- Confirm playback failures/retries leave workflow approvals, Photos identifiers and recovery journals unchanged, and never repeat an import/removal. Inspect unresolved writes separately before restoring a comparison review.

These are acceptance scenarios, not a record of a live Photos/playback test run. Crash-report analysis, compilation and symbol inspection are separate evidence.

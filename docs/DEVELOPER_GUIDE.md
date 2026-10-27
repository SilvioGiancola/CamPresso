# Developer guide

[README](../README.md) · [User guide](USER_GUIDE.md) · [Verification](VERIFICATION.md)

## Requirements and builds

The application targets **macOS 26+**, **Swift 6.2+** and Python **3.13**. Install Xcode 26+ and select its developer directory. The native executable is a Swift package without external Swift dependencies. Python uses `osxphotos==0.77.2` for the read-only catalog; FFmpeg/ffprobe perform independent output validation. The retained Python conversion engine also expects ExifTool.

```bash
brew install python@3.13 ffmpeg exiftool
"$(brew --prefix python@3.13)/bin/python3.13" -m venv .venv
source .venv/bin/activate
python -m pip install -e .

# Build an ad hoc signed bundle; does not install/launch it or access Photos.
python scripts/build_macos.py 'dist/CamPresso.app'

# Source-level checks; no application imports or test execution.
python scripts/check_repository.py
git diff --check
```

The build command requires a new destination, builds Release without debug symbols into a temporary directory, embeds the bundle metadata/permission descriptions and signs the result. In an already sandboxed build host that cannot nest SwiftPM’s compiler sandbox, its explicit `--disable-swift-sandbox` option disables only that nested build sandbox. It builds for the host architecture. It does not create a notarized universal distribution. `macos/Package.swift` selects Swift 5 language mode on the Swift 6 toolchain; adopting full Swift 6 strict concurrency is future work. Media/track properties load asynchronously before a dedicated encoding worker starts. Video compositions use the macOS 26 configuration API, preserving Photos instructions and rendering options. Native export uses the asynchronous export API and scoped locks protect shared bookkeeping.

For incremental frontend development:

```bash
xcrun swift build --package-path macos -c debug
# Use the path returned by this command rather than assuming SwiftPM's layout:
xcrun swift build --package-path macos -c debug --show-bin-path
```

Launch only when an interactive session is intended:

```bash
python -m campresso --native-app 'dist/CamPresso.app' \
  --data-dir "$PWD/.local/dev-data" --backend-python "$PWD/.venv/bin/python"
```

`--native-app` accepts a bundle or Mach-O executable. The launcher rejects old shell/Python bundle launchers to prevent recursion. `--backend-python` selects the interpreter for `python -m campresso.backend --stdio`. Installed Finder launches default to the shared application's venv.

**An isolated data directory does not isolate Photos.** Native PhotoKit operations still target the user's System Photo Library. Launching can migrate/save data and clear safe temporary files. Do not launch the app as an unattended verification step against a personal library.

## Architecture

```mermaid
flowchart LR
    UI[SwiftUI gallery and Settings] <-->|NDJSON protocol 1| Backend[Python workflow scheduler]
    Service[Photos Service] --> Requests[Request files]
    Requests --> Backend
    Catalog[OSXPhotos read-only catalog] --> Backend
    Backend <-->|requests, progress, outcomes| Native[Swift PhotoKit and AVFoundation]
    Native --> Encoder[Hardware HEVC encoder]
    Native --> Photos[Personal System Photo Library]
    Native --> Journals[Native transaction journals]
    Backend --> Records[Workflow version 4]
    UI --> Review[AVKit synchronized comparison]
```

| Sources | Responsibility |
| --- | --- |
| [CamPressoApp.swift](../macos/Sources/CamPresso/CamPressoApp.swift), [LibraryView.swift](../macos/Sources/CamPresso/LibraryView.swift), [SettingsView.swift](../macos/Sources/CamPresso/SettingsView.swift) | Native windows, menus, compact gallery, filtering, selection, preferences and quit routing. |
| [AppModel.swift](../macos/Sources/CamPresso/AppModel.swift) | Python process lifetime, asynchronous protocol, UI state, explicit decision confirmation and graceful shutdown. |
| [ReviewView.swift](../macos/Sources/CamPresso/ReviewView.swift) | Two AVPlayers, timed synchronized start, shared timeline, drift correction, divider/zoom and audio selection. |
| [PhotosOperations.swift](../macos/Sources/CamPresso/PhotosOperations.swift), [Support.swift](../macos/Sources/CamPresso/Support.swift) | PhotoKit reads/writes, exact identifiers, version snapshots, native gate, journals, media ownership and permissions. |
| [NativeEncoder.swift](../macos/Sources/CamPresso/NativeEncoder.swift) | Current rendition, hardware encoding, HDR, composition, audio and native output checks. |
| [backend.py](../src/campresso/backend.py) | Headless scheduling, workflow mutations, catalog, frontend snapshots, Service handoff and recovery policy. |
| [native_store.py](../src/campresso/native_store.py), [native_validation.py](../src/campresso/native_validation.py) | Strict persisted records/migration, hashes and independent FFmpeg checks. |
| [photos_catalog.py](../src/campresso/photos_catalog.py) | Read-only OSXPhotos queries, original resource size, favorites and positive shared flags. |
| [legacy_app.py](../src/campresso/legacy_app.py), older Photos/conversion helpers | Retained legacy compatibility and recovery contracts; not the production frontend or native write path. |

`app.py` is now a lightweight native launcher. Its lazy historical `App` import resolves to `legacy_app.App` for old helper/test imports; normal startup does not import Tk. Install the optional `legacy` extra and Homebrew `python-tk@3.13` only if deliberately working on that old code. The old controller refuses workflow version 4 before creating a GUI or cleaning files.

## Protocol 1

The private transport is newline-delimited UTF-8 JSON over the child process's stdin/stdout. Every packet includes `protocol: 1`. Stdout is reserved for packets; diagnostics go to files/stderr. File paths are JSON strings, never shell interpolations. Catalog/job records contain paths and Photos identifiers: do not publish unredacted logs.

| Packet | Required fields |
| --- | --- |
| Frontend request | `type: request`, unique `id`, `method`, `params` object |
| Backend reply | `type: reply`, matching `id`, `result` or `error.message` |
| Backend event | `type: event`, `event`, payload; snapshot/job events include a revision |
| Native operation request | `type: native.request`, unique `id`, `method`, `params` |
| Native progress | `type: native.progress`, matching `id`, `stage`, nullable `percent` |
| Native outcome | `type: native.reply`, matching `id`, `result` or `error` with `message`, `code`, `uncertain` |

Frontend commands include `initialize`, `catalog.refresh`, `preferences.update`, `jobs.enqueue/pause/resume/stop`, `review.open/batch/decide`, `recovery.inspect`, `legacy.import`, `photos.show`, the cleanup/recovery commands below and `shutdown`. Native methods are `prepare`, `encode`, `import`, `review`, `decide`, `inspect` and `cancel`.

Requests/replies are correlated independently; progress is asynchronous. The backend reader routes native replies directly to waiting workers rather than blocking behind commands. Workflow mutation uses an RLock; one backend owns each data directory via `.app.lock`. The UI owns its published state on the main actor. Media encoding runs on worker queues, with cancel checks, a stall watchdog and frame autorelease pools.

`Transport.native` has a 30-minute response timeout. A timed-out Photos write is uncertain and blocks additional writes. The native gate remains authoritative even after a caller times out. Protocol version mismatches fail visibly; do not silently guess a packet shape.

## Scheduling and workflow

Three lanes coordinate one preparation, one encoder and one Photos transaction. A prepared original may wait one ahead of the encoder. Awaiting-import work applies backpressure so offline Photos writes do not grow a local output backlog. Downloads and encoding may continue while the independent Photos lane commits. Failed cloud videos remain retryable; they do not abort local items.

Main stages:

```text
Ready → Queued → Downloading → Prepared → Converting → Verifying
      → Awaiting save → Saving to Photos → Ready to review
      → Applying decision → Completed
```

`Prepared` appears as Queued with a downloaded/waiting detail; `Awaiting save` appears as Saving to Photos. Exceptional states are Paused, Cancelled, Waiting for connection, Waiting for space, Skipped, Error, Needs attention and migrated Awaiting import. Progress events are throttled and do not fsync every frame. Durable stage transitions are saved before dependent work is started.

Pause stops new read/encode scheduling, allowing current operations and verified imports to finish. Stop cancels reads/encoding and queued decisions but never interrupts an active Photos write. Quit confirms active work, requests shutdown, waits for native transactions and clears safe working media. Backend loss cancels native reads/encoding; the native host waits for an active Photos transaction rather than killing it.

## Media contract

Preparation requests `PHVideoRequestOptions.version = .current` with network access. The PhotoKit export session supplies the asset, time range, video composition, audio mix and pitch algorithm. Preserve all of these. Never substitute a raw `AVURLAsset.url` for an edited or slow-motion rendition. A custom compositor uses a Photos-rendered intermediate before encoding.

AVAssetReader/Writer renders Photos' composition at its own geometry, then scales the completed frames. Portrait output keeps display geometry with no upscaling and even dimensions. Preset bitrates are 7.5/4.5/3/1.5 Mbps at 1080p30 (Gentle/Standard/High/Maximum), scaled by output pixels/sec with a 0.2–4 multiplier. Resolution/frame rate caps are documented in the user guide. These settings follow [HEVCut's published approach](https://hevcut.com/tools/video-compressor) and [resolution guide](https://hevcut.com/guides/keep-4k-when-compressing-iphone-video); no independent performance comparison is claimed.

Hardware acceleration is required; there is no silent software/SDR fallback. HLG/PQ uses Main10, BT.2020 and 10-bit pixel buffers with extended-range Core Image processing. Transfer, primaries and source static HDR metadata must survive native inspection; FFprobe independently verifies 10-bit/transfer/primaries and rejects unexpected Dolby Vision metadata. Unsupported HDR profiles fail closed. HLG Dolby Vision base layers omit dynamic DV metadata; HDR10 metadata insertion is automatic. Unknown/proprietary spatial, alpha, Log, protected and anamorphic video is rejected.

Spatial Audio passthrough is limited to supported full unedited URL renditions without an audio mix; alternate groups/fallback associations are preserved rather than mixing both variants. Other supported audio mixes render to stereo AAC. Supported ancillary tracks pass through when compatible; otherwise the review warning records what could not transfer.

Independent validation checks complete video/audio decoding, output duration (`max(0.5s, 0.5%)` tolerance), dimensions, frame rate, audio track count, HDR and actual size against the stored original resource. Unknown original size or non-smaller results are skipped. Successful output is bound by SHA-256 and `[size, mtime_ns]` before import. The native journal records the hash and exact imported identifier.

## Photos transactions and approvals

Only the personal System Photo Library is writable. Python enforces the selected System library before each operation; Swift handles full Photos authorization and blocks Shared Album assets. Public PhotoKit does not offer a reliable family Shared Photo Library membership check here. OSXPhotos' positive experimental shared flag blocks writes, but a false flag is not proof of personal ownership. Settings therefore require explicit confirmation that family Shared Photo Library is not configured. Do not remove that gate without a reliable replacement.

Import ordering:

1. Persist a stable operation ID and reviewed generation in workflow state.
2. Recheck the source version, output hash and measured smaller size.
3. Fsync native import intent.
4. Create the copy and checkpoint its PhotoKit placeholder ID inside the change block.
5. Verify that exact imported asset's duration/dimensions/media type after commit.
6. Persist the complete native receipt and workflow pair, then release safe app-managed bytes.

The original is not deleted by compression. `review.open` reads both identifiers and versions, then creates a fresh token bound to the job generation and both version snapshots. The comparison retrieves media again from Photos. Selected batch approvals freeze that same tuple per item. Native code rechecks it immediately before Keep/Revert. Modified assets invalidate approval and need inspection/review again.

Keep Compressed copies supported current metadata/albums and deletes only the exact original; Revert deletes only the generated copy. Verify the removal and survivor before recording completion. Metadata includes capture date, location, favorite, hidden status, filename stem and writable regular albums. Faces, edits, smart/shared albums and proprietary data cannot be assumed to transfer. All removals use Photos/Recently Deleted; permanent deletion is never automated.

Native `PhotosTransactionGate` serializes writes and reconciliation even if a backend request expires. Once intent exists, any ambiguous error becomes Needs attention and pauses further write processing. Inspection reconciles exact IDs/receipts without another write. An unknown import ID is not inferred from filename/date/size. Never automatically repeat uncertain imports or removals.

## Persistence and migration

Normal root: `~/Library/Application Support/CamPresso/`.

| Path | Ownership / contents |
| --- | --- |
| `preferences.json` | Library path, setup acknowledgments, presets and retained legacy preferences. |
| `workflow.json` | Version 4 jobs; sole writer is Python. |
| `.app.lock` | Held by backend or installer, not both. |
| `NativeJournals/<operation-id>.json` | Native write intent, generation, exact IDs, expected properties, hash and confirmed result. |
| `NativeWork/<job>/<generation>/` | App-managed compressed/rendered media only. |
| `Migrations/` | Fsynced version-3 workflow/preferences backups. |
| `Requests/` | Version-1 Service handoff files; errors become `.failed`. |
| `native.log`, `conversion.log` | Native/backend stderr and workflow diagnostics. |
| Legacy workspaces/journals | Preserved for recovery; not automatically replayed. |

`NativeStore` reads strictly: corrupt objects/unknown versions/invalid identities fail without discarding records. Stage transitions use temporary sibling files, fsync and atomic replace. Swift journal writes additionally fsync their parent directory. Jobs retain source/result IDs, source/result sizes, generation, expected media, versions, output hash/fingerprint, operation history, decision and legacy record/journal evidence. Approvals are never restored across launches.

Version-3 migration backs up state/preferences first. Completed pairs remain excluded from new compression. Confirmed imported results become pending reviews. Older local-only results become Awaiting import and require an explicit action; changed/missing verification records cannot be imported. Interrupted Photos writes become Needs attention. Interrupted preparation/encoding becomes Paused, with no automatic import. External-file records are preserved as legacy history.

Cleanup is restricted to known app-created filenames in NativeWork, excluding symlinks, external files and Photos bundles. Changed tracked files and unresolved operations are retained. Safe results are removed after confirmed import or close and requested again through Photos for comparison. Recovery bytes/reasons are visible in Details. Unchanged tracked outputs/backups in known app-owned legacy roots can be released after confirmed review/import. Legacy files outside these roots remain intact; the native release does not sweep arbitrary old workspaces. Photos' own cache is not app-managed storage.

## Installer and Service

`Install.command` delegates to `scripts/install.sh`; `install_macos.py` holds the data lock, builds the native bundle first, installs the package from a temporary source copy, rebuilds/registers the Service, then publishes the app bundle and Service. The first rebrand installation starts with fresh settings/history and protected legacy recovery; later updates preserve CamPresso data. `build_macos.py` sets deployment target/usage strings/entitlements and ad hoc signs the bundle. A trusted distribution would need an identity, hardened-runtime review and notarization separately.

The shell installer reuses installed Homebrew formulae and installs only missing dependencies; dependency upgrades remain an explicit Homebrew action. Compiler warnings and installation errors remain visible.

The Automator Service uses the bundled AppleScript to extract exact selected IDs, invokes `python -m campresso.photos_service`, writes `Requests/<uuid>.json` and opens the installed app. The backend accepts protocol 1 / `convert1080`, validates IDs against the loaded video catalog and queues the same compression workflow with a 1080p override. Setup must already allow writes. A Service request never includes deletion approval. The request wire format remains compatible; the new Service uses the CamPresso data root.

## Verification and releases

No tests or live Photos operations were run for this implementation. Static repository checks and native compilation were performed. Existing tests retain legacy contracts; they do not cover the new native player, encoder, bridge or migration. See [tests/README.md](../tests/README.md) and the [acceptance checklist](VERIFICATION.md) before requesting execution.

For a release, record actual compiler/tool versions, build results, test coverage gaps and any explicitly authorized live-library operations. Build success is not proof of playback/HDR correctness or iCloud synchronization. Never run acceptance against a personal library merely because app data is isolated. The GitHub workflow performs static checks only.

Keep version metadata in `pyproject.toml`; the native bundle reads it at build time. Package discovery includes new Python modules; `MANIFEST.in` includes native source for source distributions. Keep generated bundles, media, logs, environments and personal state out of commits. Commit/push/publish only when requested.


## CamPresso identity, fresh install and cleanup

The public package/CLI is `campresso`; the Swift product and executable are `CamPresso`; bundle ID is `local.silvio.campresso`. The GitHub repository is `SilvioGiancola/CamPresso`. The application data root is `~/Library/Application Support/CamPresso`. First install starts with no library/default preferences/empty workflow; library auto-discovery does not silently restore a prior selection. Subsequent updates retain the new installation’s state.

The installer records its ownership/phase in `installation.json`, refuses unrecognized existing data, and locks both current and previous roots. It builds the app before touching previous history. The previous installation’s pending reviews/uncertain operations are copied as metadata to `PreviousWork.json`; journals/media are not moved. A minimal `asset_links.json` ledger preserves only source/result/generated identifiers, library, decision and recovery association. Full completed job details are removed from active history. The previous app name/IDs occur only in explicit compatibility code/documentation; historical filesystem/journal strings must not be blindly renamed.

Additive protocol-1 methods are `history.previewCleanup`, `history.clear` and `previous.inspect`, with native `storage.status`. Preview returns a one-use token, record count, byte estimate and protected count. Confirmation requires the same workflow revision and an idle backend/native host. Both current and previous storage locks are held during confirmed cleanup. Once a previous review is resolved, a later cleanup may release its unchanged tracked recovery files at their original paths. The backend publishes identity/retained-file manifests and a cleanup intent before removing job records or bytes. `cleanup_pending.json` retains failed removals for retry; `retained_files.json` records changed/external/unverified paths for visibility. Only unchanged regular files under recognized app-owned media roots are disposable; source references, hard links, symlinks and Photos bundles are protected.

`PreviousWork.json` records the original root/journal/job and whether it was restored. Native inspection accepts a previous-work ID and reads only the journal recorded in that manifest, checking library/source/generation. It makes no Photos writes. Restoring a verified pair clears approvals and returns it to Review; incomplete/uncertain evidence stays in Recovery. Uncertain previous writes block new Photos imports/decisions until inspection, while bounded download/encoding work can continue. A restored item’s historical journal remains unchanged. This is independent of ordinary version-3-to-4 workflow migration, retained for older helper compatibility.

The Service is **CamPresso — Compress to 1080p**. Its identifier is `local.silvio.campresso.convert1080service`; request protocol 1 is unchanged. The installer creates a fresh venv rather than moving the old environment. It removes only previous app/Service bundles with expected bundle identifiers after publishing the new installation. Legacy recovery roots are retained; unrelated user files and Photos assets are never deleted by installation or history cleanup.

Brand source and deterministic export instructions are in the [branding guide](BRANDING.md). The build bundles the mark PNG and ICNS before signing. Package source distributions include the Swift asset exporter and brand files.

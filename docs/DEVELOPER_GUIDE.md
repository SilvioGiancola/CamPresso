# Developer guide

[Back to README](../README.md) · [User guide](USER_GUIDE.md)

This guide describes the current desktop app and its internal contracts. The user-facing flow is **Compress → Review → Replace**. Existing lower-level helpers also retain older external-file, import-only and version-removal operations; those are not the current GUI workflow.

## Contents

- [Development environment](#development-environment)
- [Architecture and source map](#architecture-and-source-map)
- [Queues and thread ownership](#queues-and-thread-ownership)
- [Conversion and replacement contracts](#conversion-and-replacement-contracts)
- [Persistence, migration and recovery](#persistence-migration-and-recovery)
- [Storage ownership and cleanup](#storage-ownership-and-cleanup)
- [Photos Service and helper protocols](#photos-service-and-helper-protocols)
- [Testing and current coverage](#testing-and-current-coverage)
- [Debugging and releasing changes](#debugging-and-releasing-changes)

## Development environment

The production installer uses Homebrew **Python 3.13**, the corresponding Tk package, FFmpeg/ffprobe and ExifTool. Python dependencies are `tkinterdnd2==0.6.3`, `osxphotos==0.77.2` and `pyobjc-framework-Photos`. The PyObjC package and Homebrew tools are not pinned to exact versions, and there is no dependency lockfile or declared minimum macOS version.

From the repository root, create a development environment separate from the installed runtime:

```bash
brew install python@3.13 python-tk@3.13 ffmpeg exiftool
"$(brew --prefix python@3.13)/bin/python3.13" -m venv "$HOME/.venvs/videoqueue-dev"
source "$HOME/.venvs/videoqueue-dev/bin/activate"
python -m pip install 'tkinterdnd2==0.6.3' 'osxphotos==0.77.2' pyobjc-framework-Photos
```

For UI development with separate application files:

```bash
VQ_DEV_DATA="$(mktemp -d "${TMPDIR:-/tmp}/videoqueue-dev.XXXXXX")"
python -c 'import sys; from app import App; App(data=sys.argv[1]).root.mainloop()' "$VQ_DEV_DATA"
```

`App(data=...)` isolates preferences, working files, workflow records and journals. **It does not isolate the Photos library:** native helpers still access the selected System Photo Library. Opening the GUI is not a read-only operation on the chosen data directory; startup/closing run cache cleanup and save state.

Running `python app.py` directly uses the normal `~/Library/Application Support/Video Queue` data directory. Its storage lock prevents another instance from owning that same directory. The macOS Service always queues into the installed app's normal data directory and opens its app bundle; it does not target an isolated development instance.

## Architecture and source map

```mermaid
flowchart LR
    Catalog[OSXPhotos catalog] --> App[App controller]
    Service[Photos Service selection] --> Requests[Request files]
    Requests --> App
    App --> Downloads[Original download queue]
    Downloads --> Conversion[Conversion queue]
    Conversion --> Review[Review in GUI]
    Review -->|Explicit approval| Replacement[Sequential Photos replacement]
    Replacement --> Journal[Native journal and recorded asset IDs]
```

| Area | Sources | Responsibilities |
| --- | --- | --- |
| Controller and workflow | [app.py](../app.py), [workflow_logic.py](../workflow_logic.py) | UI actions, job records, eligibility, approval snapshots, replacement queue, displayed statuses, migration and shutdown. |
| Catalog and UI | [photos_catalog.py](../photos_catalog.py), [photos_browser.py](../photos_browser.py), [photos_gallery.py](../photos_gallery.py) | Read-only OSXPhotos scanning, filtering, sorting, paged selection, thumbnails and original download queue. |
| Local previews | [photos_previews.py](../photos_previews.py) | Per-library/asset cache manifests, validated original previews, poster generation. |
| Downloads | [photos_download.py](../photos_download.py), [photos_resource.py](../photos_resource.py) | Independent local copies, original-resource downloads, validation, progress and cancellation. |
| Conversion | [engine.py](../engine.py), [quicktime_tracks.py](../quicktime_tracks.py) | FFmpeg/ExifTool execution, output checks, QuickTime movie/track date preservation and remapping. |
| Job/native orchestration | [jobs.py](../jobs.py), [photos_actions.py](../photos_actions.py) | Conversion preparation, reviewed replacement, PhotoKit access, exact-asset checks and durable intent journals. |
| Photos integration | [photos_service.py](../photos_service.py), [photos_service.applescript](../photos_service.applescript), [photos_navigation.py](../photos_navigation.py) | Service requests, library resolution, selection IDs and Show in Photos. |
| Persistence and deployment | [state_store.py](../state_store.py), [storage_cleanup.py](../storage_cleanup.py), [install_photos_service.py](../install_photos_service.py), [Install.command](../Install.command) | Atomic JSON, storage lock/cleanup, Automator bundle generation and app/runtime installation. |

The catalog uses public OSXPhotos query APIs. Native reads/writes use PhotoKit through PyObjC in subprocesses. Navigation and selection use Photos' scripting interface. No component should directly edit the Photos SQLite database or in-library media files.

## Queues and thread ownership

Tk widgets and `PhotoImage` objects belong to the GUI thread. Workers return events through `queue.Queue`; GUI polling uses `after` callbacks. A thumbnail executor has two workers, and generation IDs discard stale thumbnail results after a catalog/view change.

The queues have distinct responsibilities:

- `PhotosBrowser` downloads one original at a time, keyed by library path and asset UUID. Duplicate consumers share callbacks and a download. `PreviewStore` validates the local copy before publishing its manifest.
- `App.compress_media` records settings, registers download callbacks, then feeds downloaded originals into `App.convert_media`. One conversion runs at a time. The original download queue can overlap it.
- A compression generation counter prevents callbacks from starting conversions after Stop. Pause finishes the current encode, then holds queued conversions. Ordinary conversion/download failures allow other successful jobs to continue.
- Approved replacements run sequentially after current download/conversion work is idle. An uncertain Photos write pauses replacement processing and clears remaining approvals.
- Closing cancels cancellable work, waits for active operations, then joins thumbnail workers before cleanup. An in-flight Photos write is allowed to finish; do not kill that helper to implement cancellation.

Local compressed playback can run during encoding. Library changes, native replacement and retrieval of a compressed Photos copy are gated on idle work. `active`, browser `busy/task`, and the per-job `operation` describe different parts of this coordination; do not treat any one flag as the entire workflow state.

## Conversion and replacement contracts

### Main entry points

| Entry point | Contract |
| --- | --- |
| `App.compress_media(media, library, settings_override=None)` | Validate the System Photo Library, capture settings, queue original preparation and feed conversions. Does not authorize Photos replacement. |
| `Converter.convert(source, settings, report)` | Convert an independent local source and return `(output_path, detail)`. `report(stage, percent)` reports processing stages. Raises `Cancelled`, `Skipped` or a processing error. |
| `JobRunner.run(job, settings, report)` | Prepare/reuse the working original and record output expectations/fingerprints. The current GUI supplies `import=False`, `remove=False`, `replace=False`. |
| `App.replace_selected()` | Check every selected result, obtain one batch confirmation and save an approval snapshot per job. |
| `JobRunner.replace_reviewed(job, report)` | Validate approval and files, obtain an independent original if necessary, then publish with removal enabled. |
| `JobRunner.actions(request)` | Invoke the native helper with JSON stdin, validate its final JSON result and propagate `JobFailure` recovery metadata. |

Core settings are `codec` (`HEVC`/`H.264`), `size` (`720`/`1080`/`2160`), `quality` (`High`/`Balanced`/`Small`), `container` (`MP4`/`MOV`), `folder`, `replace` and `require_smaller`. The GUI uses a per-job folder under `Workflow/Converted`, `replace=False` and `require_smaller=True`. The engine's older in-place external-file replacement mode is separate from replacing a Photos asset.

The converter selects the first non-artwork video stream and all audio streams. It bounds the long/short sides without upscaling, uses software x265/x264 encoding, emits 8-bit `yuv420p`, and encodes audio to AAC at 192 kbit/s. HEVC is tagged `hvc1`. Extra video tracks, subtitle/data streams and proprietary telemetry are not retained. HDR/PQ/HLG and anamorphic inputs are skipped.

Before publishing output, conversion checks supported metadata, duration, audio stream count, complete video/audio decoding and source identity. Duration tolerance is `max(0.5 seconds, 0.5% of source duration)`. QuickTime date handling maps retained input tracks to output tracks; attached artwork must not be mistaken for a retained movie track. Unique output naming avoids overwriting an existing file.

### Approval and native ordering

A reviewable result needs a Photos source, known expected video properties, unchanged local output, a measured reduction, an original not already removed, and a retry-safe state.

The approval snapshot records `output`, `fingerprint`, `expected`, `original_uuid` and the current `new_uuid`. A fingerprint is **`[size_bytes, mtime_ns]`**, not a content hash. Expected video properties are `{duration, width, height}`, with finite positive duration and positive integer dimensions. Changing the approved file or identifiers invalidates approval.

Replacement ordering must remain:

1. Require the selected System Photo Library and full Photos access.
2. Validate the reviewed output and independent original working copy. A stale/changed backup is fetched again from the exact Photos asset.
3. Confirm that the output is smaller and that original dimensions/duration match the expected original.
4. Journal import intent, create the new Photos asset, and record its placeholder ID before commit finishes. If a converted asset ID is already recorded, verify and reuse that asset instead.
5. Verify the imported asset's media type, dimensions and duration.
6. Recheck local fingerprints, journal removal intent, then remove exactly the original asset.
7. Record completion and update workflow state.

Import transfers creation date, location, favorite/hidden flags and writable regular-album membership while the original is present. Other Photos-specific metadata is outside that guarantee. A verified PhotoKit asset confirms local library presence, not iCloud synchronization.

Native callbacks declared `void` must return `None`. Use wrappers that discard native/Python body return values and catch Python exceptions; a tuple-returning lambda can terminate the helper through `OC_PythonException`. Import `get_system_library_path` and `get_last_library_path` from **`osxphotos.utils`**, not the top-level package.

## Persistence, migration and recovery

All normal app state is under `~/Library/Application Support/Video Queue/`:

| File | Purpose |
| --- | --- |
| `preferences.json` | Remembered library path and preserved preference data. |
| `workflow.json` | Version 3 object with a `jobs` array. |
| `.app.lock` | `fcntl.flock` lock held by the single owner of this data directory. |
| `Photos work/journals/*.json` | Native operation intent, identifiers, fingerprints and outcome. |
| `Requests/` | Photos Service handoff files. |
| `conversion.log`, `launch.log` | Processing diagnostics and installed-launcher output. |

`StateFile.write` uses a temporary sibling, flush/fsync and `os.replace`. Non-object/unreadable JSON reads yield `{}`. Native journal writes use the same atomic publication approach. Do not replace these with in-place truncation.

Important job fields are grouped below; these are internal dictionaries rather than a stable external API:

| Fields | Meaning |
| --- | --- |
| `id`, `media`, `library`, `path` | Internal job ID, serialized `Media`, library identity and legacy external input path. |
| `state`, `phase`, `progress`, `operation`, `detail` | Processing state, derived display label, progress, current action and diagnostics. `phase` alone is not proof of a native outcome. |
| `source_size`, `result_size`, `expected` | Measured original/result sizes and verified output properties. |
| `output`, `output_fingerprint`, `backup`, `backup_fingerprint` | Recorded generated output and independent original working copy. |
| `new_uuid`, `last_new_uuid`, `uploaded_ids` | Current and historical converted Photos identifiers. The original ID remains in `media.uuid`. |
| `converted`, `verified`, `original_removed`, `converted_removed`, `replacement_complete` | Separate conversion and Photos outcome flags. |
| `retry_safe`, `review_operation`, `journal`, `replacement_approval` | Retry/reconciliation and approval information. |
| `queued_settings`, `conversion_settings`, `compression_pending` | Captured settings and download-to-conversion coordination. |
| `archived`, `previous_conversions`, `retained_outputs`, `cleanup_retained` | Historical results and protected local files. |

`Media` includes UUID, filename, kind, size/dimensions, date, local path/availability, Live Photo and edit flags, thumbnail, favorite and optional duration. Duration comes from OSXPhotos' `exif_info.duration`. App identity comparisons normalize UUID prefixes before `/` and resolve library paths; PhotoKit identifiers can include a suffix such as `/L0/001`.

Loading preserves legacy identifiers/history and fills newer defaults, including sizes and optional Media fields. Pending downloads/conversions are not automatically resumed. Queued replacement approvals are cleared. Interrupted native operations require inspection or a validated completed receipt.

Key journal phases include `Import pending`, `Imported`, `Import verified`, `Removal pending`, `Original moved to Recently Deleted` and `Photos inspected`. A placeholder ID or pending phase is not proof of success. `App.recover_receipt` checks the recorded library/source/output relationship and only automatically reads journals under the current `Photos work/journals` directory. Older journals elsewhere remain useful evidence but are not universally replayed.

When a native result is ambiguous, keep `retry_safe=False`, retain recovery files and inspect exact recorded IDs. An unknown imported asset ID cannot be resolved by filename matching. Never automatically repeat an uncertain import/removal or infer retry safety by searching an exception's message text. `JobFailure` carries `output`, `journal` and `retry_safe` explicitly.

## Storage ownership and cleanup

`clean_storage(data, jobs, delete_unsent=False)` runs at startup and after workers stop on close. Startup protects recorded unreviewed results. Close uses `delete_unsent=True` after the required discard confirmation.

Cleanup handles `Previews`, `Thumbnails`, `Converted previews`, `Workflow`, current working-original directories and tracked legacy working-original directories. Successful replacements no longer require a permanent local original backup. Unresolved Photos operations and originals removed without a verified replacement keep available recovery copies.

Preserve these invariants when changing cleanup:

- No external inputs or files inside `.photoslibrary` bundles are deleted.
- Unlink a recorded generated output only if its stored size/mtime still matches; preserve changed outputs, recorded `retained_outputs` and unsafe paths/symlinks.
- Do not clear files needed by a running worker or unresolved transaction.
- Preserve workflow/preferences, asset IDs, logs and journals; clearing media bytes must not lose the relationship between original and replacement.
- A `Done` entry remains grouped after its local files are cleared, and playback can retrieve the recorded Photos copy.

Free-space checks reserve an additional 256 MiB: local copying requires space for the source; a cloud download with known size requires twice that size for staging/publication; encoding requires at least the original size available for output. These are preflight checks, not reservations across concurrent workers. Later disk exhaustion remains an error path that must keep Photos originals intact.

## Photos Service and helper protocols

### Service handoff

`install_photos_service.py` builds an Automator `.workflow` containing a Run AppleScript action; it does not execute that workflow. The installer then registers Services using `/System/Library/CoreServices/pbs -update`. Keep the native action serialization key **`Class Name`** and Photos-only service metadata intact.

The AppleScript resolves each selection loop reference with `contents of chosenItem`, gets the media item's `id`, then converts that value to text. Coercing an unresolved “id of item of selection” reference causes the historical Photos selection error. Shell arguments are individually quoted.

The Python enqueue entry point validates and deduplicates 1–500 UUIDs, writes `Requests/<32-hex-id>.json`, and opens the installed app. The request has version `1`, action `convert1080`, an optional library string (normally `null`) and a `uuids` array of canonical GUID strings. The consumer rejects oversized requests (over 128 KiB), unexpected filenames and symlinks.

Library discovery runs in the app, not the Automator runner: explicit/preferred/saved paths, Photos' last-opened and System library paths, then libraries under Pictures. A candidate must contain every exact selected ID. Still pictures are ignored after matching the selection; the compression entry point enforces the System library requirement.

Requests become `.running` during preparation. Successful handoff to the compression workflow removes the request file; that acknowledgment does **not** mean conversion/replacement finished. Failed requests become `.failed`, and interrupted `.running` files become `.interrupted` on restart. Neither is automatically replayed. The Service never sets replacement approval.

### Native subprocesses

- `photos_actions.py` accepts one JSON request on stdin. It returns a final JSON object on stdout; errors include journal information. Supported internal actions are `check`, `finish`, `remove`, `verify`, `inspect`, `delete_original` and `delete_converted`. The GUI reaches these through job/controller methods rather than treating the helper as a user CLI.
- `photos_resource.py` accepts asset UUID, library and destination. It streams JSON `progress` packets with `percent`, then `complete` or `error`. It requests the original `PHAssetResourceTypeVideo` with network access allowed.
- `fetch_video` uses independent local copying when an original is available. For missing originals its default path uses the PhotoKit resource helper; the injected runner/CLI path supports OSXPhotos export and simulated tests. Staging, validation and unique publication happen outside Photos.
- `show_in_photos` validates identifiers, supplies `/L0/001` for a plain GUID, then uses Photos' `spotlight` AppleScript command. It does not switch the open/System library.

## Testing and current coverage

The repository uses Python `unittest`. The following commands are manual options; documentation updates do not imply that these checks were run or passed.

```bash
# Mocked core contracts and catalog behavior.
python -m unittest -v test_actions test_bridge test_catalog test_jobs

# Existing complete suite; requires the development environment and tools.
python -m unittest discover -v
```

| Existing modules | Main coverage / requirements |
| --- | --- |
| `test_actions`, `test_bridge`, `test_jobs` | Simulated native transactions, callback return contracts, orchestration and failure metadata. |
| `test_catalog` | Catalog queries, filtering, sorting and independent copying with temporary files. |
| `test_download`, `test_previews` | Simulated cloud orchestration, preview ownership and cancellation; several cases invoke real FFmpeg/ffprobe. |
| `test_engine`, `test_tracks`, `test_cover` | Real FFmpeg fixtures, cancellation, stream handling and QuickTime metadata remapping. Some ExifTool behavior is mocked. |
| `test_metadata_integration` | Real FFmpeg/ffprobe/ExifTool round trips; skipped when tools are missing. |
| `test_gui` | A real Tk session with temporary app data and simulated Photos behavior. Needs a graphical environment and tkinterdnd2. |

**Current coverage limitation:** `test_gui.py` still references removed UI members such as `App.folder` and `remove_menu`. Those tests need migration to the current interface. There is no recorded passing full-suite result for the Compress → Review → Replace refactor in these docs, and the existing suite is not proof of the complete current workflow.

When extending coverage, include cloud download/conversion overlap, approval invalidation, sequential batch replacement, no-reduction/HDR skips, migration, close/discard/cancel, disk exhaustion and interrupted native transactions. Test uncertain import/removal outcomes using injected adapters before attempting live Photos operations.

For an explicitly authorized live acceptance check, use disposable videos in a test **System Photo Library**. A separate `App(data=...)` directory alone does not isolate Photos. Check metadata, exact original/replacement IDs, playback, Recently Deleted and reopening/cleanup. Report mock, tool integration, GUI and live-library results separately; do not claim iCloud synchronization from a successful local import.

## Debugging and releasing changes

Start with **Video details → Copy details**, `conversion.log` and the job's journal. `launch.log` captures installed-launcher output, including startup failures. Inspect `retry_safe`, native phase and IDs before deciding whether a retry is appropriate. Redact personal paths/identifiers when publishing reports.

For syntax-only inspection without importing modules or starting the app:

```bash
python - <<'PY'
import ast
from pathlib import Path
for path in Path('.').glob('*.py'):
    ast.parse(path.read_text(), filename=str(path))
PY
bash -n Install.command
git diff --check
```

Release procedure:

1. Review changes against the native ordering and cleanup invariants above. Keep user-facing labels, statuses and both guides aligned with the implementation.
2. If adding a runtime module, add it to the explicit copy list in `Install.command`. Keep `photos_service.applescript` beside `install_photos_service.py` when building the workflow.
3. Record the checks performed, their results, current coverage limitations and whether any live Photos work was performed. Resolve or explicitly account for legacy test expectations before claiming a passing release suite.
4. Quit the installed app before running `bash Install.command`. It installs Homebrew/Python dependencies, copies runtime modules, rebuilds/registers the Service, writes the launcher/Info.plist and opens the app. It is not a read-only development command.
5. Reopen the app after source updates. Compare the installed launcher/runtime with the intended source revision when diagnosing an old UI or stale helper.
6. Preserve user workflow/recovery data across updates. Compatibility is forward migration; an older app is not guaranteed to understand a newer saved workflow.
7. Review the Git diff, commit the intended files and push through the repository's chosen branch workflow. Do not publish local environments, media, logs or personal workflow records.

The standard installer runs `app.py` from the Application Support directory using its `venv/bin/python`. A separately staged local update may instead point the launcher at `Versions/<version>/app.py`; inspect the launcher to identify the running source location. Those code folders are separate from shared app state. Service enqueue still uses the installed data directory and app bundle.

# Video Queue 2.0

One window with **one video catalog**. Each video shows its download, conversion and Photos status together. The flow is **Download and play → Convert selected → Play converted → Send to Photos**. Removal of either version is a separate, confirmed action. Conversion itself leaves Photos unchanged.

## Update / install

Quit the existing app, unzip this package, and double-click `Install.command`. It updates `~/Applications/Video Queue.app` and installs dependencies through Homebrew. Homebrew is required (https://brew.sh). If the installer is blocked, right-click → Open; alternatively run it with `bash` from Terminal.

The launcher is an unsigned, locally installed Mac app with external dependencies. Automated checks exercise the GUI on macOS, the native PyObjC bridge, real FFmpeg/ExifTool conversion and metadata preservation, queue orchestration, and safety ordering. Photos import/deletion and iCloud downloads use simulated adapters in the tests; live library operations still require a first-run check on your Mac.

## Select videos

### Convert directly from Apple Photos

The installer adds the Photos-only **Convert video to 1080p** Service in `~/Library/Services`.

1. Select one or more **videos** in Apple Photos.
2. Right-click → **Services → Convert video to 1080p**, if Photos shows Services there. The command is also available from the menu bar: **Photos → Services → Convert video to 1080p**. macOS controls the context menu; this does not add a top-level Photos menu item.
3. Video Queue opens, downloads the selected originals if needed and converts them using **1080p**, with the current codec/quality/container settings. Each downloaded video enters conversion immediately, while the next original downloads. Portrait orientation and the existing no-upscaling rule are preserved. Originals remain in Photos. Select **Send to Photos** separately to upload a converted result.

On first use, allow the Service to control Photos so it can read the current selection. Catalog access and iCloud download retain their existing Photos/Full Disk Access requirements. If the Service is missing, reopen Photos and enable it under **System Settings → Keyboard → Keyboard Shortcuts → Services**. If Video Queue was already open during the update, quit/reopen it once to enable request handling.

Select videos in the thumbnail grid or an album; opening a video in full-screen view is not required. Video Queue resolves the library using its saved path, Photos' last-opened and System library paths, and libraries in Pictures. It checks the exact selected IDs before converting. If an external library cannot be discovered, choose it in Video Queue and click **Scan**, then invoke the Service again. Library discovery runs in Video Queue so it does not depend on the Service runner's access to Photos preference files.

Still pictures are skipped in a mixed selection; a selection containing only pictures shows a message. The Service passes exact asset identifiers, not filenames. An existing 1080p conversion is reused when available. A conversion at a different resolution is kept in the workflow history while the new 1080p conversion becomes the active entry. Details lists previous conversion paths and Photos identifiers. Service requests arriving during another operation wait until the app is idle; interrupted preparations are not automatically replayed. Temporary downloads follow the normal cleanup-on-close policy.

Apple documents Services in application menus and contextual menus in [Making a Systemwide Service](https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/MakeaSystem-WideService.html).

### Browse inside Video Queue

1. Click **Scan** in the Photos Library. The library path is remembered when you reopen, including a temporarily unavailable drive. On the first launch, the app uses Photos' last-opened library path when available. You can paste a `.photoslibrary` path or choose its containing folder (such as Pictures); a single library inside is resolved automatically.
2. Browse the **Thumbnails** grid, or switch to **List** for detailed columns. Search by filename/date/asset ID; expand **Filters** for minimum size, megapixels, width or height. Use Sort or list column headings. Results are paged in sets of 500; Command-click toggles individual thumbnails and Shift-click selects a range. CSV export and permissions help are in **More**.
3. Select one video and click **Download & play original**, or double-click its thumbnail. The original downloads from iCloud if necessary, or is independently copied when local, then opens in your default video player. The button becomes **Play original** for repeat playback. Select multiple videos to **Download selected** one at a time; they do not all open players.
4. When the selected originals are available, click **Convert selected** to create and verify separate converted files. The button is enabled only when every selected video is downloaded or locally available. Downloaded originals are reused and backed up outside Photos. Conversion status and percentage appear on the same thumbnail and list row.
5. Add external videos with **Add files…** or drag them into the catalog. Details, retry/review, Photos status checks and file locations are in **More**.

Every video shows a badge in its thumbnail and a Download column in List view:

| Mark | Meaning |
| --- | --- |
| Green · Downloaded | An original is available locally, in preview storage, or in its retained conversion backup. |
| Gray · Not downloaded | Download the original before converting it. |
| Amber · Downloading / Copying / Checking | The original is being prepared. iCloud downloads show Apple's reported percentage when available; local copying shows byte progress. Preparation without a reported percentage shows elapsed time or an activity label. |

List view also has **Conversion** and **Photos** columns. Photos status distinguishes whether the original is present and whether the converted copy has been uploaded or removed. Known converted assets are grouped with their original in one entry, and the workflow remains visible after removal. **More → Check Photos status** refreshes recorded asset presence if you changed Photos outside the app. “Copy in Photos” confirms the local Photos addition; it does not confirm iCloud synchronization.

Thumbnails use cached Photos poster images or a frame from an available local video. Browsing never downloads an original. Assets without a cached poster or local original show a placeholder until downloaded. Thumbnail generation runs in the background for visible items.

Videos marked as Favorites in Apple Photos show a **♥** on their thumbnail and beside their filename in List view. Click **Scan** to refresh hearts after changing Favorites in Photos. The last scanned Favorite status is retained with saved workflow entries.

Select one item and choose **More → Show in Photos** to locate that exact asset in the Photos app. This also works for originals that are not downloaded, and while another video downloads or converts. If the original was removed, the command uses the recorded converted Photos copy when available. **Show converted in Photos** opens that copy separately when both versions exist. External files offer navigation once a converted Photos asset is recorded. macOS may ask permission to control Photos. If the item is not found, open the same library in Photos and try again; navigation never changes the System Photo Library.

Preview originals and generated thumbnails use `~/Library/Application Support/Video Queue/Previews` and `Thumbnails` during a session. Closing the app clears these caches and redundant original backups after all downloads, conversions, Photos actions and thumbnail workers have stopped. Command-Q uses the same cleanup. Old caches are also cleared on the next launch after an interrupted exit. Photos' own managed media are not changed by cache cleanup.

Local converted files are cleared once their addition to Photos has been verified. Use **Download & play converted** to fetch that version again; choosing converted removal also fetches a local copy first if needed. Unsent conversions are kept in the output folder by default. Uncheck **Settings → Keep converted videos not sent to Photos when closing** to clear those too. Cleanup verifies a recorded output's size and modification time and never deletes external input files or edited/replaced outputs.

Recovery copies remain if the original was removed from Photos or a Photos action needs review. Unsent or removed converted versions are retained by the default setting. **Video details** identifies retained recovery copies. Library preferences, workflow status, logs and small Photos journals are kept; a cleared download is no longer shown as an app-local copy on reopening.

The catalog lists original primary file sizes/dimensions. It is not a total Photos storage audit: thumbnails, edits, Live Photo paired videos and RAW companions are additional storage. Unknown sizes sort last. Recently Deleted items are excluded. Photos can be inspected, but this tool converts videos only.

## Converted videos

The default is **HEVC / 1080p / Balanced / MP4**. Click **Settings** for conversion settings and the output folder. Select videos in the catalog to start a batch. Select one video for these actions:

| Action | Meaning |
| --- | --- |
| Play original | Open the downloaded original in your video player. |
| Play converted / Download & play converted | Open the converted file, downloading the recorded uploaded version from Photos if its local copy was cleared. |
| Send to Photos | Add this existing converted file to Photos and verify the new asset. Conversion is not repeated. The state becomes In Photos and the Send button is disabled. Adding to Photos does not confirm completion of iCloud synchronization. |
| Remove from Photos → Original video… | Confirm removal of this exact original asset. An independent local recovery backup is retained, including after close. |
| Remove from Photos → Converted video… | Confirm removal of the recorded converted asset. A local copy is downloaded first if necessary, and retained by the default unsent-conversion setting. |

### Save space in Photos

Select a video and use **Remove from Photos** to choose the original or converted version. Review the confirmation, and accept Photos' own deletion prompt if shown. External source files are never deleted by this action; their uploaded converted copy can be removed from Photos.

Removal requires an unchanged, nonempty local copy of the selected version outside Photos. Duration and dimensions are checked against the exact Photos asset before requesting deletion of its recorded identifier. Original removal creates or reuses an independent backup. Converted removal requires the recorded, verified upload and its unchanged local output. Nothing overwrites media files or database tables inside the `.photoslibrary` bundle.

Sending creates a **new Photos asset**. While the original remains in Photos, its date, location, favorites, hidden status and writable regular-album membership are transferred through public PhotoKit APIs. Send the converted version before removing the original to preserve these Photos properties. Photos-only captions, keywords, face associations, edit history, poster frame and smart-album membership may not transfer. Embedded metadata remains subject to the conversion checks.

PhotoKit iCloud downloads and library changes are supported only for the **System Photo Library**. Other libraries can be browsed and their local videos converted, but cloud download/sending/removal is refused there. The app never changes which library is designated as System.

Deleting either version moves it to **Recently Deleted**, and iCloud deletion affects synced devices. It is not permanently erased by this tool. To reclaim storage after reviewing the results, remove the old assets from Recently Deleted yourself and eventually remove external backups. Photos/iCloud may take time to update storage figures.

### Save to a separate folder

Choose an output folder in **Settings** and click **Convert selected**. Keep the result locally or send it to Photos later. Conversion alone does not shrink the Photos library.

### External files

External files are converted to separate files. Their originals are kept. Once converted, **Send to Photos** is available; **Remove original** is reserved for Photos sources. The GUI does not replace external files in place.

## Conversion settings

Expand **Settings** to change the default. Choose H.264 for compatibility, 720/1080/2160 resolution, High/Balanced/Small quality, and MP4/MOV for new files. Portrait and landscape aspect ratios are preserved and small videos are not upscaled.

Original downloads and conversion have independent queues. One original downloads while one video converts, with both names, progress and waiting counts shown above the action buttons. Add more originals with **Queue download & play** or **Queue downloads** while a download is running; duplicate requests for the same video share that download. Already downloaded originals and local converted files can be played while conversion runs.

Select downloaded videos and click **Add to conversion** while another video converts. Each video converts once, in queue order. **Convert selected** also works while other originals are downloading. The settings chosen when a video is queued are retained for that video. Photos originals are copied to independent backups before conversion, and both versions keep their own catalog status. Sending to Photos, removing assets, switching libraries and cloud retrieval of a converted copy wait until the current work is idle.

**Pause** stops after the current conversion; **Resume** continues the waiting queue. **Stop conversion** cancels the current encode and clears its waiting queue. Both leave original downloads running. **Cancel downloads** stops the active download and its waiting queue while conversion continues. For a Photos Service batch, stopped conversions leave subsequent downloads available for manual conversion. Failed encodes pause conversion and open Details while other downloads continue. Closing stops both queues and waits for active work before clearing temporary storage; an in-flight Photos write finishes safely.

**Check Photos** checks recorded original and converted identifiers without importing again. A known converted asset that is absent permits another send. If an interrupted upload has no recorded converted identifier, it remains **Needs review**; inspect Photos and the journal before re-adding that file.

The library path, cleanup preference and each video's workflow are retained across restarts. Temporary previews are cleared on close. Interrupted conversions can be retried after downloading again if needed. Interrupted Photos writes are marked **Needs review**, or recovered from a completed receipt; they are never automatically replayed. Workflow and preference JSON files are saved atomically under `~/Library/Application Support/Video Queue`. Only one app instance can use this storage at a time.

## Backups and recovery

Photos conversion working copies are stored under:

`~/Library/Application Support/Video Queue/Temporary work/Original backups/<asset UUID>/`

They are cleared on close while the original remains in Photos. Backups for removed originals or unresolved Photos changes are kept for recovery. Tracked backups from older versions under `<output folder>/Video Queue Work/Original backups/` follow the same cleanup policy.

Photos action journals are under:

`~/Library/Application Support/Video Queue/Photos work/journals/`

An original removed before conversion is backed up under `~/Library/Application Support/Video Queue/Photos work/Original backups/<asset UUID>/`.

Older versions used `<output folder>/Video Queue Work/journals/`; those journals remain in place.

A journal is saved before native work starts and records the original UUID, converted output and backup paths, import/removal intent, the new placeholder identifier as soon as Photos supplies it, and the verified-import stage. Updates replace the journal atomically. An **Import pending** or **Removal pending** phase means that Photos may have committed the operation before the helper stopped. If Photos declines removal, the verified import and original can both remain. Use the recorded identifiers to inspect them before re-adding a job.

Converted files are retained even if a subsequent Photos operation fails; Details and Show file retain their location. A failed helper callback can occur after Photos has imported the converted file. A receipt records the new asset identifier as soon as it is available. Review that asset before permitting another send.

Logs are in `~/Library/Application Support/Video Queue/conversion.log`. Use Details → Copy details to share a failure. Retained recovery backups occupy space until you delete them; redundant working copies are cleared automatically. Restore an original by recovering it from Recently Deleted in Photos or importing its external backup.

## Permissions

The app is in your **personal** Applications folder: `~/Applications/Video Queue.app`.

For catalog access, if macOS denies permission: System Settings → Privacy & Security → Full Disk Access → **+** → **Command-Shift-G** → enter `~/Applications` → select Video Queue.app and enable it. Quit/reopen the app. **More → Permissions help** shows the same instructions and reveals the app in Finder.

Import/removal and iCloud download also require **Photos access**. Accept the system prompt if shown; enable full access under System Settings → Privacy & Security → Photos. A database-not-found error can mean the wrong folder rather than a permission issue: choose the `.photoslibrary` bundle, its containing folder, or leave Library blank.

## Current limits

SDR videos only; HDR/PQ/HLG and anamorphic videos are refused. Software encoding is used. Audio is re-encoded to AAC. Extra video tracks, subtitles and proprietary telemetry streams are omitted. Capture dates, location and common writable descriptive metadata are checked; QuickTime movie-header dates are preserved, including unset dates, and retained track dates follow track remapping. Attached cover art is excluded from movie-track counts. Photos picks its own poster frame for newly imported assets; set it through Photos if needed.

Photos/iCloud operations depend on macOS permissions, connectivity, the selected asset's availability and Apple's APIs. Automated verification exercises callback contracts and simulated transactions; it does not perform live import/deletion or iCloud transfers. The app uses public PhotoKit for library changes and never directly edits the Photos database. Apple may show native import/deletion permission dialogs.

## Uninstall

Delete `~/Applications/Video Queue.app`, `~/Library/Application Support/Video Queue` and `~/Library/Services/Convert video to 1080p.workflow`. Output folders, backups and Photos assets remain. Shared Homebrew dependencies are not removed.

Sources: https://developer.apple.com/documentation/photos/phassetchangerequest ; https://rhettbull.github.io/osxphotos/API_README.html ; https://ffmpeg.org/ffmpeg-filters.html ; https://exiftool.org/TagNames/QuickTime.html .

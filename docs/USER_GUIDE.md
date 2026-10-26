# User guide

[Back to README](../README.md) · [Developer guide](DEVELOPER_GUIDE.md)

Video Queue helps you keep smaller versions of videos in Apple Photos. Its workflow is **Compress → Review → Replace**. You decide which compressed results replace their originals.

## Contents

- [Install and update](#install-and-update)
- [Choose a library and allow access](#choose-a-library-and-allow-access)
- [Compress, review and replace](#compress-review-and-replace)
- [Understand video statuses](#understand-video-statuses)
- [Quality and multiple videos](#quality-and-multiple-videos)
- [Convert from the Photos menu](#convert-from-the-photos-menu)
- [Storage and closing the app](#storage-and-closing-the-app)
- [Troubleshooting and recovery](#troubleshooting-and-recovery)
- [Limits and uninstalling](#limits-and-uninstalling)

## Install and update

You need a Mac with Apple Photos, [Homebrew](https://brew.sh/), and an internet connection to install dependencies. Cloud-only videos also need an internet connection to download. This repository does not specify a minimum supported macOS version.

1. Download the repository ZIP from GitHub and unzip it, or clone the repository.
2. Quit Video Queue before installing or updating. Let an approved Photos operation finish safely if the app is busy.
3. Double-click [Install.command](../Install.command). Alternatively, open Terminal in the repository folder and run:

   ```bash
   bash Install.command
   ```

4. The installer installs dependencies and opens the app when finished. If installation stops, read the Terminal error before retrying.

The installer creates:

| Item | Location |
| --- | --- |
| App | `~/Applications/Video Queue.app` |
| App data and Python runtime | `~/Library/Application Support/Video Queue/` |
| Photos Service | `~/Library/Services/Convert video to 1080p.workflow` |

This is an unsigned local app. macOS may require you to use right-click → **Open** or allow opening it through Privacy & Security. After an update, quit and reopen Video Queue to load the new code. Updating does not itself replace any Photos videos.

## Choose a library and allow access

1. In Apple Photos, open **Photos → Settings → General** and check which library is the **System Photo Library**. Video Queue does not change that setting for you.
2. In Video Queue, click **Settings…** to open a separate window. Choose or paste the `.photoslibrary` path and click **Save**. You can also choose a folder containing exactly one Photos library. **Cancel** closes the window without applying edits.
3. The library loads after saving and automatically whenever the app launches. With no saved choice, the app tries the System Photo Library, then the last-opened library. The main window shows the library name; **Refresh** reloads its catalog.
4. The path stays remembered if an external drive is unavailable. Reconnect the drive and click **Refresh**, or change the library in Settings. An unavailable library does not cause a silent switch to another library. Finish or stop queued work before changing libraries.

Other libraries can be browsed, but the complete compression/replacement workflow requires the System Photo Library. Scanning and browsing do not download full originals.

Allow these permissions when needed:

| Permission | Used for | Where to manage it |
| --- | --- | --- |
| Full Disk Access, if catalog reading is denied | Reading the library catalog | System Settings → Privacy & Security → Full Disk Access. Add `~/Applications/Video Queue.app`, then quit and reopen it. |
| Full Photos access | Original downloads, inspecting assets and approved replacement | System Settings → Privacy & Security → Photos. |
| Automation / control of Photos | Reading a Service selection and showing an item in Photos | System Settings → Privacy & Security → Automation, or the macOS permission prompt. |

**More → Permissions help…** shows the app location and setup instructions. A “library/database not found” error can also mean the wrong folder was selected.

The menu bar belongs to Video Queue: **Help → Video Queue User Guide** opens this guide, and **About Video Queue** shows app information. On macOS, the app menu's Settings command opens the same library and compression settings window. **File → Close Window** closes the focused window; closing the main window or choosing Quit uses the normal confirmation and cleanup flow.

## Compress, review and replace

### 1. Browse your videos

The thumbnail grid and List show filenames, original sizes, durations and favorites marked with **♥**. Videos are initially sorted by original size, largest first. Unknown sizes sort last. Search by filename, date or asset ID; expand **Filters** for minimum size or dimensions.

Command-click toggles thumbnail selection; Shift-click selects a range. **Select page** selects the current page, which contains up to 500 items. Switching between Thumbnails and List preserves the current selection.

Thumbnails use Photos' cached poster images or an available local video. A placeholder means no suitable local poster is available; it does not mean the video is damaged. Click **Refresh** to reload favorites changed in Photos. **More → Save list as CSV…** exports the filtered catalog.

### 2. Compress selected videos

Select one or several videos and click **Compress**. Video Queue automatically:

1. Downloads a cloud original or prepares an independent local working copy.
2. Creates a compressed video outside the Photos library.
3. Checks duration, audio stream count, playback decoding and supported metadata.
4. Displays **Ready to review** and the measured size reduction.

One original can download while another video converts. Repeated requests for the same queued video reuse its download. Photos remains unchanged during compression.

A result such as `1.2 GB → 180 MB · 85% smaller` compares the primary video files. Original-size columns use MiB; the reduction display uses decimal KB/MB/GB. This is not a total audit of Photos caches, edits or other associated resources.

### 3. Review the result

Select one result and use **Play original** and **Play compressed**. Playback opens your default video player. Playing an original downloads it automatically if necessary; you can preview a video before deciding to compress it. Double-clicking a thumbnail also opens playback.

Review quality, orientation, sound and the parts of the video that matter to you. Files that are not smaller cannot be used for replacement. You can leave a result unapproved and keep its original unchanged.

### 4. Replace approved results

Select one or several results with **Ready to review**, then click **Replace in Photos…**. Every selected result must be eligible. Read the confirmation, including the total size reduction and metadata limitations.

For each approved video, the app imports or reuses its recorded compressed copy, verifies the exact Photos asset, then moves the original to Recently Deleted. Replacement runs one video at a time. Photos may display an additional native confirmation.

Approval applies to the reviewed file and its recorded properties. A changed or missing result needs another review: retrieve an existing verified Photos copy if available, or compress again. An uncertain Photos operation pauses the replacement batch and requires attention before retrying.

### 5. Find and play the remaining copy

After replacement, the entry becomes **Done**. The original and compressed version remain grouped in one catalog entry. **Play** opens the compressed video; if its local temporary copy was cleared, it can be downloaded from Photos again.

Use **More → Show in Photos** to select the remaining copy in Apple Photos. Before replacement, this command selects the original; **Show converted in Photos** can select an existing compressed Photos copy separately. Open the same library in Photos if navigation cannot find the item.

### What replacement changes

Replacement creates a **new Photos asset**. Dates, location, favorite and hidden flags, and writable regular-album membership are transferred while the original is present. Faces, edit history, captions, keywords, poster frames and smart-album membership may not transfer.

**Done means the local Photos replacement was verified.** It does not confirm completion of iCloud synchronization or immediately available disk space. Deleting an original also affects devices using iCloud Photos. Originals normally remain recoverable in **Recently Deleted for 30 days**; permanent deletion is manual. Review your results before permanently deleting anything. See [Apple’s deletion guide](https://support.apple.com/en-lamr/104967).

## Understand video statuses

Each video has one main status. More detailed download and Photos information is under **More → Video details…**.

| Status | Meaning / next action |
| --- | --- |
| Ready | Available to select for compression. It may still need downloading. |
| Queued | Waiting for download, conversion or an approved replacement. |
| Downloading (%) | Preparing an original or retrieving a compressed Photos copy. A percentage appears when available. |
| Converting (%) | Encoding and verifying the result. The percentage includes several processing stages. |
| Ready to review | A compressed result is available. Play it, then approve replacement if wanted. An older uploaded result may need downloading for playback first. |
| Replacing | Processing an approved import, verification and original removal. |
| Done | Replacement in the local Photos library is recorded as complete. |
| Paused | Conversion is waiting for **Resume**. |
| Cancelled | Work was stopped. Select **Compress** again when you want to retry. |
| Skipped | Unsupported HDR/anamorphic video, or no size reduction. Details gives the reason. |
| Error | Download, conversion or file validation failed. Read Details before retrying. |
| Needs attention | A Photos operation is unresolved, or an original was removed without a known verified replacement. Use **Check Photos status** and inspect the journal/Photos before retrying. |

A heart is a Photos favorite marker, not a processing status. “Downloaded” in Details describes local availability, which can include Photos' own original; it is independent of conversion progress.

## Quality and multiple videos

Open **Settings… → Conversion settings** to choose how videos are converted. Click **Save** to keep changes across launches; **Compress** uses these saved choices:

| Setting | Default | Other choices |
| --- | --- | --- |
| Codec | HEVC | H.264 |
| Resolution | 1080p | 720p, 2160p |
| Quality | Balanced | High, Small |
| Container | MP4 | MOV |

Resolution is a maximum: landscape and portrait aspect ratios are preserved, and smaller inputs are not upscaled. Quality and resolution affect both appearance and size; a smaller result is not guaranteed. The settings in effect when a video is queued are kept for that video.

| Control | Effect |
| --- | --- |
| Compress | Adds more eligible videos while download/conversion work is running. |
| Pause | Finishes the current conversion, then pauses waiting conversions. Downloads continue. |
| Resume | Continues paused conversions. |
| Stop compression | Cancels the active conversion and clears its waiting queue. Existing downloads may finish, but do not automatically start new conversions. |
| Cancel downloads | Stops the active download and waiting downloads; an active conversion can continue. |

Replacement, changing libraries and downloading a compressed Photos copy wait until work is idle. Local compressed playback remains available during conversion. Ordinary download/conversion errors do not stop other successful videos from progressing.

## Convert from the Photos menu

1. Select one or several videos in Apple Photos. Thumbnail and album selection work; full-screen playback is unnecessary.
2. Choose **Photos → Services → Convert video to 1080p**. Some right-click menus also expose **Services**; macOS controls whether it appears there.
3. Video Queue opens and compresses selected videos at up to 1080p using its current codec/quality/container settings.
4. Review the results in Video Queue and approve **Replace in Photos…** separately.

The Service never approves replacement or deletes originals. Still pictures in mixed selections are ignored. Existing results and completed replacements are reused. Service requests wait until the app is idle, and interrupted requests are not automatically replayed.

If the Service is missing, run the installer again and enable it under **System Settings → Keyboard → Keyboard Shortcuts → Services**, then reopen Photos. If selection cannot be resolved, choose the correct System Photo Library in Video Queue's Settings, save it, and try again.

## Storage and closing the app

Working videos, previews and thumbnails are stored under `~/Library/Application Support/Video Queue/`. The app checks available local space before downloads and conversion. Space is needed for originals and temporary outputs even when the aim is to reduce iCloud storage.

Closing the main window or choosing Quit (including Command-Q) shows **Stop and close Video Queue?** when downloads, conversions or queued work are present. Choose **Yes** to cancel queued work, stop active downloads/conversion and close after temporary-file cleanup. Choose **No** to leave the app and its work running. The window shows shutdown progress after confirmation. An approved Photos transaction already in progress is allowed to finish safely before closing.

| File category | Closing behavior |
| --- | --- |
| Temporary originals and compressed copies after confirmed replacement | Cleared. |
| Unreviewed generated results | Ask once before discarding. Cancel keeps the app open; discarding leaves Photos originals unchanged. |
| Recovery files for unresolved Photos actions or missing replacements | Retained; Details explains why and shows their size. |
| External input files and changed/replaced output files | Preserved. |
| Preferences, workflow records, identifiers, logs and journals | Retained. |

Unchanged generated outputs recorded by older versions follow the same confirmation and cleanup policy. After an interrupted exit, stale caches are cleaned on launch while recorded unreviewed results and recovery files remain protected. Cleanup does not remove Photos-managed originals or Photos' own caches.

## Troubleshooting and recovery

| Problem | What to do |
| --- | --- |
| Wrong or unavailable library | Check the saved path in Settings, reconnect its drive and click Refresh. Compression requires the System Photo Library. |
| Catalog access denied | Use Permissions help; allow Full Disk Access for Video Queue.app and reopen it. |
| Download stalls or fails | Check connectivity and full Photos access, read Details, then retry after the queue stops. A download without a provider percentage shows activity instead. |
| Not enough local storage | Free local space, then retry. The original remains in Photos. |
| HDR, anamorphic or no smaller result | Read the skip reason. For size alone, try Small quality or 720p; unsupported HDR is not enabled by changing quality. |
| Result changed or missing | Compress and review again. Changed files are not automatically deleted. |
| Photos helper failed / Needs attention | Use Check Photos status and inspect both versions in Photos. Do not blindly repeat an import. |
| Older osxphotos attribute error or native callback exception | Quit and update from the current repository. Share Details if it persists; check for an existing import before retrying. |
| Show in Photos cannot find the video | Open the matching library in Photos. The asset may also have been removed outside Video Queue. |
| Storage has not decreased | Compression alone does not replace originals. Check replacement, Recently Deleted, retained recovery copies and iCloud synchronization separately. |
| App says it is already open | Use the existing instance, or quit it before starting another. |
| Closing during download/conversion | Choose Yes in Stop and close. It stops work and cleans up; an approved Photos write may finish first. If reporting a shutdown issue, include `launch.log` as well as Details. |

**More → Check Photos status** reads the recorded original and replacement identifiers without importing or deleting. It can resolve a known completed operation. If an interrupted import has no recorded replacement ID, it remains unresolved; inspect Photos and the journal before trying to add the file again.

For support, open **More → Video details… → Copy details**. Include what you selected, what action failed and the displayed status. Review details before sharing: filenames, library paths and identifiers may be personal.

Useful locations:

- Conversion log: `~/Library/Application Support/Video Queue/conversion.log`
- Launcher log: `~/Library/Application Support/Video Queue/launch.log`
- Photos journals: `~/Library/Application Support/Video Queue/Photos work/journals/`

Restore an original through Photos' Recently Deleted, or use a retained local recovery copy when available. Keep recovery files and journals until the unresolved operation has been checked.

## Limits and uninstalling

Current conversion supports SDR video, software encoding and AAC audio. Extra video tracks, subtitles and proprietary telemetry are omitted. Supported capture dates, location and descriptive metadata are checked; retained QuickTime movie/track dates are preserved. Photos chooses the new poster frame.

Live Photos remain picture assets and are not processed by this video workflow. A Photos asset whose duration/dimensions differ from the independent original is refused for replacement. Historical external-file entries can still be played and inspected, but the main workflow is for Photos videos.

To uninstall, remove `~/Applications/Video Queue.app` and `~/Library/Services/Convert video to 1080p.workflow`. Inspect recovery files before removing the Application Support folder. Photos assets, older external output folders and shared Homebrew dependencies remain.

# Video Queue — Compress → Review → Replace

A compact video browser for your Apple Photos library. Compress selected videos, review the smaller copies, then approve replacing the originals to reduce Photos and iCloud storage.

## Install or update

Quit Video Queue and double-click `Install.command`, or run it with `bash` from Terminal. It installs the app in `~/Applications/Video Queue.app` and its Python runtime under `~/Library/Application Support/Video Queue`. Homebrew is required. The installer also registers the **Convert video to 1080p** Photos Service.

This is an unsigned, locally installed macOS app. Library access may require Full Disk Access for Video Queue.app. Downloads and library changes require full Photos access; showing a selected item in Photos and reading the Service selection may require Automation permission. **More → Permissions help…** shows the app location and instructions.

## Everyday workflow

1. **Browse.** Choose your System Photo Library and click **Scan**. The library path is remembered, including when a drive is temporarily unavailable. The thumbnail grid and List show favorites (♥), duration and original size; the largest originals appear first. Search and optional filters narrow the list. Browsing never downloads full originals.
2. **Compress.** Select one or several videos and click **Compress**. The app downloads or copies each original outside Photos, converts it and checks playback, audio, duration and supported metadata. One original can download while another converts. Photos is unchanged during this step.
3. **Review.** Select a result and use **Play original** and **Play compressed**. The app shows the measured reduction, for example `1.2 GB → 180 MB · 85% smaller`. The comparison describes the primary video files, not every cache or derivative in Photos.
4. **Replace in Photos…** Select reviewed results and approve one replacement or a batch. Each smaller copy is imported and its exact Photos identifier verified before the matching original moves to Recently Deleted. Replacement runs sequentially. Photos may also show its own permission/deletion prompt.
5. **Done.** The catalog keeps a single entry for the original/replacement pair. **Play** opens the compressed video, downloading it from Photos again if necessary. **More → Show in Photos** locates the remaining copy.

The main status is **Ready → Queued → Downloading (%) → Converting (%) → Ready to review → Replacing → Done**. **Paused**, **Cancelled**, **Skipped**, **Error** and **Needs attention** explain exceptions. Percentages are shown when the download provider reports them; otherwise an activity indicator is used. **More → Video details…** contains the exact processing stage, original/download availability, Photos presence, identifiers, paths and journal.

A smaller result is required for replacement. HDR/PQ/HLG and anamorphic videos are skipped with a reason. A result that is not smaller is also skipped. Ordinary download/conversion failures leave originals untouched and allow other videos to continue. Select an unsuccessful video and use **Compress** to retry, or change Settings first.

## Quality and multiple videos

The default is **HEVC / 1080p / Balanced / MP4**. Settings also offer H.264, 720p or 2160p, High/Balanced/Small quality, and MOV. Portrait orientation and aspect ratio are preserved; small videos are never upscaled. Settings are captured separately for each queued video.

Add more videos with **Compress** while work is running. Repeated requests for a queued video share its existing download. **Pause** finishes the current conversion and pauses the conversion queue; **Resume** continues it. **Stop compression** cancels the current conversion and clears queued conversions; downloads already requested can finish, but will not automatically start new conversions. **Cancel downloads** stops downloading while an active conversion can finish.

Replacement, switching libraries and downloading a compressed copy from Photos wait until the queues are idle. Local compressed copies can still be played while conversion runs.

## What replacement preserves

Replacement creates a **new Photos asset**. While the original is still present, the app transfers its date, location, favorite and hidden flags, and writable regular-album membership through PhotoKit. Faces, edit history, captions, keywords, poster frames and smart-album membership may not transfer. Embedded metadata is checked separately during conversion.

Approval is tied to the exact reviewed output, its recorded size/modification time, expected video properties and Photos identifiers. A changed output requires another review. Original and imported dimensions/duration are checked before removal. The app never edits Photos database tables or overwrites files inside a `.photoslibrary` bundle.

The complete compression/replacement workflow requires the **System Photo Library**. Another library can be browsed, but compression is refused before downloading or changing its videos. The app does not change which library is System.

Adding to Photos confirms presence in the local library, **not completion of iCloud synchronization**. Removing an original also removes it on devices using iCloud Photos. Originals normally remain recoverable in **Recently Deleted for 30 days**. Permanent deletion is manual; review your results in Photos before doing it. Storage figures can take time to update. See [Apple’s deletion guide](https://support.apple.com/en-lamr/104967).

## Temporary storage and closing

New conversions use app-managed temporary folders, including `Previews`, `Thumbnails`, `Converted previews` and `Workflow/Converted` under the app’s Application Support folder. Free-space checks refuse downloads/encodes that cannot safely fit; disk exhaustion still leaves the Photos original untouched.

Closing waits for active downloads, encodes, Photos actions and thumbnail workers to stop. An in-flight Photos transaction is allowed to finish. Command-Q uses the same path.

- Completed replacement: clear temporary originals and compressed copies.
- Unreviewed results: ask once before closing to discard them. Cancel keeps the app open. Discard leaves Photos originals unchanged.
- Unresolved Photos action or missing replacement: retain local recovery copies. Details explains why and shows their size.
- External input files and changed/replaced outputs: preserve them. Unchanged generated outputs recorded by older versions follow the same confirmation and cleanup policy.

Photos-managed originals and Photos’ own caches are not cleared by app cleanup. Preferences, workflow records, identifiers, logs and small journals remain. After an interrupted exit, stale caches are cleaned on launch, while recorded unreviewed outputs and recovery files are protected.

## Photos Service

Select video thumbnails or an album selection in Apple Photos, then choose **Photos → Services → Convert video to 1080p**. Some Photos context menus also expose **Services**. The Service uses exact asset identifiers, opens Video Queue and enters the same compression flow at 1080p. It **stops at review**; it never approves replacement or deletes originals.

Still pictures in mixed selections are ignored. Existing results and completed replacements are reused rather than imported again. Service requests wait until the app is idle. Interrupted requests and queued replacement approvals are not automatically replayed.

If the Service is missing, enable it under **System Settings → Keyboard → Keyboard Shortcuts → Services**, then reopen Photos. If the selected library cannot be found, choose it in Video Queue, click Scan and try the Service again. Selection works in thumbnail view; full-screen playback is unnecessary.

## Recovery and compatibility

Version 3 workflow records preserve original and replacement identifiers, sizes, processing stages and journals. Existing workflow records are migrated on load. Known uploaded assets remain grouped with their original; an existing uploaded result can be downloaded for review before approving removal of its original. Historical external-file entries remain accessible for playback and Details.

A native journal is flushed before import/removal and records the replacement identifier as soon as Photos provides it. If a Photos helper stops unexpectedly, the video becomes **Needs attention**. Use **More → Check Photos status** to inspect recorded identifiers without importing or deleting anything. A verified completed receipt can recover the final status. An interrupted import with no recorded replacement ID stays unresolved; inspect Photos and the journal instead of blindly importing again. Uncertain Photos writes pause the replacement batch and clear remaining approvals.

Journals: `~/Library/Application Support/Video Queue/Photos work/journals/`.

Log: `~/Library/Application Support/Video Queue/conversion.log`. Use **Video details → Copy details** when reporting a problem. Recover an original through Photos’ Recently Deleted, or use a retained local recovery copy when one is available. Older journals and conversion history remain available in Details.

## Limits

SDR videos only. Software encoding is used; audio is re-encoded to AAC. Extra video tracks, subtitles and proprietary telemetry streams are omitted. Capture dates, location and supported descriptive metadata are checked; QuickTime movie/track dates are preserved for retained tracks. Photos chooses its own poster frame for imported assets. Assets whose Photos duration/dimensions differ from the independently copied original are refused for replacement.

Live Photos remain picture assets and are not compressed through this video workflow. Library sizes exclude some derivatives, edits and companion resources. Permissions, connectivity and Apple’s APIs affect live operations.

The repository includes existing automated checks. They were not run as part of this workflow change, and no live Photos imports, deletions or iCloud transfers were performed.

## Uninstall

Remove `~/Applications/Video Queue.app` and `~/Library/Services/Convert video to 1080p.workflow`. Inspect retained recovery files before removing `~/Library/Application Support/Video Queue`. Photos assets, older external output folders and shared Homebrew dependencies are unaffected.

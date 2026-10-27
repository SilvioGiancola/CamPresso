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

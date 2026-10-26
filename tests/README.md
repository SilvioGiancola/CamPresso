# Existing test suite

These are the existing `unittest` modules, moved from the repository root. Their imports and mock targets use the `video_queue` package. Moving them does not mean they have been run or passed.

Install the development package using the [developer guide](../docs/DEVELOPER_GUIDE.md). From the repository root, manual commands include:

```bash
# Mocked core contracts; does not require a live Photos library.
python -m unittest -v tests.test_actions tests.test_bridge tests.test_catalog tests.test_jobs

# Includes real tool integration and GUI checks; read limitations first.
python -m unittest discover -s tests -t . -v
```

| Modules | Requirements |
| --- | --- |
| `test_actions`, `test_bridge`, `test_catalog`, `test_jobs` | Temporary files and simulated native dependencies. |
| `test_download`, `test_previews` | Simulated Photos downloads; some checks use real FFmpeg/ffprobe. |
| `test_engine`, `test_tracks`, `test_cover` | FFmpeg/ffprobe fixtures and conversion. |
| `test_metadata_integration` | FFmpeg/ffprobe/ExifTool round trips; skipped if tools are absent. |
| `test_gui` | macOS graphical session and TkinterDnD; contains legacy UI expectations. |

## Known limitations

`test_gui.py` still references removed UI members such as `App.folder` and `remove_menu`; it needs a separate migration to the current workflow. Its startup also needs isolation from automatic library discovery/scanning before it is suitable for unattended execution. A separate `App(data=...)` directory does not isolate Photos. Do not treat the current GUI suite as a safe, passing acceptance run.

The GitHub Actions workflow runs only syntax, package-reference, documentation-link and whitespace checks. A passing workflow is not a passing test suite or a verified Photos replacement. Live Photos checks need separate approval and a disposable test System Photo Library.

# CardPrint Architecture

This document describes version `0.1.0` as of 2026-11-26.
Entry points: [README](../README.md), [agent instructions](../AGENTS.md),
[project status](PROJECT_STATUS.md).

## Purpose and stack

Arrange the front and back of a card from photos or PDF pages on one white A4
sheet with explicit physical dimensions. This is a desktop application with no
server or database. Current session edits live in memory.

Python 3.12+, PySide6, PyMuPDF, OpenCV (`opencv-python-headless`), NumPy, Pillow,
ReportLab. `uv` manages the environment and dependencies. The `cardprint` console
entry point calls `cardprint.app:main`. The project uses a `src/` layout and Hatchling.

## Code map

| File | Responsibility |
| --- | --- |
| [app.py](../src/cardprint/app.py) | QApplication, `multiprocessing.freeze_support()`, Russian locale, Fusion style, Wayland selection when its socket is available, and MainWindow startup. |
| [pdf_worker.py](../src/cardprint/pdf_worker.py) | Single spawned PyMuPDF child process, serializable IPC requests/results, request coalescing, crash recovery, and byte-bounded `ByteLRUCache` (32 MiB thumbnails / 128 MiB display pages). No Qt dependency. |
| [imaging.py](../src/cardprint/imaging.py) | Source, import, EXIF, batched PDF discovery via `pdf_worker`, pixel loading, contour detection, rotation, perspective correction, and white rounded corners. No Qt or top-level PyMuPDF dependency. |
| [geometry.py](../src/cardprint/geometry.py) | ViewTransform, Quad, corner validation, CardSize, Layout, Placement, and arrange. Independent of the UI. |
| [model.py](../src/cardprint/model.py) | Pure-Python domain models (`Card`, `SIDE_NAMES`). No Qt dependency. |
| [export.py](../src/cardprint/export.py) | Sheet, ExportSnapshot, OriginalGuard, SourceFingerprint, atomic_write_file, render_sheet, PDF/PNG output, and staged PDF/PNG validation. No Qt dependency. |
| [ui/editor.py](../src/cardprint/ui/editor.py) | QGraphicsView-based ImageView, draggable Handle objects, four-point input, draft selection transactions, zoom, and pan. |
| [ui/window.py](../src/cardprint/ui/window.py) | MainWindow, InspectorDialog, side assignments, settings, task coordination, and export actions. |
| [ui/tasks.py](../src/cardprint/ui/tasks.py) | `QRunnable` `Task` for pure image work and `PdfTaskBridge` for delivering `PdfWorkerClient` results onto the GUI thread. |
| [tests/](../tests/) | Geometry, model, worker, processing, export, and Qt integration tests using artificial data. |
| [scripts/](../scripts/) | Example generation and a GUI smoke test that saves screenshots and exports. |

`Card` and the geometry/layout models are separated from the UI. Do not assume an
additional persistence layer or a general-purpose PDF editing engine exists.

## Data flow

1. `discover(path)` returns `Source(path, page, thumbnail)` objects; `discover_pdf_batches(path)`
   yields 8-page PDF thumbnail batches via `PdfWorkerClient` (without creating a `QApplication`).
   In the GUI, PDFs are inspected first and loaded in batches so thumbnails appear incrementally.
2. Assigning `front` or `back` calls `load_source(source)` (or `RenderPageRequest` via `PdfWorkerClient`
   followed by pure-image `detect_quad` on `QThreadPool`). Photo loading applies EXIF orientation;
   PDF pages are rendered to RGB at 300 DPI inside the spawned PyMuPDF child process and returned
   as `bytes`, yielding a NumPy `uint8` array with `writeable=False`.
3. A `Card` holds immutable `original` pixels, a rotation count `turns`, four
   `points`, physical `size`, and corner `radius`. `detect_quad` proposes an initial
   contour; if detection fails, the entire image is selected.
4. `rectify(original, turns, points)` produces `cropped`, the perspective-corrected
   image before corner rounding. `round_corners` produces `rendered`, with white
   outside the rounded contour. `display` is a separate reduced source image for the editor.
5. `CardSize.resolve(cropped.size)` determines dimensions in millimeters. `arrange`
   creates `Placement` objects and validates A4 bounds and margins. The placements
   and `rendered` images form a `Sheet`.
6. `render_sheet` creates the screen preview at 120 DPI and PNG output at 300 DPI.
   `_write_pdf` uses the same images and placements, retaining the processed card's
   pixel resolution without JPEG recompression; `export_sheet` validates staged PDFs
   in `pdf_worker` (`ValidatePdfRequest`) before publishing.

Public API compatibility: `discover(path, *, worker=None)`, `load_source(source, *, worker=None)`,
and `export_sheet(..., *, pdf_worker=None)` preserve their existing positional signatures while
allowing callers to inject an explicit `PdfWorkerClient` or fall back to the lazy process-wide
worker (`get_default_pdf_worker()`).

Never export a PDF from the reduced `display` image or the rasterized sheet preview.
`Source` references a file; before side assignment, it is not a complete snapshot
of the source document. Once assigned, processing uses the loaded `Card.original`.

## Coordinates and dimensions

| Space | Units and conventions |
| --- | --- |
| Original array | `original[y, x, channel]`, top-left origin. The array is never rotated in place. |
| Selection | Source pixels **after the logical rotation** `turns`. Order: TL → TR → BR → BL, clockwise in screen coordinates. |
| Qt scene | One scene unit equals one rotated source pixel, even when the displayed QPixmap is reduced. |
| Qt viewport | Zoom and pan belong to the view. ViewTransform supplies forward and inverse mappings. |
| Sheet | Millimeters, top-left origin; A4 = 210 × 297 mm. |
| PDF | Points: `mm * 72 / 25.4`, bottom-left origin. Convert Y using `(297 - y_mm - height_mm) * MM_TO_PT`. |
| PNG / preview | Pixels: `mm * dpi / 25.4`; rounding occurs during rasterization. |

Valid image coordinates run from `0 … width-1` and `0 … height-1`.
`quad_size` takes the maximum lengths of opposite edges, rounds, and adds one
pixel: selecting the entire image preserves its dimensions and pixels.
`validate_quad` rejects non-convex, crossing, excessively small, out-of-image,
and reverse-ordered selections. Rejecting reverse order prevents accidental
mirroring of document content.

`rotate_quad` rotates coordinates 90° clockwise and reorders the corners.
`turns` is stored modulo 4. Corner rounding is specified in millimeters; X/Y pixel
radii are calculated separately using the final physical dimensions of the card.

Size modes: `id1` = 85.60 × 53.98 mm; `aspect` = specified width with the aspect
ratio of the perspective-corrected result; `custom` = independent width and height.
Layout templates: `vertical` / `horizontal`. Defaults: 15 mm gap, 10 mm margins,
centered group. Offsets move the entire group, not each side independently.

## Background tasks and result freshness

`MainWindow.submit` sends pure-image `Task` items to a `QThreadPool` with at most two
worker threads. All PyMuPDF calls (inspection, batched thumbnails, 300 DPI page rendering,
and staged PDF validation) are routed via `submit_pdf` / `PdfTaskBridge` to one `spawn`
child process owned by `PdfWorkerClient`, never across concurrent Qt threads. `tasks`
retains active `Task` and `PdfTaskBridge` entries until `finished`. Closing the window
cancels queued non-export PDF work, waits for active tasks to drain, and shuts down
owned `PdfWorkerClient` processes. `QImage` may be prepared in a worker thread; `QPixmap`
and widgets belong to the GUI thread.

Several independent counters, caches, and state fields protect result freshness and memory:

- `ByteLRUCache` bounds PDF thumbnail storage (`32 MiB`) and rendered 300 DPI page cache
  (`128 MiB`), evicting least-recently-used entries automatically.
- `coalesce_key` (`("load_side", side)`) cancels obsolete queued PDF side renders when
  a newer page is chosen or the side is removed; `PdfWorkerCrashError` releases busy
  state cleanly with a Russian explanation and respawns on the next request.

- `Card.revision` / `ready_revision`: accept a result only for the same card and
  current edit. Changing corners or rotation invalidates `cropped`; changes to
  physical size or corner radius may reuse it.
- `display_turns`: prevent corner dragging over an image with the previous rotation
  until the updated display copy is ready.
- `load_generation[side]` and `pending_loads`: prevent a late load from replacing
  a newer assignment or restoring a removed side.
- `layout_generation`: prevent an outdated sheet preview from restoring old sizes.
  Layout changes are debounced using a 160 ms timer.
- `ImageView.is_drafting` / `MainWindow.draft_message`: treat four-point entry and
  handle dragging as draft transactions (`draftChanged` / `draftCancelled`). Starting
  a draft clears `Sheet` and suspends PDF/PNG export until valid completion commits
  once or `Esc` restores the prior committed selection.
- `inspector_request_id`, `is_inspector_fresh`, and `InspectorDialog.mark_stale`:
  reject stale inspector successes and errors across card edits, side replacement or
  removal, and window closing (`_closing` / `_closed`); open inspector dialogs
  immediately mark themselves stale when their card revision or assignment changes.

`invalidate_sheet` clears the ready `Sheet` and disables export. Export becomes
available only after current processing and successful `arrange` validation.
Selection errors persist in `Card.error`; changing the gap does not clear them.
Already-running work is not forcibly interrupted; obsolete results are ignored.

## Save responsibilities

`OriginalGuard` maintains an append-only registry of imported, assigned, and opened
sources (resolved paths, raw paths, case-normalized paths, and `(st_dev, st_ino)` file
identities where available) plus `SourceFingerprint` metadata (size and SHA-256).
`MainWindow` uses its instance `original_guard` while headless callers (`discover`,
`load_source`, `export_sheet`) share `get_default_original_guard()` unless an explicit
guard is supplied. Removing a thumbnail or clearing a side never removes protection.

`atomic_write_file(target, writer_fn, *, overwrite=False, validator_fn=None, original_guard=None)`
serializes writes per destination, checks `original_guard` both before staging and
immediately before publication, stages a temporary file beside `target`, runs
`validator_fn` (for A4 PDFs via `pdf_worker` and 300 DPI PNGs via Pillow) before
commit, and removes the staging file in a `finally` block. Confirmed replacement of a
non-original target uses `os.replace`; publishing a new file uses atomic `os.link`.
Filesystems without hard links fall back to exclusive `xb` creation and copying,
which prevents overwriting existing files but does not make the complete file appear
atomically during copying. An unexpected OS process crash can still leave a
`.cardprint-*.tmp` staging file behind.

`Source` carries a `SourceFingerprint` recorded at discovery. Reopening a source in
`load_source`, `assign_selected`, or `export_sheet` revalidates the file on disk and
blocks processing/export with `SourceChangedError` if it changed or moved until the
user reloads it explicitly («Перезагрузить выбранный файл»). `MainWindow.save`
captures an immutable `ExportSnapshot` (`snapshot_sheet`) and records
`last_exported_revisions` only upon successful completion.

## Extension points

| Change | Main locations and checks |
| --- | --- |
| Sheet template, margins, positioning | geometry.py → UI → geometry tests and exported physical dimensions. Do not introduce separate layout calculations in the UI. |
| Selection tool or zoom | ui/editor.py, geometry.py → real Qt event tests and source-pixel correspondence after zoom/pan/resize. |
| New import format | imaging.py and the UI file-dialog filter → read errors, orientation, original preservation. |
| Image processing | imaging.py → content preservation and recomputation from original; then integrate revision/cache handling. |
| New export format | export.py → physical dimensions, resolution, white background, existing-file preservation; protect originals in the calling interface. |
| Session persistence or Undo/Redo | Not implemented. Requires explicit state modeling; store operation parameters rather than a chain of repeatedly recompressed images. |

Tests by area: `tests/test_geometry.py`, `tests/test_model.py`, `tests/test_pdf_worker.py`,
`tests/test_imaging.py`, `tests/test_export.py`, `tests/test_ui.py`. Fixtures in
`tests/conftest.py` are artificial.

## Environment details

`app.main` prefers Wayland on Linux when `WAYLAND_DISPLAY`, `XDG_RUNTIME_DIR`,
and the socket are available. An explicit `QT_QPA_PLATFORM` always takes precedence.
This supports WSLg environments that advertise X11 but lack required Qt X11 libraries.

`tests/conftest.py` sets `offscreen` only when no backend is explicitly configured.
`scripts/gui_smoke.py` creates QApplication directly and does not run the setup in
`app.main`; set its backend in the command when necessary.

The smoke script creates a temporary two-page PDF, assigns both sides, corrects
the contour using known artificial-card coordinates, captures the editor and
preview, and exports PDF/PNG. It verifies the complete workflow; it does not prove
that automatic contour detection is always accurate.

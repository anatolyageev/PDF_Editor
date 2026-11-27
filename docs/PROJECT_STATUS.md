# Project Status and Handoff

Updated: **2026-11-27**. Version in `pyproject.toml`: **0.1.0**.
This is persistent context for a new chat; understanding the project does not require
access to the original conversation.

## User objective

Build a working local Python desktop application for preparing neat document copies
for printing. Main workflow: open two photos or PDF pages, assign front and back,
select each card using four corners, inspect the result on white A4, and export
with explicit physical dimensions. The application prepares copies; it is not a
general-purpose PDF content editor.

The repository was initially empty. The implementation was built from scratch,
dependencies were installed, and the application was launched. The user then asked
for Markdown handoff documentation so other agents and new chats could continue
development, and requested that agent-facing information be written in English.
The application UI and user guide remain in Russian.

## Implemented

- `src/cardprint` project, `pyproject.toml`, `uv.lock`, and `uv run cardprint` entry point.
- Russian PySide6 UI: sources on the left, editor in the center, settings on the
  right, and a separate A4 preview tab.
- PDF/JPG/JPEG/PNG, EXIF handling, thumbnails for all PDF pages with full loading
  on selection, and clear errors for damaged files and password-protected PDFs.
- Front/back assignment; four draggable manual corners; zoom/pan; preliminary
  OpenCV contour detection; 90° rotation; perspective correction; corner rounding;
  enlarged result inspection; and reset to the original.
- Two A4 templates, ID-1 size, proportional width or custom dimensions, aspect-ratio
  warnings, margins/gap/offset controls, and rejection of layouts outside the margins.
- Shared preview/export geometry; PDF with physical dimensions and no JPEG
  recompression; PNG at 300 DPI; white background; opening saved files; and a
  reminder to print at 100% / actual size.
- Background tasks with an activity indicator, stale-result rejection, protection
  against overwriting originals, and confirmation before replacing other files.
- Automated tests, artificial example generation, and a GUI smoke scenario.

Refinements fixed races between rotation, settings, and background processing;
kept invalid-selection errors visible during layout changes; made PDF page numbers
visible beneath thumbnails; rejected corner ordering that would mirror the document;
and added export support for filesystems without hard links.

### `T-18` (2026-11-27) — Draft selection export blocking & inspector freshness

- Added draft transaction tracking (`draftChanged` / `draftCancelled`, `is_drafting`)
  for four-point entry and corner-handle dragging in `src/cardprint/ui/editor.py`
  and `src/cardprint/ui/window.py`.
- Starting a draft immediately clears the ready `Sheet`, disables PDF/PNG export
  and active-side inspection, and shows progress with the current corner (`1`–`4`).
  Valid completion commits once; `Esc` (and no-op handle clicks) restores the
  previous committed corners and export eligibility without mutating card state.
- Added `InspectorDialog`, `inspector_request_id`, `is_inspector_fresh`, and
  `refresh_inspectors` so stale inspector successes and errors are rejected across
  edits, side replacement/removal, and window closing, and open snapshots mark
  themselves stale immediately when superseded.
- Added deterministic out-of-order UI tests (`ManualTaskController`) in
  `tests/test_ui.py`.
- Next ready tasks in [TASKS.md](TASKS.md): `T-19` (depends on `T-18`) and `T-01`
  (no dependencies).

### `T-19` (2026-11-27) — Isolated PyMuPDF worker process & bounded caches

- Added `src/cardprint/pdf_worker.py` (`PdfWorkerClient`, `_worker_main`,
  `ByteLRUCache`, serializable request/response dataclasses) running all PyMuPDF
  operations inside a single `spawn` child process without importing `pymupdf` in
  the parent process or `PySide6` in the worker module.
- Added byte-bounded LRU caches (`32 MiB` thumbnails, `128 MiB` display pages),
  8-page thumbnail batching (`discover_pdf_batches` + UI `_import_pdf_batched`),
  coalesced side-load rendering, clean worker cancellation/shutdown on window
  close, and crash detection/recovery (`PdfWorkerCrashError`).
- Added staged PDF export validation (`ValidatePdfRequest`) before publishing in
  `src/cardprint/export.py`, `PdfTaskBridge` / `dispatch_pdf_request` in
  `src/cardprint/ui/tasks.py`, and `multiprocessing.freeze_support()` in
  `src/cardprint/app.py` and `scripts/gui_smoke.py` (`spawn`-safe entry points).
- Kept `discover()` and `load_source()` usable without `QApplication`.
- Added deterministic worker and GUI tests in `tests/test_pdf_worker.py` and
  `tests/test_ui.py`.
- Reproducible 24-page A4 synthetic PDF peak memory measurement (3 batches of 8
  thumbnails + 2 pages rendered at 300 DPI, `3509 × 2480 × 3`): parent
  `tracemalloc` peak `101.67 MiB`, child `ru_maxrss` `146.82 MiB`, thumbnail
  cache `974.5 KiB`, display cache `49.80 MiB`.
- Next ready tasks in [TASKS.md](TASKS.md): `T-21` (depends on `T-19`) and
  `T-01` (no dependencies).

### `T-21` (2026-11-27) — Shared safe-save & original/source identity guards

- Added `OriginalGuard`, `SourceFingerprint`, `fingerprint_file`, `ExportSnapshot`,
  `snapshot_sheet`, and `atomic_write_file` in `src/cardprint/export.py` with
  per-destination write serialization, pre-staging and pre-publish original checks
  (including symlink, hardlink/inode, case-normalized, and removed-path protection),
  and staged output validation for both A4 PDF and 300 DPI PNG exports.
- Refactored `export_sheet` to delegate to `atomic_write_file` and added source
  fingerprinting + change detection (`SourceChangedError`) in `src/cardprint/imaging.py`
  (`discover`, `discover_pdf_batches`, `load_source`) and `src/cardprint/ui/window.py`
  (including «Перезагрузить выбранный файл» and `last_exported_revisions`).
- Kept honest documentation of fallback/crash limits: exclusive `xb` copy on
  filesystems without hard links prevents overwriting existing files but is not
  atomic while copying, and an unexpected process crash can leave `.cardprint-*.tmp`.
- Added unit and GUI tests in `tests/test_export.py`, `tests/test_imaging.py`, and
  `tests/test_ui.py`, plus `isolate_default_original_guard` in `tests/conftest.py`.
- Milestone 0 (Reliability: `T-18`, `T-19`, `T-21`) is now complete. Next ready
  task in [TASKS.md](TASKS.md): `T-01` (extract domain models into
  `src/cardprint/model.py`).

### `T-01` (2026-11-27) — Extract domain models into `src/cardprint/model.py`

- Extracted `Card` (preserving all revision, error, display-turn, and
  `Source.fingerprint` fields) and `SIDE_NAMES` from `src/cardprint/ui/window.py`
  into `src/cardprint/model.py` with zero `PySide6` or `pymupdf` imports.
- Updated `src/cardprint/ui/window.py` to import `Card` and `SIDE_NAMES` from
  `..model`.
- Added `tests/test_model.py` covering `Card.source_size` across turns, defaults,
  zero-Qt subprocess import verification, and `window.Card` identity.
- Next ready task in [TASKS.md](TASKS.md): `T-05` (parametric Undo/Redo for
  card, assignment, and layout edits).

### `T-05` (2026-11-27) — Parametric Undo/Redo (`Ctrl+Z` / `Ctrl+Y` / `Ctrl+Shift+Z`)

- Added `CardSnapshot`, `WorkspaceSnapshot`, `HistoryEntry`, `PushResult`, and
  memory-aware `UndoStack` in `src/cardprint/model.py` (capped at 50 steps and
  256 MiB of history-only retained original buffers, counting shared arrays once
  and retaining no derived `cropped`/`rendered`/`display` buffers).
- Wired «Отменить» (`Ctrl+Z`) and «Повторить» (`Ctrl+Y` / `Ctrl+Shift+Z`)
  buttons, shortcuts, descriptive tooltips, and status-bar eviction notices
  in `src/cardprint/ui/window.py` across corners, rotation, card options, resets,
  side assignment/replacement/removal, and layout changes.
- Ensured Undo/Redo issues fresh monotonic card revisions, load generations, and
  inspector request IDs without dropping any original-file protection, and ignores
  cancelled drafts / no-op edits.
- Added unit and deterministic UI tests in `tests/test_model.py` and
  `tests/test_ui.py` covering multi-step undo/redo, shortcuts, redo clearing,
  side replacement/removal, byte-budget eviction disclosure, and delayed pre-undo
  task rejection.
- Next ready task in [TASKS.md](TASKS.md): `T-20` (dirty workspace tracking and
  close/replacement loss guards).

### `T-20` (2026-11-27) — Dirty workspace checkpoints & close guard

- Added `ExportRecord` and `WorkspaceCheckpoints` in `src/cardprint/model.py`
  tracking content-based checkpoints independently for PDF exports, PNG exports,
  and future project saves (`T-16`).
- Integrated dirty window-title indicator (`* …`), close confirmation dialog
  (`_build_unsaved_dialog` / `_prompt_unsaved_changes` offering «Выйти без
  сохранения», «Сохранить PDF» when exportable, and «Отмена»), captured-revision
  acknowledgment on pending export completion, failure/cancel preservation of dirty
  state, and «Открыть ранний экспорт» labelling after subsequent edits in
  `src/cardprint/ui/window.py` (plus explicit non-interactive discard in
  `scripts/gui_smoke.py` and `tests/conftest.py`).
- Added unit and deterministic UI tests in `tests/test_model.py` and
  `tests/test_ui.py` covering cancel, discard, unavailable-export close prompt,
  save cancellation/failure, edits during in-flight export, and undo-to-checkpoint.
- Next ready task in [TASKS.md](TASKS.md): `T-02` (Light/Dark QSS theme system
  and SVG icon helper).

### Review fixes on `optimization_fiture` (2026-11-27, post-`051d9b5`)

Fixed four concurrency, snapshot-consistency, and memory/UI-responsiveness issues
reported against commit `051d9b5` before proceeding to `T-02`:

- **Stale source reload freshness (`src/cardprint/ui/window.py`):** Added per-path
  `_reload_generation` tokens and captured side identity (`card`), `page`, and
  `load_generation[side]` at reload start so stale photo or PDF reloads never overwrite
  newer side assignments or compete with in-flight `pending_loads` (while still updating
  source list thumbnails when fresh and refreshing `OriginalGuard` fingerprints via
  `record_fingerprint`).
- **Pre-dialog `Sheet` snapshot capture (`src/cardprint/ui/window.py`):** `MainWindow.save`
  now captures `ExportSnapshot` (`snapshot_sheet`), `frozen_sheet`,
  `WorkspaceSnapshot`, revisions, card paths, and source fingerprints up front before
  background validation or modal dialogs (`QFileDialog` / overwrite `QMessageBox`),
  preventing `AttributeError` or revision mismatches if background `autodetect` or
  render invalidation clears `self.sheet` while a dialog is open.
- **`UndoStack` memory budget on `undo()` / `redo()` (`src/cardprint/model.py`,
  `src/cardprint/ui/window.py`):** Extracted `_enforce_memory_limit()` and invoked it
  on `push()`, `undo()`, and `redo()` (evicting oldest undo entries first, then
  farthest redo entries if needed while keeping `current` intact) so `history_only_bytes`
  never stays above `max_bytes`; `MainWindow.undo()` and `MainWindow.redo()` disclose
  memory trimming in the status bar.
- **Background SHA-256 hashing & source validation (`src/cardprint/ui/window.py`):**
  Moved synchronous GUI-thread `fingerprint_file` and `OriginalGuard.validate_source`
  calls out of `_import_pdf_batched`, `reload_selected_source`, `assign_selected` / PDF
  `_load_source_for_side`, and pre-dialog `save()` into background `self.submit` tasks
  guarded by `_reload_generation`, `load_generation`, and `_save_generation`.
- **Regression coverage:** Added 1 unit test in `tests/test_model.py` and 4 Qt UI
  tests in `tests/test_ui.py` covering `UndoStack` `max_bytes` on `undo()`/`redo()`,
  stale reload non-overwrite, `save()` during modal-dialog sheet invalidation, and
  zero GUI-thread `fingerprint_file`/`validate_source` execution.

### `T-02` (2026-11-27) — Light/Dark QSS theme system & bundled SVG icons

- Added `src/cardprint/ui/theme.py` (`ThemePalette`, `LIGHT_PALETTE`, `DARK_PALETTE`,
  `build_qpalette`, `build_stylesheet`, `set_widget_property`, `apply_theme`, and
  `QSettings` load/save helpers using `CardPrint Local` / `Карточки на A4`).
- Added `src/cardprint/ui/icons.py` rendering bundled monochrome SVG icons via
  `PySide6.QtSvg.QSvgRenderer` at `1×` and `2×` device pixel ratios with LRU caching
  and live palette tinting (no external icon assets or network calls).
- Updated `src/cardprint/app.py`, `src/cardprint/ui/editor.py`, and
  `src/cardprint/ui/window.py` to apply the persisted theme at startup and live toggle
  via «Тёмная тема» / «Светлая тема» (without marking workspace dirty), assign explicit
  `variant` properties (`primary` «Сохранить PDF» CTA, `secondary`, `ghost`, `danger`),
  show side settings on the Editor tab and A4 layout settings on the Preview tab while
  keeping status/export visible, replace inline color styles with semantic `status`
  properties plus `⚠`/`✕`/`✓` text prefixes, preserve canvas-distinct backgrounds while
  keeping A4 output strictly RGB `(255, 255, 255)`, and preserve keyboard focus and
  accessible names across themes and compact `1280 × 720` geometry.
- Added `isolate_qsettings` in `tests/conftest.py` and 3 UI tests in `tests/test_ui.py`
  covering palette WCAG contrast (`>= 4.5`), SVG icon rendering, theme toggle &
  `QSettings` persistence, Dark-mode pure-white A4 export, button hierarchy, contextual
  panels, accessibility, and `1280 × 720` splitter sizing (`Qt offscreen`).
- Next ready task in [TASKS.md](TASKS.md): `T-03` (drag-and-drop import, visual
  Front/Back slot cards, and source badges).

### `T-03` (2026-11-27) — Drag-and-drop import, Front/Back slot cards & source badges

- Added `partition_dropped_urls`, `SourceListWidget`, `SideSlotCard`, and
  `render_source_item_icon` in `src/cardprint/ui/window.py`, plus matching QSS rules
  (`slot="side"`, `pill="status"`, `role="slotPreview"`, `variant="slotClear"`) in
  `src/cardprint/ui/theme.py`.
- Enabled local `.pdf` / `.jpg` / `.jpeg` / `.png` file drops on `MainWindow`,
  `SourceListWidget`, and `SideSlotCard` (rejecting non-local URLs and unsupported files
  with Russian warnings while validating mixed drops per file), and wired internal
  session-scoped source-ID drag-and-drop (`application/x-cardprint-source-id`) from
  source thumbnails onto Front/Back slots while preserving labelled keyboard buttons.
- Replaced `side_combo` with clickable/keyboard-accessible `front_slot` / `back_slot`
  (`select_side`, `remove_side(side=...)`, quick clear `×`, mini-preview, and live status
  pills: «Не назначена», «⟳ Загрузка…», «✓ Готово», «⚠ Проверьте границы»,
  «✕ Ошибка выделения»), added `Лиц.` / `Обр.` thumbnail badges, distinguished
  automatic-contour review (`_needs_contour_review`) from export readiness, and
  guarded `_side_selection_seq` so late side loads never override a user's newer
  active-side choice.
- Updated `scripts/gui_smoke.py` and `tests/test_ui.py` (`select_side`), and added
  3 new UI tests in `tests/test_ui.py` covering file/mixed/remote drops, internal-ID
  thumbnail drag-and-drop, badges, slot clear/Undo, failure preservation, auto-review
  distinction, and late-load focus preservation (`Qt offscreen`).
- Next ready task in [TASKS.md](TASKS.md): `T-04` (corner editor UX upgrades: mask,
  numbered handles, live error, loupe, shortcuts).

## Recorded implementation verification

The following results were obtained **after completing `T-03` on 2026-11-27**
(Linux, Python **3.14.2**, `spawn` child process, Qt `offscreen`; native Windows,
display-backend screenshots, and 125%/150% HiDPI scaling remain separately unverified):

| Check | Result and scope |
| --- | --- |
| `uv run pytest -q tests/test_ui.py` | **26 passed** (`17.39s`), Qt `offscreen`. |
| `uv run pytest -q` | **76 passed** (`22.15s`), Qt `offscreen` (includes the 3 new `T-03` tests in `tests/test_ui.py`). |
| `uv run ruff check src tests scripts` | All checks passed. |
| `uv run ruff format --check src tests scripts` | 22 files already formatted. |

`gui_smoke.py` was not rerun for `T-03`; its latest recorded run remains from `T-19` on 2026-11-27.

Previous `T-02` verification checks (2026-11-27, commit `d536c85`):

| Check | Result and scope |
| --- | --- |
| `uv run pytest -q tests/test_ui.py` | **23 passed** (`14.19s`), Qt `offscreen`. |
| `uv run pytest -q` | **73 passed** (`19.57s`), Qt `offscreen`. |
| `uv run ruff check src tests scripts` | All checks passed. |
| `uv run ruff format --check src tests scripts` | 22 files already formatted. |

Previous post-`051d9b5` review-fix checks (2026-11-27, commit `9f2fd30`):

| Check | Result and scope |
| --- | --- |
| `uv run pytest -q` | **70 passed** (`18.01s`), Qt `offscreen` (includes the 5 new regression tests in `tests/test_model.py` and `tests/test_ui.py`). |
| `uv run ruff check src tests scripts` | All checks passed. |
| `uv run ruff format --check src tests scripts` | 20 files already formatted. |

Previous `T-20` baseline checks (2026-11-27, commit `051d9b5`):

| Check | Result and scope |
| --- | --- |
| `uv run pytest -q tests/test_model.py tests/test_ui.py` | **24 passed** (`10.67s`), Qt `offscreen`. |
| `uv run pytest -q` | **65 passed** (`15.39s`), including 16 Qt `offscreen` UI tests and 8 `model.py` tests. |
| `uv run ruff check src tests scripts` | All checks passed. |
| `uv run ruff format --check src tests scripts` | 20 files already formatted. |

Historical **2026-11-26** `0.1.0` baseline checks (not rerun for `T-20`):

| Check | Result and scope |
| --- | --- |
| `uv run pytest -q` | **32 passed**, final full run after fixes. Includes Qt offscreen UI tests. |
| `uv run ruff check src tests scripts` | All checks passed. |
| `uv run ruff format --check src tests scripts` | 16 files already formatted. |
| `QT_QPA_PLATFORM=wayland uv run pytest -q tests/test_ui.py` | **3 passed** during GUI verification. Later test additions were included in the full offscreen run above. |
| `QT_QPA_PLATFORM=wayland uv run python scripts/gui_smoke.py` | Passed: two-page PDF → side assignment → corner correction → A4 → PDF and PNG. Window screenshots were visually inspected. |
| `timeout 3s uv run --offline cardprint` | Application started without error output; terminated by timeout (exit code 124), not a normal application exit. |

Verification environment: Linux / WSLg, Python **3.14.2**, Wayland display backend.
Dependencies and code target Python 3.12+. That original iteration did not include
separate Python 3.12, Windows or macOS runs; subsequent review evidence is recorded
below. Native Windows/macOS support must not be described as verified.

Subsequent read-only review on 2026-11-26 also ran the unchanged baseline under
Linux / Python **3.12.14** / Qt **offscreen**: 32 tests passed, Ruff and format
checks passed, and the GUI smoke scenario exported PDF/PNG with screenshots
inspected. This does not verify native Windows/macOS, a real display on that
Python version, or physical printing. These remain historical implementation
checks, not test runs for the 2026-11-27 documentation revision.

An attempted Qt launch using X11 (`xcb`) failed in this environment because
`libxcb-*` and `libxkbcommon-x11` system libraries were missing. Wayland worked,
and normal application startup accounts for its availability. X11 dependency
installation commands are documented in the [README](../README.md).

Tests cover coordinate transforms and actual Qt corner dragging, quadrilateral
validity, perspective, rotations, EXIF, transparency, import errors, A4 and both
card dimensions in a single-page PDF, preservation of embedded image pixels, white
background, PNG at 300 DPI, original preservation, overwrite cancellation, and
staging-file cleanup. Printing on a physical printer has not been tested.

Smoke screenshots and exports live in ignored `artifacts/`; the last visually
inspected set from this session is `artifacts/gui-7xslp90r/`. These files may be
absent in another checkout. The code and reproducible commands are authoritative;
local artifacts are supplementary evidence.

## Limitations and potential follow-up work

These describe the first version's boundaries, **not an approved implementation plan**:

- Automatic detection may include a shadow or surrounding background. Review and
  manual correction are required; shadows inside the selected card are intentionally retained.
- PDFs are rasterized at 300 DPI; vector text is not preserved as vectors.
  Password-protected PDFs are unsupported. Images, rendered pages, and results
  are limited to 80 megapixels.
- An arbitrary quadrilateral's aspect ratio is estimated from its edge lengths;
  the true aspect ratio is not automatically recovered from a perspective photo.
- Parametric Undo/Redo is implemented for CardPrint Mode (50 steps / 256 MiB
  history-only budget); session save/restore, multi-document batch processing,
  and direct printing remain unimplemented. Reset to original and opening
  exported files are available.
- No specialized ICC/CMYK color management. The working representation is RGB.
- Geometry supports `front`/`back` on one A4 sheet. Single-side export is allowed,
  and the number of assigned sides is shown explicitly.
- All PDF thumbnails and active cards remain in memory. There is no streaming
  processing of huge PDFs, cancellation of running tasks, or measured peak-memory profile.
- On filesystems without hard links, the export fallback protects existing files
  but does not publish a new file atomically. A process crash can leave a staging
  file; ordinary errors trigger cleanup.
- No verified installers or standalone executables for end users. The supported
  launch method is currently Python + uv.

At handoff, no required implementation step or mandatory background job remains
unfinished. The next functional task should come from a new user request. Do not
automatically start implementing the limitations listed above.

## Resuming work

1. Read [AGENTS.md](../AGENTS.md) and the [architecture](ARCHITECTURE.md).
2. Run `uv sync --dev`, then `uv run cardprint` if manual inspection is needed.
3. Generate artificial inputs with `uv run python scripts/make_demo.py`.
   Reusing the same output directory does not overwrite files; use a new directory.
4. Find the relevant layer in the architecture table. Change the model/processing
   first, then connect the UI, preserving shared geometry and task freshness checks.
5. Run appropriate targeted tests, then the full suite when changing code.
6. Update this document with the outcome, checks actually performed, and remaining work.

## Design Review — 2026-11-27

Reviewed the newly added roadmap against baseline `38c8969`, the current source,
official PyMuPDF documentation, and the installed/locked PyMuPDF `1.28.2` API.
Only documentation changed; no roadmap feature or baseline fix was implemented.

- Added reliability tasks for draft-selection export/inspector freshness (`T-18`),
  a supported PDF worker process with bounded queues/caches (`T-19`), and shared
  safe-save/original/source guards (`T-21`). The current two-thread pool still
  permits PyMuPDF work in threads; the process boundary is **planned**, and no
  concurrency crash is claimed to have been reproduced.
- Added dirty-workspace/loss guards (`T-20`), memory-aware Undo requirements,
  contextual controls and keyboard/high-DPI criteria. Separated contour detection
  (`T-22`) from direct printing. Existing task IDs remain stable; all 22 are `TODO`.
- Corrected unrotated PDF coordinate contracts, text anchors/fit/font resources,
  rotated-page fixtures, visual masking/signature limitations, forms/page-copy
  preservation and encryption policies. Removed unsupported fixed document sizes
  and claims that printer configuration guarantees physical output scale.
- Clarified staged-save fallback limits, source changes, project-file personal
  data/schema validation, and that a design review must not start implementation.

Review verification: inspected current source/lockfile, queried installed API
signatures/version, and checked Markdown links plus task IDs, statuses and
dependency consistency. Application tests and GUI smoke were **not rerun** for
this documentation-only change. Native Windows and physical printing remain
unverified. See [DESIGN.md](DESIGN.md) §10 for official references and
[TASKS.md](TASKS.md) for the revised order; `T-18` is the first ready
implementation task when implementation is requested.

## Latest documentation changes

2026-11-26: added root `AGENTS.md`, the architecture document, this status document,
and developer navigation in the README. Subsequently translated the agent/developer
documents into English at the user's request and recorded the language convention
in `AGENTS.md`. The user guide remains in Russian. Application code was unchanged;
implementation tests were not rerun for these documentation-only changes.

2026-11-26: added [DESIGN.md](DESIGN.md) (planned `v0.2+` design specification
covering UI/UX upgrades, dual-mode workspace, A4 text/watermarks, and full PDF
Editor mode) and [TASKS.md](TASKS.md) (17 atomic tasks `T-01`–`T-17` with an
execution protocol for one-task-per-run delivery), and cross-linked them from
[AGENTS.md](../AGENTS.md). All planned features remain `TODO`; application code
was unchanged and implementation tests were not rerun for these documentation-only
changes.

# CardPrint Implementation Task Backlog

**Created:** 2026-11-26  
**Reviewed:** 2026-11-27\
**Design Spec:** [DESIGN.md](DESIGN.md)  
**Architecture Baseline:** [ARCHITECTURE.md](ARCHITECTURE.md) (`0.1.0`)

This backlog breaks down the planned v0.2+ design improvements and PDF writing/editing features into independently verifiable tasks. Preserve existing task IDs; the table order expresses priority, not numeric order. M0/M1 are the intended v0.2 scope; later milestones remain planned.

---

## Agent Execution Protocol

1. **One implementation task per run:** When the user requests implementation, work on the specified task ID, or — if no ID was specified — pick the **first `TODO` task** in table order whose dependencies are `DONE`. A review/design/documentation request updates the requested documents only and does not start a `TODO` implementation task. Do not start a second implementation task unless explicitly asked; report unmet dependencies rather than silently implementing them too.
2. **Read before editing:** Read [AGENTS.md](../AGENTS.md), [ARCHITECTURE.md](ARCHITECTURE.md), the relevant section of [DESIGN.md](DESIGN.md), and the existing source/test files listed under `Target files`.
3. **Respect all invariants:**
   - Local/offline only (no cloud, telemetry, OCR, LLM, or generative code).
   - Never overwrite originals; preserve card content except necessary perspective interpolation and explicitly requested contour rounding. Keep the A4 base white; explicit user annotations/crop marks are separate layers, not automatic image enhancement.
   - Keep coordinate spaces distinct; share geometry between preview and export.
   - Keep all UI strings and messages in **Russian**; keep agent docs in **English**.
   - Use artificial test data only.
4. **Stay scoped:** Implement only the acceptance criteria of the active task. Do not refactor unrelated modules or pull in future tasks.
5. **Verify after implementation:** Run targeted tests for the changed module first, then run the full quality gate once after final code changes:
   ```bash
   uv run pytest -q
   uv run ruff check src tests scripts
   uv run ruff format --check src tests scripts
   ```
   Documentation-only changes require link/dependency/consistency checks instead. Distinguish proposed behavior from completed work; do not rerun or claim application tests merely to update prose.
6. **Update documentation before finishing:**
   - For a fully implemented/verified task only, change `TODO` to `DONE` in the table and task section, recording the date and observed checks. Design changes leave tasks `TODO`. Partial work stays `IN_PROGRESS` with an explicit remaining scope; never mark an unverified feature complete.
   - Update [PROJECT_STATUS.md](PROJECT_STATUS.md) with the date, outcome, and checks actually run.
   - Update [ARCHITECTURE.md](ARCHITECTURE.md) if modules, coordinate systems, or data flow changed.
   - Update [README.md](../README.md) if the user workflow, shortcuts, or supported features changed.
7. **Branching & push workflow:** Commit and push all completed task changes to the integration branch `optimization_fiture` (keeping `main` untouched). Do not merge or push to `main` until the user has tested `optimization_fiture` and explicitly asks to merge into `main`.
8. **Stop cleanly:** Summarize what was implemented, which tests/checks passed, and which task is next in the queue.

---

## Master Progress Table

| ID | Milestone | Title | Status | Depends On |
| --- | --- | --- | --- | --- |
| `T-18` | M0 · Reliability | Block export during draft selection and reject stale inspector results | `DONE` | — |
| `T-19` | M0 · Reliability | Isolate PyMuPDF in a process and bound PDF scheduling/caches | `DONE` | `T-18` |
| `T-21` | M0 · Reliability | Extract shared safe-save and original/source identity guards | `DONE` | `T-19` |
| `T-01` | M1 · Foundation & UX | Extract domain models from `ui/window.py` into `src/cardprint/model.py` | `DONE` | — |
| `T-05` | M1 · Foundation & UX | Add parametric Undo/Redo for card, assignment and layout edits | `DONE` | `T-01`, `T-18` |
| `T-20` | M1 · Foundation & UX | Track dirty workspaces and guard close/replacement against lost edits | `DONE` | `T-05` |
| `T-02` | M1 · Foundation & UX | Add Light/Dark QSS theme system (`ui/theme.py`) and SVG icon helper (`ui/icons.py`) | `DONE` | `T-01`, `T-20` |
| `T-03` | M1 · Foundation & UX | Add drag-and-drop import, visual Front/Back slot cards, and source badges | `DONE` | `T-02` |
| `T-04` | M1 · Foundation & UX | Upgrade corner editor UX (mask, numbered handles, live error, loupe, shortcuts) | `DONE` | `T-02`, `T-18`, `T-19` |
| `T-06` | M1 · Foundation & UX | Upgrade A4 preview (mm rulers, margin guides, crop marks, drag-to-offset) | `DONE` | `T-02`, `T-05` |
| `T-07` | M2 · Text on A4 | Add Cyrillic TTF font resolver (`fonts.py`), `TextAnnotation`, and shared text export | `DONE` | `T-01`, `T-19` |
| `T-08` | M2 · Text on A4 | Add interactive A4 text & watermark editing UI with presets and Undo/Redo | `DONE` | `T-05`, `T-06`, `T-07` |
| `T-09` | M3 · PDF Editor Mode | Add dual-mode workspace shell using shared save guards | `TODO` | `T-02`, `T-20`, `T-21` |
| `T-10` | M3 · PDF Editor Mode | Implement PDF Editor core engine (`pdf_editor.py`) for vector Cyrillic text | `TODO` | `T-07`, `T-09`, `T-19` |
| `T-11` | M3 · PDF Editor Mode | Implement PDF Editor UI (`ui/pdf_view.py`) for viewing, writing text, and saving PDFs | `TODO` | `T-10`, `T-05`, `T-20` |
| `T-12` | M3 · PDF Editor Mode | Add PDF whiteout boxes, quick stamps (`✓`, `✕`, date), and ink/PNG signatures | `TODO` | `T-11` |
| `T-13` | M3 · PDF Editor Mode | Add interactive PDF form (`AcroForm`) field detection, editing, and export | `TODO` | `T-11` |
| `T-14` | M3 · PDF Editor Mode | Add in-memory PDF password unlock and non-destructive page operations | `TODO` | `T-11`, `T-12`, `T-13` |
| `T-16` | M4 · Productivity | Add `.cardprint` project save/load preserving parameters and annotations | `TODO` | `T-08`, `T-21` |
| `T-15` | M4 · Productivity | Add verified/user size presets and multi-card / multi-page A4 layout support | `TODO` | `T-06`, `T-16` |
| `T-17` | M4 · Productivity | Add direct printing with explicit scale and printable-area handling | `TODO` | `T-06`, `T-11` |
| `T-22` | M4 · Productivity | Improve contour detection with an artificial benchmark | `TODO` | `T-19` |

---

## Milestone 0 — Correctness Before Feature Expansion

### `T-18`: Block export during draft selection and reject stale inspector results

- **Status:** `DONE` (2026-11-27)
- **Depends on:** None
- **Target files:** `src/cardprint/ui/editor.py`, `src/cardprint/ui/window.py`, `tests/test_ui.py`
- **Goal:** Correct baseline state ambiguities before changing the editor appearance.
- **Acceptance criteria:**
  - Starting four-point input or a handle drag immediately suspends PDF/PNG export; progress text identifies the draft operation and next corner. Export cannot silently use the previous crop while a new selection is incomplete.
  - Valid completion commits once; `Esc` restores the previous committed selection and export eligibility. Invalid completion retains a persistent explanation and blocks export until corrected.
  - Inspector requests check card identity, revision and window/session lifetime before opening; stale successes/errors cannot open obsolete results. Already-open snapshots clearly indicate if they became stale.
  - Deterministic UI tests use controlled completion order for partial entry, drag, Escape, invalid selection, replacement/removal, inspector and close; do not rely on sleep-based races.
- **Verification:** `uv run pytest -q tests/test_ui.py` (7 passed, `offscreen`), `uv run pytest -q` (36 passed, `offscreen`), `uv run ruff check src tests scripts` (passed), `uv run ruff format --check src tests scripts` (16 files formatted).

### `T-19`: Isolate PyMuPDF in a process and bound PDF scheduling/caches

- **Status:** `DONE` (2026-11-27)
- **Depends on:** `T-18`
- **Target files:** new `src/cardprint/pdf_worker.py`, `src/cardprint/imaging.py`, `src/cardprint/ui/tasks.py`, `src/cardprint/ui/window.py`, `src/cardprint/app.py`, `src/cardprint/export.py`, `scripts/gui_smoke.py`, relevant imaging/UI tests and worker tests
- **Goal:** Follow the supported PyMuPDF process model for existing imports and future PDF editing, without blocking Qt.
- **Acceptance criteria:**
  - Route all application PyMuPDF calls, including thumbnails, source rendering and font/PDF inspection, through one spawned child process initially. Never use concurrent Qt threads or share live PyMuPDF objects across execution boundaries. Pure image operations may still use `QThreadPool`.
  - Define serializable requests/results and monotonic session/page/revision tokens. Coalesce queued page renders, ignore stale errors/successes, and cancel queued work on close/replacement. Worker failure releases busy state with a Russian explanation; exports stage/validate before publishing.
  - Batch thumbnails and apply byte-bounded LRU caches (initial 32 MiB thumbnails / 128 MiB display pages), along with `MAX_PIXELS` allocation limits. Test eviction/queue behavior deterministically rather than asserting an exact OS RSS value.
  - Keep core image functions usable without creating a QApplication. Worker imports never start UI/processes recursively; module-level entry points work with `spawn`. Public API compatibility and intentional changes are documented.
  - Test actual child-process startup/shutdown, overlapping import/render requests, source replacement, stale results, crash recovery, and close during work using artificial PDFs. Record measured peak memory for a reproducible multi-page input. Report native Windows verification separately; Linux `spawn` alone does not establish it.
- **Verification:** `uv run pytest -q` (45 passed, Linux `spawn`, Qt `offscreen`), `uv run ruff check src tests scripts` (passed), `uv run ruff format --check src tests scripts` (18 files formatted), `QT_QPA_PLATFORM=offscreen uv run python scripts/gui_smoke.py` (passed), plus 24-page A4 synthetic PDF memory check (`parent_tracemalloc_peak = 101.67 MiB`, `child_ru_maxrss = 146.82 MiB`). Native Windows `spawn` verification remains separate/unverified.

### `T-21`: Extract shared safe-save and original/source identity guards

- **Status:** `DONE` (2026-11-27)
- **Depends on:** `T-19`
- **Target files:** `src/cardprint/export.py`, `src/cardprint/ui/window.py`, `src/cardprint/imaging.py`, `tests/conftest.py`, `tests/test_export.py`, `tests/test_imaging.py`, `tests/test_ui.py`
- **Goal:** Reuse safe output publication and original protection before adding another exporter or project saves.
- **Acceptance criteria:**
  - Expose `atomic_write_file(target, writer_fn, *, overwrite=False)` with staging cleanup, atomic replace/link paths and the explicitly non-atomic exclusive-copy fallback. `export_sheet` delegates without losing existing behavior.
  - Provide a shared original/source registry and guard; all application save entry points, including non-UI exporters, check it before staging and publication. Removed/replaced/merged sources remain protected. GUI confirmation grants replacement of the selected non-original target only.
  - Track/revalidate source identities/fingerprints when reopening source content. Changed sources block processing/export until explicit reload/relink; capture an immutable export revision and serialize writes per destination.
  - Validate staged outputs before publication. Failure/cancel does not clear dirty state, change an existing target, or leave ordinary-error staging/partial output. Document the exclusive-copy and process-crash limitations honestly.
  - Cover imported-path aliases/samefile where supported, removed originals, late target collisions, replacement refusal, writer/validation failure, fallback-copy failure and successful PDF/PNG output. Filesystem alias tests may be platform-specific with explicit skips.
- **Verification:** `uv run pytest -q tests/test_export.py tests/test_imaging.py tests/test_ui.py` (30 passed, `offscreen`), `uv run pytest -q` (53 passed, `offscreen`), `uv run ruff check src tests scripts` (passed), `uv run ruff format --check src tests scripts` (18 files formatted).

---

## Milestone 1 — Design System, Domain Refactor & Editor UX

### `T-01`: Extract domain models from `ui/window.py` into `src/cardprint/model.py`

- **Status:** `DONE` (2026-11-27)
- **Depends on:** None
- **Goal:** Separate `Card` (and future pure-Python session/state helpers) from Qt widgets in [window.py](../src/cardprint/ui/window.py) so domain logic is testable without Qt and ready for Undo/Redo and dual-mode state.
- **Target files:**
  - Create `src/cardprint/model.py`
  - Modify `src/cardprint/ui/window.py`
  - Create `tests/test_model.py`
- **Acceptance criteria:**
  - `Card` dataclass and `source_size` property live in `src/cardprint/model.py` with zero Qt imports (`PySide6` must not be imported in `model.py`).
  - `Card` preserves all current fields, revision counters and defaults, including reliability changes completed before this extraction; the refactor must not revert them to the historical `0.1.0` shape.
  - `ui/window.py` imports `Card` from `..model` and behaves identically to `0.1.0`.
  - Unit tests verify `Card.source_size` across all `turns % 4` values and ensure `model.py` imports without `PySide6`.
- **Verification:** `uv run pytest -q tests/test_model.py tests/test_ui.py` (16 passed, `offscreen`), `uv run pytest -q` (57 passed, `offscreen`), `uv run ruff check src tests scripts` (passed), `uv run ruff format --check src tests scripts` (20 files formatted).

---

### `T-02`: Add Light/Dark QSS theme system (`ui/theme.py`) and SVG icon helper (`ui/icons.py`)

- **Status:** `DONE` (2026-11-27)
- **Depends on:** `T-01`, `T-20`
- **Goal:** Replace raw unstyled `Fusion` controls with a clean Light/Dark design system, semantic button hierarchy, card-style control panels, and bundled inline SVG icons.
- **Target files:**
  - Create `src/cardprint/ui/theme.py`
  - Create `src/cardprint/ui/icons.py`
  - Modify `src/cardprint/app.py`
  - Modify `src/cardprint/ui/window.py`
  - Modify `tests/test_ui.py`
- **Acceptance criteria:**
  - `ThemePalette` defines cohesive Light and Dark palettes and generates a complete QSS stylesheet applied at startup and on live theme toggle.
  - Top toolbar has a Russian theme toggle button («Тёмная тема» / «Светлая тема») persisted in `QSettings`.
  - Primary CTA («Сохранить PDF»), secondary («PNG · 300 DPI», «Добавить файлы»), ghost, and destructive buttons have distinct visual styling via Qt properties (`variant`).
  - `ui/icons.py` renders bundled inline SVG icons tinted to the active palette without external image files or network calls.
  - Contrast and readability remain clear in both themes; exported/previewed A4 white background remains strictly RGB `(255, 255, 255)`.
  - Side settings appear in the editor and sheet settings in the A4 tab, with one clear primary save action. Keyboard navigation, accessible names and visible focus survive both themes; status does not rely on color alone. Check compact layouts at 1280 × 720; record high-DPI/native Windows checks separately.
- **Verification:** `uv run pytest -q tests/test_ui.py` (23 passed, `offscreen`), `uv run pytest -q` (73 passed, `offscreen`), `uv run ruff check src tests scripts` (passed), `uv run ruff format --check src tests scripts` (22 files formatted). Native Windows and 125%/150% HiDPI scaling remain unverified.

---

### `T-03`: Add drag-and-drop import, visual Front/Back slot cards, and source badges

- **Status:** `DONE` (2026-11-27)
- **Depends on:** `T-02`
- **Goal:** Make document loading and side assignment effortless via drag-and-drop, visual side slot cards with live status, and assignment badges on source thumbnails.
- **Target files:**
  - Modify `src/cardprint/ui/window.py`
  - Modify `src/cardprint/ui/theme.py`
  - Modify `tests/test_ui.py`
- **Acceptance criteria:**
  - Dropping `.pdf`, `.jpg`, `.jpeg`, `.png` files onto `MainWindow` or the left source list imports them through `import_paths`; unsupported drops show a clear Russian warning.
  - Replace the plain `side_combo` dropdown above the editor with two clickable side slot cards («Лицевая сторона», «Обратная сторона») showing mini-preview, status pill («Готово», «Загрузка…», «Ошибка», «Не назначена»), and quick clear button (`×`).
  - Dragging a source thumbnail from the left list onto either side slot card assigns that side.
  - Assigned items in the left `QListWidget` display `Лиц.` / `Обр.` badges.
  - Keep existing test accessors/behavior compatible or update `tests/test_ui.py` cleanly for the slot widgets.
  - Keep labelled assignment/import buttons as keyboard alternatives. Accept local URLs only; validate mixed drops per file and use internal source IDs for thumbnail drags.
  - Replacement is undoable; cancel/failure preserves the prior card. A late load cannot change the user's newer active-side choice. Clearly distinguish automatic-contour review from readiness to export.
- **Verification:** `uv run pytest -q tests/test_ui.py` (26 passed, `offscreen`), `uv run pytest -q` (76 passed, `offscreen`), `uv run ruff check src tests scripts` (passed), `uv run ruff format --check src tests scripts` (22 files formatted).

---

### `T-04`: Upgrade corner editor UX (mask, numbered handles, live error, loupe, shortcuts)

- **Status:** `DONE` (2026-11-27)
- **Depends on:** `T-02`, `T-18`, `T-19`
- **Goal:** Improve corner-selection speed and precision in `ImageView` without altering source pixels or coordinate math.
- **Target files:**
  - Modify `src/cardprint/ui/editor.py`
  - Modify `src/cardprint/ui/window.py`
  - Modify `tests/test_ui.py`
- **Acceptance criteria:**
  - Semi-transparent dark dimming mask covers the area outside the selected quadrilateral in the Qt scene only.
  - Corner handles display numbers `1`–`4` (`1 ↖`, `2 ↗`, `3 ↘`, `4 ↙`); moving handles into a non-convex, self-intersecting, reversed, or tiny quad immediately highlights the polygon outline in red during drag.
  - Dragging a corner handle displays a floating `3×`–`4×` magnified loupe with crosshair in a non-obstructing corner of the viewport.
  - Clicking/focusing a handle lets arrow keys nudge it by `1` source pixel (`Shift + Arrow` by `10` px), clamped to valid image bounds, emitting `selectionChanged`.
  - Shortcuts work cleanly: `Ctrl+O` (добавить файлы), `Ctrl+S` (сохранить PDF), `R` (повернуть на 90°), `0` (вписать изображение), `Esc` (отмена ввода 4 точек).
  - The loupe samples original rotated pixels through a bounded region request; crosshair/handle coordinates agree after zoom, pan and device scaling. Do not magnify only the reduced editor bitmap.
  - Letter/number/nudge shortcuts are scoped to the canvas and do not intercept text, spinbox or form editing. Preserve `T-18` draft/export guards and commit one edit per gesture, not a full-resolution render per mouse move.
- **Verification:**
  - `uv run pytest -q tests/test_ui.py tests/test_geometry.py`: **53 passed** (`offscreen`); focused follow-up for the final drag/nudge assertions: **2 passed**.
  - `uv run pytest -q`: **86 passed**, one `BytesIO` finalization warning (see [PROJECT_STATUS.md](PROJECT_STATUS.md)); Ruff check passed; format check: **22 files already formatted**.
  - Coordinate/loupe/nudge tests with `QT_SCALE_FACTOR=1.25` and `1.5`: **6 passed each**, Qt `offscreen`. Artificial editor screenshot visually inspected. Native display/Windows scaling remains unverified.

---

### `T-05`: Add parametric Undo/Redo for card, assignment and layout edits

- **Status:** `DONE` (2026-11-27)
- **Depends on:** `T-01`, `T-18`
- **Goal:** Provide reliable non-destructive Undo/Redo using lightweight parameter snapshots (never duplicating original pixel arrays).
- **Target files:**
  - Modify `src/cardprint/model.py`
  - Modify `src/cardprint/ui/window.py`
  - Modify `tests/test_model.py`
  - Modify `tests/test_ui.py`
- **Acceptance criteria:**
  - `UndoStack` in `model.py` stores bounded (max 50) immutable parameter snapshots (`turns`, `points`, `radius`, `CardSize`, `Layout`) referencing the existing immutable `original` arrays without copying pixels.
  - Changing corners, rotating, changing size/radius/layout, resetting, assigning/replacing/removing a side pushes an undoable step; continuous drags/spinbox edits coalesce on commit. Cancel/no-op adds nothing; a new edit clears redo.
  - Toolbar buttons «Отменить» (`Ctrl+Z`) and «Повторить» (`Ctrl+Y` / `Ctrl+Shift+Z`) accurately reflect stack availability and restore state + re-render cards and sheet.
  - Unit and UI tests verify multi-step undo/redo across corner edits, rotation, and layout changes.
  - Enforce both 50 entries and a 256 MiB initial budget for history-only retained originals/assets, counting shared buffers once; do not retain derived rendered buffers. Show when older history is discarded.
  - Undo/Redo issues fresh monotonic generations, never restores stale task counters, and never removes paths from original-file protection. Test side replacement/removal, history eviction, redo clearing, and delayed old task completion after Undo.
- **Verification:** `uv run pytest -q tests/test_model.py tests/test_ui.py` (21 passed, `offscreen`), `uv run pytest -q` (62 passed, `offscreen`), `uv run ruff check src tests scripts` (passed), `uv run ruff format --check src tests scripts` (20 files formatted).

---

### `T-20`: Track dirty workspaces and guard close/replacement against lost edits

- **Status:** `DONE` (2026-11-27)
- **Depends on:** `T-05`
- **Target files:** `src/cardprint/model.py`, `src/cardprint/ui/window.py`, `scripts/gui_smoke.py`, `tests/conftest.py`, `tests/test_model.py`, `tests/test_ui.py`
- **Goal:** Preserve edits through normal navigation and make intentional discard explicit.
- **Acceptance criteria:**
  - Track content-based dirty checkpoints, independent last-exported PDF/PNG revisions and later project-save checkpoints. Zoom/theme/focus do not mark dirty; Undo back to a checkpoint clears the relevant dirty state.
  - Close/new/open that would discard work offers Russian cancel/discard and save/export when available. A cancelled/failed save keeps work and dirty state; pending export completion only acknowledges its captured revision.
  - Side replacement/removal/reset uses Undo instead of repeated confirmation prompts. Mode switching later preserves each workspace rather than clearing it.
  - After newer edits, label Open-last-export as an earlier result. Do not describe a PDF/PNG export as saving the editable project.
  - Tests cover cancel, discard, save failure, edits during export and undo-to-checkpoint. Do not invent a project-save button before `T-16` exists.
- **Verification:** `uv run pytest -q tests/test_model.py tests/test_ui.py` (24 passed, `offscreen`), `uv run pytest -q` (65 passed, `offscreen`), `uv run ruff check src tests scripts` (passed), `uv run ruff format --check src tests scripts` (20 files formatted).

---

### `T-06`: Upgrade A4 preview (mm rulers, margin guides, crop marks, drag-to-offset)

- **Status:** `DONE` (2026-11-27)
- **Depends on:** `T-02`, `T-05`
- **Goal:** Turn the A4 preview into a precise print-layout surface with guides, direct mouse positioning, and optional crop marks rendered identically in preview and exports.
- **Target files:**
  - Modify `src/cardprint/geometry.py`
  - Modify `src/cardprint/export.py`
  - Modify `src/cardprint/ui/editor.py`
  - Modify `src/cardprint/ui/window.py`
  - Modify `tests/test_geometry.py`
  - Modify `tests/test_export.py`
  - Modify `tests/test_ui.py`
- **Acceptance criteria:**
  - `Layout` gains `crop_marks: bool = False`; shared millimeter segments with explicit 0.15 mm stroke width drive preview/PNG and vector PDF. Omit unsafe segments with an explanation rather than painting on either card or moving/resizing it. Test margins, small gaps, asymmetric sizes and page-edge clipping.
  - A4 preview tab offers toggleable millimeter rulers and dashed printable margin guides (non-exporting visual guides only).
  - Dragging the card group with the mouse on the A4 preview updates `offset_x_mm` and `offset_y_mm` (clamped or validated identically by `arrange`), snaps gently to `0.0 mm` center axes, and records one Undo step on release.
  - Tests verify crop marks presence in exported PNG/PDF when enabled and absence when disabled, plus mouse drag offset synchronization.
- **Verification:**
  - `uv run pytest -q tests/test_geometry.py tests/test_export.py tests/test_ui.py`: **75 passed** (`offscreen`).
  - `uv run pytest -q`: **99 passed**, one previously observed `BytesIO` finalization warning (see [PROJECT_STATUS.md](PROJECT_STATUS.md)); Ruff check passed; format check: **22 files already formatted**.
  - A4 drag/guide tests at `QT_SCALE_FACTOR=1.25` and `1.5`: **3 passed each**, Qt `offscreen`. Light/Dark artificial A4 previews at 1280 × 720 visually inspected. Native display/Windows scaling and physical printing remain unverified.

---

## Milestone 2 — Writing Text & Watermarks on A4 Sheet (Mode 1)

### `T-07`: Add Cyrillic TTF font resolver (`fonts.py`), `TextAnnotation`, and shared text export

- **Status:** `DONE` (2026-11-27)
- **Depends on:** `T-01`, `T-19`
- **Goal:** Build cross-platform Cyrillic TrueType font discovery and shared millimeter-accurate text rendering for A4 PDF and PNG/preview output.
- **Target files:**
  - Create `src/cardprint/fonts.py`
  - Modify `src/cardprint/geometry.py`
  - Modify `src/cardprint/export.py`
  - Create `tests/test_fonts.py`
  - Modify `tests/test_export.py`
- **Acceptance criteria:**
  - `src/cardprint/fonts.py` resolves packaged Cyrillic TTF resources for logical families (`sans`, `sans-bold`, `serif`, `mono`), verifies glyph support and registers ReportLab `TTFont`s cleanly (no Qt dependency).
  - Bundle redistributable font files/licenses for every exposed family and load them as package resources, with exact same bytes across backends. Check actual input glyphs, Ukrainian letters and stamp symbols; missing glyphs fail clearly. Keep PyMuPDF font operations in the PDF process and registration/caches safe under concurrent image-only exports.
  - `TextAnnotation` dataclass in `geometry.py` models `id`, `text`, `x_mm`, `y_mm`, `font_family`, `font_size_pt`, `color_hex`, `angle_deg`, `opacity`, `align`, `is_watermark`.
  - `Sheet` accepts `annotations: tuple[TextAnnotation, ...] = ()`.
  - `_write_pdf` renders annotations as true selectable vector Cyrillic text (with rotation and alpha) and `render_sheet` renders matching Pillow text at identical mm coordinates.
  - Tests verify Cyrillic text extraction from exported PDF via PyMuPDF (`page.get_text()`) and visual pixel presence in exported PNG (including rotated/semi-transparent watermarks).
  - Define a shared `TextLayout` with first-line baseline anchoring, explicit line advances, alignment, clockwise angle convention, stacking and rotated ink bounds as in DESIGN §6. Qt/Pillow/PDF consume the same layout; do not claim equivalent default backend metrics. Reject nonfinite values, unsupported glyphs and out-of-sheet text without clipping/shrinking.
  - Compare PDF renders against PNG/preview text bounds (target ≤0.2 mm at 300 DPI, allowing antialiasing) for multiline/rotated/transparent text. Verify font resources in an installed wheel outside the checkout; no dependency on system-installed fonts.
- **Verification:** `uv run pytest -q tests/test_fonts.py tests/test_export.py` (18 passed), `uv run pytest -q` (106 passed, 1 pre-existing `BytesIO` unraisable warning, `offscreen`), `uv run ruff check src tests scripts` (passed), `uv run ruff format --check src tests scripts` (24 files formatted).

---

### `T-08`: Add interactive A4 text & watermark editing UI with presets and Undo/Redo

- **Status:** `DONE` (2026-11-27)
- **Depends on:** `T-05`, `T-06`, `T-07`
- **Goal:** Allow users to add, position, rotate, style, and remove Russian captions or semi-transparent watermarks directly on the A4 preview tab.
- **Target files:**
  - Modify `src/cardprint/model.py`
  - Modify `src/cardprint/ui/editor.py`
  - Modify `src/cardprint/ui/window.py`
  - Modify `tests/test_ui.py`
- **Acceptance criteria:**
  - A4 preview toolbar includes «Добавить текст» (click sheet to place block) and «Водяной знак» (one-click preset such as «Копия верна» / «Только для предъявления…» with current date).
  - Text annotations appear interactively on the A4 preview (draggable, selectable, double-click or panel editable, deletable via `Delete`).
  - Right-panel inspector edits text content, font family, size (pt), color, rotation angle, and opacity, updating preview and Undo/Redo cleanly.
  - Exported PDF and PNG match the interactive preview positions and include all annotations.
  - Authoritative text appearance comes from `TextLayout`; inline editors/handles do not add duplicate exported text. Date presets capture the insertion date once. Bounds errors block export, and typing shortcuts retain native text behavior.
- **Verification:**
  - `uv run pytest -q tests/test_ui.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

## Milestone 3 — Full PDF Editor Mode (Write, Fill & Sign Arbitrary PDFs)

### `T-09`: Add dual-mode workspace shell using shared save guards

- **Status:** `TODO`
- **Depends on:** `T-02`, `T-20`, `T-21`
- **Goal:** Prepare `MainWindow` for independent CardPrint and PDF workspaces, reusing the save policy already provided by `T-21`.
- **Target files:**
  - Modify `src/cardprint/ui/window.py`
  - Modify `tests/test_ui.py`
- **Acceptance criteria:**
  - `MainWindow` adds a segmented switcher backed by `QStackedWidget`, preserving Mode 1. Until `T-11`, Mode 2 is a clearly labelled disabled development placeholder, never presented as a working editor.
  - Workspace state, selection, zoom and Undo stacks survive switching; actions/shortcuts route only to the active workspace. Reuse `T-20` dirty/lifetime guards and `T-21` original protection; do not create a second save implementation.
- **Verification:**
  - `uv run pytest -q tests/test_export.py tests/test_ui.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

### `T-10`: Implement PDF Editor core engine (`pdf_editor.py`) for vector Cyrillic text

- **Status:** `TODO`
- **Depends on:** `T-07`, `T-09`, `T-19`
- **Goal:** Create the pure-Python (no Qt) PDF editing session engine using PyMuPDF to load multi-page PDFs, render pages lazily, and export vector Cyrillic text overlays cleanly to a new file.
- **Target files:**
  - Create `src/cardprint/pdf_editor.py`
  - Create `tests/test_pdf_editor.py`
- **Acceptance criteria:**
  - Pure session/overlay models have no Qt dependency. Give sources and page instances stable IDs; store overlays in **unrotated PyMuPDF points**, separate from rotated `page.rect` display dimensions. Implement tested CropBox/rotation/pixmap/viewport adapters; rotating a page never discards overlays.
  - Route engine operations through `T-19`; no live PyMuPDF object enters the GUI/thread pool. Lazy rendering respects pixel/byte budgets and returns clear Russian errors on missing/damaged/encrypted inputs until `T-14` unlock exists.
  - `export_edited_pdf(session, target, *, overwrite=False)` uses the shared original/source guard and safe-save helper, embeds tested fonts and explicitly subsets where supported. Overlay-only export preserves tested source links/widgets/annotations/outlines/attachments without wholesale page reassembly; use DESIGN §7 preservation limits.
  - Reject text overflow/missing glyphs/nonfinite bounds without clipping, dropping or auto-shrinking text. Validate insertion return values and the staged PDF before publication. Detect signed/unsupported structures and expose applicable limitations before editing/export.
  - Tests verify opening an artificial multi-page vector PDF, adding Cyrillic text overlays, exporting to a new PDF, verifying original file unchanged, and confirming both original and added Cyrillic text via PyMuPDF `page.get_text()`.
  - Add rotated (0/90/180/270), nonzero-CropBox/MediaBox-origin, overflow, stale-render, source-changed and preservation fixtures. Test the locked PyMuPDF APIs instead of assuming current online documentation matches the installed version.
- **Verification:**
  - `uv run pytest -q tests/test_pdf_editor.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

### `T-11`: Implement PDF Editor UI (`ui/pdf_view.py`) for viewing, writing text, and saving PDFs

- **Status:** `TODO`
- **Depends on:** `T-10`, `T-05`, `T-20`
- **Goal:** Build the interactive «Редактор PDF» UI for supported readable PDFs, page browsing, Cyrillic text overlays, positioning/styling and export to a new vector PDF under DESIGN §7's explicit limits.
- **Target files:**
  - Create `src/cardprint/ui/pdf_view.py`
  - Modify `src/cardprint/ui/window.py`
  - Modify `tests/test_ui.py`
- **Acceptance criteria:**
  - Mode 2 replaces its placeholder with `PdfEditorWidget` featuring a left lazy-thumbnail rail, center zoomable/pannable canvas (`1 scene unit = 1 unrotated PDF point`, separate rotation transform), and contextual tool/typography inspector.
  - Users can open a PDF («Открыть PDF» or drag-and-drop), select the «Текст» tool, click a page to create/edit a Cyrillic text box, drag/resize it, adjust font family, size (pt), color, and alignment, and undo/redo edits.
  - Page rendering and PDF export run in the `T-19` PDF process with session/page/revision freshness tokens. Reuse shared save/dirty guards; «Сохранить новый PDF» protects every source and confirms other-file replacement. Independent workspace Undo/Redo includes text creation/edit/move/delete; text widgets retain native editing shortcuts.
  - UI tests verify opening an artificial PDF in Mode 2, adding Cyrillic text, and exporting a valid PDF containing the new text.
  - Verify overlay hit-testing and export position after crop/rotation/zoom, text-fit errors, stale results/errors after document replacement, mode switching with unsaved work, and save cancellation/failure.
- **Verification:**
  - `uv run pytest -q tests/test_ui.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

### `T-12`: Add PDF whiteout boxes, quick stamps (`✓`, `✕`, date), and ink/PNG signatures

- **Status:** `TODO`
- **Depends on:** `T-11`
- **Goal:** Expand PDF Editor Mode with practical form-filling and signing tools: whiteout masking rectangles, stamps, freehand signatures, and transparent PNG signature placement.
- **Target files:**
  - Modify `src/cardprint/pdf_editor.py`
  - Modify `src/cardprint/ui/pdf_view.py`
  - Modify `tests/test_pdf_editor.py`
  - Modify `tests/test_ui.py`
- **Acceptance criteria:**
  - `pdf_editor.py` models and exports `PdfWhiteoutOverlay` (opaque white/custom fill rect rendered beneath text), `PdfStampOverlay` (`✓`, `✕`, today's `DD.MM.YYYY` date, custom stamp), `PdfInkOverlay` (freehand vector strokes in points), and `PdfImageOverlay` (transparent PNG signature).
  - `ui/pdf_view.py` adds «Замазка», quick stamps, «Подпись от руки» (captured vector polylines without automatic smoothing), and «Вставить скан подписи (PNG)». Explain that masking does not remove underlying data and visual signatures are not digital signatures. Do not automatically persist signature assets.
  - All new overlays support selection, move/resize, delete, and Undo/Redo.
  - Tests verify vector strokes, whiteout masking, stamps, and PNG signature embedding in exported PDFs.
  - A whiteout fixture verifies both its visible coverage and that underlying text remains extractable, so the tool cannot be mistaken for redaction. Check stamp glyphs/date stability, captured stroke fidelity, transparent PNG pixels, deterministic stacking and Undo/Redo.
- **Verification:**
  - `uv run pytest -q tests/test_pdf_editor.py tests/test_ui.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

### `T-13`: Add interactive PDF form (`AcroForm`) field detection, editing, and export

- **Status:** `TODO`
- **Depends on:** `T-11`
- **Goal:** Detect existing fillable form fields (`AcroForm` text inputs, checkboxes, choice/radio widgets) in opened PDFs and allow editing and saving their values natively.
- **Target files:**
  - Modify `src/cardprint/pdf_editor.py`
  - Modify `src/cardprint/ui/pdf_view.py`
  - Modify `tests/test_pdf_editor.py`
  - Modify `tests/test_ui.py`
- **Acceptance criteria:**
  - Inspect widget names/types/rectangles/initial values in the PDF process. First enable text and checkbox fields; choice/radio groups remain read-only until dedicated round-trip fixtures pass. Use stable source/page/field identities, actual on/off/export states, and intended parent/group linkage rather than field-name-only keys or booleans alone.
  - `ui/pdf_view.py` highlights detected form fields with interactive controls (click checkbox to toggle, click text field to type Cyrillic text) synced with Undo/Redo.
  - `export_edited_pdf` persists modified `AcroForm` values cleanly so reopened PDFs retain the filled values in standard viewers and PyMuPDF `widgets()`.
  - Tests create an artificial PDF with PyMuPDF `AcroForm` text and checkbox widgets, fill them, export, and verify persisted widget values.
  - Verify visible Cyrillic appearances after reopening/rendering, read-only flags, multiple widgets of one field, and independent duplicate names from separate sources. Preserve widgets without flattening. Unsupported XFA/JavaScript/calculated/digital-signature fields show explicit limitations; do not execute document scripts.
- **Verification:**
  - `uv run pytest -q tests/test_pdf_editor.py tests/test_ui.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

### `T-14`: Add in-memory PDF password unlock and non-destructive page operations

- **Status:** `TODO`
- **Depends on:** `T-11`, `T-12`, `T-13`
- **Goal:** Support opening password-protected PDFs via an in-memory password prompt and allow reordering, rotating, deleting, and merging PDF pages cleanly.
- **Target files:**
  - Modify `src/cardprint/imaging.py`
  - Modify `src/cardprint/pdf_editor.py`
  - Modify `src/cardprint/ui/pdf_view.py`
  - Modify `src/cardprint/ui/window.py`
  - Modify `tests/test_imaging.py`
  - Modify `tests/test_pdf_editor.py`
  - Modify `tests/test_ui.py`
- **Acceptance criteria:**
  - PDF Editor encrypted input prompts in Russian with retry/cancel and in-memory per-source credentials. Respect editing permissions; never log/persist passwords or remove export encryption implicitly. Implement DESIGN §7.3's retain/decrypt/new-policy choices and unsupported-policy errors. Extending Mode 1 unlock is optional and must be documented/tested explicitly.
  - Mode 2 supports reordering pages (drag-and-drop or Up/Down buttons), rotating pages by `90°`, deleting pages (preventing deleting the last remaining page), and merging pages from another local PDF («Добавить PDF»).
  - Export produces the reordered/merged/rotated PDF with all page overlays preserved in the correct page coordinate orientation.
  - Tests verify password unlock (both valid and invalid passwords on an artificial encrypted PDF) and page reorder/rotate/delete/merge export.
  - Preserve stable page/overlay/widget IDs through structural edits and Undo. Verify rotated/cropped pages, independent merged field names, intended widget linkage, existing annotations/links and stated document-level preservation limits. Warn before an operation with known feature loss; protect sources even after their last page is deleted.
  - Test merged sources with different credentials/protection, read-only permissions, cancelled authentication, and encrypted/decrypted export policy; never assume one session password applies to every source.
- **Verification:**
  - `uv run pytest -q tests/test_imaging.py tests/test_pdf_editor.py tests/test_ui.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

## Milestone 4 — Advanced CardPrint Productivity Features

### `T-15`: Add verified/user size presets and multi-card / multi-page A4 layout support

- **Status:** `TODO`
- **Depends on:** `T-06`, `T-16`
- **Goal:** Expand Mode 1 beyond strictly 2 sides (`front`/`back`) with common document presets and configurable multi-card / multi-page A4 layouts.
- **Target files:**
  - Modify `src/cardprint/geometry.py`
  - Modify `src/cardprint/model.py`
  - Modify `src/cardprint/export.py`
  - Modify `src/cardprint/ui/window.py`
  - Modify `tests/test_geometry.py`
  - Modify `tests/test_export.py`
  - Modify `tests/test_ui.py`
- **Acceptance criteria:**
  - Keep ID-1, aspect/custom modes and add user-labelled presets with explicit dimensions/orientation. Named document presets require a recorded standard/version or user-specified dimensions; do not reuse unverified passport-spread/СНИЛС/old-licence sizes from the initial proposal.
  - Layout engine supports grid/list placement of `N` items (including repeated photo grids) across 1 or more A4 pages when multi-page mode is selected, validating margins strictly per page.
  - PDF export writes all pages cleanly with physical mm accuracy; PNG export saves single-page or numbered multi-page PNGs with full overwrite protection.
  - Introduce stable slot/sheet-page IDs and page-local annotations with a versioned `T-16` project migration; legacy single-page projects retain identical output and geometry. Undo covers added/removed slots and pages.
  - Before a multi-PNG export, enumerate all destinations and check original guards/collisions; confirm replacements together. Stage/validate the full set before publishing. If publication fails partway, report exactly which files were written; do not claim filesystem-wide atomicity or delete earlier existing outputs in rollback.
- **Verification:**
  - `uv run pytest -q tests/test_geometry.py tests/test_export.py tests/test_ui.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

### `T-16`: Add `.cardprint` project save/load preserving parameters and annotations

- **Status:** `TODO`
- **Depends on:** `T-08`, `T-21`
- **Goal:** Allow users to save and reopen CardPrint sessions (`.cardprint` JSON) storing edit parameters, layout, and annotations without duplicating source documents.
- **Target files:**
  - Create `src/cardprint/project.py`
  - Modify `src/cardprint/ui/window.py`
  - Create `tests/test_project.py`
  - Modify `tests/test_ui.py`
- **Acceptance criteria:**
  - Versioned `.cardprint` JSON stores stable source/page/slot IDs, source fingerprints, paths, `turns`, `points`, `radius`, `CardSize`, `Layout`, and annotations. Paths and annotation text can contain personal data; never store passwords, raw document pixels or credentials. Relative paths resolve against the project directory, not process CWD.
  - Loading a project restores all cards, layout settings, and annotations cleanly and records all referenced source files in `original_paths` overwrite protection.
  - Validate types, finite numbers, ranges, bounded file/string/collection sizes and schema versions before swapping the active session. Malformed/newer-schema projects and unavailable sources leave current work intact. Changed sources require explicit relink/reload and encrypted sources reauthentication.
  - Project files use shared safe-save/guards and dirty checkpoints. Protect the opened project from PDF/PNG export overwrite while allowing its explicit project Save action. Cancelled/failed save never clears dirty state. PDF Editor persistence is outside this task's initial schema.
  - Test round trips, moved/relinked/changed sources, relative paths under a different CWD, invalid/oversized/newer-schema input, source protection and preservation of current work after failed load.
- **Verification:**
  - `uv run pytest -q tests/test_project.py tests/test_ui.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

### `T-17`: Add direct printing with explicit scale and printable-area handling

- **Status:** `TODO`
- **Depends on:** `T-06`, `T-11`
- **Goal:** Request true physical-scale printing without silently fitting documents to the printer's page or promising unverified hardware behavior. Detection improvements are a separate task (`T-22`).
- **Target files:**
  - Modify `src/cardprint/ui/window.py`
  - Modify `tests/test_ui.py`
- **Acceptance criteria:**
  - «Печать…» (`Ctrl+P`) uses shared mm/point geometry with application fit-to-page disabled; PDF rendering uses `T-19`. Preserve actual PDF page size rather than silently forcing arbitrary PDF pages into A4.
  - Compare content with device printable bounds and show clipping/unsupported-paper errors before submitting a job; never silently shrink. `FullPage` does not guarantee borderless hardware. Cancellation does not mark work saved/exported.
  - Automated tests verify unit conversions, margins, cancellation and rendering helpers without a physical printer. Separately record a physical ruler/calibration print per verified platform/printer; otherwise label native printing and scale as unverified.
- **Verification:**
  - `uv run pytest -q tests/test_export.py tests/test_ui.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

### `T-22`: Improve contour detection with an artificial benchmark

- **Status:** `TODO`
- **Depends on:** `T-19`
- **Target files:** `src/cardprint/imaging.py`, `tests/test_imaging.py`, artificial demo/benchmark scripts as needed
- **Goal:** Improve automatic corner proposals independently of printing or PDF Editor features.
- **Acceptance criteria:**
  - Evaluate multiple contour/threshold strategies on reproducible artificial low-contrast, shadowed, textured and distractor backgrounds. Thresholding operates on detection copies only; original/exported card pixels are never enhanced.
  - Record baseline versus candidate corner error, false-positive/abstention rates and runtime on the same fixtures. Use this evidence to accept an improvement; do not optimize only for the easiest synthetic rectangle.
  - Return `None` when uncertain; keep manual four-point correction immediately available and avoid displaying an uncalibrated confidence score as proof of correctness.
  - Verify original-array/file invariance and existing detection regressions. Keep expensive detection off the GUI thread and honor current request generations.
- **Verification:** Target imaging tests and the recorded artificial benchmark, then the full quality gate.

## Review Record — 2026-11-27

The design review added reliability/save prerequisites (`T-18`, `T-19`, `T-21`), loss guards (`T-20`) and separated detection from printing (`T-22`). Existing IDs remain stable; table order and dependency lists define execution. All 22 implementation tasks remain `TODO`: this review changes documents only. See DESIGN §10 for technical references and the distinction between observed behavior and identified risks.

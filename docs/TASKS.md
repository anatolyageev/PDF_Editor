# CardPrint Implementation Task Backlog

**Created:** 2026-11-26  
**Design Spec:** [DESIGN.md](DESIGN.md)  
**Architecture Baseline:** [ARCHITECTURE.md](ARCHITECTURE.md) (`0.1.0`)

This backlog breaks down the planned v0.2+ design improvements and PDF writing/editing features into atomic, sequential tasks so an agent can execute **one task per run** cleanly and verifiably.

---

## Agent Execution Protocol

1. **One task per run:** Work on the task ID specified by the user, or — if none was specified — pick the **first `TODO` task** in the table below whose `Depends On` tasks are all `DONE`. Do not start a second task in the same run unless explicitly asked.
2. **Read before editing:** Read [AGENTS.md](../AGENTS.md), [ARCHITECTURE.md](ARCHITECTURE.md), the relevant section of [DESIGN.md](DESIGN.md), and the existing source/test files listed under `Target files`.
3. **Respect all invariants:**
   - Local/offline only (no cloud, telemetry, OCR, LLM, or generative code).
   - Never overwrite originals; never modify pixels inside card regions; keep A4 background pure RGB `(255, 255, 255)`.
   - Keep coordinate spaces distinct; share geometry between preview and export.
   - Keep all UI strings and messages in **Russian**; keep agent docs in **English**.
   - Use artificial test data only.
4. **Stay scoped:** Implement only the acceptance criteria of the active task. Do not refactor unrelated modules or pull in future tasks.
5. **Verify after editing:** Run targeted tests for the changed module first, then run the full quality gate:
   ```bash
   uv run pytest -q
   uv run ruff check src tests scripts
   uv run ruff format --check src tests scripts
   ```
6. **Update documentation before finishing:**
   - Change the task's `Status` from `TODO` to `DONE` in both the Master Progress Table and the task section below, recording the date and observed test summary.
   - Update [PROJECT_STATUS.md](PROJECT_STATUS.md) with the date, outcome, and checks actually run.
   - Update [ARCHITECTURE.md](ARCHITECTURE.md) if modules, coordinate systems, or data flow changed.
   - Update [README.md](../README.md) if the user workflow, shortcuts, or supported features changed.
7. **Stop cleanly:** Summarize what was implemented, which tests/checks passed, and which task is next in the queue.

---

## Master Progress Table

| ID | Milestone | Title | Status | Depends On |
| --- | --- | --- | --- | --- |
| `T-01` | M1 · Foundation & UX | Extract domain models from `ui/window.py` into `src/cardprint/model.py` | `TODO` | — |
| `T-02` | M1 · Foundation & UX | Add Light/Dark QSS theme system (`ui/theme.py`) and SVG icon helper (`ui/icons.py`) | `TODO` | `T-01` |
| `T-03` | M1 · Foundation & UX | Add drag-and-drop import, visual Front/Back slot cards, and source badges | `TODO` | `T-02` |
| `T-04` | M1 · Foundation & UX | Upgrade corner editor UX (mask, numbered handles, live error, loupe, shortcuts) | `TODO` | `T-02` |
| `T-05` | M1 · Foundation & UX | Add parametric Undo/Redo (`Ctrl+Z` / `Ctrl+Y`) for card and layout edits | `TODO` | `T-01` |
| `T-06` | M1 · Foundation & UX | Upgrade A4 preview (mm rulers, margin guides, crop marks, drag-to-offset) | `TODO` | `T-02`, `T-05` |
| `T-07` | M2 · Text on A4 | Add Cyrillic TTF font resolver (`fonts.py`), `TextAnnotation`, and shared text export | `TODO` | `T-01` |
| `T-08` | M2 · Text on A4 | Add interactive A4 text & watermark editing UI with presets and Undo/Redo | `TODO` | `T-05`, `T-06`, `T-07` |
| `T-09` | M3 · PDF Editor Mode | Add dual-mode workspace shell and extract shared atomic safe-save helper | `TODO` | `T-02` |
| `T-10` | M3 · PDF Editor Mode | Implement PDF Editor core engine (`pdf_editor.py`) for vector Cyrillic text | `TODO` | `T-07`, `T-09` |
| `T-11` | M3 · PDF Editor Mode | Implement PDF Editor UI (`ui/pdf_view.py`) for viewing, writing text, and saving PDFs | `TODO` | `T-10` |
| `T-12` | M3 · PDF Editor Mode | Add PDF whiteout boxes, quick stamps (`✓`, `✕`, date), and ink/PNG signatures | `TODO` | `T-11` |
| `T-13` | M3 · PDF Editor Mode | Add interactive PDF form (`AcroForm`) field detection, editing, and export | `TODO` | `T-11` |
| `T-14` | M3 · PDF Editor Mode | Add in-memory PDF password unlock and non-destructive page operations | `TODO` | `T-11` |
| `T-15` | M4 · Productivity | Add document size presets and multi-card / multi-page A4 layout support | `TODO` | `T-06` |
| `T-16` | M4 · Productivity | Add `.cardprint` project save/load preserving parameters and annotations | `TODO` | `T-08` |
| `T-17` | M4 · Productivity | Add direct 100%-scale printing and improved multi-strategy `detect_quad` | `TODO` | `T-02` |

---

## Milestone 1 — Design System, Domain Refactor & Editor UX

### `T-01`: Extract domain models from `ui/window.py` into `src/cardprint/model.py`

- **Status:** `TODO`
- **Depends on:** None
- **Goal:** Separate `Card` (and future pure-Python session/state helpers) from Qt widgets in [window.py](../src/cardprint/ui/window.py) so domain logic is testable without Qt and ready for Undo/Redo and dual-mode state.
- **Target files:**
  - Create `src/cardprint/model.py`
  - Modify `src/cardprint/ui/window.py`
  - Create `tests/test_model.py`
- **Acceptance criteria:**
  - `Card` dataclass and `source_size` property live in `src/cardprint/model.py` with zero Qt imports (`PySide6` must not be imported in `model.py`).
  - `Card` preserves all existing fields, revision counters, and defaults from `0.1.0`.
  - `ui/window.py` imports `Card` from `..model` and behaves identically to `0.1.0`.
  - Unit tests verify `Card.source_size` across all `turns % 4` values and ensure `model.py` imports without `PySide6`.
- **Verification:**
  - `uv run pytest -q tests/test_model.py tests/test_ui.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

### `T-02`: Add Light/Dark QSS theme system (`ui/theme.py`) and SVG icon helper (`ui/icons.py`)

- **Status:** `TODO`
- **Depends on:** `T-01`
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
- **Verification:**
  - `uv run pytest -q tests/test_ui.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

### `T-03`: Add drag-and-drop import, visual Front/Back slot cards, and source badges

- **Status:** `TODO`
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
- **Verification:**
  - `uv run pytest -q tests/test_ui.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

### `T-04`: Upgrade corner editor UX (mask, numbered handles, live error, loupe, shortcuts)

- **Status:** `TODO`
- **Depends on:** `T-02`
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
- **Verification:**
  - `uv run pytest -q tests/test_ui.py tests/test_geometry.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

### `T-05`: Add parametric Undo/Redo (`Ctrl+Z` / `Ctrl+Y`) for card and layout edits

- **Status:** `TODO`
- **Depends on:** `T-01`
- **Goal:** Provide reliable non-destructive Undo/Redo using lightweight parameter snapshots (never duplicating original pixel arrays).
- **Target files:**
  - Modify `src/cardprint/model.py`
  - Modify `src/cardprint/ui/window.py`
  - Modify `tests/test_model.py`
  - Modify `tests/test_ui.py`
- **Acceptance criteria:**
  - `UndoStack` in `model.py` stores bounded (max 50) immutable parameter snapshots (`turns`, `points`, `radius`, `CardSize`, `Layout`) referencing the existing immutable `original` arrays without copying pixels.
  - Changing corners, rotating, changing size/radius, changing layout, or resetting a card pushes an undoable step; continuous drags/spinbox edits coalesce cleanly on commit.
  - Toolbar buttons «Отменить» (`Ctrl+Z`) and «Повторить» (`Ctrl+Y` / `Ctrl+Shift+Z`) accurately reflect stack availability and restore state + re-render cards and sheet.
  - Unit and UI tests verify multi-step undo/redo across corner edits, rotation, and layout changes.
- **Verification:**
  - `uv run pytest -q tests/test_model.py tests/test_ui.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

### `T-06`: Upgrade A4 preview (mm rulers, margin guides, crop marks, drag-to-offset)

- **Status:** `TODO`
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
  - `Layout` gains `crop_marks: bool = False`; `export.py` draws identical hairline corner crop marks in `render_sheet` (preview/PNG) and `_write_pdf` (vector PDF lines) when enabled.
  - A4 preview tab offers toggleable millimeter rulers and dashed printable margin guides (non-exporting visual guides only).
  - Dragging the card group with the mouse on the A4 preview updates `offset_x_mm` and `offset_y_mm` (clamped or validated identically by `arrange`), snaps gently to `0.0 mm` center axes, and records one Undo step on release.
  - Tests verify crop marks presence in exported PNG/PDF when enabled and absence when disabled, plus mouse drag offset synchronization.
- **Verification:**
  - `uv run pytest -q tests/test_geometry.py tests/test_export.py tests/test_ui.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

## Milestone 2 — Writing Text & Watermarks on A4 Sheet (Mode 1)

### `T-07`: Add Cyrillic TTF font resolver (`fonts.py`), `TextAnnotation`, and shared text export

- **Status:** `TODO`
- **Depends on:** `T-01`
- **Goal:** Build cross-platform Cyrillic TrueType font discovery and shared millimeter-accurate text rendering for A4 PDF and PNG/preview output.
- **Target files:**
  - Create `src/cardprint/fonts.py`
  - Modify `src/cardprint/geometry.py`
  - Modify `src/cardprint/export.py`
  - Create `tests/test_fonts.py`
  - Modify `tests/test_export.py`
- **Acceptance criteria:**
  - `src/cardprint/fonts.py` resolves cross-platform Cyrillic `.ttf` paths for logical families (`sans`, `sans-bold`, `serif`, `mono`), verifies Cyrillic glyph support (`А`–`я`, `Ё`, `ё`), and registers ReportLab `TTFont`s cleanly (no Qt dependency).
  - `TextAnnotation` dataclass in `geometry.py` models `id`, `text`, `x_mm`, `y_mm`, `font_family`, `font_size_pt`, `color_hex`, `angle_deg`, `opacity`, `align`, `is_watermark`.
  - `Sheet` accepts `annotations: tuple[TextAnnotation, ...] = ()`.
  - `_write_pdf` renders annotations as true selectable vector Cyrillic text (with rotation and alpha) and `render_sheet` renders matching Pillow text at identical mm coordinates.
  - Tests verify Cyrillic text extraction from exported PDF via PyMuPDF (`page.get_text()`) and visual pixel presence in exported PNG (including rotated/semi-transparent watermarks).
- **Verification:**
  - `uv run pytest -q tests/test_fonts.py tests/test_export.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

### `T-08`: Add interactive A4 text & watermark editing UI with presets and Undo/Redo

- **Status:** `TODO`
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
- **Verification:**
  - `uv run pytest -q tests/test_ui.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

## Milestone 3 — Full PDF Editor Mode (Write, Fill & Sign Arbitrary PDFs)

### `T-09`: Add dual-mode workspace shell and extract shared atomic safe-save helper

- **Status:** `TODO`
- **Depends on:** `T-02`
- **Goal:** Prepare `MainWindow` for two workspace modes («Карточки на A4» / «Редактор PDF») and extract reusable atomic file staging for both exporters.
- **Target files:**
  - Modify `src/cardprint/export.py`
  - Modify `src/cardprint/ui/window.py`
  - Modify `tests/test_export.py`
  - Modify `tests/test_ui.py`
- **Acceptance criteria:**
  - `export.py` exposes a reusable `atomic_write_file(target, writer_fn, *, overwrite: bool = False)` helper preserving staging cleanup, `os.link` atomic creation, non-hardlink fallback, and `FileExistsError` protection; `export_sheet` delegates to it.
  - `MainWindow` adds a top segmented mode switcher («Карточки на A4» | «Редактор PDF») backed by a `QStackedWidget`, keeping Mode 1 completely functional and displaying a clean placeholder shell for Mode 2 until `T-11`.
  - Original-file overwrite protection helper (`is_protected_original`) is shared across modes.
- **Verification:**
  - `uv run pytest -q tests/test_export.py tests/test_ui.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

### `T-10`: Implement PDF Editor core engine (`pdf_editor.py`) for vector Cyrillic text

- **Status:** `TODO`
- **Depends on:** `T-07`, `T-09`
- **Goal:** Create the pure-Python (no Qt) PDF editing session engine using PyMuPDF to load multi-page PDFs, render pages lazily, and export vector Cyrillic text overlays cleanly to a new file.
- **Target files:**
  - Create `src/cardprint/pdf_editor.py`
  - Create `tests/test_pdf_editor.py`
- **Acceptance criteria:**
  - `pdf_editor.py` has zero `PySide6` imports and defines `PdfDocumentSession`, `PdfPageState`, and `PdfTextOverlay` in PDF points (top-left origin matching PyMuPDF `page.rect`).
  - Lazy page rendering respects `MAX_PIXELS` (`80_000_000`) and raises clear Russian `ValueError` messages on missing/damaged/encrypted PDFs.
  - `export_edited_pdf(session, target, *, overwrite=False)` writes via `atomic_write_file`, embeds Cyrillic TTF subsets from `fonts.py` (`insert_textbox` / `insert_text`), preserves all original vector text/graphics on untouched regions, and keeps original page dimensions.
  - Tests verify opening an artificial multi-page vector PDF, adding Cyrillic text overlays, exporting to a new PDF, verifying original file unchanged, and confirming both original and added Cyrillic text via PyMuPDF `page.get_text()`.
- **Verification:**
  - `uv run pytest -q tests/test_pdf_editor.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

### `T-11`: Implement PDF Editor UI (`ui/pdf_view.py`) for viewing, writing text, and saving PDFs

- **Status:** `TODO`
- **Depends on:** `T-10`
- **Goal:** Build the interactive «Редактор PDF» UI so users can open any PDF, browse pages, click to type Cyrillic text anywhere on a page, move/style text boxes, and save a new vector PDF.
- **Target files:**
  - Create `src/cardprint/ui/pdf_view.py`
  - Modify `src/cardprint/ui/window.py`
  - Modify `tests/test_ui.py`
- **Acceptance criteria:**
  - Mode 2 replaces its placeholder with `PdfEditorWidget` featuring a left page-thumbnail rail, center zoomable/pannable page canvas (`1 scene unit = 1 PDF point`), and right tool/typography inspector.
  - Users can open a PDF («Открыть PDF» or drag-and-drop), select the «Текст» tool, click a page to create/edit a Cyrillic text box, drag/resize it, adjust font family, size (pt), color, and alignment, and undo/redo edits.
  - Page rendering and PDF export run in `QThreadPool` background tasks with freshness tokens; «Сохранить новый PDF» blocks overwriting any opened original file and confirms before replacing other existing files.
  - UI tests verify opening an artificial PDF in Mode 2, adding Cyrillic text, and exporting a valid PDF containing the new text.
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
  - `ui/pdf_view.py` adds tools for «Замазка», quick stamp insertion, «Подпись от руки» (modal drawing pad producing clean smooth polylines), and «Вставить скан подписи (PNG)».
  - All new overlays support selection, move/resize, delete, and Undo/Redo.
  - Tests verify vector strokes, whiteout masking, stamps, and PNG signature embedding in exported PDFs.
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
  - `pdf_editor.py` inspects `page.widgets()` to extract field names, types (text, checkbox, choice), rectangles, and initial values, storing user edits in `PdfPageState.form_values`.
  - `ui/pdf_view.py` highlights detected form fields with interactive controls (click checkbox to toggle, click text field to type Cyrillic text) synced with Undo/Redo.
  - `export_edited_pdf` persists modified `AcroForm` values cleanly so reopened PDFs retain the filled values in standard viewers and PyMuPDF `widgets()`.
  - Tests create an artificial PDF with PyMuPDF `AcroForm` text and checkbox widgets, fill them, export, and verify persisted widget values.
- **Verification:**
  - `uv run pytest -q tests/test_pdf_editor.py tests/test_ui.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

### `T-14`: Add in-memory PDF password unlock and non-destructive page operations

- **Status:** `TODO`
- **Depends on:** `T-11`
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
  - Opening an encrypted PDF prompts the user with a Russian password dialog (`QLineEdit.EchoMode.Password`); wrong passwords show a clear retry message, and passwords are never logged or saved to disk. (Optional `password` parameter works in `imaging.py` and `pdf_editor.py`.)
  - Mode 2 supports reordering pages (drag-and-drop or Up/Down buttons), rotating pages by `90°`, deleting pages (preventing deleting the last remaining page), and merging pages from another local PDF («Добавить PDF»).
  - Export produces the reordered/merged/rotated PDF with all page overlays preserved in the correct page coordinate orientation.
  - Tests verify password unlock (both valid and invalid passwords on an artificial encrypted PDF) and page reorder/rotate/delete/merge export.
- **Verification:**
  - `uv run pytest -q tests/test_imaging.py tests/test_pdf_editor.py tests/test_ui.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

## Milestone 4 — Advanced CardPrint Productivity Features

### `T-15`: Add document size presets and multi-card / multi-page A4 layout support

- **Status:** `TODO`
- **Depends on:** `T-06`
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
  - `CardSize` supports Russian presets: «Удостоверение ID-1 (85,60 × 53,98 мм)», «Паспорт разворот (125 × 88 мм)», «СНИЛС / старый формат (105 × 74 мм)», «Визитка (90 × 50 мм)», «Фото 3×4 (30 × 40 мм)», «Фото 35×45 мм», plus existing «Исходные пропорции» and «Произвольный размер».
  - Layout engine supports grid/list placement of `N` items (including repeated photo grids) across 1 or more A4 pages when multi-page mode is selected, validating margins strictly per page.
  - PDF export writes all pages cleanly with physical mm accuracy; PNG export saves single-page or numbered multi-page PNGs with full overwrite protection.
- **Verification:**
  - `uv run pytest -q tests/test_geometry.py tests/test_export.py tests/test_ui.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

### `T-16`: Add `.cardprint` project save/load preserving parameters and annotations

- **Status:** `TODO`
- **Depends on:** `T-08`
- **Goal:** Allow users to save and reopen CardPrint sessions (`.cardprint` JSON) storing edit parameters, layout, and annotations without duplicating source documents.
- **Target files:**
  - Create `src/cardprint/project.py`
  - Modify `src/cardprint/ui/window.py`
  - Create `tests/test_project.py`
  - Modify `tests/test_ui.py`
- **Acceptance criteria:**
  - `.cardprint` JSON stores schema version, source file paths + page indices, `turns`, `points`, `radius`, `CardSize`, `Layout`, and `TextAnnotation` items — never raw pixel dumps or personal metadata beyond referenced paths.
  - Loading a project restores all cards, layout settings, and annotations cleanly and records all referenced source files in `original_paths` overwrite protection.
  - Missing or unreadable source files trigger a clear Russian error/warning without crashing.
  - Project files save via `atomic_write_file` and never overwrite original documents.
- **Verification:**
  - `uv run pytest -q tests/test_project.py tests/test_ui.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

---

### `T-17`: Add direct 100%-scale printing and improved multi-strategy `detect_quad`

- **Status:** `TODO`
- **Depends on:** `T-02`
- **Goal:** Add direct system printing at true 100% physical scale and improve automatic 4-corner contour detection accuracy without altering card pixels.
- **Target files:**
  - Modify `src/cardprint/imaging.py`
  - Modify `src/cardprint/ui/window.py`
  - Modify `tests/test_imaging.py`
  - Modify `tests/test_ui.py`
- **Acceptance criteria:**
  - Toolbar/actions offer «Печать…» (`Ctrl+P`) opening `QPrintDialog` with `QPrinter` configured for A4 at 100% physical scale (using shared millimeter placements for Mode 1 and PyMuPDF page rendering for Mode 2 if active).
  - `detect_quad` evaluates multiple OpenCV contour/threshold strategies (preserving manual 4-point override and returning `None` when uncertain, never modifying card pixels).
  - Tests cover improved `detect_quad` accuracy on low-contrast / shadowed artificial cards and verify print-job rendering helper without requiring a physical printer.
- **Verification:**
  - `uv run pytest -q tests/test_imaging.py tests/test_ui.py`
  - `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`

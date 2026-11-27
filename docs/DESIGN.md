# CardPrint Design Specification (v0.2+ Roadmap)

**Date:** 2026-11-26  
**Status:** Proposed / Planned (not yet implemented)  
**Baseline:** Version `0.1.0` (implemented and documented in [ARCHITECTURE.md](ARCHITECTURE.md) and [PROJECT_STATUS.md](PROJECT_STATUS.md))  
**Related documents:** [Agent Instructions](../AGENTS.md) · [Architecture](ARCHITECTURE.md) · [Project Status](PROJECT_STATUS.md) · [Task Backlog](TASKS.md) · [User Guide (RU)](../README.md)

> [!IMPORTANT]
> This document defines the target design for evolving CardPrint beyond version `0.1.0`. Nothing in this specification is implemented until the corresponding task in [TASKS.md](TASKS.md) is marked `DONE`. New modules listed below (such as `src/cardprint/model.py`, `src/cardprint/fonts.py`, `src/cardprint/pdf_editor.py`, `src/cardprint/ui/theme.py`, `src/cardprint/ui/icons.py`, and `src/cardprint/ui/pdf_view.py`) do not exist in `0.1.0`.

---

## 1. Non-Negotiable Invariants

All v0.2+ features must preserve the core invariants established in [AGENTS.md](../AGENTS.md):

1. **Strictly local and offline:** No cloud APIs, telemetry, accounts, servers, databases, OCR, LLMs, or generative processing.
2. **Non-destructive editing:**
   - Original files on disk are never overwritten (even after removing thumbnails, clearing sides, or switching modes).
   - Image edits always recompute from immutable original pixel arrays (`writeable=False`).
   - PDF edits always export to a new file (or an explicitly user-confirmed non-original replacement) via atomic staging.
3. **Card content fidelity:** No automatic retouching, sharpening, denoising, thresholding, or color correction inside card regions. Outside card rounding / A4 margins, the external A4 sheet background remains strict RGB `(255, 255, 255)`.
4. **Explicit coordinate systems & shared geometry:** Keep source pixels, Qt viewport coordinates, A4 sheet millimeters, PDF points (`1 pt = 1/72 in`), and raster DPI pixels strictly separate. Preview and export must read from the same geometry definitions.
5. **Responsive background execution:** Expensive I/O, PDF rendering, image warping, and export run in `QThreadPool` workers guarded by revision/generation freshness tokens. `QPixmap` and all widgets are created and mutated exclusively on the GUI thread.
6. **Language & fixture rules:** User interface, tooltips, status messages, and error messages are in **Russian**. Tests and demos use artificial data only — never user documents or real PII.

---

## 2. Workspace Architecture: Dual-Mode Desktop App

Currently (`0.1.0`), `MainWindow` in [window.py](../src/cardprint/ui/window.py) mixes domain state (`Card`) with Qt widgets and handles only the two-sided A4 card preparation workflow.

In `v0.2+`, `MainWindow` evolves into a clean two-mode workspace driven by a top segmented mode switcher backed by a `QStackedWidget`:

1. **Mode 1 — «Карточки на A4» (CardPrint Mode):**
   - Preserves and upgrades the existing workflow: import photos/PDFs → assign Front/Back → manual/auto 4-corner perspective rectification → size/rounding → A4 layout & preview → PDF/PNG export.
   - Gains modern Light/Dark UI styling, drag-and-drop, visual slot cards, corner magnifier loupe, parametric Undo/Redo, interactive A4 controls, crop marks, presets, and **vector text / watermarks on the A4 sheet**.
2. **Mode 2 — «Редактор PDF» (PDF Editor Mode):**
   - Dedicated local editor for arbitrary multi-page PDFs: open a PDF, view pages lazily, write Cyrillic vector text, mask fields with whiteout rectangles, place stamps (`✓`, `✕`, current date) and signatures (freehand vector ink or transparent PNG), fill existing `AcroForm` fields, reorder/rotate/delete/merge pages, and save a new PDF via PyMuPDF without rasterizing original vector pages.

### Structural Extraction (Before Feature Expansion)

- **Domain state (`src/cardprint/model.py`):** Move `Card` and introduce sheet/document state and bounded parametric `UndoStack` out of `ui/window.py` so model transitions are unit-testable without instantiating Qt windows.
- **Shared atomic safe-save (`src/cardprint/export.py`):** Extract the staging-and-commit logic (`atomic_write_file` or `safe_write`) from `export_sheet` into a reusable helper so both A4 export (ReportLab / Pillow) and PDF Editor export (PyMuPDF) share identical staging cleanup, `os.link` atomic creation, non-hardlink filesystem fallback, and original-path protection.

---

## 3. Visual Design System & UX (`ui/theme.py`, `ui/icons.py`)

Currently, [app.py](../src/cardprint/app.py) uses default `Fusion` styling with uniform `QPushButton` controls, no icons, and plain `QGroupBox` borders.

### 3.1 Theme & QSS Token System (`src/cardprint/ui/theme.py`)

- Define semantic `ThemePalette` dataclasses for **Light** (default) and **Dark** themes:
  - Surfaces (`window_bg`, `panel_bg`, `card_bg`, `canvas_bg`, `input_bg`)
  - Borders & dividers (`border`, `border_focus`, `divider`)
  - Text (`text_primary`, `text_secondary`, `text_muted`)
  - Semantic accents (`accent`, `accent_hover`, `accent_pressed`, `accent_subtle`, `danger`, `warning`, `success`)
- Generate a centralized QSS stylesheet from the active `ThemePalette`.
- Provide a clean theme switcher in the top toolbar («Светлая тема» / «Тёмная тема») persisted locally via `QSettings` (`CardPrint Local` / `Карточки на A4`).
- Ensure canvas backgrounds remain visually distinct from pure white paper (`#dbe1e8` in Light, muted slate in Dark) while exported A4 sheet surfaces stay strictly `#ffffff`.

### 3.2 Visual Hierarchy & Bundled SVG Icons (`src/cardprint/ui/icons.py`)

- **Button hierarchy via Qt dynamic properties (`variant`):**
  - `primary`: accented CTA («Сохранить PDF», «Сохранить новый PDF»).
  - `secondary`: standard bordered actions («PNG · 300 DPI», «Добавить файлы»).
  - `ghost`: compact toolbar / icon buttons (zoom, fit, rotate, theme toggle, undo/redo).
  - `danger`: destructive or clear actions («Убрать сторону», «Сбросить правки стороны») with restrained warning hover styling.
- **Card-style control panels:** Replace raw etched `QGroupBox` outlines with rounded elevated panel containers (`border-radius`, consistent `10–12px` padding, bold section titles).
- **Bundled inline SVG icon helper (`ui/icons.py`):** Store lightweight monochrome SVG strings in code and render them via `QSvgRenderer` / `QIcon` tinted dynamically to the active `ThemePalette` text/accent color — zero external icon pack or runtime network dependency.
- **Tooltips with shortcuts:** Every toolbar and editor action displays its Russian label and shortcut in `toolTip`.

### 3.3 Drag-and-Drop, Side Slot Cards & Badges

- **File drag-and-drop:** Accept local file drops (`.pdf`, `.jpg`, `.jpeg`, `.png`) on the main window and source list; reject unsupported types with a clear Russian status/warning message.
- **Thumbnail-to-slot drag-and-drop:** Allow dragging a source item from the left list directly onto the Front or Back slot card.
- **Visual Front/Back slot cards:** Replace the plain `QComboBox` above the editor with two clickable side cards («Лицевая сторона», «Обратная сторона») showing:
  - Mini thumbnail of the currently assigned source/cropped result (or empty drop placeholder),
  - Status pill («Готово», «Ошибка выделения», «Загрузка…», «Не назначена»),
  - Quick clear button (`×`) on each card.
- **Source list assignment badges:** Draw subtle overlay badges (`Лиц.`, `Обр.`) on thumbnails in the left `QListWidget` when assigned to a side.

### 3.4 Corner Editor Upgrades (`src/cardprint/ui/editor.py`)

All enhancements below operate purely in Qt scene/viewport overlays and never alter source or exported pixels:

- **Outside dimming mask:** Render a semi-transparent dark overlay (`QGraphicsPathItem` using an even-odd path: full scene rect minus the selected quadrilateral) so the user immediately sees what falls outside the crop.
- **Numbered corner handles (`1`–`4`):** Render index numbers inside/beside each `Handle` matching the clockwise order (`1 ↖`, `2 ↗`, `3 ↘`, `4 ↙`).
- **Live invalid-quad feedback:** Validate the quadrilateral on every `ItemPositionChange` / `ItemPositionHasChanged` (not only on mouse release) and switch the polygon pen/fill to red immediately while non-convex, self-intersecting, reversed, or too small.
- **Floating magnifier loupe:** While dragging a corner handle, render a circular/rounded-square `3×`–`4×` magnified snippet of the image around the cursor with a precision crosshair in an unobscured corner of `ImageView` (auto-flipping corner if the cursor approaches it).
- **Keyboard precision & shortcuts:**
  - Arrow keys nudge the last-selected/focused handle by `1` source pixel (`Shift + Arrow` by `10` source pixels), clamped to `[0 .. width-1] × [0 .. height-1]`.
  - Global/view shortcuts: `Ctrl+O` (добавить файлы), `Ctrl+S` (сохранить PDF), `Ctrl+Z` (отменить), `Ctrl+Shift+Z` / `Ctrl+Y` (повторить), `R` (повернуть на 90°), `0` (вписать изображение), `Esc` (отмена ввода 4 точек / сброс активного инструмента).

### 3.5 Interactive A4 Preview & Crop Marks

- **Toggleable millimeter rulers & margin guides:** Optional top/left mm rulers along the A4 preview viewport and a dashed non-printing guide rectangle showing `margin_mm` bounds.
- **Drag-to-offset card group:** Allow dragging the card group directly on the A4 preview to update `offset_x_mm` and `offset_y_mm` (snapping cleanly to `0.0 mm` center axes within a small threshold, displaying live snap lines). Commits as a single Undo step on mouse release.
- **Optional crop marks («Метки реза»):** Boolean option on `Layout` (`crop_marks: bool = False`, default off). When enabled, draw thin hairline corner ticks (`~3 mm` length, `1 mm` offset outside each card corner, clipped safely inside A4) using shared millimeter coordinates in both `render_sheet` (preview/PNG) and `_write_pdf` (ReportLab vector lines).

---

## 4. Parametric Undo / Redo (`src/cardprint/model.py`)

Because all CardPrint edits are non-destructive parameters applied to immutable originals, Undo/Redo stores **lightweight parameter snapshots** rather than pixel buffers:

- **Snapshot contents (CardPrint Mode):**
  - Per-side immutable state (`source` identity, `turns`, `points`, `radius`, `CardSize`) — keeping a reference to the shared read-only `original` array without copying it.
  - `Layout` parameters (`template`, `gap_mm`, `margin_mm`, `offset_x_mm`, `offset_y_mm`, `crop_marks`) and A4 `TextAnnotation` list.
- **Snapshot contents (PDF Editor Mode):** Per-page overlay list (`PdfTextOverlay`, `PdfWhiteoutOverlay`, `PdfStampOverlay`, `PdfInkOverlay`, `PdfImageOverlay`), form field values, and page order/rotation state.
- **Coalescing & limits:**
  - Bounded history stack (50 entries).
  - Continuous interactions (dragging a corner handle, dragging A4 offset, typing in a spinbox) push one undoable command upon commit/release rather than on every intermediate pixel/tick.
- **UI integration:** Toolbar buttons «Отменить» (`Ctrl+Z`) and «Повторить» (`Ctrl+Y` / `Ctrl+Shift+Z`) enabled only when the corresponding stack is non-empty, with descriptive Russian tooltips.

---

## 5. Cyrillic Font Infrastructure (`src/cardprint/fonts.py`)

ReportLab's standard PDF Type 1 fonts (`Helvetica`, `Times-Roman`, `Courier`) do **not** render Cyrillic glyphs cleanly across PDF viewers without TrueType embedding. Meanwhile, both ReportLab (A4 PDF export), Pillow (A4 preview & 300 DPI PNG), PyMuPDF (PDF Editor vector text insertion), and Qt (interactive text editors) need consistent Cyrillic font metrics.

- **Font registry (`src/cardprint/fonts.py`, no Qt-required core):**
  - Defines curated logical font families (e.g., `sans`, `sans-bold`, `serif`, `mono`) with Russian display names («Без засечек», «Полужирный», «С засечками», «Моноширинный»).
  - Resolves cross-platform `.ttf` paths cleanly:
    1. Optional bundled open-license fallback TTF (e.g., DejaVu Sans / Liberation Sans if packaged under `src/cardprint/assets/fonts/`),
    2. Standard OS font directories on Linux (`/usr/share/fonts/...`, e.g., DejaVu, Liberation, Noto, FreeSans), Windows (`%WINDIR%\Fonts\arial.ttf`, `times.ttf`, `cour.ttf`), and macOS (`/System/Library/Fonts/...`, `/Library/Fonts/...`).
  - Verifies glyph coverage for Cyrillic (`А`–`я`, `Ё`, `ё`) before accepting a candidate font file.
  - Registers ReportLab `TTFont` instances lazily and caches `ImageFont.truetype` / `pymupdf.Font` descriptors safely.

---

## 6. Writing Text & Watermarks on A4 Sheet (Mode 1)

Users frequently need to annotate copied documents on A4 (e.g., «Копия верна», date, recipient organization, «Лицевая сторона / Обратная сторона», or a diagonal semi-transparent watermark).

### 6.1 Data Model (`src/cardprint/geometry.py`)

Add an immutable `TextAnnotation` dataclass in millimeters (top-left A4 origin, matching `Placement`):

- `id: str`
- `text: str`
- `x_mm: float`, `y_mm: float` (anchor position in A4 mm)
- `font_family: str` (`"sans"`, `"sans-bold"`, `"serif"`, `"mono"`)
- `font_size_pt: float` (range e.g. `6.0`–`96.0`)
- `color_hex: str` (e.g., `"#111827"` or `"#6b7280"`)
- `angle_deg: float` (`-180` to `180`, e.g., `-30` for diagonal watermarks)
- `opacity: float` (`0.05`–`1.0`)
- `align: str` (`"left"`, `"center"`, `"right"`)
- `is_watermark: bool = False` (optional semantic tag for layering above cards vs beside cards)

Include `annotations: tuple[TextAnnotation, ...] = ()` on `Sheet`.

### 6.2 Shared Preview & Export Rendering (`src/cardprint/export.py`)

- **PDF (`_write_pdf`):** Draw each `TextAnnotation` via ReportLab using the registered Cyrillic `TTFont`, converting `(x_mm, y_mm)` to PDF bottom-left points via `MM_TO_PT`, applying `saveState()` / `translate()` / `rotate()` / `setFillAlpha()` / `drawString()` (supporting multi-line text with consistent line height) / `restoreState()`. Text remains crisp selectable vector PDF text.
- **Preview & PNG (`render_sheet`):** Render each `TextAnnotation` at the exact same millimeter coordinates and font point size (`pt * dpi / 72`) onto an RGBA overlay using Pillow `ImageFont.truetype` + rotation + opacity, then alpha-composite onto the A4 sheet.
- **Bounds policy:** Watermarks (`is_watermark=True`) may overlap cards intentionally; sheet export validates that regular page text stays finite and within A4 bounds (or warns clearly if clipped).

### 6.3 Interactive A4 Text UI

- On the «Предпросмотр A4» tab:
  - Toggleable **«Добавить текст»** tool (click anywhere on the sheet to place a new text block) and a **«Водяной знак»** quick-preset button (inserts a configurable diagonal semi-transparent watermark such as *«Копия верна · Для предъявления по месту требования»* with today's date).
  - Interactive `QGraphicsTextItem` / handle items on the A4 preview scene allowing drag-to-move, double-click inline editing, rotation/delete, and right-panel controls (text, font family, size in pt, color, angle, opacity slider).

---

## 7. PDF Editor Mode — Writing, Filling & Signing Arbitrary PDFs (Mode 2)

This mode fulfills the user's request to **write directly in PDFs** without rasterizing existing vector documents into images.

### 7.1 Design Scope & Honest Boundaries

- **What it supports:** Opening any readable single- or multi-page PDF and adding **non-destructive vector overlays and form edits**:
  1. **Vector Cyrillic text blocks** (`insert_textbox` / `insert_text` via PyMuPDF with embedded TTF font subsetting).
  2. **Whiteout / masking rectangles** (opaque white vector rectangles placed beneath new text to cover typos or unwanted lines without modifying the source file).
  3. **Quick stamps:** `✓`, `✕`, formatted current date (`DD.MM.YYYY`), or custom short stamp.
  4. **Signatures:** Freehand mouse/stylus vector polyline ink (`PdfInkOverlay`) and transparent PNG signature placement (`PdfImageOverlay`), saved/reusable locally in memory or user-picked PNG file.
  5. **Interactive `AcroForm` fields:** Detect existing PDF text fields and checkboxes/radio buttons, edit their values in-app, and write updated widget values cleanly on export.
  6. **Page operations:** Reorder pages, rotate individual pages (`90°` increments), delete pages, and merge pages from another local PDF.
- **Explicit non-goal (documented in UI/README):** Reflowing or editing existing foreign PDF body text inline (like a word processor) is intentionally out of scope because PDF streams lack reliable paragraph reflow metadata. The whiteout + overlay text workflow provides predictable, lossless visual results without corrupting original layouts.

### 7.2 Coordinate System & Core Engine (`src/cardprint/pdf_editor.py`)

- **No Qt dependency** in `pdf_editor.py`.
- **Coordinate system:** PDF page points (`1 pt = 1/72 in`), **top-left origin** (`0 .. page_rect.width`, `0 .. page_rect.height`), matching PyMuPDF's native `page.rect` coordinate space. Rotation of a page transforms overlay coordinates consistently or resets cleanly per operation.
- **Session model (`PdfDocumentSession`):**
  - `source_path: Path`, `password: str | None` (held in memory only, never written to logs/disk).
  - `pages: list[PdfPageState]` where each `PdfPageState` references `(source_path, original_page_index)`, `extra_turns: int` (`0..3`), `overlays: list[PdfOverlay]`, and `form_values: dict[str, str | bool]`.
  - Typed overlay dataclasses:
    - `PdfTextOverlay(id, x_pt, y_pt, width_pt, height_pt, text, font_family, font_size_pt, color_hex, align)`
    - `PdfWhiteoutOverlay(id, x_pt, y_pt, width_pt, height_pt, color_hex="#ffffff")`
    - `PdfStampOverlay(id, kind, x_pt, y_pt, size_pt, color_hex, label)`
    - `PdfInkOverlay(id, strokes_pt, stroke_width_pt, color_hex)`
    - `PdfImageOverlay(id, x_pt, y_pt, width_pt, height_pt, png_bytes)`
- **Lazy rendering:** Render only the active visible page (plus small thumbnail previews in the page rail) at viewport-appropriate DPI (capped by `MAX_PIXELS`), executed in `QThreadPool` workers with generation tokens.
- **Export (`export_edited_pdf`):**
  1. Open original PDF(s) read-only with PyMuPDF (supplying in-memory password if needed).
  2. Assemble target page sequence in a new `pymupdf.Document` (applying page deletions, reorderings, merges, and rotations).
  3. Apply `AcroForm` field updates (`widget.field_value = ...`, `widget.update()`).
  4. Render overlays in deterministic z-order (`PdfWhiteoutOverlay` → `PdfTextOverlay` / `PdfStampOverlay` / `PdfImageOverlay` / `PdfInkOverlay`) using embedded Cyrillic TTF fonts (`fontname`, `fontfile` via `fonts.py` so PyMuPDF embeds a compact subset and preserves Cyrillic everywhere).
  5. Save via the shared atomic safe-save helper (`export.atomic_write_file`) after verifying the destination is not any opened original file (including `samefile` check).

### 7.3 PDF Editor UI (`src/cardprint/ui/pdf_view.py`)

- **Three-pane layout matching Mode 1 ergonomics:**
  - **Left:** Page thumbnails rail (drag-and-drop reorder, rotate, delete, «Добавить PDF» to merge, page count indicator).
  - **Center:** Zoomable/pannable `QGraphicsView` showing the current page background image (`1 scene unit = 1 PDF point`) with crisp interactive vector overlay items on top (`Text`, `Whiteout`, `Stamp`, `Ink`, `Image`, plus clickable `AcroForm` input overlays).
  - **Right:** Contextual tool & inspector panel: tool selector («Выбор», «Текст», «Замазка», «Штамп», «Подпись», «Поля формы»), typography controls (font, size, color), quick stamp buttons (`✓`, `✕`, «Дата»), signature pad dialog, and «Сохранить новый PDF» primary button.
- **Password-protected PDFs:** Replace the hard failure on encrypted PDFs with an optional Russian password prompt dialog (`QLineEdit.EchoMode.Password`). Passwords are kept strictly in memory for the active session.

---

## 8. Later Productivity Enhancements (Milestone 4)

1. **Document presets & multi-card A4 layouts:**
   - Extend `CardSize` presets beyond `ID-1 (85,60 × 53,98 мм)` to include common Russian/international formats (Паспорт разворот `125 × 88 мм`, СНИЛС / старые права `105 × 74 мм`, Визитка `90 × 50 мм`, Фото на документы `30 × 40 мм` / `35 × 45 мм`).
   - Support placing `N` card slots or repeated photo grids across one or more A4 pages while keeping shared `arrange` margin validation.
2. **Project persistence (`.cardprint` JSON):**
   - Save and reopen work-in-progress sessions storing relative/absolute source paths, `turns`, `points`, `size`, `radius`, `layout`, and `annotations` — never duplicating original images or embedding user PII in logs. Gracefully warn if a referenced source file moved or changed.
3. **Direct 100%-scale printing (`QPrinter` / `QPrintDialog`):**
   - Print A4 sheets or edited PDFs directly from the desktop app at forced 100% physical scale (`FullPage` / explicit millimeter-to-device mapping) without external viewer scaling surprises.
4. **Improved contour detection (`detect_quad`):**
   - Evaluate multiple thresholding / Canny + adaptive threshold passes ranked by quadrilateral area, rectangularity, and aspect plausibility, while keeping manual 4-corner selection always immediately available.

---

## 9. Verification & Quality Standards

Every implementation task in [TASKS.md](TASKS.md) must satisfy:

- **Automated test coverage** using artificial cards/PDFs only:
  - `uv run pytest -q` (must pass completely, including `tests/test_ui.py` under `offscreen`).
  - `uv run ruff check src tests scripts`
  - `uv run ruff format --check src tests scripts`
- **GUI visual verification** on UI-touching milestones when a graphical session is available:
  - `QT_QPA_PLATFORM=wayland uv run pytest -q tests/test_ui.py`
  - `QT_QPA_PLATFORM=wayland uv run python scripts/gui_smoke.py`
- **Documentation sync:** Update [TASKS.md](TASKS.md), [PROJECT_STATUS.md](PROJECT_STATUS.md), [ARCHITECTURE.md](ARCHITECTURE.md) (when modules, coordinates, or data flow change), and [README.md](../README.md) (when user workflow or capabilities change).

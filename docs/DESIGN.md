# CardPrint Design Specification (v0.2+ Roadmap)

**Reviewed:** 2026-11-27\
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
   - PDF edits always export to a new file (or an explicitly user-confirmed non-original replacement) via safe staging. Publication is atomic where supported; exclusive-copy fallback is not atomic.
3. **Card content fidelity:** No automatic retouching, sharpening, denoising, thresholding, or color correction inside card regions. The A4 base surface remains strict RGB `(255, 255, 255)`; only explicit user-added text, watermarks, or crop marks may paint outside cards. An existing PDF page's background is preserved, not whitened.
4. **Explicit coordinate systems & shared geometry:** Keep source pixels, Qt viewport coordinates, A4 sheet millimeters, PDF points (`1 pt = 1/72 in`), and raster DPI pixels strictly separate. Preview and export must read from the same geometry definitions.
5. **Responsive background execution:** Expensive work stays off the GUI thread and carries revision/generation tokens. Image-only work may use `QThreadPool`. All PyMuPDF operations use the process boundary defined in §2.2, not concurrent Qt worker threads. `QPixmap` and all widgets are created and mutated exclusively on the GUI thread.
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
- **Shared safe-save (`src/cardprint/export.py`):** Extract a pure file-staging helper and a shared original-path guard before adding another exporter (`T-21`). Keep this separate from the dual-mode UI shell (`T-09`). Both exporters and project saves must use the guard and the same staging policy; the current `export_sheet` alone does not protect imported originals.

### 2.1 Delivery Order and Scope

The full roadmap is not the `v0.2` release scope. Preserve task IDs while ordering the backlog by readiness and risk:

| Slice | Intended outcome | Tasks |
| --- | --- | --- |
| M0 · Reliability | Correct editing/export state, safe PDF execution, reusable safe-save | `T-18`, `T-19`, `T-21` |
| M1 · CardPrint UX | State extraction, Undo/Redo, loss guards, clearer controls and precision tools | `T-01`–`T-06`, `T-20` |
| M2 · A4 text | Font-backed captions and watermarks using shared text layout | `T-07`, `T-08` |
| M3 · PDF overlays | Separate workspace, vector text, masking/signatures, tested forms and page operations | `T-09`–`T-14` |
| M4 · Productivity | Presets, project persistence, printing, detection improvements | `T-15`–`T-17`, `T-22` |

`v0.2` targets M0/M1. Later slices remain planned until explicitly selected. Do not show a working PDF editor mode before it is usable; a development placeholder in `T-09` stays disabled and clearly labelled. Design/documentation review does not authorize implementing the next `TODO` task.

### 2.2 PDF Process Boundary, Scheduling and Memory

PyMuPDF documents that multithreaded use is unsupported (see §10). The existing two-thread pool can currently reach `discover` / `load_source` concurrently; separate document objects in those threads do not establish a supported architecture. Correct this in `T-19` before expanding PDF work.

- Introduce `src/cardprint/pdf_worker.py`: one spawned child process initially, owning all PyMuPDF objects, font descriptors, reads, rendering, inspection, and edited-PDF generation. Qt-thread callbacks may orchestrate requests and receive data, but must not call PyMuPDF themselves. Do not use a global mutex as a substitute for the process boundary.
- Send serializable requests (source identity, page ID, revision, parameters); return RGB bytes/arrays, dimensions, or plain metadata. Never pass live `Document`, `Page`, `Pixmap`, `Widget`, or `Font` objects across threads/processes. Worker entry points are module-level and compatible with Windows `spawn`; importing them must not create a window or worker recursively.
- Coalesce pending renders per page/card: one running request plus the latest pending request; skip obsolete queued work. Ignore stale successes **and errors**, including inspector requests, removed/reassigned sides and closed/replaced documents.
- Keep thumbnails/render caches bounded by bytes and evict least-recently-used entries. Starting budgets: 32 MiB for thumbnails, 128 MiB for display-page cache. Generate PDF thumbnails in batches rather than holding every page thumbnail before displaying the first batch. `MAX_PIXELS` limits an allocation, not total process memory.
- Export an immutable snapshot of the requested revision. Serialize writes to the same destination. Edits made during export remain dirty; completion reports which revision was saved and never marks newer edits as exported.
- Cancel queued work on close/replacement. Let active export finish or fail cleanly before closing; a worker crash reports a recoverable Russian error, releases request state, and does not publish a partial staged file. Record peak memory on reproducible artificial inputs; native Windows startup/shutdown requires separate verification.

### 2.3 Shared Save Contract

`atomic_write_file(target, writer_fn, *, overwrite=False)` stages beside the destination, then uses `os.replace` for confirmed replacement or `os.link` for exclusive creation. Where hard links are unavailable, exclusive `xb` copying prevents replacement but is not atomic. Ordinary failures remove staging and any incomplete file created by that attempt; no power-loss durability guarantee is made.

A separate shared guard owns an append-only registry of every imported/opened/merged source, including removed sides/pages and sources referenced by loaded projects. All save entry points, including non-UI APIs, check resolved paths and `samefile` identities where available. Confirmation belongs to the GUI; the guard is checked again immediately before publication. Export workers must never silently replace an imported original. This is ordinary desktop file protection, not a guarantee against hostile filesystem changes.

Before publication, reopen/validate the staged output in the appropriate worker (PDF page count, dimensions, requested changes; PNG dimensions/DPI; project schema). A source changed since opening blocks export with a reload/relink choice; do not silently combine stale previews and new file contents.

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
- **Contextual controls:** Show side size/rounding controls in the side editor, and layout/text controls in the A4 tab. Keep one primary save action visible. Preserve the existing tabs; avoid a mandatory wizard that interrupts repeated corrections.
- **Keyboard and display access:** All actions remain available without drag-and-drop or icon recognition; retain labelled import/assignment buttons, visible focus and tab order. Use logical Qt sizes, accessible names, system-scaled fonts, and scrollable panels. Verify 1280 × 720 and Windows 125%/150% scaling before claiming support. Status must use text/icons as well as color.

### 3.3 Drag-and-Drop, Side Slot Cards & Badges

- **File drag-and-drop:** Accept local file drops (`.pdf`, `.jpg`, `.jpeg`, `.png`) on the main window and source list; reject unsupported types with a clear Russian status/warning message.
- **Thumbnail-to-slot drag-and-drop:** Allow dragging a source item from the left list directly onto the Front or Back slot card.
- **Visual Front/Back slot cards:** Replace the plain `QComboBox` above the editor with two clickable side cards («Лицевая сторона», «Обратная сторона») showing:
  - Mini thumbnail of the currently assigned source/cropped result (or empty drop placeholder),
  - Status pill («Готово», «Ошибка выделения», «Загрузка…», «Не назначена»),
  - Quick clear button (`×`) on each card.
- **Source list assignment badges:** Draw subtle overlay badges (`Лиц.`, `Обр.`) on thumbnails in the left `QListWidget` when assigned to a side.
- **Assignment behavior:** Replacing/removing a side is undoable; cancelling or failing a replacement retains the previous side. A late load cannot steal focus after the user switches sides. Distinguish «Готово к экспорту» from «Проверьте границы»: successful automatic detection is not proof of a correct crop.
- **Drop boundaries:** Accept local file URLs only; no network downloads. Validate mixed drops per file and retain the normal import limits/error handling. Thumbnail assignment uses an internal source ID, not arbitrary serialized objects supplied by external drag data.

### 3.4 Corner Editor Upgrades (`src/cardprint/ui/editor.py`)

All enhancements below operate purely in Qt scene/viewport overlays and never alter source or exported pixels:

- **Outside dimming mask:** Render a semi-transparent dark overlay (`QGraphicsPathItem` using an even-odd path: full scene rect minus the selected quadrilateral) so the user immediately sees what falls outside the crop.
- **Numbered corner handles (`1`–`4`):** Render index numbers inside/beside each `Handle` matching the clockwise order (`1 ↖`, `2 ↗`, `3 ↘`, `4 ↙`).
- **Live invalid-quad feedback:** Validate the quadrilateral on every `ItemPositionChange` / `ItemPositionHasChanged` (not only on mouse release) and switch the polygon pen/fill to red immediately while non-convex, self-intersecting, reversed, or too small.
- **Floating magnifier loupe:** While dragging a corner handle, render a circular/rounded-square `3×`–`4×` magnified snippet of the image around the cursor with a precision crosshair in an unobscured corner of `ImageView` (auto-flipping corner if the cursor approaches it).
  Sample the original rotated pixels through a bounded region request, not the reduced 2000-pixel display copy. A crosshair must refer to the same source-pixel coordinate as the handle, independently of zoom and device pixel ratio.
- **Keyboard precision & shortcuts:**
  - Arrow keys nudge the last-selected/focused handle by `1` source pixel (`Shift + Arrow` by `10` source pixels), clamped to `[0 .. width-1] × [0 .. height-1]`.
  - Global/view shortcuts: `Ctrl+O` (добавить файлы), `Ctrl+S` (сохранить PDF), `Ctrl+Z` (отменить), `Ctrl+Shift+Z` / `Ctrl+Y` (повторить), `R` (повернуть на 90°), `0` (вписать изображение), `Esc` (отмена ввода 4 точек / сброс активного инструмента).
  Scope letter/number shortcuts to the focused canvas; typing `R`, `0`, or arrows in text/spinbox/form controls must edit those controls normally. Route save/open to the active workspace; text widgets retain native text Undo while focused.

**Editing state (`T-18`):** Treat four-point entry and a handle drag as draft transactions. Suspend export at transaction start and show «Укажите угол N из 4» or «Редактирование выделения…». Four valid points / mouse release commit once; `Esc` restores the previous committed selection and export eligibility. Invalid completion keeps export blocked with a persistent explanation. Do not run a full-resolution warp on every mouse move. The inspector must check card identity and revision before opening; an already-open snapshot is labelled with its revision or marked stale when edits change.

### 3.5 Interactive A4 Preview & Crop Marks

- **Toggleable millimeter rulers & margin guides:** Optional top/left mm rulers along the A4 preview viewport and a dashed non-printing guide rectangle showing `margin_mm` bounds.
- **Drag-to-offset card group:** Allow dragging the card group directly on the A4 preview to update `offset_x_mm` and `offset_y_mm` (snapping cleanly to `0.0 mm` center axes within a small threshold, displaying live snap lines). Commits as a single Undo step on mouse release.
- **Optional crop marks («Метки реза»):** Boolean option on `Layout` (`crop_marks: bool = False`, default off). Generate shared millimeter line segments (~3 mm length, 1 mm offset, explicit 0.15 mm stroke width) for preview/PNG and vector PDF export. Never draw through either card or silently move/resize cards to make room. Omit segments that cannot fit safely within A4 without crossing another card and show a non-blocking explanation; guides and selection handles never export.

---

## 4. Parametric Undo / Redo (`src/cardprint/model.py`)

Because all CardPrint edits are non-destructive parameters applied to immutable originals, Undo/Redo stores **lightweight parameter snapshots** rather than pixel buffers:

- **Snapshot contents (CardPrint Mode):**
  - Per-side immutable state (`source` identity, `turns`, `points`, `radius`, `CardSize`) — keeping a reference to the shared read-only `original` array without copying it.
  - `Layout` parameters (`template`, `gap_mm`, `margin_mm`, `offset_x_mm`, `offset_y_mm`, `crop_marks`) and A4 `TextAnnotation` list.
- **Snapshot contents (PDF Editor Mode):** Per-page overlay list (`PdfTextOverlay`, `PdfWhiteoutOverlay`, `PdfStampOverlay`, `PdfInkOverlay`, `PdfImageOverlay`), form field values, and page order/rotation state.
  Each workspace owns its own history; PDF snapshots are added with `T-11` and extended by subsequent PDF tasks, not preimplemented in `T-05`.
- **Coalescing & limits:**
  - Bounded history stack (50 entries).
  - Entry count alone is insufficient: history may retain originals of replaced sides. Also cap history-only retained image/asset bytes (initial budget 256 MiB, shared buffers counted once), evict oldest history as needed, and disclose the reduced available depth. Do not retain derived render buffers in snapshots.
  - Continuous interactions (dragging a corner handle, dragging A4 offset, typing in a spinbox) push one undoable command upon commit/release rather than on every intermediate pixel/tick.
  - Assignment, removal, reset, annotation changes and page operations are undoable. Cancelled/no-op edits do not create entries; a new committed edit clears redo. Restoring old parameters issues **new monotonic task generations**, never resurrects old freshness tokens, and leaves the original-file protection registry intact.
- **UI integration:** Toolbar buttons «Отменить» (`Ctrl+Z`) and «Повторить» (`Ctrl+Y` / `Ctrl+Shift+Z`) enabled only when the corresponding stack is non-empty, with descriptive Russian tooltips.

**Unsaved work (`T-20`):** Maintain independent workspace state and content-based dirty checkpoints; zoom, theme and selection focus are not document edits. Switching modes preserves both sessions. Before close/new/open replaces work, offer Russian «Отмена» / «Выйти без сохранения» and a save/export action only if available. A cancelled/failed save does not clear dirty state. Track last-exported state separately from last-saved project: export does not save editable parameters, and PDF/PNG exports have separate revision records. An old export's Open button must say that it is an earlier result after further edits.

---

## 5. Cyrillic Font Infrastructure (`src/cardprint/fonts.py`)

ReportLab's standard PDF Type 1 fonts (`Helvetica`, `Times-Roman`, `Courier`) do **not** render Cyrillic glyphs cleanly across PDF viewers without TrueType embedding. Meanwhile, both ReportLab (A4 PDF export), Pillow (A4 preview & 300 DPI PNG), PyMuPDF (PDF Editor vector text insertion), and Qt (interactive text editors) need consistent Cyrillic font metrics.

- **Font registry (`src/cardprint/fonts.py`, no Qt-required core):**
  - Defines curated logical font families (e.g., `sans`, `sans-bold`, `serif`, `mono`) with Russian display names («Без засечек», «Полужирный», «С засечками», «Моноширинный»).
  - Bundles a redistributable TTF and its license for every exposed logical family under `src/cardprint/assets/fonts/`. Resolve resources through package APIs, not the working directory. System fonts may be an explicit later option; they must not silently change a project's layout across platforms.
  - Verifies actual entered glyphs, including Cyrillic, Ukrainian `ІіЇїЄєҐґ`, punctuation and stamp symbols. Missing glyphs block the affected annotation with a Russian explanation; never export replacement boxes silently. Synthetic bold is not a replacement for a bundled bold face.
  - Uses the exact same resolved font bytes in Qt, Pillow, ReportLab and the PDF process. ReportLab registration is serialized; PyMuPDF font objects stay inside the worker process. Subsetting is an explicit tested export step, not assumed to happen on insertion.
  - Initial text scope is left-to-right Latin/Cyrillic and explicit newlines. General bidirectional/complex-script shaping and automatic paragraph reflow are deferred. Font/asset licensing and packaged resource availability are checked before standalone distribution.

---

## 6. Writing Text & Watermarks on A4 Sheet (Mode 1)

Users frequently need to annotate copied documents on A4 (e.g., «Копия верна», date, recipient organization, «Лицевая сторона / Обратная сторона», or a diagonal semi-transparent watermark).

### 6.1 Data Model (`src/cardprint/geometry.py`)

Add an immutable `TextAnnotation` dataclass in millimeters (top-left A4 origin, matching `Placement`):

- `id: str`
- `text: str`
- `x_mm: float`, `y_mm: float` (first-line baseline anchor in A4 mm; alignment applies about this anchor)
- `font_family: str` (`"sans"`, `"sans-bold"`, `"serif"`, `"mono"`)
- `font_size_pt: float` (range e.g. `6.0`–`96.0`)
- `color_hex: str` (e.g., `"#111827"` or `"#6b7280"`)
- `angle_deg: float` (`-180` to `180`, e.g., `-30` for diagonal watermarks)
- `opacity: float` (`0.05`–`1.0`)
- `align: str` (`"left"`, `"center"`, `"right"`)
- `is_watermark: bool = False` (semantic preset tag only; not a second hidden stacking rule)

Include `annotations: tuple[TextAnnotation, ...] = ()` on `Sheet`.

### 6.2 Shared Preview & Export Rendering (`src/cardprint/export.py`)

- **Shared text layout:** Compute a backend-neutral `TextLayout` (line baselines, glyph advances, line height, alignment and transformed ink bounds) using the resolved font. Qt/Pillow/PDF consume that layout; default `QGraphicsTextItem` wrapping or independent backend measurements are not authoritative. First-line baseline is explicit; subsequent baselines advance downward by `1.2 × font_size_pt`, unless a later model field overrides it.
- **PDF (`_write_pdf`):** Map the baseline to `(x_mm * MM_TO_PT, (297 - y_mm) * MM_TO_PT)`. Positive model angles rotate clockwise in sheet coordinates; ReportLab therefore uses the negative angle. Draw selectable vector text with embedded fonts and explicit opacity, resetting graphics state for every annotation.
- **Preview & PNG (`render_sheet`):** Use the same layout at `pt * dpi / 72`, rotate about the same baseline anchor, apply opacity once, then composite onto white. Interactive handles are separate from the authoritative preview layer; do not draw editable text twice.
- **Bounds and stacking:** Require finite values and valid text/font/color/ranges. Validate rotated ink bounds for **all** annotations against A4 before export; no silent clipping or auto-shrink. Annotations paint in tuple order above cards; watermark tags do not reorder them. Card overlap is explicit and visible; a new ordinary caption starts in a blank sheet area when possible.

### 6.3 Interactive A4 Text UI

- On the «Предпросмотр A4» tab:
  - Toggleable **«Добавить текст»** tool (click anywhere on the sheet to place a new text block) and a **«Водяной знак»** quick-preset button (inserts a configurable diagonal semi-transparent watermark such as *«Копия верна · Для предъявления по месту требования»* with today's date).
  - Interactive handles and an inline/panel text editor allow drag-to-move, editing, rotation/delete, and right-panel controls. Use the shared `TextLayout` for committed display/export, not Qt's independent default text metrics. Resolve a date preset once on insertion; it must not change at midnight or on reopening/export.

---

## 7. PDF Editor Mode — Writing, Filling & Signing Arbitrary PDFs (Mode 2)

This planned mode adds overlays to PDFs without rasterizing existing vector pages. It is separate from card-image correction: importing a PDF page into CardPrint Mode still rasterizes that page at 300 DPI.

### 7.1 Design Scope & Honest Boundaries

- **Target support:** Readable, permitted single- or multi-page PDFs with the explicitly tested features below; do not promise every PDF variant:
  1. **Vector Cyrillic text blocks** (`insert_textbox` / `insert_text` via PyMuPDF with embedded TTF font subsetting).
  2. **Whiteout / masking rectangles** (opaque white vector rectangles beneath replacement text). This is a visual cover, **not redaction**: underlying text/images remain extractable. Show «Замазка скрывает область визуально, но не удаляет данные» in the tool and guide. Secure redaction is outside this roadmap.
  3. **Quick stamps:** `✓`, `✕`, formatted current date (`DD.MM.YYYY`), or custom short stamp.
  4. **Signatures:** Freehand mouse/stylus vector polylines and user-picked transparent PNGs. These are visual signatures, not cryptographic digital signatures. Assets remain in memory unless the user explicitly saves a PNG/project containing them; no automatic signature library or stroke smoothing that changes the captured signature.
  5. **Interactive `AcroForm` fields:** Detect existing PDF text fields and checkboxes/radio buttons, edit their values in-app, and write updated widget values cleanly on export.
  6. **Page operations:** Reorder pages, rotate individual pages (`90°` increments), delete pages, and merge pages from another local PDF.
- **Explicit non-goals:** Existing body-text reflow, XFA, JavaScript/calculation execution, PDF portfolios, signature creation/validation and PDF/A conformance preservation. Unsupported field types are read-only with an explanation. Ordinary page content remains vector; this does not imply byte-identical output or preservation of every document-level structure.
- **Preservation contract:** Without page-structure changes, edit a newly opened source document in the PDF process and save to a different staged path, preserving existing links, annotations, form widgets, outlines and attachments where supported. Page assembly requires an explicit copy/remap policy: never assume `insert_pdf` preserves document-level metadata, outlines, attachments or all links. Preserve tested features; detect unsupported structures before enabling that operation and explain any loss before export. No hidden flattening, JavaScript execution or attachment opening.
- **Signed/encrypted inputs:** Detect signature fields and explain that a rewritten copy can invalidate existing digital signatures; the original remains intact. Password unlock must respect editing permissions and export encryption policy (§7.3); authentication alone is not permission to edit.

### 7.2 Coordinate System & Core Engine (`src/cardprint/pdf_editor.py`)

- **No Qt dependency** in `pdf_editor.py`.
- **Coordinate system:** Store overlays in **unrotated PyMuPDF page coordinates**, in points with a top-left origin. `page.rect` reflects page rotation, whereas insertion/widget/text coordinates are unrotated; they are not interchangeable. Use explicit forward/inverse adapters from native page space through CropBox/rotation, rendered pixmap origin/scale and Qt viewport. Use `rotation_matrix` / `derotation_matrix` and the relevant page transforms; never manually add CropBox offsets twice. Test the adapters using nonzero CropBox/MediaBox origins and `/Rotate` 0/90/180/270. Page rotation changes the display transform, never clears or repeatedly transforms stored overlay coordinates.
- **Session model (`PdfDocumentSession`):**
  - Source registry with stable `source_id`, canonical path, size/content fingerprint and per-source in-memory credentials. Revalidate source identity before rereading/export; a changed/moved file requires reload/relink, never silently substitutes new content.
  - `pages: list[PdfPageState]`; each state has a stable `page_id` independent of list position, `(source_id, original_page_index)`, `extra_turns: int` (`0..3`), overlays and typed form values. Stable IDs survive reorder/delete/undo and distinguish repeated imported pages. Credentials and live PyMuPDF handles are not part of history, project JSON or logs.
  - Typed overlay dataclasses:
    - `PdfTextOverlay(id, x_pt, y_pt, width_pt, height_pt, text, font_family, font_size_pt, color_hex, align)`
    - `PdfWhiteoutOverlay(id, x_pt, y_pt, width_pt, height_pt, color_hex="#ffffff")`
    - `PdfStampOverlay(id, kind, x_pt, y_pt, size_pt, color_hex, label)`
    - `PdfInkOverlay(id, strokes_pt, stroke_width_pt, color_hex)`
    - `PdfImageOverlay(id, x_pt, y_pt, width_pt, height_pt, png_bytes)`
- **Lazy rendering:** Use the PDF process and bounded caches/queue in §2.2, with `(session_generation, source_id, page_id, revision, render_scale)` request identity. The GUI scene uses unrotated page points and a separate display transform for page rotation; the background raster must use that same adapter. A reopened document with the same path is a new session generation.
- **Text fit:** Validate finite nonempty boxes, actual glyph coverage and font metrics. If `insert_textbox` or another insertion API reports that text does not fit, block export with a Russian explanation; never silently drop, clip or shrink text. Arbitrary angles need a tested transform/layout path rather than assuming `rotate` accepts any degree. Initial PDF text uses the same LTR/font scope as §5.
- **Export (`export_edited_pdf`):**
  1. Capture an immutable session revision, revalidate all source fingerprints and check permissions/credentials in the PDF process.
  2. Open a fresh source document for overlay-only edits; assemble pages only when needed under the preservation contract. Build stable page/widget identity mappings rather than using mutable page positions or field names alone.
  3. Apply supported form values and regenerate appearances, checking visible Cyrillic output as well as persisted values. Use widget on/off/export states and radio groups; a boolean alone is insufficient. Duplicate field names from different merged sources stay independent, while genuinely linked widgets keep their intended linkage. Gate the exact copy APIs against `uv.lock` (currently PyMuPDF `1.28.2`, not assumed latest documentation).
  4. Compose a deterministic overlay order. Whiteout is below newly added text/stamps/ink/images but above original content; remaining items preserve their stored order. Embed verified fonts and explicitly subset where supported. Preview the same composed page/appearance path before save; Qt controls/selection decorations do not export.
  5. Save and validate via §2.3, retaining original protection for every source including deleted/merged pages. Failure leaves the destination and original untouched on atomic paths; ordinary exclusive-copy failure removes only its own incomplete output.

### 7.3 PDF Editor UI (`src/cardprint/ui/pdf_view.py`)

- **Three-pane layout matching Mode 1 ergonomics:**
  - **Left:** Page thumbnails rail (drag-and-drop reorder, rotate, delete, «Добавить PDF» to merge, page count indicator).
  - **Center:** Zoomable/pannable `QGraphicsView` showing the current page background image (`1 scene unit = 1 PDF point`) with crisp interactive vector overlay items on top (`Text`, `Whiteout`, `Stamp`, `Ink`, `Image`, plus clickable `AcroForm` input overlays).
  - **Right:** Contextual tool & inspector panel: tool selector («Выбор», «Текст», «Замазка», «Штамп», «Подпись», «Поля формы»), typography controls (font, size, color), quick stamp buttons (`✓`, `✕`, «Дата»), signature pad dialog, and «Сохранить новый PDF» primary button.
- **Password-protected PDFs (`T-14`):** Offer a Russian password prompt with retry/cancel. Keep per-source credentials in memory only. Do not remove export encryption implicitly: retain supported source protection for a single-source edited copy, or require an explicit user choice for a decrypted/newly encrypted copy. For merged sources with incompatible policies, require a destination policy before saving. If protection cannot be preserved without unavailable credentials, block that option. The initial Mode 1 password behavior remains unchanged unless `T-14` explicitly extends it and adds tests.
- **AcroForm milestone boundary (`T-13`):** First support text and checkbox fields, with Cyrillic appearance round trips. Choice/radio groups become editable only after dedicated fixtures pass; otherwise show read-only controls and the limitation. Identify widgets by source/page/field identity, not `dict[field_name]` alone. No automatic flattening of fields on save.

---

## 8. Later Productivity Enhancements (Milestone 4)

1. **Document presets & multi-card A4 layouts:**
   - Keep ID-1 and add clearly labelled user presets with explicit dimensions/orientation. Do not assign an unverified size to a passport spread, СНИЛС or old driving licence: document variants differ, and a closed-page size is not a spread size. Add a named document standard only with a recorded source/version or explicit user-specified dimensions.
   - Multi-card/page support is a separate step within `T-15`, after presets and a migration of the session/project schema. Support stable slot/page IDs and shared per-page margin validation, including page-local annotations and Undo. Single-page projects must retain identical output.
2. **Project persistence (`.cardprint` JSON):**
   - Save versioned, validated JSON with stable source/page/slot IDs, relative paths resolved against the project directory (absolute fallback only when needed), content fingerprints, parameters and annotation text. These files **can contain personal data** in paths and user-entered text; no claim that they are anonymous. Never store passwords, raw document pixels or credentials.
   - Load into a temporary session, validate finite numbers, modes, bounds and bounded input sizes, then swap only on success; a broken project must not destroy current work. Reject unknown newer schema versions with an explanation. Offer relink/reload for changed sources; require reauthentication for encrypted sources. Protect the loaded project itself and every source against unintended export overwrite; explicit project Save is allowed only through the project-save action. PDF Editor session persistence is deferred until its own schema is specified.
3. **Direct 100%-scale printing (`QPrinter` / `QPrintDialog`):**
   - Request true physical scale using shared millimeter/point-to-device transforms and disable application-level fit-to-page. Printer drivers and hardware margins still apply: compare against the selected device's printable area and explain clipping before printing; never silently shrink. `FullPage` is a coordinate choice, not a guarantee of borderless printing. Label native printing support as unverified until measured on a physical printer.
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

For documentation-only reviews, check links, task dependencies and consistency with the current code; full application tests are not required and must not be reported as rerun. For implementation, use targeted checks first and one relevant full run afterward.

Add behavioral checks for draft/committed editing, stale successes/errors, worker crash/close, save cancellation/failure, original aliases, text overflow/missing glyphs, rotated/cropped PDF pages, duplicate form names, and source changes. Compare PDF renders and PNG/preview placement against shared bounds (target: ≤0.2 mm at 300 DPI for text bounds, allowing antialiasing); do not assert cross-backend pixel identity for font rasterization.

Before a desktop release, separately record Python 3.12 tests, native Windows launch and worker `spawn`, Unicode paths, 125%/150% display scaling, and offline startup with installed dependencies. Convenient launching and standalone packaging remain follow-up work; do not claim installers, cross-platform or printer support from Linux offscreen tests. Bundled fonts/icons must load from an installed wheel outside the checkout.

## 10. Technical References and Review Notes

Reviewed on 2026-11-27 against the existing implementation and the installed/locked PyMuPDF `1.28.2`. Official documentation is reference material, not evidence that newer APIs are present in the lockfile:

- [PyMuPDF multiprocessing guidance](https://pymupdf.readthedocs.io/en/latest/recipes-multiprocessing.html): unsupported multithreaded use; own document objects in worker processes.
- [PyMuPDF Page API](https://pymupdf.readthedocs.io/en/latest/page.html): unrotated insertion coordinates, rotation/derotation transforms and page-box distinctions.
- [PyMuPDF Document API](https://pymupdf.readthedocs.io/en/latest/document.html): page-copy options, duplicate form names, font subsetting and signature/save limitations.
- [PyMuPDF Widget API](https://pymupdf.readthedocs.io/en/latest/widget.html): actual widget values, appearance updates and button states.

The reliability items in `T-18` reflect baseline behavior observed during review (partial four-point entry can export the old selection; the inspector callback lacks a revision guard). They remain planned fixes, not completed changes. The `T-19` process boundary addresses an additional implementation risk identified from the threading guidance; no concurrency crash is claimed to have been reproduced.

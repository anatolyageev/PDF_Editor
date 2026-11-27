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

## Recorded implementation verification

The following results were obtained **during the implementation iteration on
2026-11-26**. Documentation-only updates do not constitute a rerun of these checks.

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
- No session save/restore, Undo/Redo, multi-document batch processing, or direct
  printing. Reset to original and opening exported files are available.
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

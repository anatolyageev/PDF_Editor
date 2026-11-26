# Working on CardPrint

These instructions apply to the entire repository. CardPrint is a local desktop
application for arranging document copies on A4, distributed as the Python package
`cardprint`. A working first version already exists; extend the existing implementation.

## Start here in a new chat

1. Read [README.md](README.md) for the user workflow, setup, and limitations.
2. Read [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) for implemented features,
   recorded verification results, and unfinished or unsupported capabilities.
3. Before changing code, read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for
   coordinate systems, data flow, background tasks, and extension points.
4. Inspect the current affected modules and local changes. Documentation records
   the state on its stated date; it is not evidence of a fresh test run.

## Required application properties

- Process documents locally. Do not add document uploads, telemetry, cloud APIs,
  LLMs, or generative processing.
- Never modify originals. Recompute edits from the original pixel array;
  a processed image must not become the new original.
- Preserve card content: no automatic retouching, thresholding, denoising,
  sharpening, or color correction. Necessary perspective interpolation is allowed.
- Keep the external background pure white: RGB `(255, 255, 255)`. Do not remove
  shadows inside the card as part of external background removal.
- Use shared geometry for preview and export. Keep source pixels, Qt viewport
  coordinates, millimeters, and PDF points distinct.
- Keep manual corner selection available regardless of automatic detection quality.
- Run expensive operations in background tasks; create widgets and QPixmap in the
  GUI thread. Stale task results must not overwrite newer edits.
- Do not silently resize cards, place them beyond page margins, or overwrite files.
  Protect imported originals even after their thumbnail or side assignment is removed.
- Keep the user interface and user-facing messages in Russian. Use artificial
  cards in tests; never add user documents to fixtures, logs, or the repository.
- OCR, personal data recognition, accounts, servers, and databases are outside the
  first-version scope. Add features in response to user tasks, not an ideas list.

## Commands and verification

```bash
uv sync --dev
uv run cardprint
uv run pytest -q
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
```

Dependencies are declared in `pyproject.toml` and locked in `uv.lock`.
Qt tests use `offscreen` by default. To exercise a real display:

```bash
QT_QPA_PLATFORM=wayland uv run pytest -q tests/test_ui.py
QT_QPA_PLATFORM=wayland uv run python scripts/gui_smoke.py
```

For X11, use `xcb` with the required system libraries installed. On Windows/macOS,
remove the environment-variable prefix. Application startup and test startup use
different Qt backend selection rules; see the architecture document.

Run targeted tests first, then the full suite once before completing a code change.
Do not repeat unchanged successful checks without a reason. For documentation-only
changes, check links and consistency with the code. Do not repeatedly poll healthy
background processes just to report progress percentages; report meaningful results
and failures.

## Handoff maintenance

- Keep agent/developer documentation (`AGENTS.md` and the documents under `docs/`)
  in English. Keep the user guide in `README.md` and the application UI in Russian.
- After substantial changes, update `docs/PROJECT_STATUS.md` with the date, outcome,
  checks actually performed, and remaining limitations or unfinished steps.
- Update `docs/ARCHITECTURE.md` when modules, coordinate systems, or data flow change.
- Update `README.md` when the user workflow, setup, or limitations change.
- Distinguish implemented features from proposals and verified behavior from
  assumptions. Do not invent commits, releases, or test results.
- Make handoffs independent of chat history, machine-specific absolute paths, and
  ignored `artifacts/` files. Describe a reproducible workflow instead.

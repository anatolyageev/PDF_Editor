import subprocess
import sys

import numpy as np
from PIL import Image

import cardprint.model as model
import cardprint.ui.window as window
from cardprint.export import SourceFingerprint
from cardprint.geometry import CardSize, Layout
from cardprint.imaging import Source, full_quad


def test_card_source_size_across_turns(tmp_path):
    array = np.zeros((540, 856, 3), dtype=np.uint8)
    array.flags.writeable = False
    source = Source(path=tmp_path / "dummy.png", page=None, thumbnail=Image.new("RGB", (10, 10)))
    card = model.Card(source=source, original=array, points=full_quad(856, 540))
    for turns in range(6):
        card.turns = turns
        expected = (540, 856) if turns % 2 else (856, 540)
        assert card.source_size == expected


def test_card_defaults_and_fields(tmp_path):
    path = tmp_path / "card.png"
    path.write_bytes(b"synthetic")
    fp = SourceFingerprint(path=path, size=9, mtime_ns=1, sha256="0" * 64)
    source = Source(path=path, page=None, thumbnail=Image.new("RGB", (20, 12)), fingerprint=fp)
    array = np.zeros((100, 160, 3), dtype=np.uint8)
    array.flags.writeable = False
    points = full_quad(160, 100)
    card = model.Card(source=source, original=array, points=points)

    assert card.turns == 0
    assert card.radius == 0
    assert card.size == CardSize()
    assert card.revision == 0
    assert card.ready_revision == -1
    assert card.cropped is None
    assert card.rendered is None
    assert card.display is None
    assert card.display_turns == 0
    assert card.error is None
    assert card.source.fingerprint == fp


def test_model_imports_without_pyside6():
    code = (
        "import sys, cardprint.model; "
        "assert 'PySide6' not in sys.modules, list(sys.modules); "
        "assert 'pymupdf' not in sys.modules, list(sys.modules)"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


def test_window_uses_model_card():
    assert window.Card is model.Card
    assert window.SIDE_NAMES is model.SIDE_NAMES


def _make_card(tmp_path, name: str, w: int = 160, h: int = 100) -> model.Card:
    path = tmp_path / name
    path.write_bytes(name.encode())
    source = Source(path=path, page=None, thumbnail=Image.new("RGB", (20, 12)))
    array = np.zeros((h, w, 3), dtype=np.uint8)
    array.flags.writeable = False
    return model.Card(source=source, original=array, points=full_quad(w, h))


def test_undo_stack_multi_step_noop_and_redo_clearing(tmp_path):
    card = _make_card(tmp_path, "front.png")
    stack = model.UndoStack(model.WorkspaceSnapshot((), Layout()))
    assert not stack.can_undo and not stack.can_redo

    snap1 = model.WorkspaceSnapshot.from_state({"front": card}, Layout())
    assert stack.push(snap1, "Назначение").recorded
    assert stack.undo_depth == 1

    # No-op push records nothing.
    assert not stack.push(snap1, "Повтор").recorded
    assert stack.undo_depth == 1

    card.turns = 1
    card.radius = 4.0
    snap2 = model.WorkspaceSnapshot.from_state({"front": card}, Layout(gap_mm=22))
    assert stack.push(snap2, "Поворот и отступ").recorded
    assert stack.undo_depth == 2

    undone = stack.undo()
    assert undone is not None
    assert undone[0] == snap1
    assert undone[1] == "Поворот и отступ"
    assert stack.can_redo

    # No-op push does not clear redo stack.
    assert not stack.push(snap1, "Без изменений").recorded
    assert stack.can_redo

    # Redo restores snap2.
    redone = stack.redo()
    assert redone is not None and redone[0] == snap2
    assert not stack.can_redo

    # Undo then commit a new edit clears redo.
    stack.undo()
    card.turns = 2
    snap3 = model.WorkspaceSnapshot.from_state({"front": card}, Layout(gap_mm=15))
    assert stack.push(snap3, "Новая правка").recorded
    assert not stack.can_redo and stack.redo_depth == 0


def test_undo_stack_shares_originals_and_excludes_derived_buffers(tmp_path):
    card = _make_card(tmp_path, "card.png")
    card.cropped = Image.new("RGB", (50, 30), "white")
    card.rendered = Image.new("RGB", (50, 30), "white")
    card.display = Image.new("RGB", (25, 15), "white")
    snap = model.WorkspaceSnapshot.from_state({"front": card}, Layout())
    card_snap = snap.card_map()["front"]

    assert card_snap.original is card.original
    for attr in ("cropped", "rendered", "display"):
        assert not hasattr(card_snap, attr)

    restored = model.Card.from_snapshot(card_snap, revision=7)
    assert restored.original is card.original
    assert restored.cropped is None and restored.rendered is None and restored.display is None
    assert restored.revision == 7 and restored.ready_revision == -1


def test_undo_stack_enforces_entry_cap_and_history_only_byte_budget(tmp_path):
    card = _make_card(tmp_path, "shared.png", w=100, h=100)  # 30_000 bytes
    stack = model.UndoStack(model.WorkspaceSnapshot(), max_entries=3, max_bytes=50_000)

    # Multiple parameter edits on the same shared buffer keep history_only_bytes == 0.
    for r in range(5):
        card.radius = float(r + 1)
        res = stack.push(
            model.WorkspaceSnapshot.from_state({"front": card}, Layout()), f"r={r + 1}"
        )
        assert res.recorded
        assert stack.history_only_bytes == 0
    assert stack.undo_depth == 3
    assert stack.evicted_count == 2
    assert stack.evicted_for_memory_count == 0

    # Replace card with different 30_000-byte originals -> active buffer excluded,
    # history-only buffers counted once each; > 50_000 triggers memory eviction.
    card_b = _make_card(tmp_path, "b.png", w=100, h=100)
    res_b = stack.push(model.WorkspaceSnapshot.from_state({"front": card_b}, Layout()), "B")
    assert res_b.recorded
    assert stack.history_only_bytes == 30_000  # card A retained in history once

    card_c = _make_card(tmp_path, "c.png", w=100, h=100)
    res_c = stack.push(model.WorkspaceSnapshot.from_state({"front": card_c}, Layout()), "C")
    assert res_c.recorded
    assert res_c.evicted_for_memory >= 1
    assert stack.history_only_bytes <= 50_000
    assert stack.evicted_for_memory_count >= 1

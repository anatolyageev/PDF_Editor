import subprocess
import sys

import numpy as np
from PIL import Image

import cardprint.model as model
import cardprint.ui.window as window
from cardprint.export import SourceFingerprint
from cardprint.geometry import CardSize
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

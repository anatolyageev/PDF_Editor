import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from cardprint.imaging import full_quad
from cardprint.ui.editor import ImageView
from cardprint.ui.window import MainWindow


def wait_ready(qtbot, window):
    qtbot.waitUntil(
        lambda: (
            window.sheet is not None and not window.tasks and not window.layout_timer.isActive()
        ),
        timeout=20000,
    )


def test_actual_editor_coordinates_survive_resize_zoom_pan_and_drag(qtbot):
    view = ImageView(editable=True)
    qtbot.addWidget(view)
    view.resize(700, 500)
    # Downsampled display: scene coordinates must still refer to original pixels.
    view.set_image(Image.new("RGB", (600, 400), "white"), (3000, 2000), full_quad(3000, 2000))
    view.show()
    qtbot.waitExposed(view)
    original = view.points()
    for zoom, size in [(2.3, (850, 520)), (0.6, (650, 460))]:
        view.zoom(zoom)
        view.resize(*size)
        view.horizontalScrollBar().setValue(view.horizontalScrollBar().value() + 35)
        view.verticalScrollBar().setValue(view.verticalScrollBar().value() + 20)
        source = (1432.3, 812.7)
        viewport = view.source_to_viewport(source)
        assert view.viewport_to_source(QPointF(*viewport)) == pytest.approx(source, abs=1e-8)
        assert view.points() == original
    view.fit_image()
    # Move a real graphics handle through Qt events, then validate source coordinates.
    start = view.mapFromScene(view.handles[0].pos())
    destination = view.mapFromScene(QPointF(180, 150))
    qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(view.viewport(), destination, delay=30)
    qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=destination)
    assert view.points()[0] == pytest.approx((180, 150), abs=8)
    assert view.points()[1:] == original[1:]


def test_full_gui_import_assign_edit_preview_export_and_original_guard(
    qtbot, tmp_path, synthetic_card, monkeypatch
):
    paths = [tmp_path / "front.png", tmp_path / "back.png"]
    synthetic_card.save(paths[0])
    synthetic_card.transpose(Image.Transpose.FLIP_LEFT_RIGHT).save(paths[1])
    originals = [p.read_bytes() for p in paths]
    window = MainWindow()
    qtbot.addWidget(window)
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    window.show()
    window.import_paths(paths)
    qtbot.waitUntil(lambda: window.sources.count() == 2 and not window.tasks, timeout=20000)
    for row, side in enumerate(("front", "back")):
        window.sources.setCurrentRow(row)
        window.assign_selected(side)
        wait_ready(qtbot, window)
    assert len(window.sheet.placements) == 2
    window.radius.setValue(3)
    window.rotate()
    window.size_mode.setCurrentIndex(1)
    wait_ready(qtbot, window)
    assert window.cards["back"].display_turns == 1
    assert window.cards["back"].rendered.size[0] < window.cards["back"].rendered.size[1]
    window.reset_card()
    wait_ready(qtbot, window)
    window.gap.setValue(21)
    wait_ready(qtbot, window)
    first, second = window.sheet.placements
    assert second.y_mm - first.y_mm - first.height_mm == pytest.approx(21)
    window.offset_x.setValue(150)
    qtbot.waitUntil(lambda: not window.layout_timer.isActive())
    assert window.sheet is None and not window.pdf_button.isEnabled()
    assert "выходят" in window.layout_message.text()
    window.offset_x.setValue(0)
    wait_ready(qtbot, window)
    window.tabs.setCurrentIndex(1)
    for kind in ("pdf", "png"):
        target = tmp_path / f"sheet.{kind}"
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", lambda *args, p=target, **kw: (str(p), "")
        )
        window.save(kind)
        qtbot.waitUntil(lambda: target.exists() and not window.tasks, timeout=20000)
        assert window.export_path == target
    assert window.open_button.isVisible()
    assert "100%" in window.export_message.text()
    assert not errors
    window.inspect_card()
    qtbot.waitUntil(lambda: bool(window.findChildren(QDialog)) and not window.tasks)
    inspector = window.findChildren(QDialog)[0]
    inspected_view = inspector.findChild(ImageView)
    assert (inspected_view.source_width, inspected_view.source_height) == window.cards[
        "back"
    ].rendered.size
    inspector.close()
    saved = target.read_bytes()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    window.save("png")
    assert not window.tasks and target.read_bytes() == saved
    # Even removed thumbnails and cleared sides cannot make originals export targets.
    window.sources.setCurrentRow(0)
    window.remove_source()
    window.side_combo.setCurrentIndex(0)
    front_pixels = window.cards["front"].original
    window.remove_side()
    wait_ready(qtbot, window)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: (str(paths[0]), ""))
    window.save("png")
    assert "оригинал" in errors[-1]
    assert [p.read_bytes() for p in paths] == originals
    np.testing.assert_array_equal(front_pixels, np.array(synthetic_card))
    window.close()


def test_invalid_selection_recovers_and_four_point_mode_works(qtbot, tmp_path, synthetic_card):
    path = tmp_path / "test.png"
    synthetic_card.save(path)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.import_paths([path])
    qtbot.waitUntil(lambda: window.sources.count() == 1 and not window.tasks, timeout=20000)
    window.assign_selected("front")
    wait_ready(qtbot, window)
    window.selection_changed(((10, 10), (800, 500), (800, 10), (10, 500)))
    qtbot.waitUntil(lambda: not window.tasks)
    assert window.sheet is None
    assert "пересечений" in window.layout_message.text()
    window.gap.setValue(22)
    qtbot.waitUntil(lambda: not window.layout_timer.isActive())
    assert "пересечений" in window.layout_message.text()
    window.editor.begin_selection()
    points = ((15, 15), (830, 15), (830, 520), (15, 520))
    for point in points:
        viewport = window.editor.mapFromScene(QPointF(*point))
        qtbot.mouseClick(window.editor.viewport(), Qt.MouseButton.LeftButton, pos=viewport)
    wait_ready(qtbot, window)
    for actual, expected in zip(window.cards["front"].points, points):
        assert actual == pytest.approx(expected, abs=3)
    window.close()

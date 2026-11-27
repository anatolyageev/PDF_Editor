import os
import signal
from dataclasses import dataclass

import numpy as np
import pymupdf
import pytest
from PIL import Image
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from cardprint.imaging import full_quad
from cardprint.pdf_worker import PdfWorkerClient
from cardprint.ui.editor import ImageView
from cardprint.ui.window import InspectorDialog, MainWindow


@dataclass
class PendingTask:
    key: int
    function: object
    success: object
    error: object


class ManualTaskController:
    """Deterministic task queue with controlled completion order (no sleep races)."""

    def __init__(self, window: MainWindow):
        self.window = window
        self.pending: list[PendingTask] = []
        self._next_key = 1
        window.submit = self.submit

    def submit(self, function, success, error=None):
        if self.window._closing or self.window._closed:
            return
        key = self._next_key
        self._next_key += 1
        task = PendingTask(key, function, success, error or self.window.show_error)
        self.pending.append(task)
        self.window.tasks[key] = task
        self.window.progress.show()

    def complete_at(self, index: int = 0):
        task = self.pending.pop(index)
        try:
            result = task.function()
        except Exception as exc:
            task.error(str(exc) or "Не удалось выполнить операцию.")
        else:
            task.success(result)
        finally:
            self.window.task_finished(task.key)

    def fail_at(self, index: int, message: str):
        task = self.pending.pop(index)
        try:
            task.error(message)
        finally:
            self.window.task_finished(task.key)

    def succeed_at(self, index: int, result):
        task = self.pending.pop(index)
        try:
            task.success(result)
        finally:
            self.window.task_finished(task.key)

    def drain(self):
        while self.pending or self.window.layout_timer.isActive():
            while self.pending:
                self.complete_at(0)
            if self.window.layout_timer.isActive():
                self.window.layout_timer.stop()
                self.window.refresh_sheet()


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


def _setup_front_with_controller(qtbot, tmp_path, synthetic_card):
    path = tmp_path / "card.png"
    synthetic_card.save(path)
    window = MainWindow()
    qtbot.addWidget(window)
    controller = ManualTaskController(window)
    window.show()
    qtbot.waitExposed(window)
    window.import_paths([path])
    controller.drain()
    window.sources.setCurrentRow(0)
    window.assign_selected("front")
    controller.drain()
    assert window.sheet is not None
    assert window.pdf_button.isEnabled() and window.png_button.isEnabled()
    return window, controller


def test_four_point_draft_suspends_export_and_escape_restores(
    qtbot, tmp_path, synthetic_card, monkeypatch
):
    window, controller = _setup_front_with_controller(qtbot, tmp_path, synthetic_card)
    warnings = []
    dialogs_opened = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: dialogs_opened.append(True) or ("", ""),
    )

    committed = window.cards["front"].points
    window.pick_points()
    assert window.sheet is None
    assert not window.pdf_button.isEnabled()
    assert not window.png_button.isEnabled()
    assert "угол 1 из 4" in window.layout_message.text()

    # Exporting during a draft selection must be blocked before opening a file dialog.
    window.save("pdf")
    assert warnings and not dialogs_opened and not controller.pending

    partial = ((15, 15), (830, 15))
    for idx, pt in enumerate(partial, start=2):
        viewport = window.editor.mapFromScene(QPointF(*pt))
        qtbot.mouseClick(window.editor.viewport(), Qt.MouseButton.LeftButton, pos=viewport)
        assert f"угол {idx} из 4" in window.layout_message.text()
    assert not controller.pending

    # Unrelated layout changes during a draft must keep export suspended.
    window.gap.setValue(20)
    window.refresh_sheet()
    assert window.sheet is None and not window.pdf_button.isEnabled()
    assert "угол 3 из 4" in window.layout_message.text()

    # Esc cancels partial entry and restores committed selection + export eligibility.
    qtbot.keyClick(window.editor, Qt.Key.Key_Escape)
    assert not window.is_drafting()
    assert window.cards["front"].points == committed
    controller.drain()
    assert window.sheet is not None
    assert window.pdf_button.isEnabled() and window.png_button.isEnabled()

    # Valid 4-point completion commits exactly once on the 4th click.
    window.pick_points()
    valid_points = ((20, 20), (820, 20), (820, 510), (20, 510))
    for i, pt in enumerate(valid_points):
        viewport = window.editor.mapFromScene(QPointF(*pt))
        qtbot.mouseClick(window.editor.viewport(), Qt.MouseButton.LeftButton, pos=viewport)
        if i < 3:
            assert len(controller.pending) == 0
    assert len(controller.pending) == 1
    controller.drain()
    assert window.sheet is not None and window.pdf_button.isEnabled()
    for actual, expected in zip(window.cards["front"].points, valid_points):
        assert actual == pytest.approx(expected, abs=3)

    # Invalid 4-point completion blocks export with a persistent explanation;
    # starting and Esc-cancelling another draft restores that persistent error.
    window.pick_points()
    crossing = ((20, 20), (820, 510), (820, 20), (20, 510))
    for pt in crossing:
        viewport = window.editor.mapFromScene(QPointF(*pt))
        qtbot.mouseClick(window.editor.viewport(), Qt.MouseButton.LeftButton, pos=viewport)
    assert len(controller.pending) == 1
    controller.drain()
    assert window.sheet is None
    assert not window.pdf_button.isEnabled()
    assert "пересечений" in window.layout_message.text()

    window.pick_points()
    qtbot.mouseClick(
        window.editor.viewport(),
        Qt.MouseButton.LeftButton,
        pos=window.editor.mapFromScene(QPointF(30, 30)),
    )
    assert (
        "угол 1 из 4" in window.layout_message.text()
        or "угол 2 из 4" in window.layout_message.text()
    )
    qtbot.keyClick(window.editor, Qt.Key.Key_Escape)
    controller.drain()
    assert window.sheet is None and not window.pdf_button.isEnabled()
    assert "пересечений" in window.layout_message.text()
    window.close()


def test_handle_drag_suspends_export_escape_cancels_and_release_commits_once(
    qtbot, tmp_path, synthetic_card
):
    window, controller = _setup_front_with_controller(qtbot, tmp_path, synthetic_card)
    origin = window.cards["front"].points
    rev_before = window.cards["front"].revision

    start = window.editor.mapFromScene(window.editor.handles[0].pos())
    mid = window.editor.mapFromScene(QPointF(110, 90))
    dest = window.editor.mapFromScene(QPointF(160, 120))

    # Press begins drag transaction and immediately suspends export.
    qtbot.mousePress(window.editor.viewport(), Qt.MouseButton.LeftButton, pos=start)
    assert window.is_drafting()
    assert window.sheet is None
    assert not window.pdf_button.isEnabled() and not window.png_button.isEnabled()
    assert not window.inspect_button.isEnabled()
    assert "Редактирование выделения" in window.layout_message.text()
    assert "Угол 1 из 4" in window.layout_message.text()

    # Mouse moves must not enqueue full-resolution card renders.
    qtbot.mouseMove(window.editor.viewport(), mid)
    qtbot.mouseMove(window.editor.viewport(), dest)
    assert len(controller.pending) == 0

    # Esc during drag restores handles and triggers sheet preview restore only;
    # late mouseRelease must not enqueue any card render task.
    qtbot.keyClick(window.editor, Qt.Key.Key_Escape)
    assert not window.is_drafting()
    assert window.editor.points() == origin
    pending_after_cancel = len(controller.pending)
    assert pending_after_cancel == 1
    qtbot.mouseRelease(window.editor.viewport(), Qt.MouseButton.LeftButton, pos=dest)
    assert len(controller.pending) == pending_after_cancel
    controller.drain()
    assert window.cards["front"].revision == rev_before
    assert window.sheet is not None and window.pdf_button.isEnabled()

    # Press + release without moving is a no-op that restores export without bumping revision.
    qtbot.mousePress(window.editor.viewport(), Qt.MouseButton.LeftButton, pos=start)
    assert window.sheet is None
    qtbot.mouseRelease(window.editor.viewport(), Qt.MouseButton.LeftButton, pos=start)
    assert len(controller.pending) == 1
    controller.drain()
    assert window.cards["front"].revision == rev_before
    assert window.sheet is not None and window.pdf_button.isEnabled()

    # Dragging to a new valid position commits once on release.
    qtbot.mousePress(window.editor.viewport(), Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(window.editor.viewport(), dest)
    assert len(controller.pending) == 0
    qtbot.mouseRelease(window.editor.viewport(), Qt.MouseButton.LeftButton, pos=dest)
    assert len(controller.pending) == 1
    controller.drain()
    assert window.cards["front"].points[0] == pytest.approx((160, 120), abs=8)
    assert window.sheet is not None and window.pdf_button.isEnabled()

    # Dragging handle 0 past opposite corner commits once and blocks export with error.
    bad_start = window.editor.mapFromScene(window.editor.handles[0].pos())
    bad_dest = window.editor.mapFromScene(QPointF(850, 530))
    qtbot.mousePress(window.editor.viewport(), Qt.MouseButton.LeftButton, pos=bad_start)
    qtbot.mouseMove(window.editor.viewport(), bad_dest)
    qtbot.mouseRelease(window.editor.viewport(), Qt.MouseButton.LeftButton, pos=bad_dest)
    assert len(controller.pending) == 1
    controller.drain()
    assert window.sheet is None and not window.pdf_button.isEnabled()
    assert window.cards["front"].error is not None
    window.close()


def test_inspector_rejects_stale_success_and_error_across_edit_replace_remove_and_close(
    qtbot, tmp_path, synthetic_card, monkeypatch
):
    window, controller = _setup_front_with_controller(qtbot, tmp_path, synthetic_card)
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))

    # 1. Edit card before inspector task finishes (stale success and stale error).
    window.inspect_card()
    assert len(controller.pending) == 1
    window.radius.setValue(3)
    controller.complete_at(0)  # Stale inspector success
    assert not window.findChildren(InspectorDialog)
    controller.drain()

    window.inspect_card()
    assert len(controller.pending) == 1
    window.radius.setValue(4)
    controller.fail_at(0, "Устаревшая ошибка инспектора")
    assert not window.findChildren(InspectorDialog)
    assert not errors
    controller.drain()

    # 2. Replace side before inspector finishes.
    window.inspect_card()
    assert len(controller.pending) == 1
    window.sources.setCurrentRow(0)
    window.assign_selected("front")
    controller.complete_at(0)  # Stale inspector completes before replacement load finishes
    assert not window.findChildren(InspectorDialog)
    controller.drain()

    # 3. Remove side before inspector finishes (both success and error).
    window.inspect_card()
    window.remove_side()
    controller.complete_at(0)
    assert not window.findChildren(InspectorDialog)
    controller.drain()

    window.sources.setCurrentRow(0)
    window.assign_selected("front")
    controller.drain()
    window.inspect_card()
    window.remove_side()
    controller.fail_at(0, "Ошибка удалённой стороны")
    assert not window.findChildren(InspectorDialog)
    assert not errors
    controller.drain()

    # 4. Close window before inspector finishes (stale success and stale error).
    window.sources.setCurrentRow(0)
    window.assign_selected("front")
    controller.drain()
    window.inspect_card()
    assert len(controller.pending) == 1
    window.close()
    assert window._closing and not window._closed
    controller.succeed_at(0, QImage(10, 10, QImage.Format.Format_RGB888))
    assert not window.findChildren(InspectorDialog)
    assert window._closed

    # Also check stale error path during window close on a fresh window.
    window2, controller2 = _setup_front_with_controller(qtbot, tmp_path, synthetic_card)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    window2.inspect_card()
    window2.close()
    assert window2._closing and not window2._closed
    controller2.fail_at(0, "Ошибка после закрытия")
    assert not errors
    assert not window2.findChildren(InspectorDialog)
    assert window2._closed


def test_open_inspector_marks_itself_stale_on_edit_replace_and_remove(
    qtbot, tmp_path, synthetic_card
):
    window, controller = _setup_front_with_controller(qtbot, tmp_path, synthetic_card)

    # Open inspector at current revision -> active & labelled with revision.
    rev1 = window.cards["front"].revision
    window.inspect_card()
    controller.drain()
    dialogs = window.findChildren(InspectorDialog)
    assert len(dialogs) == 1
    dialog1 = dialogs[0]
    assert not dialog1.stale
    assert f"#{rev1}" in dialog1.windowTitle()
    assert "Актуальный" in dialog1.status_label.text()

    # Editing side immediately marks the open snapshot stale.
    window.radius.setValue(4)
    assert dialog1.stale
    assert "устарел" in dialog1.windowTitle()
    assert "устарел" in dialog1.status_label.text().lower()
    dialog1.close()
    controller.drain()

    # Replacing side marks an open snapshot stale immediately.
    window.inspect_card()
    controller.drain()
    dialog2 = window.findChildren(InspectorDialog)[-1]
    assert not dialog2.stale
    window.sources.setCurrentRow(0)
    window.assign_selected("front")
    assert dialog2.stale
    assert "устарел" in dialog2.status_label.text().lower()
    dialog2.close()
    controller.drain()

    # Removing side marks an open snapshot stale immediately.
    window.inspect_card()
    controller.drain()
    dialog3 = window.findChildren(InspectorDialog)[-1]
    assert not dialog3.stale
    window.remove_side()
    assert dialog3.stale
    assert "убрана" in dialog3.status_label.text().lower()
    dialog3.close()
    controller.drain()
    window.close()


def _make_multipage_pdf(path, pages: int = 10):
    with pymupdf.open() as doc:
        for i in range(pages):
            page = doc.new_page(width=220, height=140)
            page.draw_rect(
                pymupdf.Rect(20, 20, 200, 120),
                color=(0.1, 0.25, 0.5),
                fill=(0.88, 0.92, 0.96),
            )
            page.insert_text((35, 65), f"CARD {i + 1}", fontsize=16, color=(0.1, 0.1, 0.1))
        doc.save(path)


def test_pdf_batched_import_and_overlapping_side_replacement(qtbot, tmp_path):
    pdf = tmp_path / "ten_pages.pdf"
    _make_multipage_pdf(pdf, pages=10)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.import_paths([pdf])
    qtbot.waitUntil(lambda: window.sources.count() == 10 and not window.tasks, timeout=20000)

    # Rapidly trigger overlapping PDF side loads; final requested page must win.
    for row in (0, 3, 7):
        window.sources.setCurrentRow(row)
        window.assign_selected("front")
    wait_ready(qtbot, window)
    assert window.cards["front"].source.page == 7
    assert len(window.sheet.placements) == 1
    window.close()
    qtbot.waitUntil(lambda: window._closed, timeout=10000)
    assert not window.pdf_worker.is_alive


def test_pdf_worker_crash_releases_busy_state_and_recovers_in_gui(qtbot, tmp_path, monkeypatch):
    pdf = tmp_path / "two_pages.pdf"
    _make_multipage_pdf(pdf, pages=2)
    client = PdfWorkerClient()
    window = MainWindow(pdf_worker=client)
    qtbot.addWidget(window)
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    try:
        window.show()
        window.import_paths([pdf])
        qtbot.waitUntil(lambda: window.sources.count() == 2 and not window.tasks, timeout=20000)
        pid1 = client.worker_pid
        assert pid1 is not None
        os.kill(pid1, signal.SIGKILL)
        os.waitpid(pid1, 0)

        window.sources.setCurrentRow(0)
        window.assign_selected("front")
        qtbot.waitUntil(lambda: not window.tasks, timeout=20000)
        assert errors and "аварийно" in errors[-1]
        assert not window.progress.isVisible()
        assert "front" not in window.cards

        # Subsequent retry spawns a fresh PDF worker and succeeds.
        window.assign_selected("front")
        wait_ready(qtbot, window)
        assert window.cards["front"].source.page == 0
        assert client.worker_pid is not None and client.worker_pid != pid1
        window.close()
        qtbot.waitUntil(lambda: window._closed, timeout=10000)
    finally:
        client.shutdown()


def test_window_close_cancels_queued_pdf_work_and_shuts_down_worker(qtbot, tmp_path):
    pdf = tmp_path / "twenty_pages.pdf"
    _make_multipage_pdf(pdf, pages=20)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.import_paths([pdf])
    qtbot.waitUntil(lambda: window.sources.count() >= 1, timeout=10000)
    for row in range(min(5, window.sources.count())):
        window.sources.setCurrentRow(row)
        window.assign_selected("front")
    window.close()
    qtbot.waitUntil(lambda: window._closed, timeout=15000)
    assert not window.tasks
    assert not window.pdf_worker.is_alive

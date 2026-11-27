import os
import signal
import threading
from dataclasses import dataclass

import numpy as np
import pymupdf
import pytest
from PIL import Image
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox, QPushButton

from cardprint.export import OriginalGuard
from cardprint.imaging import full_quad
from cardprint.pdf_worker import PdfWorkerClient
from cardprint.ui.editor import ImageView
from cardprint.ui.icons import ICONS, render_svg_icon
from cardprint.ui.theme import (
    DARK_PALETTE,
    LIGHT_PALETTE,
    THEME_DARK,
    THEME_LIGHT,
    build_stylesheet,
)
from cardprint.ui.window import BASE_WINDOW_TITLE, InspectorDialog, MainWindow

REAL_PROMPT_UNSAVED = MainWindow._prompt_unsaved_changes


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
    qtbot.waitUntil(lambda: not window.tasks, timeout=20000)
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
    qtbot.waitUntil(lambda: not window.tasks, timeout=20000)
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
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow, timeout=2000)
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


def test_gui_blocks_export_and_assign_when_source_changed_until_reload(
    qtbot, tmp_path, synthetic_card, monkeypatch
):
    path = tmp_path / "front.png"
    synthetic_card.save(path)
    window = MainWindow()
    qtbot.addWidget(window)
    errors = []
    dialogs_opened = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: dialogs_opened.append(True) or ("", ""),
    )
    window.show()
    window.import_paths([path])
    qtbot.waitUntil(lambda: window.sources.count() == 1 and not window.tasks, timeout=20000)
    window.sources.setCurrentRow(0)
    window.assign_selected("front")
    wait_ready(qtbot, window)

    # Mutate source on disk after assignment.
    updated = Image.new("RGB", (400, 260), (20, 130, 60))
    updated.save(path)

    # Export is blocked before opening save dialog and records no exported revision.
    window.save("pdf")
    qtbot.waitUntil(lambda: not window.tasks, timeout=20000)
    assert errors and "изменился" in errors[-1]
    assert not dialogs_opened
    assert "pdf" not in window.last_exported_revisions

    # Re-assigning using the stale thumbnail is also blocked, preserving current Card.
    prev_card = window.cards["front"]
    window.assign_selected("front")
    wait_ready(qtbot, window)
    assert "изменился" in errors[-1]
    assert window.cards["front"] is prev_card

    # Explicit reload updates source, reloads assigned side, and unblocks export.
    window.reload_selected_source()
    wait_ready(qtbot, window)
    assert window.cards["front"].original.shape[:2] == (260, 400)

    out_pdf = tmp_path / "exported.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, p=out_pdf, **kw: (str(p), ""))
    window.save("pdf")
    qtbot.waitUntil(lambda: out_pdf.exists() and not window.tasks, timeout=20000)
    assert window.last_exported_revisions["pdf"] == (("front", window.cards["front"].revision),)
    window.close()
    qtbot.waitUntil(lambda: window._closed, timeout=10000)


def test_gui_protects_symlink_and_hardlink_to_original_without_overwrite_prompt(
    qtbot, tmp_path, synthetic_card, monkeypatch
):
    orig = tmp_path / "orig.png"
    synthetic_card.save(orig)
    window = MainWindow()
    qtbot.addWidget(window)
    errors = []
    prompts = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args: prompts.append(args) or QMessageBox.StandardButton.Yes,
    )
    window.show()
    window.import_paths([orig])
    qtbot.waitUntil(lambda: window.sources.count() == 1 and not window.tasks, timeout=20000)
    window.sources.setCurrentRow(0)
    window.assign_selected("front")
    wait_ready(qtbot, window)

    aliases = []
    symlink = tmp_path / "alias_sym.png"
    try:
        symlink.symlink_to(orig)
        aliases.append(symlink)
    except OSError:
        pass
    hardlink = tmp_path / "alias_hard.png"
    try:
        hardlink.hardlink_to(orig)
        aliases.append(hardlink)
    except OSError:
        pass
    if not aliases:
        pytest.skip("Neither symlinks nor hard links are supported on this filesystem.")

    for alias in aliases:
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", lambda *args, p=alias, **kw: (str(p), "")
        )
        window.save("png")
        qtbot.waitUntil(lambda: not window.tasks, timeout=20000)
        assert errors and "оригинал" in errors[-1]
        assert not prompts
    window.close()
    qtbot.waitUntil(lambda: window._closed, timeout=10000)


def test_undo_redo_across_corners_rotation_layout_shortcuts_and_cancel_noop(
    qtbot, tmp_path, synthetic_card
):
    window, controller = _setup_front_with_controller(qtbot, tmp_path, synthetic_card)
    assert window.history.undo_depth == 1
    assert window.undo_button.isEnabled()
    assert not window.redo_button.isEnabled()

    # Cancelled 4-point draft must not push a history step.
    window.pick_points()
    qtbot.keyClick(window.editor, Qt.Key.Key_Escape)
    controller.drain()
    assert window.history.undo_depth == 1

    # Commit 1: 4-point selection
    initial_points = window.cards["front"].points
    new_points = ((25, 25), (815, 25), (815, 505), (25, 505))
    window.pick_points()
    for pt in new_points:
        qtbot.mouseClick(
            window.editor.viewport(),
            Qt.MouseButton.LeftButton,
            pos=window.editor.mapFromScene(QPointF(*pt)),
        )
    controller.drain()
    committed_points = window.cards["front"].points
    assert window.history.undo_depth == 2

    # Commit 2: rotate 90 degrees
    window.rotate()
    controller.drain()
    assert window.cards["front"].turns == 1
    assert window.history.undo_depth == 3

    # Commit 3: corner radius
    window.radius.setValue(4)
    controller.drain()
    assert window.cards["front"].radius == 4
    assert window.history.undo_depth == 4

    # Commit 4: layout gap
    window.gap.setValue(24)
    controller.drain()
    assert window.gap.value() == 24
    assert window.history.undo_depth == 5

    # Undo gap via Ctrl+Z shortcut (focus editor so spinbox doesn't intercept Ctrl+Z)
    window.editor.setFocus()
    qtbot.keyClick(window.editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    controller.drain()
    assert window.gap.value() == 15
    assert window.cards["front"].radius == 4
    assert window.redo_button.isEnabled()
    assert "Компоновка" in window.redo_button.toolTip()

    # Undo radius, rotation, and corner selection
    window.undo()
    controller.drain()
    assert window.cards["front"].radius == 0
    assert window.cards["front"].turns == 1

    window.undo()
    controller.drain()
    assert window.cards["front"].turns == 0
    assert window.cards["front"].points == committed_points

    window.undo()
    controller.drain()
    assert window.cards["front"].points == initial_points
    assert window.history.undo_depth == 1

    # Redo via Ctrl+Y and Ctrl+Shift+Z
    window.editor.setFocus()
    qtbot.keyClick(window.editor, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    controller.drain()
    assert window.cards["front"].points == committed_points

    qtbot.keyClick(
        window.editor,
        Qt.Key.Key_Z,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    controller.drain()
    assert window.cards["front"].turns == 1
    assert window.history.can_redo

    # New edit clears redo stack.
    window.radius.setValue(6)
    controller.drain()
    assert not window.history.can_redo
    assert not window.redo_button.isEnabled()
    window.close()


def test_undo_redo_side_replacement_removal_original_guard_eviction_and_delayed_task(
    qtbot, tmp_path, synthetic_card
):
    front_path = tmp_path / "front.png"
    back_path = tmp_path / "back.png"
    synthetic_card.save(front_path)
    synthetic_card.transpose(Image.Transpose.FLIP_LEFT_RIGHT).save(back_path)

    window = MainWindow()
    qtbot.addWidget(window)
    controller = ManualTaskController(window)
    window.show()
    qtbot.waitExposed(window)
    window.import_paths([front_path, back_path])
    controller.drain()

    # Assign front, replace front with back, then remove front.
    window.sources.setCurrentRow(0)
    window.assign_selected("front")
    controller.drain()
    assert window.cards["front"].source.path == front_path.resolve()

    window.sources.setCurrentRow(1)
    window.assign_selected("front")
    controller.drain()
    assert window.cards["front"].source.path == back_path.resolve()

    window.remove_side()
    controller.drain()
    assert "front" not in window.cards

    # Undo removal -> back.png restored; undo replacement -> front.png restored.
    window.undo()
    controller.drain()
    assert window.cards["front"].source.path == back_path.resolve()

    window.undo()
    controller.drain()
    assert window.cards["front"].source.path == front_path.resolve()

    # Original protection still guards both files after undo/removal cycles.
    assert window.original_guard.is_protected(front_path)
    assert window.original_guard.is_protected(back_path)

    # Delayed old render task completing AFTER Undo must be ignored.
    window.radius.setValue(5)
    assert len(controller.pending) == 1
    window.undo()
    assert len(controller.pending) == 2
    # Complete the delayed OLD (radius=5) task first!
    controller.complete_at(0)
    assert window.cards["front"].radius == 0
    assert window.cards["front"].ready_revision != window.cards["front"].revision
    controller.drain()
    assert window.cards["front"].radius == 0
    assert window.cards["front"].ready_revision == window.cards["front"].revision

    # Memory-budget eviction discloses reduced history in status bar.
    window.history.max_bytes = 100
    window.sources.setCurrentRow(1)
    window.assign_selected("front")
    controller.drain()
    assert window.history.evicted_for_memory_count >= 1
    assert "истории" in window.statusBar().currentMessage().lower()
    window.close()


def test_close_guard_cancel_discard_export_and_unavailable_export_button(
    qtbot, tmp_path, synthetic_card, monkeypatch
):
    monkeypatch.setattr(MainWindow, "_prompt_unsaved_changes", REAL_PROMPT_UNSAVED)
    window, controller = _setup_front_with_controller(qtbot, tmp_path, synthetic_card)

    # Clean/dirty state is unaffected by zoom or pan.
    assert window.windowTitle() == f"* {BASE_WINDOW_TITLE}"
    window.editor.zoom(1.5)
    assert window.windowTitle() == f"* {BASE_WINDOW_TITLE}"

    seen_buttons: list[list[str]] = []
    choices = ["Отмена", "Сохранить PDF", "Сохранить PDF"]
    chosen_btn = {}

    def fake_exec(box):
        texts = [b.text() for b in box.buttons()]
        seen_buttons.append(texts)
        pick = choices.pop(0)
        for btn in box.buttons():
            if btn.text() == pick:
                chosen_btn[id(box)] = btn
                break
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: chosen_btn.get(id(box)))

    # 1. Cancel close -> stays open, work preserved.
    window.close()
    assert not window._closed and not window._closing
    assert "front" in window.cards
    assert "Сохранить PDF" in seen_buttons[-1]

    # 2. When export is unavailable (invalid selection), Save PDF is omitted from dialog.
    window.selection_changed(((10, 10), (800, 500), (800, 10), (10, 500)))
    controller.drain()
    assert not window.can_export_now()
    box_no_export, _, export_btn, _ = window._build_unsaved_dialog(
        can_export=window.can_export_now()
    )
    assert export_btn is None
    assert "Сохранить PDF" not in [b.text() for b in box_no_export.buttons()]
    box_no_export.deleteLater()

    # Restore valid selection.
    window.undo()
    controller.drain()
    assert window.can_export_now()

    # 3. Choose Save PDF from close dialog, then cancel file dialog -> stays open and dirty.
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kw: ("", ""))
    window.close()
    controller.drain()
    assert not window._closed and not window._closing
    assert window.checkpoints.needs_close_confirmation(window.current_workspace_snapshot())

    # 4. Choose Save PDF from close dialog and save -> exports and closes cleanly.
    out_pdf = tmp_path / "saved_on_close.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, p=out_pdf, **kw: (str(p), ""))
    window.close()
    controller.drain()
    assert out_pdf.exists()
    assert window._closed

    # 5. Discard on second window closes without exporting.
    sub = tmp_path / "sub"
    sub.mkdir()
    window2, _ = _setup_front_with_controller(qtbot, sub, synthetic_card)
    choices[:] = ["Выйти без сохранения"]
    window2.close()
    assert window2._closed


def test_dirty_state_save_failure_edits_during_export_and_undo_to_checkpoint(
    qtbot, tmp_path, synthetic_card, monkeypatch
):
    monkeypatch.setattr(MainWindow, "_prompt_unsaved_changes", REAL_PROMPT_UNSAVED)
    window, controller = _setup_front_with_controller(qtbot, tmp_path, synthetic_card)
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    monkeypatch.setattr(
        QMessageBox,
        "exec",
        lambda _box: (_ for _ in ()).throw(
            AssertionError("Close prompt must not appear when clean")
        ),
    )

    out_pdf = tmp_path / "out.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, p=out_pdf, **kw: (str(p), ""))

    # 1. Failed export keeps dirty state and does not mark exported.
    assert window.save("pdf")
    assert len(controller.pending) == 1
    controller.complete_at(0)  # source fingerprint validation passes
    assert len(controller.pending) == 1
    controller.fail_at(0, "Ошибка записи")
    assert errors and "Ошибка записи" in errors[-1]
    assert window.checkpoints.needs_close_confirmation(window.current_workspace_snapshot())
    assert "pdf" not in window.last_exported_revisions

    # 2. Edits made while export is in flight: export acknowledges only captured snapshot S1.
    s1 = window.current_workspace_snapshot()
    assert window.save("pdf")
    assert len(controller.pending) == 1
    controller.complete_at(
        0
    )  # source fingerprint validation passes -> opens dialog & queues export
    assert len(controller.pending) == 1
    window.radius.setValue(5)
    controller.complete_at(0)  # Complete export of S1 while radius=5 render is still pending
    controller.drain()

    assert window.checkpoints.exports["pdf"].snapshot == s1
    assert window.checkpoints.needs_close_confirmation(window.current_workspace_snapshot())
    assert window.windowTitle() == f"* {BASE_WINDOW_TITLE}"
    assert window.open_button.isVisible()
    assert window.open_button.text() == "Открыть ранний экспорт"
    assert "ранней" in window.export_message.text().lower()
    assert "не сохраняет редактируемый проект" in window.export_message.text().lower()

    # 3. Undoing back to S1 clears dirty state and restores current-export labelling.
    window.undo()
    controller.drain()
    assert not window.checkpoints.needs_close_confirmation(window.current_workspace_snapshot())
    assert window.windowTitle() == BASE_WINDOW_TITLE
    assert window.open_button.text() == "Открыть сохранённый файл"

    # 4. Closing clean window does not invoke close prompt.
    window.close()
    assert window._closed


def test_stale_reload_does_not_overwrite_newer_side_assignment(qtbot, tmp_path, synthetic_card):
    path_a = tmp_path / "a.png"
    path_b = tmp_path / "b.png"
    synthetic_card.save(path_a)
    Image.new("RGB", (400, 260), (20, 120, 70)).save(path_b)

    window = MainWindow()
    qtbot.addWidget(window)
    controller = ManualTaskController(window)
    window.show()
    window.import_paths([path_a, path_b])
    controller.drain()

    # Assign A to front.
    window.sources.setCurrentRow(0)
    window.assign_selected("front")
    controller.drain()
    assert window.cards["front"].source.path == path_a.resolve()

    # Start reloading A (pending task 0), then replace front with B before reload A completes.
    window.sources.setCurrentRow(0)
    window.reload_selected_source()
    assert len(controller.pending) == 1  # reload A

    window.sources.setCurrentRow(1)
    window.assign_selected("front")
    # Complete B's load + render while reload A is still at index 0.
    controller.complete_at(1)  # load B
    controller.complete_at(1)  # render B
    window.layout_timer.stop()
    window.refresh_sheet()
    assert window.cards["front"].source.path == path_b.resolve()
    card_b = window.cards["front"]

    # Completing stale reload A must update A's thumbnail only, never overwrite front back to A.
    controller.complete_at(0)
    controller.drain()
    assert window.cards["front"] is card_b
    assert window.cards["front"].source.path == path_b.resolve()

    # Also ignore stale reload A when B's new load is currently in flight (pending_loads).
    window.sources.setCurrentRow(0)
    window.reload_selected_source()
    controller.drain()  # finish clean state
    # Re-assign A so A is active again.
    window.sources.setCurrentRow(0)
    window.assign_selected("front")
    controller.drain()
    window.sources.setCurrentRow(0)
    window.reload_selected_source()  # pending[0] = reload A
    window.sources.setCurrentRow(1)
    window.assign_selected("front")  # pending[1] = load B (front in pending_loads)
    assert "front" in window.pending_loads
    controller.complete_at(0)  # reload A finishes while B is still loading -> must NOT queue load A
    assert len(controller.pending) == 1  # only B's load remains
    controller.drain()
    assert window.cards["front"].source.path == path_b.resolve()
    window.close()


def test_save_survives_sheet_invalidation_during_file_dialog(
    qtbot, tmp_path, synthetic_card, monkeypatch
):
    window, controller = _setup_front_with_controller(qtbot, tmp_path, synthetic_card)
    s1 = window.current_workspace_snapshot()
    rev1 = window.cards["front"].revision
    out_pdf = tmp_path / "sheet.pdf"

    # Start autodetect so a background task is ready to invalidate window.sheet during save dialog.
    window.autodetect()
    assert len(controller.pending) == 1  # pending[0] = autodetect

    def dialog_triggering_invalidation(*_args, **_kwargs):
        # Complete autodetect while modal file dialog is open; render_card sets window.sheet = None.
        controller.complete_at(0)
        assert window.sheet is None
        return (str(out_pdf), "")

    monkeypatch.setattr(QFileDialog, "getSaveFileName", dialog_triggering_invalidation)
    assert window.save("pdf")
    # pending[0] is autodetect, pending[1] is save's pre-dialog source validation.
    controller.complete_at(
        1
    )  # validates sources -> opens dialog -> invalidates sheet -> queues export
    controller.drain()

    assert out_pdf.exists()
    assert window.checkpoints.exports["pdf"].snapshot == s1
    assert window.last_exported_revisions["pdf"] == (("front", rev1),)
    window.close()


def test_source_hashing_and_validation_never_run_on_gui_thread(
    qtbot, tmp_path, synthetic_card, monkeypatch
):
    import cardprint.imaging as imaging_mod
    import cardprint.ui.window as window_mod

    gui_thread = threading.current_thread()
    fingerprint_threads: list[threading.Thread] = []
    validate_threads: list[threading.Thread] = []

    orig_window_fp = window_mod.fingerprint_file
    orig_imaging_fp = imaging_mod.fingerprint_file
    orig_validate = OriginalGuard.validate_source

    def tracked_window_fp(path):
        fingerprint_threads.append(threading.current_thread())
        return orig_window_fp(path)

    def tracked_imaging_fp(path):
        fingerprint_threads.append(threading.current_thread())
        return orig_imaging_fp(path)

    def tracked_validate(self, path, *, expected=None):
        validate_threads.append(threading.current_thread())
        return orig_validate(self, path, expected=expected)

    monkeypatch.setattr(window_mod, "fingerprint_file", tracked_window_fp)
    monkeypatch.setattr(imaging_mod, "fingerprint_file", tracked_imaging_fp)
    monkeypatch.setattr(OriginalGuard, "validate_source", tracked_validate)

    photo_path = tmp_path / "photo.png"
    pdf_path = tmp_path / "doc.pdf"
    synthetic_card.save(photo_path)
    _make_multipage_pdf(pdf_path, pages=2)

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    # Photo import, assign, reload
    window.import_paths([photo_path])
    qtbot.waitUntil(lambda: window.sources.count() == 1 and not window.tasks, timeout=20000)
    window.sources.setCurrentRow(0)
    window.assign_selected("front")
    wait_ready(qtbot, window)
    window.reload_selected_source()
    wait_ready(qtbot, window)

    # PDF import, assign, reload
    window.import_paths([pdf_path])
    qtbot.waitUntil(lambda: window.sources.count() == 3 and not window.tasks, timeout=20000)
    window.sources.setCurrentRow(1)
    window.assign_selected("back")
    wait_ready(qtbot, window)
    window.reload_selected_source()
    wait_ready(qtbot, window)

    # Save PDF
    out_pdf = tmp_path / "out.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, p=out_pdf, **kw: (str(p), ""))
    assert window.save("pdf")
    qtbot.waitUntil(lambda: out_pdf.exists() and not window.tasks, timeout=20000)

    assert len(fingerprint_threads) >= 4
    assert len(validate_threads) >= 4
    assert all(thread is not gui_thread for thread in fingerprint_threads)
    assert all(thread is not gui_thread for thread in validate_threads)

    window.close()
    qtbot.waitUntil(lambda: window._closed, timeout=10000)


def test_undo_memory_budget_eviction_updates_status_and_controls(qtbot, tmp_path, synthetic_card):
    window, controller = _setup_front_with_controller(qtbot, tmp_path, synthetic_card)
    window.history.max_bytes = 100
    window.undo()
    controller.drain()

    assert window.history.history_only_bytes <= 100
    assert not window.history.can_redo
    assert not window.redo_button.isEnabled()
    assert window.history.evicted_for_memory_count >= 1
    assert "памяти" in window.statusBar().currentMessage().lower()
    window.close()


def _contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    def _lum(hex_color: str) -> float:
        color = QColor(hex_color)
        channels = [
            ch / 12.92 if ch <= 0.03928 else ((ch + 0.055) / 1.055) ** 2.4
            for ch in (color.redF(), color.greenF(), color.blueF())
        ]
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    l1, l2 = _lum(fg_hex), _lum(bg_hex)
    return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)


def test_theme_palettes_contrast_and_svg_icons(qtbot):
    for palette in (LIGHT_PALETTE, DARK_PALETTE):
        assert palette.canvas_bg.lower() != "#ffffff"
        assert _contrast_ratio(palette.text_primary, palette.card_bg) >= 4.5
        assert _contrast_ratio(palette.text_primary, palette.window_bg) >= 4.5
        assert _contrast_ratio(palette.text_secondary, palette.card_bg) >= 4.5
        assert _contrast_ratio(palette.text_secondary, palette.window_bg) >= 4.5
        assert _contrast_ratio(palette.accent_text, palette.accent) >= 4.5
        assert _contrast_ratio(palette.danger, palette.card_bg) >= 4.5
        assert _contrast_ratio(palette.warning, palette.card_bg) >= 4.5
        assert _contrast_ratio(palette.success, palette.card_bg) >= 4.5

        qss = build_stylesheet(palette)
        for selector in (
            'QPushButton[variant="primary"]',
            'QPushButton[variant="secondary"]',
            'QPushButton[variant="ghost"]',
            'QPushButton[variant="danger"]',
            'QLabel[status="error"]',
            'QLabel[status="warning"]',
            'QLabel[status="ok"]',
        ):
            assert selector in qss

    for icon_name in ICONS:
        for color in (LIGHT_PALETTE.text_primary, DARK_PALETTE.text_primary):
            icon = render_svg_icon(icon_name, color, size=18)
            assert not icon.isNull()
            img = icon.pixmap(18, 18).toImage()
            assert any(img.pixelColor(x, y).alpha() > 0 for x in range(18) for y in range(18)), (
                f"Icon {icon_name} rendered empty for {color}"
            )

    with pytest.raises(KeyError):
        render_svg_icon("nonexistent_icon", "#000000")


def test_theme_toggle_persists_in_qsettings_keeps_dirty_clean_and_a4_white(
    qtbot, tmp_path, synthetic_card, monkeypatch
):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert window.theme_name == THEME_LIGHT
    assert window.theme_button.text() == "Тёмная тема"
    assert window.preview.backgroundBrush().color().name().lower() == LIGHT_PALETTE.canvas_bg
    assert not window.checkpoints.needs_close_confirmation(window.current_workspace_snapshot())

    # Toggle to dark theme: UI updates immediately, QSettings saves, dirty state stays clean.
    assert window.toggle_theme() == THEME_DARK
    assert window.theme_button.text() == "Светлая тема"
    assert window.preview.backgroundBrush().color().name().lower() == DARK_PALETTE.canvas_bg
    assert window.editor.backgroundBrush().color().name().lower() == DARK_PALETTE.canvas_bg
    app = QApplication.instance()
    assert DARK_PALETTE.window_bg in app.styleSheet()
    assert not window.checkpoints.needs_close_confirmation(window.current_workspace_snapshot())

    # Newly created window restores Dark theme from QSettings.
    restored = MainWindow()
    qtbot.addWidget(restored)
    assert restored.theme_name == THEME_DARK
    assert restored.theme_button.text() == "Светлая тема"
    restored.close()
    qtbot.waitUntil(lambda: restored._closed, timeout=10000)

    # Export PNG under Dark theme: exported A4 background remains strictly white (255, 255, 255).
    path = tmp_path / "card.png"
    synthetic_card.save(path)
    window.import_paths([path])
    qtbot.waitUntil(lambda: window.sources.count() == 1 and not window.tasks, timeout=20000)
    window.sources.setCurrentRow(0)
    window.assign_selected("front")
    wait_ready(qtbot, window)

    out_png = tmp_path / "dark_mode_sheet.png"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, p=out_png, **kw: (str(p), ""))
    assert window.save("png")
    qtbot.waitUntil(lambda: out_png.exists() and not window.tasks, timeout=20000)

    with Image.open(out_png) as exported:
        assert exported.getpixel((0, 0)) == (255, 255, 255)
        assert exported.getpixel((exported.width - 1, exported.height - 1)) == (255, 255, 255)

    window.close()
    qtbot.waitUntil(lambda: window._closed, timeout=10000)


def test_button_variants_contextual_panels_accessibility_and_compact_layout(
    qtbot, tmp_path, synthetic_card
):
    window, controller = _setup_front_with_controller(qtbot, tmp_path, synthetic_card)

    # Exactly one primary CTA; secondary, ghost, and danger variants assigned explicitly.
    primary_buttons = [
        btn for btn in window.findChildren(QPushButton) if btn.property("variant") == "primary"
    ]
    assert primary_buttons == [window.pdf_button]
    assert window.png_button.property("variant") == "secondary"
    assert window.add_button.property("variant") == "secondary"
    assert window.undo_button.property("variant") == "ghost"
    assert window.redo_button.property("variant") == "ghost"
    assert window.theme_button.property("variant") == "ghost"
    assert window.reset_button.property("variant") == "danger"

    # Contextual right panel: side settings on Editor tab, sheet layout on Preview tab.
    assert window.tabs.currentIndex() == 0
    assert window.card_group.isVisible()
    assert not window.layout_group.isVisible()
    assert window.status_group.isVisible()

    window.tabs.setCurrentIndex(1)
    qtbot.waitUntil(lambda: window.layout_group.isVisible(), timeout=2000)
    assert not window.card_group.isVisible()
    assert window.layout_group.isVisible()
    assert window.status_group.isVisible()
    window.tabs.setCurrentIndex(0)

    # Accessibility and focus policy survive Light -> Dark toggle, with non-color status text.
    assert window.pdf_button.accessibleName()
    assert window.sources.accessibleName()
    assert window.editor.focusPolicy() != Qt.FocusPolicy.NoFocus
    assert window.preview.focusPolicy() != Qt.FocusPolicy.NoFocus
    assert "✓" in window.layout_message.text()

    window.toggle_theme()
    assert window.pdf_button.accessibleName()
    assert window.editor.focusPolicy() != Qt.FocusPolicy.NoFocus

    # Invalid size aspect warns with warning icon/property, not color alone.
    window.size_mode.setCurrentIndex(window.size_mode.findData("custom"))
    window.width.setValue(120.0)
    window.height.setValue(40.0)
    controller.drain()
    assert window.ratio_label.property("status") == "warning"
    assert "⚠" in window.ratio_label.text()

    # Compact 1280x720 desktop viewport keeps all three splitter panes usable.
    window.resize(1280, 720)
    QApplication.processEvents()
    assert window.size().width() <= 1280 and window.size().height() <= 720
    sizes = window.splitter.sizes()
    assert len(sizes) == 3 and all(pane > 180 for pane in sizes)
    assert sum(sizes) <= 1280

    window.close()

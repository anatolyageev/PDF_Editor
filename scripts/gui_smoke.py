"""Exercise two-page PDF in the GUI; save screenshots and exports using fake data."""

import multiprocessing
import sys
import tempfile
import time
from pathlib import Path

from make_demo import make_demo
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from cardprint.export import export_sheet
from cardprint.ui.window import MainWindow


def main() -> int:
    multiprocessing.freeze_support()
    app = QApplication(sys.argv)
    output_root = Path("artifacts")
    output_root.mkdir(exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="gui-", dir=output_root))
    source_dir = tempfile.TemporaryDirectory(prefix="cardprint-demo-")
    make_demo(Path(source_dir.name))
    window = MainWindow()
    window.show()
    window.import_paths([Path(source_dir.name) / "two_pages.pdf"])
    phase = 0
    started = time.monotonic()
    failed = False

    def tick():
        nonlocal phase, failed
        if time.monotonic() - started > 60:
            print("GUI smoke timed out", file=sys.stderr)
            failed = True
            timer.stop()
            window.close()
            app.quit()
            return
        if window.tasks or window.layout_timer.isActive():
            return
        if phase == 0 and window.sources.count() == 2:
            window.sources.setCurrentRow(0)
            window.assign_selected("front")
            phase = 1
        elif phase == 1 and window.sheet:
            window.sources.setCurrentRow(1)
            window.assign_selected("back")
            phase = 2
        elif phase == 2 and window.sheet and len(window.sheet.placements) == 2:
            # Correct the preliminary contour using the known synthetic card corners.
            # This exercises the same selection callback as manual handle adjustment.
            for index, side in enumerate(("front", "back")):
                window.side_combo.setCurrentIndex(index)
                width, height = window.cards[side].source_size
                points = tuple(
                    (x * width / 1400, y * height / 1000)
                    for x, y in ((180, 200), (1180, 200), (1180, 830), (180, 830))
                )
                window.editor.set_points(points)
                window.selection_changed(points)
            phase = 5
        elif phase == 5 and window.sheet:
            window.grab().save(str(output / "editor.png"))
            window.tabs.setCurrentIndex(1)
            phase = 3
        elif phase == 3:
            window.grab().save(str(output / "preview.png"))
            sheet = window.sheet

            def exports():
                export_sheet(sheet, output / "sheet.pdf", "pdf", pdf_worker=window.pdf_worker)
                export_sheet(sheet, output / "sheet.png", "png")

            window.submit(exports, lambda _: None)
            phase = 4
        elif phase == 4:
            if not (output / "sheet.png").exists():
                failed = True
            print(f"GUI smoke {'FAILED' if failed else 'passed'}: {output.resolve()}")
            timer.stop()
            window.close()
            app.quit()

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(100)
    app.exec()
    window.pool.waitForDone()
    window.pdf_worker.shutdown()
    source_dir.cleanup()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

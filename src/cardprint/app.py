import os
import sys
from pathlib import Path

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QApplication

from .ui.window import MainWindow


def main():
    # WSLg may expose Wayland without setting XDG_SESSION_TYPE. Prefer its working
    # compositor over X11 when present; an explicit user Qt backend always wins.
    if sys.platform == "linux" and "QT_QPA_PLATFORM" not in os.environ:
        display = os.environ.get("WAYLAND_DISPLAY")
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        if display and runtime and (Path(runtime) / display).exists():
            os.environ["QT_QPA_PLATFORM"] = "wayland"
    QLocale.setDefault(QLocale(QLocale.Language.Russian, QLocale.Country.Russia))
    app = QApplication(sys.argv)
    app.setApplicationName("Карточки на A4")
    app.setOrganizationName("CardPrint Local")
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

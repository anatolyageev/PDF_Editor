import multiprocessing
import os
import sys
from pathlib import Path

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QApplication

from .ui.theme import SETTINGS_APP, SETTINGS_ORG, apply_theme, load_saved_theme
from .ui.window import MainWindow


def main():
    multiprocessing.freeze_support()
    # WSLg may expose Wayland without setting XDG_SESSION_TYPE. Prefer its working
    # compositor over X11 when present; an explicit user Qt backend always wins.
    if sys.platform == "linux" and "QT_QPA_PLATFORM" not in os.environ:
        display = os.environ.get("WAYLAND_DISPLAY")
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        if display and runtime and (Path(runtime) / display).exists():
            os.environ["QT_QPA_PLATFORM"] = "wayland"
    QLocale.setDefault(QLocale(QLocale.Language.Russian, QLocale.Country.Russia))
    app = QApplication(sys.argv)
    app.setApplicationName(SETTINGS_APP)
    app.setOrganizationName(SETTINGS_ORG)
    app.setStyle("Fusion")
    apply_theme(app, load_saved_theme())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

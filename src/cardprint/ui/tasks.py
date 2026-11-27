from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from ..pdf_worker import PdfRequestHandle, PdfWorkerClient


class TaskSignals(QObject):
    success = Signal(object)
    error = Signal(str)
    finished = Signal()


class Task(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = TaskSignals()

    @Slot()
    def run(self):
        try:
            result = self.function()
        except Exception as exc:
            self.signals.error.emit(str(exc) or "Не удалось выполнить операцию.")
        else:
            self.signals.success.emit(result)
        finally:
            self.signals.finished.emit()


class PdfTaskBridge(QObject):
    """Delivers PdfWorkerClient callbacks safely onto the Qt GUI thread."""

    success = Signal(object)
    error = Signal(str)
    cancelled = Signal()
    finished = Signal()


def dispatch_pdf_request(
    bridge: PdfTaskBridge,
    worker: PdfWorkerClient,
    request: object,
    *,
    coalesce_key: object = None,
    is_export: bool = False,
) -> PdfRequestHandle:
    """Submit a request to ``worker`` after ``bridge`` signals are connected."""

    def on_done(payload: object) -> None:
        bridge.success.emit(payload)
        bridge.finished.emit()

    def on_error(exc: Exception) -> None:
        bridge.error.emit(str(exc) or "Не удалось выполнить операцию с PDF.")
        bridge.finished.emit()

    def on_cancelled() -> None:
        bridge.cancelled.emit()
        bridge.finished.emit()

    return worker.submit(
        request,
        coalesce_key=coalesce_key,
        is_export=is_export,
        on_done=on_done,
        on_error=on_error,
        on_cancelled=on_cancelled,
    )

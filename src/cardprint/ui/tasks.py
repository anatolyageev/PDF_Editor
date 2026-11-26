from PySide6.QtCore import QObject, QRunnable, Signal, Slot


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

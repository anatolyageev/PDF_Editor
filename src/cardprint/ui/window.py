from pathlib import Path

from PIL import Image
from PySide6.QtCore import QSize, Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..export import (
    PRINT_HINT,
    OriginalGuard,
    OriginalOverwriteError,
    Sheet,
    SourceChangedError,
    export_sheet,
    fingerprint_file,
    render_sheet,
    snapshot_sheet,
)
from ..geometry import CardSize, Layout, arrange, validate_quad
from ..imaging import (
    Source,
    detect_quad,
    discover,
    full_quad,
    load_source,
    rectify,
    rotate_quad,
    rotated,
    round_corners,
)
from ..model import SIDE_NAMES, Card, UndoStack, WorkspaceCheckpoints, WorkspaceSnapshot
from ..pdf_worker import (
    DEFAULT_THUMBNAIL_BATCH_SIZE,
    PDF_DPI,
    InspectPdfRequest,
    PdfInfo,
    PdfWorkerClient,
    RenderPageRequest,
    ThumbnailBatchRequest,
    ThumbnailBatchResult,
)
from .editor import CORNER_LABELS, ImageView, pixmap, to_qimage
from .tasks import PdfTaskBridge, Task, dispatch_pdf_request

BASE_WINDOW_TITLE = "Карточки на A4 · локальная подготовка к печати"


class InspectorDialog(QDialog):
    def __init__(
        self,
        parent,
        image,
        side: str,
        card: "Card",
        revision: int,
        load_generation: int,
        view_toolbar_builder,
        on_close=None,
    ):
        super().__init__(parent)
        self.side = side
        self.card = card
        self.revision = revision
        self.load_generation = load_generation
        self.stale = False
        self._on_close = on_close
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"Проверка краёв · {SIDE_NAMES[side]} · правка #{revision}")
        self.resize(1000, 720)
        layout = QVBoxLayout(self)
        self.status_label = QLabel(f"Актуальный снимок · {SIDE_NAMES[side]} (правка #{revision}).")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        view = ImageView()
        view.set_image(image)
        layout.addWidget(view)
        layout.addLayout(view_toolbar_builder(view))
        layout.addWidget(
            parent.button(
                "1 пиксель изображения = 1 пиксель просмотра",
                lambda: view.zoom(1 / view.transform().m11()),
            )
        )
        layout.addWidget(
            QLabel(
                "Белое за пределами скругления. Содержимое карточки не улучшается и не перерисовывается."
            )
        )
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.finished.connect(self._notify_closed)

    def _notify_closed(self, _result=0):
        if self._on_close is not None:
            callback = self._on_close
            self._on_close = None
            callback(self)

    def mark_stale(self, reason: str):
        if self.stale:
            return
        self.stale = True
        self.setWindowTitle(
            f"Проверка краёв · {SIDE_NAMES[self.side]} · устарел (правка #{self.revision})"
        )
        self.status_label.setText(f"⚠ {reason}")
        self.status_label.setStyleSheet("color: #a34c00; font-weight: 600;")

    def closeEvent(self, event):
        self._notify_closed()
        super().closeEvent(event)


def thumbnail(image, limit=2000):
    result = image.copy()
    result.thumbnail((limit, limit))
    return result


class MainWindow(QMainWindow):
    def __init__(self, pdf_worker: PdfWorkerClient | None = None):
        super().__init__()
        self.setWindowTitle(BASE_WINDOW_TITLE)
        self.resize(1380, 880)
        self.cards: dict[str, Card] = {}
        self.active_side = "front"
        self.sheet = None
        self.layout_generation = 0
        self.import_generation = 0
        self.session_id = 1
        self._card_revision_seq = 0
        self.history = UndoStack(WorkspaceSnapshot((), Layout()))
        self.checkpoints = WorkspaceCheckpoints(self.history.current)
        self.load_generation = {"front": 0, "back": 0}
        self.inspector_request_id = {"front": 0, "back": 0}
        self.inspectors: list[InspectorDialog] = []
        self.draft_message: str | None = None
        self.draft_side: str | None = None
        self.tasks = {}
        self.pending_loads = set()
        self.original_paths = set()
        self.original_guard = OriginalGuard()
        self.last_exported_revisions: dict[str, tuple[tuple[str, int], ...]] = {}
        self._owns_pdf_worker = pdf_worker is None
        self.pdf_worker = pdf_worker or PdfWorkerClient()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.syncing = False
        self._closing = False
        self._closed = False
        self._pending_close_after_export = False
        self._discard_confirmed = False
        self._build_ui()
        self.layout_timer = QTimer(self)
        self.layout_timer.setSingleShot(True)
        self.layout_timer.setInterval(160)
        self.layout_timer.timeout.connect(self.refresh_sheet)
        self.statusBar().showMessage(
            "Добавьте изображения или PDF. Все документы обрабатываются на этом компьютере."
        )
        self._sync_controls()
        self._sync_history_controls()
        self._sync_dirty_ui()
        self.invalidate_sheet("Назначьте стороны документа.")
        self.preview.set_image(Image.new("RGB", (420, 594), "white"))

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        toolbar = QHBoxLayout()
        self.add_button = self.button("Добавить файлы", self.add_files)
        toolbar.addWidget(self.add_button)
        self.undo_button = self.button("Отменить", self.undo)
        self.redo_button = self.button("Повторить", self.redo)
        toolbar.addWidget(self.undo_button)
        toolbar.addWidget(self.redo_button)
        toolbar.addStretch()
        toolbar.addWidget(self.button("Предпросмотр", lambda: self.tabs.setCurrentIndex(1)))
        self.pdf_button = self.button("Сохранить PDF", lambda: self.save("pdf"))
        self.png_button = self.button("PNG · 300 DPI", lambda: self.save("png"))
        toolbar.addWidget(self.pdf_button)
        toolbar.addWidget(self.png_button)
        outer.addLayout(toolbar)
        self._undo_shortcut = QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo)
        self._redo_shortcut = QShortcut(QKeySequence("Ctrl+Y"), self, activated=self.redo)
        self._redo_shift_shortcut = QShortcut(
            QKeySequence("Ctrl+Shift+Z"), self, activated=self.redo
        )
        splitter = QSplitter()
        outer.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Файлы и страницы PDF"))
        self.sources = QListWidget()
        self.sources.setIconSize(QSize(144, 108))
        self.sources.setViewMode(QListView.ViewMode.IconMode)
        self.sources.setResizeMode(QListView.ResizeMode.Adjust)
        self.sources.setMovement(QListView.Movement.Static)
        self.sources.setWordWrap(True)
        self.sources.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.sources.itemDoubleClicked.connect(self.assign_next)
        left_layout.addWidget(self.sources)
        label = QLabel(
            "Выберите миниатюру и назначьте сторону.\nДвойной щелчок заполняет следующую сторону."
        )
        label.setWordWrap(True)
        left_layout.addWidget(label)
        left_layout.addWidget(
            self.button("Назначить лицевой", lambda: self.assign_selected("front"))
        )
        left_layout.addWidget(
            self.button("Назначить обратной", lambda: self.assign_selected("back"))
        )
        left_layout.addWidget(self.button("Убрать выбранный файл", self.remove_source))
        self.reload_source_button = self.button(
            "Перезагрузить выбранный файл", self.reload_selected_source
        )
        left_layout.addWidget(self.reload_source_button)
        splitter.addWidget(left)

        self.tabs = QTabWidget()
        edit_tab = QWidget()
        edit_layout = QVBoxLayout(edit_tab)
        side_row = QHBoxLayout()
        self.side_combo = QComboBox()
        for side, name in SIDE_NAMES.items():
            self.side_combo.addItem(name, side)
        self.side_combo.currentIndexChanged.connect(self.change_side)
        side_row.addWidget(self.side_combo)
        side_row.addWidget(self.button("Убрать сторону", self.remove_side))
        edit_layout.addLayout(side_row)
        self.source_label = QLabel("Сторона не назначена")
        self.source_label.setWordWrap(True)
        edit_layout.addWidget(self.source_label)
        self.editor = ImageView(editable=True)
        self.editor.selectionChanged.connect(self.selection_changed)
        self.editor.draftChanged.connect(self.on_draft_changed)
        self.editor.draftCancelled.connect(self.on_draft_cancelled)
        edit_layout.addWidget(self.editor, 1)
        self.editor_actions = []
        for names in (
            (("Определить границы", self.autodetect), ("Сбросить выделение", self.reset_selection)),
            (("Выделить 4 точки", self.pick_points), ("Повернуть на 90°", self.rotate)),
        ):
            row = QHBoxLayout()
            for title, callback in names:
                button = self.button(title, callback)
                row.addWidget(button)
                self.editor_actions.append(button)
            edit_layout.addLayout(row)
        edit_layout.addLayout(self.view_toolbar(self.editor))
        hint = QLabel(
            "Колесо — масштаб; фон — перемещение. Углы: 1 ↖ → 2 ↗ → 3 ↘ → 4 ↙.\nПосле выделения четырёх точек можно перетаскивать каждый угол; Esc отменяет ввод точек."
        )
        hint.setWordWrap(True)
        edit_layout.addWidget(hint)
        self.tabs.addTab(edit_tab, "Редактор стороны")

        preview_tab = QWidget()
        preview_layout = QVBoxLayout(preview_tab)
        self.preview = ImageView()
        preview_layout.addWidget(self.preview, 1)
        preview_layout.addLayout(self.view_toolbar(self.preview))
        preview_layout.addWidget(QLabel("A4 · 210 × 297 мм · белый фон RGB 255, 255, 255"))
        self.tabs.addTab(preview_tab, "Предпросмотр A4")
        splitter.addWidget(self.tabs)

        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        card_group = QGroupBox("Размер выбранной стороны")
        form = QFormLayout(card_group)
        self.size_mode = QComboBox()
        self.size_mode.addItem("Удостоверение · 85,60 × 53,98 мм", "id1")
        self.size_mode.addItem("Исходные пропорции + ширина", "aspect")
        self.size_mode.addItem("Произвольный размер", "custom")
        form.addRow(self.size_mode)
        self.width = self.spin(1, 400, 85.6)
        self.height = self.spin(1, 400, 53.98)
        self.radius = self.spin(0, 20, 0)
        form.addRow("Ширина", self.width)
        form.addRow("Высота", self.height)
        form.addRow("Радиус углов", self.radius)
        self.ratio_label = QLabel()
        self.ratio_label.setWordWrap(True)
        form.addRow(self.ratio_label)
        self.resolution_label = QLabel()
        self.resolution_label.setWordWrap(True)
        form.addRow(self.resolution_label)
        self.inspect_button = self.button("Увеличить результат / края", self.inspect_card)
        form.addRow(self.inspect_button)
        self.reset_button = self.button("Сбросить правки стороны", self.reset_card)
        form.addRow(self.reset_button)
        controls_layout.addWidget(card_group)
        layout_group = QGroupBox("Компоновка A4")
        form = QFormLayout(layout_group)
        self.template = QComboBox()
        self.template.addItem("Друг под другом", "vertical")
        self.template.addItem("Рядом", "horizontal")
        form.addRow("Шаблон", self.template)
        self.gap = self.spin(0, 150, 15)
        self.margin = self.spin(0, 100, 10)
        self.offset_x = self.spin(-200, 200, 0)
        self.offset_y = self.spin(-290, 290, 0)
        form.addRow("Промежуток", self.gap)
        form.addRow("Минимальные поля", self.margin)
        form.addRow("Смещение X →", self.offset_x)
        form.addRow("Смещение Y ↓", self.offset_y)
        note = QLabel("Группа карточек центрируется на листе. Смещения отсчитываются от центра.")
        note.setWordWrap(True)
        form.addRow(note)
        controls_layout.addWidget(layout_group)
        self.layout_message = QLabel("Назначьте стороны документа.")
        self.layout_message.setWordWrap(True)
        controls_layout.addWidget(self.layout_message)
        self.export_message = QLabel(PRINT_HINT)
        self.export_message.setWordWrap(True)
        controls_layout.addWidget(self.export_message)
        self.open_button = self.button("Открыть сохранённый файл", self.open_export)
        self.open_button.hide()
        self.export_path = None
        controls_layout.addWidget(self.open_button)
        controls_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(controls)
        scroll.setMinimumWidth(310)
        splitter.addWidget(scroll)
        splitter.setSizes([230, 790, 360])
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumHeight(5)
        self.progress.hide()
        outer.addWidget(self.progress)
        self.size_mode.currentIndexChanged.connect(self.card_options_changed)
        for spin in (self.width, self.height, self.radius):
            spin.valueChanged.connect(self.card_options_changed)
        self.template.currentIndexChanged.connect(self.layout_options_changed)
        for spin in (self.gap, self.margin, self.offset_x, self.offset_y):
            spin.valueChanged.connect(self.layout_options_changed)

    @staticmethod
    def button(title, callback):
        button = QPushButton(title)
        button.clicked.connect(callback)
        return button

    @staticmethod
    def spin(minimum, maximum, value):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(2)
        spin.setSuffix(" мм")
        spin.setValue(value)
        spin.setKeyboardTracking(False)
        return spin

    def view_toolbar(self, view):
        row = QHBoxLayout()
        row.addWidget(self.button("−", lambda: view.zoom(1 / 1.25)))
        row.addWidget(self.button("Вписать", view.fit_image))
        row.addWidget(self.button("+", lambda: view.zoom(1.25)))
        return row

    def submit(self, function, success, error=None):
        if self._closing or self._closed:
            return
        task = Task(function)
        key = id(task)
        self.tasks[key] = task
        task.signals.success.connect(success)
        task.signals.error.connect(error or self.show_error)
        task.signals.finished.connect(lambda: self.task_finished(key))
        self.progress.show()
        self.pool.start(task)

    def submit_pdf(
        self,
        request,
        success,
        error=None,
        *,
        coalesce_key=None,
        cancelled=None,
        is_export: bool = False,
    ):
        if self._closing or self._closed:
            return None
        bridge = PdfTaskBridge(self)
        key = id(bridge)
        self.tasks[key] = bridge
        bridge.success.connect(success)
        bridge.error.connect(error or self.show_error)
        if cancelled is not None:
            bridge.cancelled.connect(cancelled)
        bridge.finished.connect(lambda: self.task_finished(key))
        self.progress.show()
        return dispatch_pdf_request(
            bridge,
            self.pdf_worker,
            request,
            coalesce_key=coalesce_key,
            is_export=is_export,
        )

    def task_finished(self, key):
        self.tasks.pop(key, None)
        if not self.tasks:
            self.progress.hide()
            if self._closing and not self._closed:
                self.close()

    def show_error(self, message):
        QMessageBox.warning(self, "Не удалось выполнить действие", message)

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Добавить документы", "", "Документы (*.pdf *.jpg *.jpeg *.png);;Все файлы (*)"
        )
        if paths:
            self.import_paths(paths)

    def _append_sources(self, sources: list[Source]) -> int:
        self.original_paths.update(source.path for source in sources)
        for source in sources:
            if source.fingerprint is not None:
                self.original_guard.record_fingerprint(source.fingerprint)
            else:
                self.original_guard.register(source.path)
        existing = {
            (
                self.sources.item(i).data(Qt.ItemDataRole.UserRole).path,
                self.sources.item(i).data(Qt.ItemDataRole.UserRole).page,
            )
            for i in range(self.sources.count())
        }
        added = 0
        for source in sources:
            if (source.path, source.page) in existing:
                continue
            item = QListWidgetItem(QIcon(pixmap(source.thumbnail)), source.label)
            item.setData(Qt.ItemDataRole.UserRole, source)
            item.setSizeHint(QSize(190, 150))
            item.setToolTip(str(source.path))
            self.sources.addItem(item)
            existing.add((source.path, source.page))
            added += 1
        if self.sources.currentRow() < 0 and self.sources.count():
            self.sources.setCurrentRow(0)
        return added

    def import_paths(self, paths):
        if self._closing or self._closed:
            return
        self.original_paths.update(Path(path).resolve() for path in paths)
        self.original_guard.register_many(paths)
        pdf_paths = [Path(path).resolve() for path in paths if Path(path).suffix.lower() == ".pdf"]
        other_paths = [path for path in paths if Path(path).suffix.lower() != ".pdf"]

        if other_paths:

            def work():
                sources, errors = [], []
                for path in other_paths:
                    try:
                        sources.extend(discover(path, original_guard=self.original_guard))
                    except Exception as exc:
                        errors.append(f"{Path(path).name}: {exc}")
                return sources, errors

            def done(result):
                if self._closing or self._closed:
                    return
                sources, errors = result
                self._append_sources(sources)
                self.statusBar().showMessage(
                    f"Добавлено страниц и изображений: {len(sources)}. Выберите стороны документа."
                )
                if errors:
                    self.show_error("\n\n".join(errors))

            self.submit(work, done)

        if pdf_paths:
            self.import_generation += 1
            import_gen = self.import_generation
            for pdf_path in pdf_paths:
                self._import_pdf_batched(pdf_path, import_gen)

    def _import_pdf_batched(
        self, pdf_path: Path, import_gen: int, *, on_source_loaded=None
    ) -> None:
        try:
            fp = fingerprint_file(pdf_path)
        except ValueError as exc:
            self.show_error(f"{pdf_path.name}: {exc}")
            return
        self.original_guard.record_fingerprint(fp)

        def request_batch(start_page: int) -> None:
            if self._closing or self._closed:
                return

            def on_batch(batch: ThumbnailBatchResult) -> None:
                if self._closing or self._closed:
                    return
                sources = [
                    Source(Path(batch.path), page_idx, img.to_pil(), fingerprint=fp)
                    for page_idx, img in batch.items
                ]
                self._append_sources(sources)
                if on_source_loaded is not None:
                    for src in sources:
                        on_source_loaded(src)
                next_start = batch.items[-1][0] + 1 if batch.items else batch.total_pages
                if next_start < batch.total_pages:
                    self.statusBar().showMessage(
                        f"Загрузка миниатюр {pdf_path.name}: {next_start} из {batch.total_pages}…"
                    )
                    request_batch(next_start)
                else:
                    self.statusBar().showMessage(
                        f"Добавлено страниц PDF ({batch.total_pages}): {pdf_path.name}. Выберите стороны документа."
                    )

            def on_batch_error(message: str) -> None:
                if not (self._closing or self._closed):
                    self.show_error(f"{pdf_path.name}: {message}")

            self.submit_pdf(
                ThumbnailBatchRequest(
                    str(pdf_path),
                    start_page=start_page,
                    count=DEFAULT_THUMBNAIL_BATCH_SIZE,
                    session_id=import_gen,
                ),
                on_batch,
                on_batch_error,
            )

        def on_inspected(info: PdfInfo) -> None:
            if self._closing or self._closed:
                return
            self.original_paths.add(Path(info.path))
            self.original_guard.register(info.path)
            request_batch(0)

        def on_inspect_error(message: str) -> None:
            if not (self._closing or self._closed):
                self.show_error(f"{pdf_path.name}: {message}")

        self.submit_pdf(
            InspectPdfRequest(str(pdf_path), session_id=import_gen),
            on_inspected,
            on_inspect_error,
        )

    def reload_selected_source(self):
        if self._closing or self._closed:
            return
        item = self.sources.currentItem()
        if item is None:
            self.statusBar().showMessage("Сначала выберите файл или страницу слева.")
            return
        selected_source: Source = item.data(Qt.ItemDataRole.UserRole)
        target_path = selected_source.path.resolve()
        try:
            fp = fingerprint_file(target_path)
        except ValueError as exc:
            self.show_error(str(exc))
            return
        self.original_guard.record_fingerprint(fp)
        affected_sides = [
            (side, card.source.page)
            for side, card in self.cards.items()
            if card.source.path.resolve() == target_path
        ]

        if selected_source.page is None:

            def work():
                return discover(target_path, original_guard=self.original_guard)

            def done(sources):
                if self._closing or self._closed or not sources:
                    return
                new_source = sources[0]
                for i in range(self.sources.count()):
                    row_item = self.sources.item(i)
                    row_src = row_item.data(Qt.ItemDataRole.UserRole)
                    if row_src.path.resolve() == target_path and row_src.page is None:
                        row_item.setData(Qt.ItemDataRole.UserRole, new_source)
                        row_item.setIcon(QIcon(pixmap(new_source.thumbnail)))
                for side, _ in affected_sides:
                    self._load_source_for_side(side, new_source)
                self.statusBar().showMessage(f"Файл перезагружен: {target_path.name}")

            self.submit(work, done)
        else:
            for i in range(self.sources.count() - 1, -1, -1):
                if (
                    self.sources.item(i).data(Qt.ItemDataRole.UserRole).path.resolve()
                    == target_path
                ):
                    self.sources.takeItem(i)
            self.import_generation += 1

            def on_pdf_source(reloaded_src: Source):
                for side, expected_page in affected_sides:
                    if reloaded_src.page == expected_page:
                        self._load_source_for_side(side, reloaded_src)

            self._import_pdf_batched(
                target_path, self.import_generation, on_source_loaded=on_pdf_source
            )

    def assign_next(self, _item=None):
        self.assign_selected(
            "front" if "front" not in self.cards and "front" not in self.pending_loads else "back"
        )

    def assign_selected(self, side):
        if self._closing or self._closed:
            return
        item = self.sources.currentItem()
        if item is None:
            self.statusBar().showMessage("Сначала выберите файл или страницу слева.")
            return
        source = item.data(Qt.ItemDataRole.UserRole)
        if source.fingerprint is not None:
            try:
                self.original_guard.validate_source(source.path, expected=source.fingerprint)
            except SourceChangedError as exc:
                self.show_error(str(exc))
                return
        self._load_source_for_side(side, source)

    def _load_source_for_side(self, side: str, source: Source):
        self._clear_draft()
        self.load_generation[side] += 1
        token = self.load_generation[side]
        self.pending_loads.add(side)
        self.refresh_inspectors()
        self.invalidate_sheet("Загрузка выбранной стороны…")

        def finish_load(result):
            if self._closing or self._closed or token != self.load_generation[side]:
                return
            self.pending_loads.discard(side)
            self._clear_draft()
            original, points, display, found = result
            card = Card(source, original, points, display=display)
            self.cards[side] = card
            self.refresh_inspectors()
            self.side_combo.setCurrentIndex(0 if side == "front" else 1)
            self.active_side = side
            self.statusBar().showMessage(
                "Проверьте автоматически найденные углы."
                if found
                else "Контур не найден. Укажите четыре угла вручную или перетащите точки."
            )
            self._record_history(f"Назначение: {SIDE_NAMES[side]}")
            self.show_card()
            self.render_card(side)

        def fail_load(message):
            if not (self._closing or self._closed) and token == self.load_generation[side]:
                self.pending_loads.discard(side)
                self.show_error(message)
                self.layout_changed()

        if source.page is None:

            def work():
                original = load_source(source, original_guard=self.original_guard)
                found = detect_quad(original)
                h, w = original.shape[:2]
                return (
                    original,
                    found or full_quad(w, h),
                    thumbnail(Image.fromarray(original)),
                    found is not None,
                )

            self.submit(work, finish_load, fail_load)
        else:

            def on_pdf_rendered(rendered):
                if self._closing or self._closed or token != self.load_generation[side]:
                    return
                original = rendered.to_array()

                def image_work():
                    found = detect_quad(original)
                    h, w = original.shape[:2]
                    return (
                        original,
                        found or full_quad(w, h),
                        thumbnail(Image.fromarray(original)),
                        found is not None,
                    )

                self.submit(image_work, finish_load, fail_load)

            self.submit_pdf(
                RenderPageRequest(
                    str(source.path),
                    source.page,
                    dpi=PDF_DPI,
                    session_id=self.session_id,
                    revision=token,
                ),
                on_pdf_rendered,
                fail_load,
                coalesce_key=("load_side", side),
            )

    def remove_source(self):
        row = self.sources.currentRow()
        if row >= 0:
            self.sources.takeItem(row)
            self.statusBar().showMessage(
                "Миниатюра убрана. Назначенные стороны и исходный файл сохранены."
            )

    def remove_side(self):
        if self.draft_side == self.active_side:
            self._clear_draft()
        self.load_generation[self.active_side] += 1
        self.pending_loads.discard(self.active_side)
        self.pdf_worker.cancel_key(("load_side", self.active_side))
        removed = self.cards.pop(self.active_side, None)
        if removed is not None:
            self._record_history(f"Удаление: {SIDE_NAMES[self.active_side]}")
        self.refresh_inspectors()
        self.show_card()
        self.layout_changed()

    def change_side(self):
        had_draft = self._clear_draft()
        self.active_side = self.side_combo.currentData()
        self.show_card()
        if had_draft:
            self.refresh_sheet()

    def is_drafting(self) -> bool:
        return self.draft_message is not None or self.editor.is_drafting

    def _clear_draft(self) -> bool:
        had_draft = self.is_drafting()
        self.editor.cancel_draft(emit=False)
        self.draft_message = None
        self.draft_side = None
        return had_draft

    def on_draft_changed(self, kind: str, corner: int):
        label = CORNER_LABELS[corner - 1]
        if kind == "pick":
            message = (
                f"Выделение по 4 точкам — укажите угол {corner} из 4 ({label}). "
                "Экспорт приостановлен. Esc — отмена."
            )
        else:
            message = (
                f"Редактирование выделения… Угол {corner} из 4 ({label}). "
                "Экспорт приостановлен до завершения. Esc — отмена."
            )
        self.draft_message = message
        self.draft_side = self.active_side
        self.layout_timer.stop()
        self.invalidate_sheet(message)
        self.tabs.setTabText(1, "Предпросмотр A4 · редактирование")
        self.statusBar().showMessage(message)
        self._sync_controls()

    def on_draft_cancelled(self):
        self.draft_message = None
        self.draft_side = None
        self.layout_timer.stop()
        self.statusBar().showMessage("Выделение отменено. Восстановлены предыдущие углы.")
        self._sync_controls()
        self.refresh_sheet()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.editor.is_drafting:
            self.editor.cancel_draft()
            event.accept()
            return
        super().keyPressEvent(event)

    def show_card(self):
        card = self.cards.get(self.active_side)
        if card and card.display:
            self.editor.set_image(card.display, card.source_size, card.points)
            self.source_label.setText(card.source.label)
        else:
            self.editor.set_image(Image.new("RGB", (700, 440), "#edf1f5"))
            self.source_label.setText("Сторона не назначена. Выберите миниатюру слева.")
        self._sync_controls()

    def _sync_controls(self):
        self.syncing = True
        card = self.cards.get(self.active_side)
        self.editor.setEnabled(bool(card and card.display_turns == card.turns))
        for button in self.editor_actions:
            button.setEnabled(card is not None)
        self.size_mode.setEnabled(card is not None)
        self.radius.setEnabled(card is not None)
        self.reset_button.setEnabled(card is not None)
        self.inspect_button.setEnabled(
            bool(
                card
                and card.rendered
                and card.ready_revision == card.revision
                and not (self.is_drafting() and self.draft_side == self.active_side)
            )
        )
        if card:
            self.size_mode.setCurrentIndex(self.size_mode.findData(card.size.mode))
            size = card.size.resolve(card.cropped.size if card.cropped else card.source_size)
            self.width.setValue(size[0])
            self.height.setValue(size[1])
            self.radius.setValue(card.radius)
        mode = self.size_mode.currentData()
        self.width.setEnabled(card is not None and mode != "id1")
        self.height.setEnabled(card is not None and mode == "custom")
        self.syncing = False
        self.update_card_info()

    def update_card_info(self):
        card = self.cards.get(self.active_side)
        self.ratio_label.clear()
        self.resolution_label.clear()
        if card is None or card.cropped is None:
            return
        w, h = card.cropped.size
        mm_w, mm_h = card.size.resolve((w, h))
        deviation = (mm_w / mm_h) / (w / h) - 1
        if abs(deviation) > 0.005:
            self.ratio_label.setText(
                f"⚠ Размер изменяет пропорции выделения на {deviation:+.1%}. Для сохранения выберите «Исходные пропорции»."
            )
            self.ratio_label.setStyleSheet("color: #a34c00")
        else:
            self.ratio_label.setText("Пропорции выделения сохранены (отклонение ≤ 0,5%).")
            self.ratio_label.setStyleSheet("")
        dpi = min(w / mm_w, h / mm_h) * 25.4
        self.resolution_label.setText(
            f"После перспективы: {w} × {h} пикс.\nПри печати: ≈ {dpi:.0f} DPI.\nУвеличение размера не добавляет деталей."
        )

    def invalidate_sheet(self, message="Обновление предпросмотра…"):
        self.layout_generation += 1
        self.sheet = None
        self.pdf_button.setEnabled(False)
        self.png_button.setEnabled(False)
        self.layout_message.setText(message)
        self.layout_message.setStyleSheet("")
        self.preview.setEnabled(False)
        self.tabs.setTabText(1, "Предпросмотр A4 · обновление")

    def selection_changed(self, points):
        self.draft_message = None
        self.draft_side = None
        card = self.cards.get(self.active_side)
        if card is None:
            return
        card.points = points
        card.cropped = None
        self._record_history("Изменение выделения")
        self.render_card(self.active_side)

    def pick_points(self):
        if self.active_side not in self.cards:
            return
        self.editor.begin_selection()

    def reset_selection(self):
        self._clear_draft()
        card = self.cards.get(self.active_side)
        if card:
            card.points = full_quad(*card.source_size)
            self.editor.set_points(card.points)
            card.cropped = None
            self._record_history("Сброс выделения")
            self.render_card(self.active_side)

    def autodetect(self):
        had_draft = self._clear_draft()
        if had_draft:
            self._sync_controls()
        side = self.active_side
        card = self.cards.get(side)
        if not card:
            if had_draft:
                self.refresh_sheet()
            return
        revision, turns = card.revision, card.turns

        def done(points):
            if (
                self._closing
                or self._closed
                or self.cards.get(side) is not card
                or card.revision != revision
            ):
                return
            if points is None:
                self.statusBar().showMessage(
                    "Контур не найден. Ручное выделение доступно: нажмите «Выделить 4 точки»."
                )
                if had_draft:
                    self.refresh_sheet()
                return
            card.points = points
            card.cropped = None
            if self.active_side == side:
                self.editor.set_points(points)
            self._record_history("Определение границ")
            self.render_card(side)
            self.statusBar().showMessage("Контур найден. Проверьте углы и края результата.")

        def failed(message):
            if (
                not (self._closing or self._closed)
                and self.cards.get(side) is card
                and card.revision == revision
            ):
                self.show_error(message)
                if had_draft:
                    self.refresh_sheet()

        self.submit(lambda: detect_quad(rotated(card.original, turns)), done, failed)

    def rotate(self):
        self._clear_draft()
        side = self.active_side
        card = self.cards.get(side)
        if not card:
            return
        card.points = rotate_quad(card.points, card.source_size[1])
        card.turns = (card.turns + 1) % 4
        card.cropped = None
        self._record_history("Поворот на 90°")
        self.render_card(side, update_display=True)

    def reset_card(self):
        self._clear_draft()
        card = self.cards.get(self.active_side)
        if card:
            card.turns = 0
            card.points = full_quad(*card.source_size)
            card.radius = 0
            card.size = CardSize()
            card.cropped = None
            self._record_history("Сброс правок стороны")
            self.render_card(self.active_side, update_display=True)

    def card_options_changed(self):
        if self.syncing:
            return
        card = self.cards.get(self.active_side)
        if card:
            card.size = CardSize(
                self.size_mode.currentData(), self.width.value(), self.height.value()
            )
            card.radius = self.radius.value()
            self._record_history("Параметры стороны")
            self.render_card(self.active_side)
            self._sync_controls()

    def render_card(self, side, update_display=False):
        if self._closing or self._closed:
            return
        card = self.cards[side]
        self._card_revision_seq = max(self._card_revision_seq, card.revision) + 1
        card.revision = self._card_revision_seq
        revision = card.revision
        card.error = None
        source_size = card.source_size
        update_display = update_display or card.display is None or card.display_turns != card.turns
        turns, points, radius, size, cached = (
            card.turns,
            card.points,
            card.radius,
            card.size,
            card.cropped,
        )
        self.refresh_inspectors()
        if self.is_drafting():
            self.invalidate_sheet(self.draft_message or "Редактирование выделения…")
            self.tabs.setTabText(1, "Предпросмотр A4 · редактирование")
        else:
            self.invalidate_sheet()
        self._sync_controls()

        def work():
            if card.revision != revision:
                return None
            validate_quad(points, *source_size)
            cropped = cached if cached is not None else rectify(card.original, turns, points)
            rendered = round_corners(cropped, radius, size.resolve(cropped.size))
            display = (
                thumbnail(Image.fromarray(rotated(card.original, turns)))
                if update_display
                else None
            )
            return cropped, rendered, display

        def done(result):
            if (
                self._closing
                or self._closed
                or result is None
                or self.cards.get(side) is not card
                or card.revision != revision
            ):
                return
            card.cropped, card.rendered, display = result
            card.ready_revision = revision
            if display:
                card.display = display
                card.display_turns = turns
            if self.active_side == side:
                if update_display:
                    self.show_card()
                else:
                    self._sync_controls()
            self.layout_changed()

        def failed(message):
            if (
                not (self._closing or self._closed)
                and self.cards.get(side) is card
                and card.revision == revision
            ):
                card.error = message
                if self.is_drafting():
                    self.invalidate_sheet(self.draft_message or "Редактирование выделения…")
                    self.tabs.setTabText(1, "Предпросмотр A4 · редактирование")
                else:
                    self.invalidate_sheet(message)
                    self.layout_message.setStyleSheet("color: #ad352c")
                    self.tabs.setTabText(1, "Предпросмотр A4 · исправьте выделение")

        self.submit(work, done, failed)

    def current_layout(self):
        return Layout(
            self.template.currentData(),
            self.gap.value(),
            self.margin.value(),
            self.offset_x.value(),
            self.offset_y.value(),
        )

    def layout_options_changed(self):
        if self.syncing or self._closing or self._closed:
            return
        self._record_history("Компоновка A4")
        self.layout_changed()

    def _sync_history_controls(self):
        self.undo_button.setEnabled(self.history.can_undo)
        self.redo_button.setEnabled(self.history.can_redo)
        evicted_note = (
            f" (часть старой истории очищена; доступно шагов: {self.history.undo_depth})"
            if self.history.evicted_count > 0
            else ""
        )
        if self.history.can_undo:
            label = self.history.undo_label or "Правка"
            self.undo_button.setToolTip(f"Отменить: {label} (Ctrl+Z){evicted_note}")
        else:
            self.undo_button.setToolTip(f"Нечего отменять (Ctrl+Z){evicted_note}")
        if self.history.can_redo:
            label = self.history.redo_label or "Правка"
            self.redo_button.setToolTip(f"Повторить: {label} (Ctrl+Y / Ctrl+Shift+Z)")
        else:
            self.redo_button.setToolTip("Нечего повторять (Ctrl+Y / Ctrl+Shift+Z)")

    def _record_history(self, label: str):
        snap = WorkspaceSnapshot.from_state(self.cards, self.current_layout())
        result = self.history.push(snap, label)
        self._sync_history_controls()
        self._sync_dirty_ui()
        if result.evicted > 0:
            if result.evicted_for_memory > 0:
                self.statusBar().showMessage(
                    f"Часть старой истории отмены очищена по лимиту памяти (доступно шагов: {self.history.undo_depth})."
                )
            else:
                self.statusBar().showMessage(
                    f"Часть старой истории отмены очищена (доступно шагов: {self.history.undo_depth})."
                )

    def undo(self):
        if self._closing or self._closed:
            return
        step = self.history.undo()
        if step is None:
            return
        snapshot, label = step
        suffix = f": {label}" if label else ""
        self._restore_snapshot(snapshot, f"Отменено{suffix}.")

    def redo(self):
        if self._closing or self._closed:
            return
        step = self.history.redo()
        if step is None:
            return
        snapshot, label = step
        suffix = f": {label}" if label else ""
        self._restore_snapshot(snapshot, f"Повторено{suffix}.")

    def _restore_snapshot(self, snapshot: WorkspaceSnapshot, status_message: str):
        self._clear_draft()
        self.layout_timer.stop()
        for side in ("front", "back"):
            self.load_generation[side] += 1
            self.pending_loads.discard(side)
            self.pdf_worker.cancel_key(("load_side", side))
            self.inspector_request_id[side] += 1

        previous_cards = self.cards
        restored: dict[str, Card] = {}
        for side, card_snap in snapshot.cards:
            self._card_revision_seq += 1
            card = Card.from_snapshot(card_snap, revision=self._card_revision_seq)
            old_card = previous_cards.get(side)
            if (
                old_card is not None
                and old_card.original is card_snap.original
                and old_card.display is not None
                and old_card.display_turns == card.turns
            ):
                card.display = old_card.display
                card.display_turns = old_card.display_turns
            restored[side] = card
        self.cards = restored

        self.syncing = True
        try:
            idx = self.template.findData(snapshot.layout.template)
            if idx >= 0:
                self.template.setCurrentIndex(idx)
            self.gap.setValue(snapshot.layout.gap_mm)
            self.margin.setValue(snapshot.layout.margin_mm)
            self.offset_x.setValue(snapshot.layout.offset_x_mm)
            self.offset_y.setValue(snapshot.layout.offset_y_mm)
            if self.active_side not in self.cards and self.cards:
                next_side = next(iter(self.cards))
                self.active_side = next_side
                self.side_combo.setCurrentIndex(0 if next_side == "front" else 1)
        finally:
            self.syncing = False

        self.refresh_inspectors()
        self.show_card()
        self._sync_history_controls()
        self._sync_dirty_ui()
        self.statusBar().showMessage(status_message)
        if self.cards:
            for side, card in self.cards.items():
                self.render_card(side, update_display=(card.display is None))
        else:
            self.refresh_sheet()

    def layout_changed(self):
        if self.syncing or self._closing or self._closed:
            return
        if self.is_drafting():
            self.layout_timer.stop()
            self.invalidate_sheet(self.draft_message or "Редактирование выделения…")
            self.tabs.setTabText(1, "Предпросмотр A4 · редактирование")
            return
        self.invalidate_sheet()
        self.layout_timer.start()

    def refresh_sheet(self):
        if self._closing or self._closed:
            return
        if self.is_drafting():
            self.invalidate_sheet(self.draft_message or "Редактирование выделения…")
            self.tabs.setTabText(1, "Предпросмотр A4 · редактирование")
            return
        if self.pending_loads:
            self.layout_message.setText("Загрузка выбранной стороны…")
            return
        errors = [
            f"{SIDE_NAMES[side]}: {card.error}" for side, card in self.cards.items() if card.error
        ]
        if errors:
            self.layout_message.setText("\n".join(errors))
            self.layout_message.setStyleSheet("color: #ad352c")
            self.tabs.setTabText(1, "Предпросмотр A4 · исправьте выделение")
            return
        if not self.cards:
            self.invalidate_sheet("Назначьте стороны документа.")
            self.preview.set_image(Image.new("RGB", (420, 594), "white"))
            self.tabs.setTabText(1, "Предпросмотр A4")
            return
        if any(card.ready_revision != card.revision for card in self.cards.values()):
            return
        generation = self.layout_generation
        try:
            sizes = {
                side: card.size.resolve(card.rendered.size) for side, card in self.cards.items()
            }
            placements = arrange(sizes, self.current_layout())
            sheet = Sheet({side: card.rendered for side, card in self.cards.items()}, placements)
        except ValueError as exc:
            self.layout_message.setText(str(exc))
            self.layout_message.setStyleSheet("color: #ad352c")
            self.tabs.setTabText(1, "Предпросмотр A4 · не помещается")
            return

        def done(preview):
            if (
                self._closing
                or self._closed
                or generation != self.layout_generation
                or self.is_drafting()
            ):
                return
            self.sheet = sheet
            self.preview.set_image(preview)
            self.preview.setEnabled(True)
            self.pdf_button.setEnabled(True)
            self.png_button.setEnabled(True)
            self.tabs.setTabText(1, "Предпросмотр A4")
            self.layout_message.setStyleSheet("")
            count = len(sheet.placements)
            self.layout_message.setText(
                f"На листе: {count} из 2 сторон.\n"
                + "\n".join(
                    f"{SIDE_NAMES[p.side]}: {p.width_mm:.2f} × {p.height_mm:.2f} мм"
                    for p in sheet.placements
                )
            )

        self.submit(lambda: render_sheet(sheet), done)

    def is_inspector_fresh(
        self, side: str, card: Card, revision: int, load_gen: int, request_id: int
    ) -> bool:
        if self._closing or self._closed:
            return False
        if self.cards.get(side) is not card:
            return False
        if self.load_generation.get(side) != load_gen or side in self.pending_loads:
            return False
        if card.revision != revision or card.ready_revision != revision:
            return False
        if self.inspector_request_id.get(side) != request_id:
            return False
        return True

    def inspect_card(self):
        if self._closing or self._closed:
            return
        side = self.active_side
        card = self.cards.get(side)
        if not card or card.ready_revision != card.revision or card.rendered is None:
            return
        if self.is_drafting() and self.draft_side == side:
            return
        image = card.rendered
        revision = card.revision
        load_gen = self.load_generation[side]
        self.inspector_request_id[side] += 1
        request_id = self.inspector_request_id[side]

        def done(result):
            if self.is_inspector_fresh(side, card, revision, load_gen, request_id):
                self.show_inspector(result, side, card, revision, load_gen)

        def failed(message):
            if self.is_inspector_fresh(side, card, revision, load_gen, request_id):
                self.show_error(message)

        self.submit(lambda: to_qimage(image), done, failed)

    def _remove_inspector(self, dialog: InspectorDialog):
        if dialog in self.inspectors:
            self.inspectors.remove(dialog)

    def show_inspector(self, image, side: str, card: Card, revision: int, load_gen: int):
        dialog = InspectorDialog(
            self,
            image,
            side,
            card,
            revision,
            load_gen,
            self.view_toolbar,
            on_close=self._remove_inspector,
        )
        self.inspectors.append(dialog)
        dialog.show()

    def refresh_inspectors(self):
        for dialog in list(self.inspectors):
            if dialog.stale:
                continue
            current = self.cards.get(dialog.side)
            if dialog.side in self.pending_loads:
                dialog.mark_stale("Снимок устарел: загружается новый документ для этой стороны.")
            elif current is None:
                dialog.mark_stale("Снимок устарел: сторона была убрана.")
            elif (
                current is not dialog.card
                or self.load_generation.get(dialog.side) != dialog.load_generation
            ):
                dialog.mark_stale("Снимок устарел: для стороны назначен другой документ.")
            elif current.revision != dialog.revision:
                dialog.mark_stale(
                    f"Снимок устарел: параметры стороны изменены (правка #{current.revision})."
                )

    def current_workspace_snapshot(self) -> WorkspaceSnapshot:
        return WorkspaceSnapshot.from_state(self.cards, self.current_layout())

    def can_export_now(self) -> bool:
        return bool(self.sheet is not None and not self.is_drafting() and not self.pending_loads)

    def _sync_dirty_ui(self):
        snap = self.current_workspace_snapshot()
        dirty = self.checkpoints.needs_close_confirmation(snap)
        self.setWindowTitle(f"* {BASE_WINDOW_TITLE}" if dirty else BASE_WINDOW_TITLE)
        record = self.checkpoints.last_export_record()
        if record is not None and self.export_path is not None:
            self.open_button.show()
            if self.checkpoints.is_last_export_stale(snap):
                self.open_button.setText("Открыть ранний экспорт")
                self.open_button.setToolTip(
                    f"Открыть более ранний результат ({self.export_path.name}); "
                    "после него были внесены правки."
                )
                self.export_message.setText(
                    f"Последний экспорт ({self.export_path.name}) относится к более ранней "
                    f"правке и не сохраняет редактируемый проект.\n\n{PRINT_HINT}"
                )
            else:
                self.open_button.setText("Открыть сохранённый файл")
                self.open_button.setToolTip(f"Открыть {self.export_path.name}")
                self.export_message.setText(
                    f"Экспортировано: {self.export_path.name} "
                    f"(экспорт не сохраняет редактируемый проект).\n\n{PRINT_HINT}"
                )

    def _build_unsaved_dialog(self, *, can_export: bool):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Есть неэкспортированные изменения")
        box.setText("Текущие правки ещё не экспортированы в PDF или PNG.")
        box.setInformativeText("При закрытии окна неэкспортированные изменения будут потеряны.")
        discard_btn = box.addButton("Выйти без сохранения", QMessageBox.ButtonRole.DestructiveRole)
        export_btn = (
            box.addButton("Сохранить PDF", QMessageBox.ButtonRole.AcceptRole)
            if can_export
            else None
        )
        cancel_btn = box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        box.setEscapeButton(cancel_btn)
        box.setDefaultButton(export_btn or cancel_btn)
        return box, discard_btn, export_btn, cancel_btn

    def _prompt_unsaved_changes(self, *, can_export: bool) -> str:
        box, discard_btn, export_btn, _cancel_btn = self._build_unsaved_dialog(
            can_export=can_export
        )
        box.exec()
        clicked = box.clickedButton()
        if clicked is discard_btn:
            return "discard"
        if export_btn is not None and clicked is export_btn:
            return "export_pdf"
        return "cancel"

    def save(self, kind, *, close_on_success: bool = False) -> bool:
        if self.is_drafting():
            self._pending_close_after_export = False
            self.show_error("Завершите выделение или нажмите Esc для отмены перед экспортом.")
            return False
        if self.sheet is None:
            self._pending_close_after_export = False
            self.show_error("Дождитесь обработки и исправьте выделение или компоновку.")
            return False
        captured_snapshot = self.current_workspace_snapshot()
        revisions = tuple(sorted((side, card.revision) for side, card in self.cards.items()))
        source_fps = tuple(
            card.source.fingerprint
            for card in self.cards.values()
            if card.source.fingerprint is not None
        )
        for fp in source_fps:
            try:
                self.original_guard.validate_source(fp.path, expected=fp)
            except SourceChangedError as exc:
                self._pending_close_after_export = False
                self.show_error(str(exc))
                return False
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить лист A4",
            f"Документы_A4.{kind}",
            f"{kind.upper()} (*.{kind})",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not path:
            self._pending_close_after_export = False
            return False
        target = Path(path)
        if target.suffix.lower() != f".{kind}":
            target = target.with_suffix(f".{kind}")
        self.original_guard.register_many(self.original_paths)
        self.original_guard.register_many(card.source.path for card in self.cards.values())
        try:
            self.original_guard.assert_not_protected(target)
        except OriginalOverwriteError as exc:
            self._pending_close_after_export = False
            self.show_error(str(exc))
            return False
        overwrite = target.exists()
        if overwrite:
            answer = QMessageBox.question(
                self,
                "Заменить файл?",
                f"Файл «{target.name}» уже существует. Заменить его?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._pending_close_after_export = False
                return False
        snap = snapshot_sheet(self.sheet, revisions=revisions, source_fingerprints=source_fps)
        frozen_sheet = Sheet(dict(snap.images), list(snap.placements))
        self._pending_close_after_export = close_on_success

        def done(_):
            if self._closing or self._closed:
                return
            self.checkpoints.mark_exported(kind, captured_snapshot, snap.revisions, target)
            self.last_exported_revisions[kind] = snap.revisions
            self.export_path = target
            self._sync_dirty_ui()
            self.statusBar().showMessage(f"Сохранено: {target}")
            if self._pending_close_after_export:
                self._pending_close_after_export = False
                if not self.checkpoints.needs_close_confirmation(self.current_workspace_snapshot()):
                    self.close()

        def failed(message):
            self._pending_close_after_export = False
            if not (self._closing or self._closed):
                self.show_error(message)

        self.submit(
            lambda: export_sheet(
                frozen_sheet,
                target,
                kind,
                overwrite=overwrite,
                pdf_worker=self.pdf_worker,
                original_guard=self.original_guard,
                source_fingerprints=snap.source_fingerprints,
            ),
            done,
            failed,
        )
        return True

    def open_export(self):
        if self.export_path and not QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(self.export_path.resolve()))
        ):
            self.show_error("Не удалось открыть файл в системной программе.")

    def closeEvent(self, event):
        if self._closed:
            event.accept()
            return
        if (
            not self._closing
            and not self._discard_confirmed
            and self.checkpoints.needs_close_confirmation(self.current_workspace_snapshot())
        ):
            choice = self._prompt_unsaved_changes(can_export=self.can_export_now())
            if choice == "cancel":
                event.ignore()
                return
            if choice == "export_pdf":
                event.ignore()
                self.save("pdf", close_on_success=True)
                return
            if choice == "discard":
                self._discard_confirmed = True
        self.layout_timer.stop()
        self._clear_draft()
        self.pdf_worker.cancel_pending(keep_exports=True)
        if self.tasks:
            self._closing = True
            self.statusBar().showMessage("Завершение фоновых операций перед закрытием…")
            self.centralWidget().setEnabled(False)
            event.ignore()
        else:
            self._closed = True
            for dialog in list(self.inspectors):
                dialog.close()
            self.inspectors.clear()
            if self._owns_pdf_worker:
                self.pdf_worker.shutdown()
            event.accept()

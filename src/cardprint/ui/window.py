from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image
from PySide6.QtCore import QSize, Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
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

from ..export import PRINT_HINT, Sheet, export_sheet, render_sheet
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
from .editor import CORNER_LABELS, ImageView, pixmap, to_qimage
from .tasks import Task

SIDE_NAMES = {"front": "Лицевая сторона", "back": "Обратная сторона"}


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


@dataclass
class Card:
    source: Source
    original: np.ndarray
    points: tuple
    turns: int = 0
    radius: float = 0
    size: CardSize = field(default_factory=CardSize)
    revision: int = 0
    ready_revision: int = -1
    cropped: Image.Image | None = None
    rendered: Image.Image | None = None
    display: Image.Image | None = None
    display_turns: int = 0
    error: str | None = None

    @property
    def source_size(self):
        h, w = self.original.shape[:2]
        return (h, w) if self.turns % 2 else (w, h)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Карточки на A4 · локальная подготовка к печати")
        self.resize(1380, 880)
        self.cards: dict[str, Card] = {}
        self.active_side = "front"
        self.sheet = None
        self.layout_generation = 0
        self.load_generation = {"front": 0, "back": 0}
        self.inspector_request_id = {"front": 0, "back": 0}
        self.inspectors: list[InspectorDialog] = []
        self.draft_message: str | None = None
        self.draft_side: str | None = None
        self.tasks = {}
        self.pending_loads = set()
        self.original_paths = set()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.syncing = False
        self._closing = False
        self._closed = False
        self._build_ui()
        self.layout_timer = QTimer(self)
        self.layout_timer.setSingleShot(True)
        self.layout_timer.setInterval(160)
        self.layout_timer.timeout.connect(self.refresh_sheet)
        self.statusBar().showMessage(
            "Добавьте изображения или PDF. Все документы обрабатываются на этом компьютере."
        )
        self._sync_controls()
        self.invalidate_sheet("Назначьте стороны документа.")
        self.preview.set_image(Image.new("RGB", (420, 594), "white"))

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        toolbar = QHBoxLayout()
        self.add_button = self.button("Добавить файлы", self.add_files)
        toolbar.addWidget(self.add_button)
        toolbar.addStretch()
        toolbar.addWidget(self.button("Предпросмотр", lambda: self.tabs.setCurrentIndex(1)))
        self.pdf_button = self.button("Сохранить PDF", lambda: self.save("pdf"))
        self.png_button = self.button("PNG · 300 DPI", lambda: self.save("png"))
        toolbar.addWidget(self.pdf_button)
        toolbar.addWidget(self.png_button)
        outer.addLayout(toolbar)
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
        self.template.currentIndexChanged.connect(self.layout_changed)
        for spin in (self.gap, self.margin, self.offset_x, self.offset_y):
            spin.valueChanged.connect(self.layout_changed)

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

    def import_paths(self, paths):
        self.original_paths.update(Path(path).resolve() for path in paths)

        def work():
            sources, errors = [], []
            for path in paths:
                try:
                    sources.extend(discover(path))
                except Exception as exc:
                    errors.append(f"{Path(path).name}: {exc}")
            return sources, errors

        def done(result):
            sources, errors = result
            self.original_paths.update(source.path for source in sources)
            existing = {
                (
                    self.sources.item(i).data(Qt.ItemDataRole.UserRole).path,
                    self.sources.item(i).data(Qt.ItemDataRole.UserRole).page,
                )
                for i in range(self.sources.count())
            }
            for source in sources:
                if (source.path, source.page) in existing:
                    continue
                item = QListWidgetItem(QIcon(pixmap(source.thumbnail)), source.label)
                item.setData(Qt.ItemDataRole.UserRole, source)
                item.setSizeHint(QSize(190, 150))
                item.setToolTip(str(source.path))
                self.sources.addItem(item)
                existing.add((source.path, source.page))
            if self.sources.currentRow() < 0 and self.sources.count():
                self.sources.setCurrentRow(0)
            self.statusBar().showMessage(
                f"Добавлено страниц и изображений: {len(sources)}. Выберите стороны документа."
            )
            if errors:
                self.show_error("\n\n".join(errors))

        self.submit(work, done)

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
        self._clear_draft()
        self.load_generation[side] += 1
        token = self.load_generation[side]
        self.pending_loads.add(side)
        self.refresh_inspectors()
        self.invalidate_sheet("Загрузка выбранной стороны…")

        def work():
            original = load_source(source)
            found = detect_quad(original)
            h, w = original.shape[:2]
            return (
                original,
                found or full_quad(w, h),
                thumbnail(Image.fromarray(original)),
                found is not None,
            )

        def done(result):
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
            self.show_card()
            self.render_card(side)
            self.statusBar().showMessage(
                "Проверьте автоматически найденные углы."
                if found
                else "Контур не найден. Укажите четыре угла вручную или перетащите точки."
            )

        def failed(message):
            if not (self._closing or self._closed) and token == self.load_generation[side]:
                self.pending_loads.discard(side)
                self.show_error(message)
                self.layout_changed()

        self.submit(work, done, failed)

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
        self.cards.pop(self.active_side, None)
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
            self.render_card(self.active_side)
            self._sync_controls()

    def render_card(self, side, update_display=False):
        if self._closing or self._closed:
            return
        card = self.cards[side]
        card.revision += 1
        revision = card.revision
        card.error = None
        source_size = card.source_size
        update_display = update_display or card.display_turns != card.turns
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

    def save(self, kind):
        if self.is_drafting():
            self.show_error("Завершите выделение или нажмите Esc для отмены перед экспортом.")
            return
        if self.sheet is None:
            self.show_error("Дождитесь обработки и исправьте выделение или компоновку.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить лист A4",
            f"Документы_A4.{kind}",
            f"{kind.upper()} (*.{kind})",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != f".{kind}":
            target = target.with_suffix(f".{kind}")
        originals = set(self.original_paths)
        originals.update(card.source.path for card in self.cards.values())
        if any(
            target.resolve() == original.resolve()
            or (target.exists() and original.exists() and target.samefile(original))
            for original in originals
        ):
            self.show_error("Нельзя перезаписать загруженный оригинал. Выберите другое имя файла.")
            return
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
                return
        sheet = self.sheet

        def done(_):
            if self._closing or self._closed:
                return
            self.export_path = target
            self.open_button.show()
            self.export_message.setText(f"Сохранено: {target.name}\n\n{PRINT_HINT}")
            self.statusBar().showMessage(f"Сохранено: {target}")

        self.submit(lambda: export_sheet(sheet, target, kind, overwrite=overwrite), done)

    def open_export(self):
        if self.export_path and not QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(self.export_path.resolve()))
        ):
            self.show_error("Не удалось открыть файл в системной программе.")

    def closeEvent(self, event):
        if self._closed:
            event.accept()
            return
        self.layout_timer.stop()
        self._clear_draft()
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
            event.accept()

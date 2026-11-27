import secrets
from pathlib import Path

from PIL import Image
from PySide6.QtCore import (
    QByteArray,
    QMimeData,
    QSettings,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QKeySequence,
    QPainter,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
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
    QSizePolicy,
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
    export_sheet,
    fingerprint_file,
    render_sheet,
    snapshot_sheet,
)
from ..geometry import CardSize, Layout, arrange, crop_marks, validate_quad
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
from .editor import CORNER_LABELS, ImageView, SheetView, pixmap, to_qimage
from .icons import render_svg_icon
from .tasks import PdfTaskBridge, Task, dispatch_pdf_request
from .theme import (
    THEME_DARK,
    THEME_LIGHT,
    ThemePalette,
    apply_theme,
    get_palette,
    load_saved_theme,
    save_theme,
    set_widget_property,
)

BASE_WINDOW_TITLE = "Карточки на A4 · локальная подготовка к печати"
SUPPORTED_IMPORT_EXTENSIONS = frozenset({".pdf", ".jpg", ".jpeg", ".png"})
SOURCE_DRAG_MIME = "application/x-cardprint-source-id"
SOURCE_ID_ROLE = Qt.ItemDataRole.UserRole + 1
SOURCE_BADGES_ROLE = Qt.ItemDataRole.UserRole + 2
SIDE_BADGE_LABELS = {"front": "Лиц.", "back": "Обр."}


def partition_dropped_urls(urls: list[QUrl]) -> tuple[list[Path], list[str]]:
    """Split dropped URLs into supported existing local files and rejected items."""
    valid_paths: list[Path] = []
    rejected: list[str] = []
    for url in urls:
        if not url.isLocalFile():
            rejected.append(url.toString() or "внешняя ссылка")
            continue
        path = Path(url.toLocalFile())
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_IMPORT_EXTENSIONS:
            rejected.append(path.name or str(path))
            continue
        valid_paths.append(path)
    return valid_paths, rejected


def render_source_item_icon(
    source: Source, badges: tuple[str, ...], palette: ThemePalette
) -> QIcon:
    """Render source thumbnail into a fixed icon tile with optional Front/Back badges."""
    tile_w, tile_h = 144, 108
    canvas = QPixmap(tile_w, tile_h)
    canvas.fill(Qt.GlobalColor.transparent)
    thumb_pm = pixmap(source.thumbnail)
    scaled = thumb_pm.scaled(
        tile_w,
        tile_h,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    x = (tile_w - scaled.width()) // 2
    y = (tile_h - scaled.height()) // 2
    painter.drawPixmap(x, y, scaled)

    if badges:
        font = QFont(painter.font())
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        bx = 4
        by = 4
        for side in badges:
            label = SIDE_BADGE_LABELS.get(side)
            if not label:
                continue
            bg = QColor(palette.accent if side == "front" else palette.success)
            fg = QColor(
                palette.accent_text
                if side == "front"
                else ("#0f172a" if palette.name == THEME_DARK else "#ffffff")
            )
            text_w = metrics.horizontalAdvance(label)
            pill_w = text_w + 12
            pill_h = metrics.height() + 4
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(bx, by, pill_w, pill_h, 6, 6)
            painter.setPen(fg)
            painter.drawText(bx, by, pill_w, pill_h, Qt.AlignmentFlag.AlignCenter, label)
            bx += pill_w + 4
    painter.end()
    return QIcon(canvas)


class SourceListWidget(QListWidget):
    """Source thumbnail list accepting local file drops and dragging internal source IDs."""

    def __init__(self, on_urls_dropped=None, parent=None):
        super().__init__(parent)
        self._on_urls_dropped = on_urls_dropped
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def mimeTypes(self) -> list[str]:
        return [SOURCE_DRAG_MIME]

    def supportedDropActions(self) -> Qt.DropActions:
        return Qt.DropAction.CopyAction

    def mimeData(self, items) -> QMimeData:
        mime = QMimeData()
        for item in items:
            source_id = item.data(SOURCE_ID_ROLE)
            if isinstance(source_id, str) and source_id:
                mime.setData(SOURCE_DRAG_MIME, QByteArray(source_id.encode("utf-8")))
                break
        return mime

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            if self._on_urls_dropped is not None:
                self._on_urls_dropped(list(event.mimeData().urls()))
            return
        event.ignore()


class SideSlotCard(QFrame):
    """Clickable, droppable visual slot card for the Front or Back document side."""

    selected = Signal(str)
    clearRequested = Signal(str)
    sourceDropped = Signal(str, str)
    urlsDropped = Signal(object)

    def __init__(self, side: str, *, can_accept_source_id=None, parent=None):
        super().__init__(parent)
        self.side = side
        self._can_accept_source_id = can_accept_source_id
        self.setObjectName(f"side_slot_{side}")
        self.setAccessibleName(f"Слот стороны: {SIDE_NAMES[side]}")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptDrops(True)
        set_widget_property(self, "slot", "side")
        set_widget_property(self, "active", "false")
        set_widget_property(self, "dragOver", "false")

        root = QHBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(8)

        self.preview_label = QLabel("Перетащите\nфайл")
        self.preview_label.setFixedSize(64, 44)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setWordWrap(True)
        set_widget_property(self.preview_label, "role", "slotPreview")
        root.addWidget(self.preview_label)

        info = QVBoxLayout()
        info.setSpacing(3)
        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        self.title_label = QLabel(SIDE_NAMES[side])
        set_widget_property(self.title_label, "role", "sectionTitle")
        top_row.addWidget(self.title_label)
        self.status_pill = QLabel("Не назначена")
        self.status_pill.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        set_widget_property(self.status_pill, "pill", "status")
        set_widget_property(self.status_pill, "status", "muted")
        top_row.addWidget(self.status_pill)
        top_row.addStretch()
        info.addLayout(top_row)

        self.source_name_label = QLabel("Не выбрано")
        set_widget_property(self.source_name_label, "status", "muted")
        info.addWidget(self.source_name_label)
        root.addLayout(info, 1)

        self.clear_button = QPushButton("×")
        set_widget_property(self.clear_button, "variant", "slotClear")
        self.clear_button.setAccessibleName(f"Убрать сторону: {SIDE_NAMES[side]}")
        self.clear_button.setToolTip(f"Очистить {SIDE_NAMES[side].lower()}")
        self.clear_button.clicked.connect(lambda: self.clearRequested.emit(self.side))
        root.addWidget(self.clear_button, 0, Qt.AlignmentFlag.AlignTop)

    def update_state(
        self,
        *,
        active: bool,
        source_label: str,
        status_text: str,
        status_kind: str,
        tooltip: str,
        preview_pixmap: QPixmap | None,
        can_clear: bool,
    ) -> None:
        set_widget_property(self, "active", "true" if active else "false")
        self.source_name_label.setText(source_label)
        self.source_name_label.setToolTip(tooltip or source_label)
        self.status_pill.setText(status_text)
        set_widget_property(self.status_pill, "status", status_kind)
        if preview_pixmap is not None and not preview_pixmap.isNull():
            self.preview_label.setText("")
            self.preview_label.setPixmap(preview_pixmap)
        else:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Перетащите\nфайл")
        self.clear_button.setEnabled(can_clear)
        self.setToolTip(tooltip or f"Выбрать для редактирования: {SIDE_NAMES[self.side]}")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.side)
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.selected.emit(self.side)
            event.accept()
            return
        super().keyPressEvent(event)

    def _source_id_from_mime(self, mime: QMimeData) -> str | None:
        if not mime.hasFormat(SOURCE_DRAG_MIME):
            return None
        source_id = bytes(mime.data(SOURCE_DRAG_MIME)).decode("utf-8", errors="ignore").strip()
        if not source_id:
            return None
        if self._can_accept_source_id is not None and not self._can_accept_source_id(source_id):
            return None
        return source_id

    def dragEnterEvent(self, event):
        mime = event.mimeData()
        if self._source_id_from_mime(mime) is not None or mime.hasUrls():
            set_widget_property(self, "dragOver", "true")
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event):
        mime = event.mimeData()
        if self._source_id_from_mime(mime) is not None or mime.hasUrls():
            set_widget_property(self, "dragOver", "true")
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event):
        set_widget_property(self, "dragOver", "false")
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        set_widget_property(self, "dragOver", "false")
        mime = event.mimeData()
        source_id = self._source_id_from_mime(mime)
        if source_id is not None:
            event.acceptProposedAction()
            self.sourceDropped.emit(self.side, source_id)
            return
        if mime.hasUrls():
            event.acceptProposedAction()
            self.urlsDropped.emit(list(mime.urls()))
            return
        event.ignore()


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
        set_widget_property(self.status_label, "status", "ok")
        layout.addWidget(self.status_label)
        view = ImageView()
        if hasattr(parent, "theme_palette"):
            view.set_canvas_color(parent.theme_palette.canvas_bg)
        view.set_image(image)
        self.view = view
        layout.addWidget(view)
        layout.addLayout(view_toolbar_builder(view))
        layout.addWidget(
            parent.button(
                "1 пиксель изображения = 1 пиксель просмотра",
                lambda: view.zoom(1 / view.transform().m11()),
                variant="secondary",
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
        set_widget_property(self.status_label, "status", "warning")

    def closeEvent(self, event):
        self._notify_closed()
        super().closeEvent(event)


def thumbnail(image, limit=2000):
    result = image.copy()
    result.thumbnail((limit, limit))
    return result


class MainWindow(QMainWindow):
    def __init__(
        self, pdf_worker: PdfWorkerClient | None = None, *, settings: QSettings | None = None
    ):
        super().__init__()
        self._settings = settings
        self.theme_name = load_saved_theme(self._settings)
        self.theme_palette: ThemePalette = get_palette(self.theme_name)
        self._themed_buttons: list[tuple[QPushButton, str, str]] = []
        self.setWindowTitle(BASE_WINDOW_TITLE)
        self.setAcceptDrops(True)
        self.setMinimumSize(960, 620)
        self.resize(1360, 860)
        self.cards: dict[str, Card] = {}
        self.active_side = "front"
        self.sheet = None
        self.layout_generation = 0
        self.import_generation = 0
        self._reload_generation: dict[Path, int] = {}
        self._save_generation = 0
        self.session_id = 1
        self._card_revision_seq = 0
        self._drag_session_token = secrets.token_hex(8)
        self._source_id_seq = 0
        self._source_registry: dict[str, Source] = {}
        self._side_selection_seq = 0
        self._needs_contour_review: set[str] = set()
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
        self.apply_theme(self.theme_name, persist=False)
        self._sync_controls()
        self._sync_side_slots()
        self._sync_context_panels()
        self._sync_history_controls()
        self._sync_dirty_ui()
        self.invalidate_sheet("Назначьте стороны документа.")
        self.preview.clear_sheet()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(10, 10, 10, 8)
        outer.setSpacing(8)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.add_button = self._button(
            "Добавить файлы",
            self.add_files,
            variant="secondary",
            icon_name="add_files",
            tooltip="Добавить изображения или PDF с этого компьютера (Ctrl+O)",
        )
        toolbar.addWidget(self.add_button)
        self.undo_button = self._button(
            "Отменить",
            self.undo,
            variant="ghost",
            icon_name="undo",
            tooltip="Отменить последнее действие (Ctrl+Z)",
        )
        self.redo_button = self._button(
            "Повторить",
            self.redo,
            variant="ghost",
            icon_name="redo",
            tooltip="Повторить отменённое действие (Ctrl+Y / Ctrl+Shift+Z)",
        )
        toolbar.addWidget(self.undo_button)
        toolbar.addWidget(self.redo_button)
        toolbar.addStretch()
        self.preview_nav_button = self._button(
            "Предпросмотр",
            lambda: self.tabs.setCurrentIndex(1),
            variant="ghost",
            icon_name="preview",
            tooltip="Перейти на вкладку «Предпросмотр A4»",
        )
        toolbar.addWidget(self.preview_nav_button)
        self.theme_button = self._button(
            "Тёмная тема",
            self.toggle_theme,
            variant="ghost",
            icon_name="theme_dark",
            tooltip="Переключить светлую и тёмную тему оформления",
        )
        toolbar.addWidget(self.theme_button)
        self.png_button = self._button(
            "PNG · 300 DPI",
            lambda: self.save("png"),
            variant="secondary",
            icon_name="save_png",
            tooltip="Сохранить лист A4 как растровое изображение PNG 300 DPI",
        )
        self.pdf_button = self._button(
            "Сохранить PDF",
            lambda: self.save("pdf"),
            variant="primary",
            icon_name="save_pdf",
            tooltip="Сохранить готовый лист A4 в PDF без пересжатия карточек (Ctrl+S)",
        )
        toolbar.addWidget(self.png_button)
        toolbar.addWidget(self.pdf_button)
        outer.addLayout(toolbar)
        self._open_shortcut = QShortcut(
            QKeySequence("Ctrl+O"), self, activated=lambda: self.add_button.click()
        )
        self._save_shortcut = QShortcut(
            QKeySequence("Ctrl+S"), self, activated=lambda: self.pdf_button.click()
        )
        self._undo_shortcut = QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo)
        self._redo_shortcut = QShortcut(QKeySequence("Ctrl+Y"), self, activated=self.redo)
        self._redo_shift_shortcut = QShortcut(
            QKeySequence("Ctrl+Shift+Z"), self, activated=self.redo
        )
        splitter = QSplitter()
        self.splitter = splitter
        outer.addWidget(splitter, 1)

        left = QWidget()
        left.setMinimumWidth(205)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 4, 0)
        left_title = QLabel("Файлы и страницы PDF")
        set_widget_property(left_title, "role", "sectionTitle")
        left_layout.addWidget(left_title)
        self.sources = SourceListWidget(on_urls_dropped=self.handle_dropped_urls)
        self.sources.setAccessibleName("Список загруженных файлов и страниц PDF")
        self.sources.setIconSize(QSize(144, 108))
        self.sources.setViewMode(QListView.ViewMode.IconMode)
        self.sources.setResizeMode(QListView.ResizeMode.Adjust)
        self.sources.setMovement(QListView.Movement.Static)
        self.sources.setWordWrap(True)
        self.sources.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.sources.itemDoubleClicked.connect(self.assign_next)
        left_layout.addWidget(self.sources)
        label = QLabel(
            "Перетащите файл или миниатюру на слот стороны, либо выберите миниатюру и нажмите кнопку."
        )
        label.setWordWrap(True)
        set_widget_property(label, "status", "muted")
        left_layout.addWidget(label)
        left_layout.addWidget(
            self._button(
                "Назначить лицевой",
                lambda: self.assign_selected("front"),
                variant="secondary",
                icon_name="assign_front",
                tooltip="Назначить выбранную миниатюру лицевой стороной",
            )
        )
        left_layout.addWidget(
            self._button(
                "Назначить обратной",
                lambda: self.assign_selected("back"),
                variant="secondary",
                icon_name="assign_back",
                tooltip="Назначить выбранную миниатюру обратной стороной",
            )
        )
        self.reload_source_button = self._button(
            "Перезагрузить выбранный файл",
            self.reload_selected_source,
            variant="secondary",
            icon_name="reload",
            tooltip="Повторно прочитать выбранный файл с диска",
        )
        left_layout.addWidget(self.reload_source_button)
        left_layout.addWidget(
            self._button(
                "Убрать выбранный файл",
                self.remove_source,
                variant="danger",
                icon_name="remove",
                tooltip="Убрать миниатюру из списка (исходный файл на диске сохраняется)",
            )
        )
        splitter.addWidget(left)

        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("Режимы редактирования и предпросмотра")
        edit_tab = QWidget()
        edit_layout = QVBoxLayout(edit_tab)
        side_row = QHBoxLayout()
        side_row.setSpacing(8)
        self.front_slot = SideSlotCard(
            "front", can_accept_source_id=lambda sid: sid in self._source_registry
        )
        self.back_slot = SideSlotCard(
            "back", can_accept_source_id=lambda sid: sid in self._source_registry
        )
        self.side_slots: dict[str, SideSlotCard] = {
            "front": self.front_slot,
            "back": self.back_slot,
        }
        for slot in (self.front_slot, self.back_slot):
            slot.selected.connect(self.select_side)
            slot.clearRequested.connect(self.remove_side)
            slot.sourceDropped.connect(self.assign_source_by_id)
            slot.urlsDropped.connect(self.handle_dropped_urls)
            side_row.addWidget(slot, 1)
        edit_layout.addLayout(side_row)
        self.source_label = QLabel("Сторона не назначена")
        self.source_label.setWordWrap(True)
        set_widget_property(self.source_label, "status", "muted")
        edit_layout.addWidget(self.source_label)
        self.editor = ImageView(editable=True)
        self.editor.setAccessibleName("Редактор границ выбранной стороны")
        self.editor.selectionChanged.connect(self.selection_changed)
        self.editor.draftChanged.connect(self.on_draft_changed)
        self.editor.draftCancelled.connect(self.on_draft_cancelled)
        self.editor.rotateRequested.connect(self.rotate)
        self.editor.validationChanged.connect(self.on_selection_validation)
        edit_layout.addWidget(self.editor, 1)
        self.editor_actions = []
        action_specs = (
            (
                ("Определить границы", self.autodetect, "secondary", "autodetect"),
                ("Сбросить выделение", self.reset_selection, "danger", "reset"),
            ),
            (
                ("Выделить 4 точки", self.pick_points, "secondary", "pick_points"),
                ("Повернуть на 90°", self.rotate, "secondary", "rotate"),
            ),
        )
        for pairs in action_specs:
            row = QHBoxLayout()
            for title, callback, variant, icon_name in pairs:
                btn = self._button(title, callback, variant=variant, icon_name=icon_name)
                if icon_name == "rotate":
                    btn.setToolTip("Повернуть на 90° (R в редакторе)")
                elif icon_name == "pick_points":
                    btn.setToolTip("Выделить 4 точки; Esc — отмена")
                row.addWidget(btn)
                self.editor_actions.append(btn)
            edit_layout.addLayout(row)
        edit_layout.addLayout(self.view_toolbar(self.editor))
        hint = QLabel(
            "Углы: 1 ↖ → 2 ↗ → 3 ↘ → 4 ↙. Перетаскивание — лупа 4×.\n"
            "Выберите угол: стрелки — 1 пкс, Shift — 10. R — поворот; 0 — вписать; "
            "Esc — отмена. Колесо — масштаб; фон — перемещение."
        )
        hint.setWordWrap(True)
        set_widget_property(hint, "status", "muted")
        edit_layout.addWidget(hint)
        self.tabs.addTab(edit_tab, "Редактор стороны")

        preview_tab = QWidget()
        preview_layout = QVBoxLayout(preview_tab)
        self.preview = SheetView()
        self.preview.setAccessibleName("Предпросмотр листа A4")
        self.preview.layoutDragStarted.connect(self.on_layout_drag_started)
        self.preview.layoutMoved.connect(self.on_layout_moved)
        self.preview.layoutDragFinished.connect(self.on_layout_drag_finished)
        guides = QHBoxLayout()
        self.rulers_toggle = QCheckBox("Линейки · мм")
        self.guides_toggle = QCheckBox("Поля")
        for toggle in (self.rulers_toggle, self.guides_toggle):
            toggle.setChecked(True)
            toggle.setToolTip("Направляющие видны только на экране и не печатаются")
            guides.addWidget(toggle)
        self.rulers_toggle.toggled.connect(self.preview.set_rulers_visible)
        self.guides_toggle.toggled.connect(self.preview.set_guides_visible)
        guides.addStretch()
        preview_layout.addLayout(guides)
        preview_layout.addWidget(self.preview, 1)
        preview_layout.addLayout(self.view_toolbar(self.preview))
        preview_note = QLabel(
            "A4 · 210 × 297 мм · белый фон RGB 255, 255, 255\n"
            "Перетащите карточку — сдвинется вся группа. Привязка к центру: 0,8 мм. Esc — отмена."
        )
        preview_note.setWordWrap(True)
        set_widget_property(preview_note, "status", "muted")
        preview_layout.addWidget(preview_note)
        self.tabs.addTab(preview_tab, "Предпросмотр A4")
        self.tabs.currentChanged.connect(self._sync_context_panels)
        splitter.addWidget(self.tabs)

        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(4, 0, 2, 0)
        self.card_group = QGroupBox("Размер выбранной стороны")
        form = QFormLayout(self.card_group)
        self.size_mode = QComboBox()
        self.size_mode.setAccessibleName("Режим физического размера карточки")
        self.size_mode.addItem("Удостоверение · 85,60 × 53,98 мм", "id1")
        self.size_mode.addItem("Исходные пропорции + ширина", "aspect")
        self.size_mode.addItem("Произвольный размер", "custom")
        form.addRow(self.size_mode)
        self.width = self.spin(1, 400, 85.6, accessible_name="Ширина карточки в миллиметрах")
        self.height = self.spin(1, 400, 53.98, accessible_name="Высота карточки в миллиметрах")
        self.radius = self.spin(0, 20, 0, accessible_name="Радиус скругления углов в миллиметрах")
        form.addRow("Ширина", self.width)
        form.addRow("Высота", self.height)
        form.addRow("Радиус углов", self.radius)
        self.ratio_label = QLabel()
        self.ratio_label.setWordWrap(True)
        form.addRow(self.ratio_label)
        self.resolution_label = QLabel()
        self.resolution_label.setWordWrap(True)
        set_widget_property(self.resolution_label, "status", "muted")
        form.addRow(self.resolution_label)
        self.inspect_button = self._button(
            "Увеличить результат / края",
            self.inspect_card,
            variant="secondary",
            icon_name="inspect",
            tooltip="Открыть крупный снимок вырезанной карточки для проверки краёв",
        )
        form.addRow(self.inspect_button)
        self.reset_button = self._button(
            "Сбросить правки стороны",
            self.reset_card,
            variant="danger",
            icon_name="reset",
            tooltip="Сбросить углы, поворот, размер и скругление текущей стороны",
        )
        form.addRow(self.reset_button)
        controls_layout.addWidget(self.card_group)

        self.layout_group = QGroupBox("Компоновка A4")
        form = QFormLayout(self.layout_group)
        self.template = QComboBox()
        self.template.setAccessibleName("Шаблон расположения карточек на A4")
        self.template.addItem("Друг под другом", "vertical")
        self.template.addItem("Рядом", "horizontal")
        form.addRow("Шаблон", self.template)
        self.gap = self.spin(
            0, 150, 15, accessible_name="Промежуток между карточками в миллиметрах"
        )
        self.margin = self.spin(0, 100, 10, accessible_name="Минимальные поля листа в миллиметрах")
        self.offset_x = self.spin(
            -200, 200, 0, accessible_name="Смещение по горизонтали X в миллиметрах"
        )
        self.offset_y = self.spin(
            -290, 290, 0, accessible_name="Смещение по вертикали Y в миллиметрах"
        )
        form.addRow("Промежуток", self.gap)
        form.addRow("Минимальные поля", self.margin)
        form.addRow("Смещение X →", self.offset_x)
        form.addRow("Смещение Y ↓", self.offset_y)
        self.crop_marks_toggle = QCheckBox("Метки реза")
        self.crop_marks_toggle.setToolTip("Печатать метки реза длиной 3 мм, толщиной 0,15 мм")
        form.addRow(self.crop_marks_toggle)
        note = QLabel("Группа карточек центрируется на листе. Смещения отсчитываются от центра.")
        note.setWordWrap(True)
        set_widget_property(note, "status", "muted")
        form.addRow(note)
        controls_layout.addWidget(self.layout_group)

        self.status_group = QGroupBox("Статус и экспорт")
        status_layout = QVBoxLayout(self.status_group)
        self.layout_message = QLabel("Назначьте стороны документа.")
        self.layout_message.setWordWrap(True)
        status_layout.addWidget(self.layout_message)
        self.export_message = QLabel(PRINT_HINT)
        self.export_message.setWordWrap(True)
        set_widget_property(self.export_message, "status", "muted")
        status_layout.addWidget(self.export_message)
        self.open_button = self._button(
            "Открыть сохранённый файл",
            self.open_export,
            variant="secondary",
            icon_name="open_external",
            tooltip="Открыть последний экспортированный PDF или PNG",
        )
        self.open_button.hide()
        self.export_path = None
        status_layout.addWidget(self.open_button)
        controls_layout.addWidget(self.status_group)

        controls_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(controls)
        scroll.setMinimumWidth(290)
        splitter.addWidget(scroll)
        splitter.setSizes([230, 720, 330])
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumHeight(5)
        self.progress.hide()
        outer.addWidget(self.progress)
        self.size_mode.currentIndexChanged.connect(self.card_options_changed)
        for spin in (self.width, self.height, self.radius):
            spin.valueChanged.connect(self.card_options_changed)
        self.template.currentIndexChanged.connect(self.layout_options_changed)
        self.crop_marks_toggle.toggled.connect(self.layout_options_changed)
        for spin in (self.gap, self.margin, self.offset_x, self.offset_y):
            spin.valueChanged.connect(self.layout_options_changed)

    def _button(
        self,
        title: str,
        callback,
        *,
        variant: str = "secondary",
        icon_name: str | None = None,
        tooltip: str | None = None,
        accessible_name: str | None = None,
    ) -> QPushButton:
        btn = self.button(
            title,
            callback,
            variant=variant,
            tooltip=tooltip,
            accessible_name=accessible_name,
        )
        if icon_name is not None:
            self._themed_buttons.append((btn, icon_name, variant))
        return btn

    @staticmethod
    def button(
        title,
        callback,
        *,
        variant: str = "secondary",
        tooltip: str | None = None,
        accessible_name: str | None = None,
    ):
        button = QPushButton(title)
        set_widget_property(button, "variant", variant)
        button.setAccessibleName(accessible_name or title)
        if tooltip:
            button.setToolTip(tooltip)
        button.clicked.connect(callback)
        return button

    @staticmethod
    def spin(minimum, maximum, value, *, accessible_name: str | None = None):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(2)
        spin.setSuffix(" мм")
        spin.setValue(value)
        spin.setKeyboardTracking(False)
        if accessible_name:
            spin.setAccessibleName(accessible_name)
        return spin

    def view_toolbar(self, view):
        row = QHBoxLayout()
        row.addWidget(
            self._button(
                "−",
                lambda: view.zoom(1 / 1.25),
                variant="ghost",
                icon_name="zoom_out",
                tooltip="Уменьшить масштаб просмотра",
                accessible_name="Уменьшить масштаб",
            )
        )
        row.addWidget(
            self._button(
                "Вписать",
                view.fit_image,
                variant="ghost",
                icon_name="fit",
                tooltip="Вписать изображение целиком (0 на холсте)",
            )
        )
        row.addWidget(
            self._button(
                "+",
                lambda: view.zoom(1.25),
                variant="ghost",
                icon_name="zoom_in",
                tooltip="Увеличить масштаб просмотра",
                accessible_name="Увеличить масштаб",
            )
        )
        return row

    def _icon_color_for_variant(self, palette: ThemePalette, variant: str) -> str:
        if variant == "primary":
            return palette.accent_text
        if variant == "danger":
            return palette.danger
        return palette.text_primary

    def _refresh_theme_icons(self) -> None:
        palette = self.theme_palette
        for button, icon_name, variant in self._themed_buttons:
            actual_icon = icon_name
            if button is self.theme_button:
                actual_icon = "theme_light" if self.theme_name == THEME_DARK else "theme_dark"
            color = self._icon_color_for_variant(palette, variant)
            button.setIcon(render_svg_icon(actual_icon, color, size=18))
            button.setIconSize(QSize(18, 18))

    def _sync_context_panels(self, tab_index: int | None = None) -> None:
        index = self.tabs.currentIndex() if tab_index is None else tab_index
        in_preview = index == 1
        self.card_group.setVisible(not in_preview)
        self.layout_group.setVisible(in_preview)

    def apply_theme(self, theme_name: str, *, persist: bool = True) -> ThemePalette:
        palette = get_palette(theme_name)
        self.theme_name = palette.name
        self.theme_palette = palette
        app = QApplication.instance()
        apply_theme(app if app is not None else self, palette.name)
        self.editor.set_canvas_color(palette.canvas_bg)
        self.editor.set_accent_color(palette.accent)
        self.preview.set_canvas_color(palette.canvas_bg)
        self.preview.set_accent_color(palette.accent)
        for inspector in list(self.inspectors):
            inspector.view.set_canvas_color(palette.canvas_bg)
        if self.active_side not in self.cards:
            self.editor.set_image(Image.new("RGB", (700, 440), palette.panel_bg))
        if palette.name == THEME_DARK:
            self.theme_button.setText("Светлая тема")
            self.theme_button.setAccessibleName("Переключить на светлую тему")
            self.theme_button.setToolTip("Переключить на светлую тему оформления")
        else:
            self.theme_button.setText("Тёмная тема")
            self.theme_button.setAccessibleName("Переключить на тёмную тему")
            self.theme_button.setToolTip("Переключить на тёмную тему оформления")
        self._refresh_theme_icons()
        if hasattr(self, "sources"):
            self._refresh_source_badges()
        if hasattr(self, "side_slots"):
            self._sync_side_slots()
        if persist:
            save_theme(palette.name, self._settings)
        return palette

    def toggle_theme(self) -> str:
        next_theme = THEME_LIGHT if self.theme_name == THEME_DARK else THEME_DARK
        return self.apply_theme(next_theme, persist=True).name

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.handle_dropped_urls(list(event.mimeData().urls()))
            return
        super().dropEvent(event)

    def handle_dropped_urls(self, urls: list[QUrl]) -> None:
        if self._closing or self._closed:
            return
        valid_paths, rejected = partition_dropped_urls(urls)
        if valid_paths:
            self.import_paths(valid_paths)
        if rejected:
            sample = ", ".join(rejected[:3])
            suffix = f" и ещё {len(rejected) - 3}" if len(rejected) > 3 else ""
            message = (
                f"Не удалось добавить перетащенные элементы ({sample}{suffix}): "
                "поддерживаются только локальные файлы PDF, JPG, JPEG и PNG."
            )
            self.statusBar().showMessage(message)
            self.show_error(message)

    def _assigned_badges_by_key(self) -> dict[tuple[Path, int | None], tuple[str, ...]]:
        sides_by_key: dict[tuple[Path, int | None], list[str]] = {}
        for side in ("front", "back"):
            card = self.cards.get(side)
            if card is None:
                continue
            key = (card.source.path.resolve(), card.source.page)
            sides_by_key.setdefault(key, []).append(side)
        return {key: tuple(sides) for key, sides in sides_by_key.items()}

    def _refresh_source_badges(self) -> None:
        assigned = self._assigned_badges_by_key()
        for i in range(self.sources.count()):
            item = self.sources.item(i)
            source: Source = item.data(Qt.ItemDataRole.UserRole)
            key = (source.path.resolve(), source.page)
            badges = assigned.get(key, ())
            item.setData(SOURCE_BADGES_ROLE, badges)
            item.setIcon(render_source_item_icon(source, badges, self.theme_palette))

    def _sync_side_slots(self) -> None:
        for side, slot in self.side_slots.items():
            active = self.active_side == side
            card = self.cards.get(side)
            loading = side in self.pending_loads
            preview_pm: QPixmap | None = None
            source_text = "Не выбрано"
            tooltip = ""
            if card is not None:
                source_text = card.source.label
                tooltip = str(card.source.path)
                thumb_src = card.display if card.display is not None else card.source.thumbnail
                preview_pm = pixmap(thumb_src).scaled(
                    60,
                    40,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            elif loading:
                source_text = "Загрузка выбранного файла…"

            if loading:
                status_text, status_kind = "⟳ Загрузка…", "info"
            elif card is None:
                status_text, status_kind = "Не назначена", "muted"
            elif card.error:
                status_text, status_kind = "✕ Ошибка выделения", "error"
            elif self.is_drafting() and self.draft_side == side:
                status_text, status_kind = "✎ Выделение…", "warning"
            elif card.ready_revision != card.revision:
                status_text, status_kind = "⟳ Обработка…", "info"
            elif side in self._needs_contour_review:
                status_text, status_kind = "⚠ Проверьте границы", "warning"
            else:
                status_text, status_kind = "✓ Готово", "ok"

            slot.update_state(
                active=active,
                source_label=source_text,
                status_text=status_text,
                status_kind=status_kind,
                tooltip=tooltip,
                preview_pixmap=preview_pm,
                can_clear=(card is not None or loading),
            )

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
        assigned = self._assigned_badges_by_key()
        added = 0
        for source in sources:
            if (source.path, source.page) in existing:
                continue
            self._source_id_seq += 1
            source_id = f"{self._drag_session_token}:{self._source_id_seq}"
            self._source_registry[source_id] = source
            key = (source.path.resolve(), source.page)
            badges = assigned.get(key, ())
            item = QListWidgetItem(
                render_source_item_icon(source, badges, self.theme_palette), source.label
            )
            item.setData(Qt.ItemDataRole.UserRole, source)
            item.setData(SOURCE_ID_ROLE, source_id)
            item.setData(SOURCE_BADGES_ROLE, badges)
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
        self,
        pdf_path: Path,
        import_gen: int,
        *,
        replace_existing: bool = False,
        is_stale=None,
        on_source_loaded=None,
    ) -> None:
        resolved_pdf = pdf_path.resolve()

        def stale() -> bool:
            return is_stale is not None and bool(is_stale())

        def on_fingerprint_error(message: str) -> None:
            if not (self._closing or self._closed) and not stale():
                prefix = f"{pdf_path.name}:"
                self.show_error(message if message.startswith(prefix) else f"{prefix} {message}")

        def on_fingerprinted(fp) -> None:
            if self._closing or self._closed or stale():
                return
            self.original_guard.record_fingerprint(fp)

            def request_batch(start_page: int) -> None:
                if self._closing or self._closed or stale():
                    return

                def on_batch(batch: ThumbnailBatchResult) -> None:
                    if self._closing or self._closed or stale():
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
                    if not (self._closing or self._closed) and not stale():
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
                if self._closing or self._closed or stale():
                    return
                if replace_existing:
                    for i in range(self.sources.count() - 1, -1, -1):
                        if (
                            self.sources.item(i).data(Qt.ItemDataRole.UserRole).path.resolve()
                            == resolved_pdf
                        ):
                            taken = self.sources.takeItem(i)
                            source_id = taken.data(SOURCE_ID_ROLE) if taken is not None else None
                            if isinstance(source_id, str):
                                self._source_registry.pop(source_id, None)
                self.original_paths.add(Path(info.path))
                self.original_guard.register(info.path)
                request_batch(0)

            def on_inspect_error(message: str) -> None:
                if not (self._closing or self._closed) and not stale():
                    self.show_error(f"{pdf_path.name}: {message}")

            self.submit_pdf(
                InspectPdfRequest(str(pdf_path), session_id=import_gen),
                on_inspected,
                on_inspect_error,
            )

        self.submit(lambda: fingerprint_file(pdf_path), on_fingerprinted, on_fingerprint_error)

    def reload_selected_source(self):
        if self._closing or self._closed:
            return
        item = self.sources.currentItem()
        if item is None:
            self.statusBar().showMessage("Сначала выберите файл или страницу слева.")
            return
        selected_source: Source = item.data(Qt.ItemDataRole.UserRole)
        target_path = selected_source.path.resolve()
        reload_gen = self._reload_generation.get(target_path, 0) + 1
        self._reload_generation[target_path] = reload_gen

        def is_stale() -> bool:
            return self._reload_generation.get(target_path) != reload_gen

        affected_sides = [
            (side, card, card.source.page, self.load_generation[side])
            for side, card in self.cards.items()
            if card.source.path.resolve() == target_path
        ]

        def side_still_matches(side: str, expected_card: Card, expected_load_gen: int) -> bool:
            return (
                self.cards.get(side) is expected_card
                and self.load_generation[side] == expected_load_gen
                and side not in self.pending_loads
            )

        if selected_source.page is None:

            def work():
                return discover(target_path, original_guard=self.original_guard)

            def done(sources):
                if self._closing or self._closed or is_stale() or not sources:
                    return
                new_source = sources[0]
                if new_source.fingerprint is not None:
                    self.original_guard.record_fingerprint(new_source.fingerprint)
                for i in range(self.sources.count()):
                    row_item = self.sources.item(i)
                    row_src = row_item.data(Qt.ItemDataRole.UserRole)
                    if row_src.path.resolve() == target_path and row_src.page is None:
                        row_item.setData(Qt.ItemDataRole.UserRole, new_source)
                        row_id = row_item.data(SOURCE_ID_ROLE)
                        if isinstance(row_id, str):
                            self._source_registry[row_id] = new_source
                        badges = tuple(row_item.data(SOURCE_BADGES_ROLE) or ())
                        row_item.setIcon(
                            render_source_item_icon(new_source, badges, self.theme_palette)
                        )
                for side, expected_card, _, expected_load_gen in affected_sides:
                    if side_still_matches(side, expected_card, expected_load_gen):
                        self._load_source_for_side(side, new_source)
                self.statusBar().showMessage(f"Файл перезагружен: {target_path.name}")

            def failed(message: str):
                if not (self._closing or self._closed) and not is_stale():
                    self.show_error(message)

            self.submit(work, done, failed)
        else:
            self.import_generation += 1

            def on_pdf_source(reloaded_src: Source):
                if is_stale():
                    return
                for side, expected_card, expected_page, expected_load_gen in affected_sides:
                    if reloaded_src.page == expected_page and side_still_matches(
                        side, expected_card, expected_load_gen
                    ):
                        self._load_source_for_side(side, reloaded_src)

            self._import_pdf_batched(
                target_path,
                self.import_generation,
                replace_existing=True,
                is_stale=is_stale,
                on_source_loaded=on_pdf_source,
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
        self._load_source_for_side(side, source)

    def assign_source_by_id(self, side: str, source_id: str) -> bool:
        if self._closing or self._closed or side not in SIDE_NAMES:
            return False
        source = self._source_registry.get(source_id)
        if source is None:
            return False
        self._load_source_for_side(side, source)
        return True

    def _load_source_for_side(self, side: str, source: Source):
        if self.draft_side == side:
            self._clear_draft()
        self.load_generation[side] += 1
        token = self.load_generation[side]
        selection_seq_at_start = self._side_selection_seq
        self.pending_loads.add(side)
        self.refresh_inspectors()
        self._sync_side_slots()
        self.invalidate_sheet("Загрузка выбранной стороны…")

        def finish_load(result):
            if self._closing or self._closed or token != self.load_generation[side]:
                return
            self.pending_loads.discard(side)
            if self.draft_side == side:
                self._clear_draft()
            original, points, display, found = result
            card = Card(source, original, points, display=display)
            self.cards[side] = card
            self._needs_contour_review.add(side)
            self.refresh_inspectors()
            if self._side_selection_seq == selection_seq_at_start:
                self.active_side = side
            self.statusBar().showMessage(
                "Контур найден автоматически — проверьте углы перед экспортом."
                if found
                else "Контур не найден. Укажите четыре угла вручную или перетащите точки."
            )
            self._record_history(f"Назначение: {SIDE_NAMES[side]}")
            if self.active_side == side:
                self.show_card()
            self._sync_side_slots()
            self._refresh_source_badges()
            self.render_card(side)

        def fail_load(message):
            if not (self._closing or self._closed) and token == self.load_generation[side]:
                self.pending_loads.discard(side)
                self._sync_side_slots()
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

            def dispatch_pdf_render():
                if self._closing or self._closed or token != self.load_generation[side]:
                    return
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

            if source.fingerprint is not None:
                self.submit(
                    lambda: self.original_guard.validate_source(
                        source.path, expected=source.fingerprint
                    ),
                    lambda _res: dispatch_pdf_render(),
                    fail_load,
                )
            else:
                dispatch_pdf_render()

    def remove_source(self):
        row = self.sources.currentRow()
        if row >= 0:
            taken = self.sources.takeItem(row)
            source_id = taken.data(SOURCE_ID_ROLE) if taken is not None else None
            if isinstance(source_id, str):
                self._source_registry.pop(source_id, None)
            self.statusBar().showMessage(
                "Миниатюра убрана. Назначенные стороны и исходный файл сохранены."
            )

    def remove_side(self, side: str | None = None):
        target_side = self.active_side if side is None else side
        if target_side not in SIDE_NAMES:
            return
        if self.draft_side == target_side:
            self._clear_draft()
        self.load_generation[target_side] += 1
        self.pending_loads.discard(target_side)
        self._needs_contour_review.discard(target_side)
        self.pdf_worker.cancel_key(("load_side", target_side))
        removed = self.cards.pop(target_side, None)
        if removed is not None:
            self._record_history(f"Удаление: {SIDE_NAMES[target_side]}")
        self.refresh_inspectors()
        if target_side == self.active_side:
            self.show_card()
        self._sync_side_slots()
        self._refresh_source_badges()
        self.layout_changed()

    def select_side(self, side: str):
        if side not in SIDE_NAMES:
            return
        self._side_selection_seq += 1
        had_draft = False
        if self.active_side != side:
            had_draft = self._clear_draft()
            self.active_side = side
            self.show_card()
        self._sync_side_slots()
        if had_draft:
            self.refresh_sheet()

    def is_drafting(self) -> bool:
        return (
            self.draft_message is not None
            or self.editor.is_drafting
            or self.preview.is_layout_dragging
        )

    def _clear_draft(self) -> bool:
        had_draft = self.is_drafting()
        self.editor.cancel_draft(emit=False)
        self.preview.cancel_layout_drag(emit=False)
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
        self._sync_side_slots()

    def on_draft_cancelled(self):
        self.draft_message = None
        self.draft_side = None
        self.layout_timer.stop()
        self.statusBar().showMessage("Выделение отменено. Восстановлены предыдущие углы.")
        self._sync_controls()
        self._sync_side_slots()
        self.refresh_sheet()

    def on_selection_validation(self, error: str):
        if self.editor.is_drafting:
            self.statusBar().showMessage(f"✕ {error}" if error else self.draft_message or "")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.preview.is_layout_dragging:
            self.preview.cancel_layout_drag()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self.editor.is_drafting:
            self.editor.cancel_draft()
            event.accept()
            return
        super().keyPressEvent(event)

    def show_card(self):
        card = self.cards.get(self.active_side)
        if card and card.display:
            self.editor.set_image(
                card.display,
                card.source_size,
                card.points,
                original=card.original,
                turns=card.display_turns,
            )
            self.source_label.setText(card.source.label)
        else:
            self.editor.set_image(Image.new("RGB", (700, 440), self.theme_palette.panel_bg))
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
            set_widget_property(self.ratio_label, "status", "warning")
        else:
            self.ratio_label.setText("✓ Пропорции выделения сохранены (отклонение ≤ 0,5%).")
            set_widget_property(self.ratio_label, "status", "ok")
        dpi = min(w / mm_w, h / mm_h) * 25.4
        self.resolution_label.setText(
            f"После перспективы: {w} × {h} пикс.\nПри печати: ≈ {dpi:.0f} DPI.\nУвеличение размера не добавляет деталей."
        )

    def invalidate_sheet(self, message="Обновление предпросмотра…", *, keep_preview=False):
        if self.preview.is_layout_dragging and not keep_preview:
            self._clear_draft()
        self.layout_generation += 1
        self.sheet = None
        self.pdf_button.setEnabled(False)
        self.png_button.setEnabled(False)
        self.layout_message.setText(message)
        set_widget_property(self.layout_message, "status", "")
        self.preview.setEnabled(keep_preview)
        self.tabs.setTabText(1, "Предпросмотр A4 · обновление")

    def selection_changed(self, points):
        self.draft_message = None
        self.draft_side = None
        card = self.cards.get(self.active_side)
        if card is None:
            return
        card.points = points
        card.cropped = None
        self._needs_contour_review.discard(self.active_side)
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
            self._needs_contour_review.discard(self.active_side)
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
            self._needs_contour_review.add(side)
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
            self._needs_contour_review.discard(self.active_side)
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
        self._sync_side_slots()

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
            self._sync_side_slots()
            self.layout_changed()

        def failed(message):
            if (
                not (self._closing or self._closed)
                and self.cards.get(side) is card
                and card.revision == revision
            ):
                card.error = message
                self._sync_side_slots()
                if self.is_drafting():
                    self.invalidate_sheet(self.draft_message or "Редактирование выделения…")
                    self.tabs.setTabText(1, "Предпросмотр A4 · редактирование")
                else:
                    self.invalidate_sheet(f"✕ {message}")
                    set_widget_property(self.layout_message, "status", "error")
                    self.tabs.setTabText(1, "Предпросмотр A4 · исправьте выделение")

        self.submit(work, done, failed)

    def current_layout(self):
        if self.preview.is_layout_dragging:
            return self.preview.committed_layout
        return Layout(
            self.template.currentData(),
            self.gap.value(),
            self.margin.value(),
            self.offset_x.value(),
            self.offset_y.value(),
            self.crop_marks_toggle.isChecked(),
        )

    def on_layout_drag_started(self):
        self.draft_message = "Перемещение карточек… Экспорт приостановлен. Esc — отмена."
        self.draft_side = None
        self.layout_timer.stop()
        self.invalidate_sheet(self.draft_message, keep_preview=True)

    def on_layout_moved(self, layout):
        self.syncing = True
        try:
            self.offset_x.setValue(layout.offset_x_mm)
            self.offset_y.setValue(layout.offset_y_mm)
        finally:
            self.syncing = False

    def on_layout_drag_finished(self, changed):
        self.draft_message = self.draft_side = None
        if changed:
            self._record_history("Перемещение карточек на A4")
        self.layout_changed()

    def layout_options_changed(self):
        if self.syncing or self._closing or self._closed:
            return
        if self.preview.is_layout_dragging:
            self._clear_draft()
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
        before_mem = self.history.evicted_for_memory_count
        step = self.history.undo()
        if step is None:
            return
        snapshot, label = step
        suffix = f": {label}" if label else ""
        mem_note = (
            " Часть истории очищена по лимиту памяти."
            if self.history.evicted_for_memory_count > before_mem
            else ""
        )
        self._restore_snapshot(snapshot, f"Отменено{suffix}.{mem_note}")

    def redo(self):
        if self._closing or self._closed:
            return
        before_mem = self.history.evicted_for_memory_count
        step = self.history.redo()
        if step is None:
            return
        snapshot, label = step
        suffix = f": {label}" if label else ""
        mem_note = (
            " Часть истории очищена по лимиту памяти."
            if self.history.evicted_for_memory_count > before_mem
            else ""
        )
        self._restore_snapshot(snapshot, f"Повторено{suffix}.{mem_note}")

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
            self.crop_marks_toggle.setChecked(snapshot.layout.crop_marks)
            if self.active_side not in self.cards and self.cards:
                next_side = next(iter(self.cards))
                self.active_side = next_side
        finally:
            self.syncing = False

        self._needs_contour_review.intersection_update(self.cards.keys())
        self.refresh_inspectors()
        self.show_card()
        self._sync_side_slots()
        self._refresh_source_badges()
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
        if self.preview.is_layout_dragging:
            self._clear_draft()
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
        if self.preview.is_layout_dragging:
            return
        if self.is_drafting():
            self.invalidate_sheet(self.draft_message or "Редактирование выделения…")
            self.tabs.setTabText(1, "Предпросмотр A4 · редактирование")
            return
        if self.pending_loads:
            self.layout_message.setText("Загрузка выбранной стороны…")
            return
        errors = [
            f"✕ {SIDE_NAMES[side]}: {card.error}" for side, card in self.cards.items() if card.error
        ]
        if errors:
            self.layout_message.setText("\n".join(errors))
            set_widget_property(self.layout_message, "status", "error")
            self.tabs.setTabText(1, "Предпросмотр A4 · исправьте выделение")
            return
        if not self.cards:
            self.invalidate_sheet("Назначьте стороны документа.")
            self.preview.clear_sheet()
            self.tabs.setTabText(1, "Предпросмотр A4")
            return
        if any(card.ready_revision != card.revision for card in self.cards.values()):
            return
        generation = self.layout_generation
        try:
            sizes = {
                side: card.size.resolve(card.rendered.size) for side, card in self.cards.items()
            }
            layout = self.current_layout()
            placements = arrange(sizes, layout)
            sheet = Sheet(
                {side: card.rendered for side, card in self.cards.items()},
                placements,
                crop_marks=layout.crop_marks,
            )
        except ValueError as exc:
            self.layout_message.setText(f"✕ {exc}")
            set_widget_property(self.layout_message, "status", "error")
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
            self.preview.set_sheet(preview, sheet.placements, layout)
            self.preview.setEnabled(True)
            self.pdf_button.setEnabled(True)
            self.png_button.setEnabled(True)
            self.tabs.setTabText(1, "Предпросмотр A4")
            set_widget_property(self.layout_message, "status", "ok")
            count = len(sheet.placements)
            review_sides = [
                SIDE_NAMES[side]
                for side in ("front", "back")
                if side in self.cards and side in self._needs_contour_review
            ]
            header = f"✓ Готово к экспорту · На листе: {count} из 2 сторон."
            if sheet.crop_marks:
                warning = crop_marks(sheet.placements).warning
                if warning:
                    header += "\n" + warning
            if review_sides:
                header += (
                    "\n⚠ Проверьте автоматически найденные границы ("
                    + ", ".join(review_sides)
                    + ")."
                )
            self.layout_message.setText(
                header
                + "\n"
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
        card_paths = tuple(card.source.path for card in self.cards.values())
        snap = snapshot_sheet(self.sheet, revisions=revisions, source_fingerprints=source_fps)
        frozen_sheet = Sheet(
            dict(snap.images),
            list(snap.placements),
            crop_marks=snap.crop_marks,
            annotations=snap.annotations,
        )
        self._save_generation += 1
        save_gen = self._save_generation

        if not source_fps:
            return self._prompt_and_export(
                kind,
                captured_snapshot=captured_snapshot,
                snap=snap,
                frozen_sheet=frozen_sheet,
                card_paths=card_paths,
                close_on_success=close_on_success,
            )

        self._pending_close_after_export = close_on_success

        def validate_sources():
            for fp in source_fps:
                self.original_guard.validate_source(fp.path, expected=fp)

        def on_validated(_):
            if self._closing or self._closed or save_gen != self._save_generation:
                return
            self._prompt_and_export(
                kind,
                captured_snapshot=captured_snapshot,
                snap=snap,
                frozen_sheet=frozen_sheet,
                card_paths=card_paths,
                close_on_success=close_on_success,
            )

        def on_validate_failed(message: str):
            if save_gen != self._save_generation:
                return
            self._pending_close_after_export = False
            if not (self._closing or self._closed):
                self.show_error(message)

        self.statusBar().showMessage("Проверка исходных файлов…")
        self.submit(validate_sources, on_validated, on_validate_failed)
        return True

    def _prompt_and_export(
        self,
        kind: str,
        *,
        captured_snapshot: WorkspaceSnapshot,
        snap,
        frozen_sheet: Sheet,
        card_paths: tuple[Path, ...],
        close_on_success: bool,
    ) -> bool:
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
        self.original_guard.register_many(card_paths)
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

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QPolygonF, QTransform
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsPolygonItem,
    QGraphicsScene,
    QGraphicsView,
)

from ..geometry import ViewTransform

CORNER_LABELS = ("верхний левый", "верхний правый", "нижний правый", "нижний левый")


def to_qimage(image):
    rgb = image.convert("RGB")
    data = rgb.tobytes()
    qimage = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888)
    return qimage.copy()


def pixmap(image):
    return QPixmap.fromImage(image if isinstance(image, QImage) else to_qimage(image))


class Handle(QGraphicsEllipseItem):
    def __init__(self, editor, index, point):
        super().__init__(-7, -7, 14, 14)
        self.editor = editor
        self.index = index
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
            | QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
        )
        self.setBrush(QColor("#ffffff"))
        self.setPen(QPen(QColor("#187cce"), 2))
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setZValue(3)
        self.setToolTip(
            ("1 · Верхний левый", "2 · Верхний правый", "3 · Нижний правый", "4 · Нижний левый")[
                index
            ]
        )
        self.setPos(QPointF(*point))

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            return QPointF(
                max(0, min(value.x(), self.editor.source_width - 1)),
                max(0, min(value.y(), self.editor.source_height - 1)),
            )
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.editor.update_polygon()
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.editor.picking:
            self.editor.begin_handle_drag(self.index)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.editor.finish_handle_drag()


class ImageView(QGraphicsView):
    """Scene units are source pixels; the view alone owns zoom and pan."""

    selectionChanged = Signal(object)
    draftChanged = Signal(str, int)
    draftCancelled = Signal()

    def __init__(self, editable=False, parent=None):
        super().__init__(parent)
        self.editable = editable
        self.setScene(QGraphicsScene(self))
        self.handles = []
        self.polygon = None
        self.image_item = None
        self.source_width = self.source_height = 1
        self.picking = False
        self.pick_points = []
        self._pick_markers = []
        self._dragging_handle: int | None = None
        self._drag_origin_points: tuple[tuple[float, float], ...] | None = None
        self._accent_color = QColor("#187cce")
        self.auto_fit = True
        self.setBackgroundBrush(QColor("#dbe1e8"))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setMinimumSize(280, 240)

    def set_canvas_color(self, color: str | QColor) -> None:
        self.setBackgroundBrush(QColor(color))

    def set_accent_color(self, color: str | QColor) -> None:
        self._accent_color = QColor(color)
        if self.polygon is not None:
            pen = QPen(self._accent_color, 2)
            pen.setCosmetic(True)
            self.polygon.setPen(pen)
            fill = QColor(self._accent_color)
            fill.setAlpha(28)
            self.polygon.setBrush(fill)
        for handle in self.handles:
            handle.setPen(QPen(self._accent_color, 2))

    @property
    def is_drafting(self) -> bool:
        return bool(self.picking or self._dragging_handle is not None)

    def set_image(self, image, source_size=None, points=None):
        self.handles = []
        self.polygon = None
        self.image_item = None
        self._pick_markers = []
        self.scene().clear()
        self.picking = False
        self.pick_points = []
        self._dragging_handle = None
        self._drag_origin_points = None
        image_width, image_height = (
            (image.width(), image.height()) if isinstance(image, QImage) else image.size
        )
        self.source_width, self.source_height = source_size or (image_width, image_height)
        self.image_item = QGraphicsPixmapItem(pixmap(image))
        self.image_item.setTransform(
            QTransform.fromScale(self.source_width / image_width, self.source_height / image_height)
        )
        self.scene().addItem(self.image_item)
        self.scene().setSceneRect(QRectF(0, 0, self.source_width, self.source_height))
        if points is not None and self.editable:
            self.set_points(points)
        self.auto_fit = True
        self.fit_image()

    def set_points(self, points):
        self.picking = False
        self.pick_points = []
        self._dragging_handle = None
        self._drag_origin_points = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        for item in self._pick_markers:
            self.scene().removeItem(item)
        self._pick_markers = []
        for handle in self.handles:
            self.scene().removeItem(handle)
        self.handles = []
        if self.polygon is None:
            self.polygon = QGraphicsPolygonItem()
            pen = QPen(self._accent_color, 2)
            pen.setCosmetic(True)
            self.polygon.setPen(pen)
            fill = QColor(self._accent_color)
            fill.setAlpha(28)
            self.polygon.setBrush(fill)
            self.polygon.setZValue(2)
            self.scene().addItem(self.polygon)
        for index, point in enumerate(points):
            handle = Handle(self, index, point)
            handle.setPen(QPen(self._accent_color, 2))
            self.scene().addItem(handle)
            self.handles.append(handle)
        self.update_polygon()

    def points(self):
        return tuple((h.pos().x(), h.pos().y()) for h in self.handles)

    def update_polygon(self):
        if self.polygon is not None:
            self.polygon.setPolygon(QPolygonF([QPointF(*p) for p in self.points()]))

    def begin_handle_drag(self, index: int):
        if not self.editable or self.picking:
            return
        self._drag_origin_points = self.points()
        self._dragging_handle = index
        self.setFocus()
        self.draftChanged.emit("drag", index + 1)

    def finish_handle_drag(self):
        if self._dragging_handle is None:
            return
        origin = self._drag_origin_points
        self._dragging_handle = None
        self._drag_origin_points = None
        current = self.points()
        if origin is not None and current == origin:
            self.draftCancelled.emit()
            return
        self.selectionChanged.emit(current)

    def begin_selection(self):
        if not self.image_item or not self.editable:
            return
        if self._dragging_handle is not None:
            self.cancel_draft(emit=False)
        self.picking = True
        self.pick_points = []
        for item in self._pick_markers:
            self.scene().removeItem(item)
        self._pick_markers = []
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocus()
        self.draftChanged.emit("pick", 1)

    def cancel_draft(self, *, emit: bool = True) -> bool:
        if not self.is_drafting:
            return False
        if self.picking:
            self.picking = False
            self.pick_points = []
            for item in self._pick_markers:
                self.scene().removeItem(item)
            self._pick_markers = []
            self.setCursor(Qt.CursorShape.ArrowCursor)
        if self._dragging_handle is not None:
            origin = self._drag_origin_points
            handle_index = self._dragging_handle
            self._dragging_handle = None
            self._drag_origin_points = None
            if 0 <= handle_index < len(self.handles):
                self.handles[handle_index].ungrabMouse()
            if origin is not None:
                for handle, point in zip(self.handles, origin):
                    handle.setPos(QPointF(*point))
                self.update_polygon()
        if emit:
            self.draftCancelled.emit()
        return True

    def mousePressEvent(self, event):
        if self.picking and event.button() == Qt.MouseButton.LeftButton:
            point = self.viewport_to_source(event.position())
            x, y = point
            if 0 <= x <= self.source_width - 1 and 0 <= y <= self.source_height - 1:
                self.pick_points.append(point)
                marker = QGraphicsEllipseItem(-5, -5, 10, 10)
                marker.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
                marker.setPos(x, y)
                marker.setBrush(QColor("#f59e0b"))
                marker.setZValue(4)
                self.scene().addItem(marker)
                self._pick_markers.append(marker)
                if len(self.pick_points) == 4:
                    picked = tuple(self.pick_points)
                    self.set_points(picked)
                    self.selectionChanged.emit(self.points())
                else:
                    self.draftChanged.emit("pick", len(self.pick_points) + 1)
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.is_drafting:
            self.cancel_draft()
            event.accept()
            return
        super().keyPressEvent(event)

    def coordinate_transform(self):
        t = self.viewportTransform()
        return ViewTransform(t.m11(), t.dx(), t.dy())

    def viewport_to_source(self, point):
        return self.coordinate_transform().to_source((point.x(), point.y()))

    def source_to_viewport(self, point):
        return self.coordinate_transform().to_view(point)

    def fit_image(self):
        if self.image_item:
            self.fitInView(
                self.sceneRect().adjusted(-12, -12, 12, 12), Qt.AspectRatioMode.KeepAspectRatio
            )
            self.auto_fit = True

    def zoom(self, factor):
        current = self.transform().m11()
        target = max(0.005, min(12.0, current * factor))
        self.scale(target / current, target / current)
        self.auto_fit = False

    def wheelEvent(self, event):
        self.zoom(1.2 if event.angleDelta().y() > 0 else 1 / 1.2)
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.auto_fit:
            self.fit_image()

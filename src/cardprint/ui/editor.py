from math import floor

import numpy as np
from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QTransform,
)
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsPolygonItem,
    QGraphicsScene,
    QGraphicsView,
    QWidget,
)

from ..geometry import ViewTransform, validate_quad

CORNER_LABELS = ("верхний левый", "верхний правый", "нижний правый", "нижний левый")
CORNER_ARROWS = ("↖", "↗", "↘", "↙")


class Loupe(QWidget):
    """Fixed logical-pixel overlay; only a 41×41 original-pixel region is copied."""

    REGION_SIZE = 41
    MAGNIFICATION = 4

    def __init__(self, viewport):
        super().__init__(viewport)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAccessibleName("Лупа угла · 4×")
        self.setFixedSize(168, 192)
        self.sample = QImage()
        self.crosshair = QPointF()
        self.source_point = (0, 0)
        self.hide()

    def set_region(self, sample, origin, point):
        self.sample = sample
        self.source_point = point
        # Scene integer coordinates denote source pixel centres, not their edges.
        self.crosshair = QPointF(
            2 + (point[0] - origin[0] + 0.5) * self.MAGNIFICATION,
            2 + (point[1] - origin[1] + 0.5) * self.MAGNIFICATION,
        )
        self.update()

    def avoid(self, cursor):
        viewport = self.parentWidget()
        right = max(4, viewport.width() - self.width() - 8)
        bottom = max(4, viewport.height() - self.height() - 8)
        if not self.isVisible() or self.geometry().adjusted(-24, -24, 24, 24).contains(cursor):
            candidates = (
                QPointF(8, 8),
                QPointF(right, 8),
                QPointF(8, bottom),
                QPointF(right, bottom),
            )
            position = max(
                candidates,
                key=lambda p: (
                    (p.x() + self.width() / 2 - cursor.x()) ** 2
                    + (p.y() + self.height() / 2 - cursor.y()) ** 2
                ),
            )
            self.move(position.toPoint())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        painter.setPen(QPen(QColor("#1f2937"), 2))
        painter.drawRect(self.rect().adjusted(1, 1, -1, -1))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.drawImage(QRectF(2, 2, 164, 164), self.sample)
        x, y = self.crosshair.x(), self.crosshair.y()
        for color, width in (("#ffffff", 3), ("#111827", 1)):
            painter.setPen(QPen(QColor(color), width))
            painter.drawLine(QPointF(x - 12, y), QPointF(x + 12, y))
            painter.drawLine(QPointF(x, y - 12), QPointF(x, y + 12))
        painter.setPen(QColor("#111827"))
        painter.drawText(
            QRectF(2, 166, 164, 24),
            Qt.AlignmentFlag.AlignCenter,
            f"4× · {self.source_point[0]:.1f}; {self.source_point[1]:.1f}",
        )


def to_qimage(image):
    rgb = image.convert("RGB")
    data = rgb.tobytes()
    qimage = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888)
    return qimage.copy()


def pixmap(image):
    return QPixmap.fromImage(image if isinstance(image, QImage) else to_qimage(image))


class Handle(QGraphicsEllipseItem):
    def __init__(self, editor, index, point):
        super().__init__(-11, -11, 22, 22)
        self.editor = editor
        self.index = index
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
            | QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
            | QGraphicsItem.GraphicsItemFlag.ItemIsFocusable
        )
        self.setBrush(QColor("#ffffff"))
        self.setPen(QPen(QColor("#187cce"), 2))
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setZValue(3)
        self.setToolTip(
            f"{index + 1} {CORNER_ARROWS[index]} · {CORNER_LABELS[index].capitalize()}\n"
            "Стрелки — 1 пиксель; Shift + стрелки — 10 пикселей"
        )
        self.setPos(QPointF(*point))

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        painter.setPen(QColor("#111827"))
        font = painter.font()
        font.setBold(True)
        font.setPixelSize(13)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, str(self.index + 1))
        if self.hasFocus():
            painter.setPen(QPen(QColor("#111827"), 1, Qt.PenStyle.DotLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(self.rect().adjusted(3, 3, -3, -3))

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
            self.setFocus()
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
    validationChanged = Signal(str)
    rotateRequested = Signal()

    def __init__(self, editable=False, parent=None):
        super().__init__(parent)
        self.editable = editable
        self.setScene(QGraphicsScene(self))
        self.handles = []
        self.polygon = None
        self.mask = None
        self.image_item = None
        self.source_width = self.source_height = 1
        self.picking = False
        self.pick_points = []
        self._pick_markers = []
        self._dragging_handle: int | None = None
        self._drag_origin_points: tuple[tuple[float, float], ...] | None = None
        self._keyboard_nudge = False
        self._original_pixels = None
        self.validation_error = ""
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
        self.loupe = Loupe(self.viewport())

    def set_canvas_color(self, color: str | QColor) -> None:
        self.setBackgroundBrush(QColor(color))

    def set_accent_color(self, color: str | QColor) -> None:
        self._accent_color = QColor(color)
        self.update_polygon()
        for handle in self.handles:
            handle.setPen(QPen(self._accent_color, 2))

    @property
    def is_drafting(self) -> bool:
        return bool(self.picking or self._dragging_handle is not None)

    def set_image(self, image, source_size=None, points=None, *, original=None, turns=0):
        self.loupe.hide()
        self.validation_error = ""
        # np.rot90 is a strided view: rotation never copies a full source for the loupe.
        self._original_pixels = None if original is None else np.rot90(original, -(turns % 4))
        self.handles = []
        self.polygon = None
        self.mask = None
        self.image_item = None
        self._pick_markers = []
        self.scene().clear()
        self.picking = False
        self.pick_points = []
        self._dragging_handle = None
        self._drag_origin_points = None
        self._keyboard_nudge = False
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
        self.loupe.hide()
        self.picking = False
        self.pick_points = []
        self._dragging_handle = None
        self._drag_origin_points = None
        self._keyboard_nudge = False
        self.setCursor(Qt.CursorShape.ArrowCursor)
        for item in self._pick_markers:
            self.scene().removeItem(item)
        self._pick_markers = []
        for handle in self.handles:
            self.scene().removeItem(handle)
        self.handles = []
        if self.polygon is None:
            self.mask = QGraphicsPathItem()
            self.mask.setPen(QPen(Qt.PenStyle.NoPen))
            self.mask.setBrush(QColor(0, 0, 0, 115))
            self.mask.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.mask.setZValue(1)
            self.scene().addItem(self.mask)
            self.polygon = QGraphicsPolygonItem()
            pen = QPen(self._accent_color, 2)
            pen.setCosmetic(True)
            self.polygon.setPen(pen)
            self.polygon.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
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
            points = self.points()
            polygon = QPolygonF([QPointF(*p) for p in points])
            self.polygon.setPolygon(polygon)
            path = QPainterPath()
            path.setFillRule(Qt.FillRule.OddEvenFill)
            path.addRect(self.sceneRect())
            path.addPolygon(polygon)
            path.closeSubpath()
            self.mask.setPath(path)
            error = ""
            if len(points) == 4:
                try:
                    validate_quad(points, self.source_width, self.source_height)
                except ValueError as exc:
                    error = str(exc)
            pen = QPen(QColor("#dc2626") if error else self._accent_color, 2)
            pen.setCosmetic(True)
            self.polygon.setPen(pen)
            self.polygon.setBrush(QColor(220, 38, 38, 28) if error else Qt.BrushStyle.NoBrush)
            self.polygon.setToolTip(f"✕ {error}" if error else "")
            self.validation_error = error
            if len(points) == 4:
                self.validationChanged.emit(error)
            self.update_loupe()

    def original_region(self, x: int, y: int) -> QImage:
        """Bounded request in rotated source pixels; pad beyond image edges."""
        size = Loupe.REGION_SIZE
        region = np.full((size, size, 3), 235, dtype=np.uint8)
        pixels = self._original_pixels
        if pixels is not None:
            height, width = pixels.shape[:2]
            left, top = max(0, x), max(0, y)
            right, bottom = min(width, x + size), min(height, y + size)
            if right > left and bottom > top:
                region[top - y : bottom - y, left - x : right - x] = pixels[top:bottom, left:right]
        return to_qimage(Image.fromarray(region))

    def update_loupe(self):
        if self._dragging_handle is None or self._original_pixels is None:
            self.loupe.hide()
            return
        point = self.points()[self._dragging_handle]
        half = Loupe.REGION_SIZE // 2
        origin = (floor(point[0]) - half, floor(point[1]) - half)
        self.loupe.set_region(self.original_region(*origin), origin, point)
        self.loupe.avoid(self.mapFromScene(QPointF(*point)))
        self.loupe.show()
        self.loupe.raise_()

    def begin_handle_drag(self, index: int):
        if not self.editable or self.picking:
            return
        self._drag_origin_points = self.points()
        self._dragging_handle = index
        self.setFocus()
        self.draftChanged.emit("drag", index + 1)
        self.update_loupe()

    def finish_handle_drag(self):
        if self._dragging_handle is None:
            return
        origin = self._drag_origin_points
        self._dragging_handle = None
        self._drag_origin_points = None
        self._keyboard_nudge = False
        self.loupe.hide()
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
        self.loupe.hide()
        self._keyboard_nudge = False
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
                if self.scene().mouseGrabberItem() is self.handles[handle_index]:
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
        if event.modifiers() == Qt.KeyboardModifier.NoModifier and not self.is_drafting:
            if event.key() == Qt.Key.Key_0:
                self.fit_image()
                event.accept()
                return
            if event.key() == Qt.Key.Key_R and self.editable and self.handles:
                if not event.isAutoRepeat():
                    self.rotateRequested.emit()
                event.accept()
                return
        directions = {
            Qt.Key.Key_Left: (-1, 0),
            Qt.Key.Key_Right: (1, 0),
            Qt.Key.Key_Up: (0, -1),
            Qt.Key.Key_Down: (0, 1),
        }
        handle = self.scene().focusItem()
        if (
            event.key() in directions
            and event.modifiers()
            in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ShiftModifier)
            and isinstance(handle, Handle)
            and not self.picking
            and (self._dragging_handle is None or self._keyboard_nudge)
        ):
            dx, dy = directions[event.key()]
            step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            if self._dragging_handle is None:
                self.begin_handle_drag(handle.index)
                self._keyboard_nudge = True
            handle.setPos(handle.pos() + QPointF(dx * step, dy * step))
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if self._keyboard_nudge and event.key() in (
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
        ):
            if not event.isAutoRepeat():
                self.finish_handle_drag()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        if self._keyboard_nudge:
            self.finish_handle_drag()
        super().focusOutEvent(event)

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
        self.loupe.hide()
        self.update_loupe()

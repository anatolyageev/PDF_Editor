from dataclasses import replace
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
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsPolygonItem,
    QGraphicsScene,
    QGraphicsView,
    QWidget,
)

from ..export import render_text_annotation_rgba
from ..fonts import layout_text_annotation
from ..geometry import (
    A4_HEIGHT_MM,
    A4_WIDTH_MM,
    Layout,
    TextAnnotation,
    TextLayout,
    ViewTransform,
    arrange,
    crop_marks,
    position_group,
    validate_quad,
)

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


class SheetView(ImageView):
    """A4 scene in millimetres; derived card pixmaps move without rerasterizing."""

    layoutDragStarted = Signal()
    layoutMoved = Signal(object)
    layoutDragFinished = Signal(bool)
    annotationPlaceRequested = Signal(float, float)
    annotationSelected = Signal(object)
    annotationEditRequested = Signal(str)
    annotationDragStarted = Signal(str)
    annotationMoved = Signal(str, float, float)
    annotationDragFinished = Signal(str, bool)
    annotationDeleteRequested = Signal(str)
    placingTextCancelled = Signal()

    def __init__(self, parent=None):
        self.annotations: list[TextAnnotation] = []
        self.annotation_layouts: dict[str, TextLayout] = {}
        self.annotation_renders: dict[
            str, tuple[Image.Image, tuple[float, float, float, float]]
        ] = {}
        self.annotation_items: dict[str, QGraphicsPixmapItem] = {}
        self.annotation_valid: dict[str, bool] = {}
        self.selected_annotation_id: str | None = None
        self.placing_text: bool = False
        self._text_drag_id: str | None = None
        self._text_drag_start: QPointF | None = None
        self._text_drag_origin: TextAnnotation | None = None
        self._text_drag_origin_layout: TextLayout | None = None
        self._text_drag_current: TextAnnotation | None = None
        self._text_drag_base_pos: QPointF | None = None
        super().__init__(parent=parent)
        self.rulers_visible = True
        self.guides_visible = True
        self.sheet_layout = Layout()
        self.placements = []
        self.card_items = {}
        self.mark_items = []
        self._drag_start = None
        self._drag_layout = None
        self.clear_sheet()

    @property
    def is_layout_dragging(self):
        return self._drag_start is not None

    @property
    def is_text_dragging(self) -> bool:
        return self._text_drag_id is not None

    @property
    def committed_layout(self):
        return self._drag_layout if self.is_layout_dragging else self.sheet_layout

    def set_placing_text(self, active: bool) -> None:
        self.placing_text = bool(active)
        if self.placing_text:
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.viewport().unsetCursor()

    def select_annotation(self, ann_id: str | None, *, emit: bool = True) -> None:
        if ann_id is not None and all(a.id != ann_id for a in self.annotations):
            ann_id = None
        if self.selected_annotation_id == ann_id:
            return
        self.selected_annotation_id = ann_id
        self.viewport().update()
        if emit:
            self.annotationSelected.emit(ann_id)

    def _rebuild_annotation_items(self) -> None:
        for item in self.annotation_items.values():
            if item.scene() is self.scene():
                self.scene().removeItem(item)
        self.annotation_items = {}
        for idx, ann in enumerate(self.annotations):
            rendered = self.annotation_renders.get(ann.id)
            if rendered is None:
                continue
            rgba, (x0_mm, y0_mm, w_mm, h_mm) = rendered
            if rgba.width <= 0 or rgba.height <= 0 or w_mm <= 0 or h_mm <= 0:
                continue
            qimg = QImage(
                rgba.tobytes("raw", "RGBA"),
                rgba.width,
                rgba.height,
                rgba.width * 4,
                QImage.Format.Format_RGBA8888,
            ).copy()
            item = QGraphicsPixmapItem(QPixmap.fromImage(qimg))
            item.setTransform(QTransform.fromScale(w_mm / rgba.width, h_mm / rgba.height))
            item.setPos(x0_mm, y0_mm)
            item.setZValue(3 + idx)
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.scene().addItem(item)
            self.annotation_items[ann.id] = item

    def set_annotations(
        self,
        annotations: list[TextAnnotation] | tuple[TextAnnotation, ...],
        *,
        layouts: dict[str, TextLayout] | None = None,
        rendered: dict[str, tuple[Image.Image, tuple[float, float, float, float]]] | None = None,
        valid_map: dict[str, bool] | None = None,
    ) -> None:
        self.cancel_text_drag(emit=False)
        self.annotations = list(annotations)
        self.annotation_layouts = {}
        self.annotation_renders = {}
        self.annotation_valid = {}
        for ann in self.annotations:
            layout = layouts.get(ann.id) if layouts is not None else None
            if layout is None:
                try:
                    layout = layout_text_annotation(ann, validate_bounds=False)
                except ValueError:
                    self.annotation_valid[ann.id] = False
                    continue
            self.annotation_layouts[ann.id] = layout
            if valid_map is not None and ann.id in valid_map:
                self.annotation_valid[ann.id] = valid_map[ann.id]
            else:
                min_x, min_y, max_x, max_y = layout.bounds_mm
                self.annotation_valid[ann.id] = (
                    min_x >= -1e-6
                    and min_y >= -1e-6
                    and max_x <= A4_WIDTH_MM + 1e-6
                    and max_y <= A4_HEIGHT_MM + 1e-6
                )
            render_entry = rendered.get(ann.id) if rendered is not None else None
            if render_entry is None:
                render_entry = render_text_annotation_rgba(layout, dpi=150.0)
            self.annotation_renders[ann.id] = render_entry
        if self.selected_annotation_id is not None and all(
            a.id != self.selected_annotation_id for a in self.annotations
        ):
            self.selected_annotation_id = None
        self._rebuild_annotation_items()
        self.viewport().update()

    def clear_sheet(self):
        self.cancel_layout_drag(emit=False)
        self.cancel_text_drag(emit=False)
        self.card_items = {}
        self.mark_items = []
        self.annotation_items = {}
        self.placements = []
        self.set_image(Image.new("RGB", (210, 297), "white"), (A4_WIDTH_MM, A4_HEIGHT_MM))
        self._rebuild_annotation_items()

    def set_sheet(self, image, placements, layout):
        preserve_view = bool(self.placements) and not self.auto_fit
        transform = self.transform()
        centre = self.mapToScene(self.viewport().rect().center())
        self.clear_sheet()
        self.sheet_layout = layout
        self.placements = placements
        rendered = pixmap(image)
        scale = 120 / 25.4  # render_sheet's preview DPI; scene positions stay in mm.
        for p in placements:
            x, y = round(p.x_mm * scale), round(p.y_mm * scale)
            right = round((p.x_mm + p.width_mm) * scale)
            bottom = round((p.y_mm + p.height_mm) * scale)
            item = QGraphicsPixmapItem(rendered.copy(x, y, right - x, bottom - y))
            item.setTransform(
                QTransform.fromScale(p.width_mm / (right - x), p.height_mm / (bottom - y))
            )
            item.setPos(p.x_mm, p.y_mm)
            item.setZValue(2)
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.scene().addItem(item)
            self.card_items[p.side] = item
        self._update_marks()
        if preserve_view:
            self.setTransform(transform)
            self.centerOn(centre)
            self.auto_fit = False
        self.viewport().update()

    def set_rulers_visible(self, visible):
        self.rulers_visible = visible
        if self.auto_fit:
            self.fit_image()
        self.viewport().update()

    def set_guides_visible(self, visible):
        self.guides_visible = visible
        self.viewport().update()

    def _update_marks(self):
        for item in self.mark_items:
            self.scene().removeItem(item)
        self.mark_items = []
        if self.sheet_layout.crop_marks:
            for mark in crop_marks(self.placements).segments:
                item = QGraphicsLineItem(*mark.start, *mark.end)
                pen = QPen(QColor("#000000"), mark.width_mm)
                pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                item.setPen(pen)
                item.setZValue(1)
                item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                self.scene().addItem(item)
                self.mark_items.append(item)

    def _move_layout(self, layout):
        sizes = {p.side: (p.width_mm, p.height_mm) for p in self.placements}
        self.sheet_layout = layout
        self.placements = arrange(sizes, layout)
        for p in self.placements:
            self.card_items[p.side].setPos(p.x_mm, p.y_mm)
        self._update_marks()
        self.layoutMoved.emit(layout)
        self.viewport().update()

    def _hit_annotation(self, point: QPointF) -> TextAnnotation | None:
        for ann in reversed(self.annotations):
            layout = self.annotation_layouts.get(ann.id)
            if layout is None:
                continue
            poly = QPolygonF([QPointF(*pt) for pt in layout.quad_mm])
            if poly.containsPoint(point, Qt.FillRule.OddEvenFill):
                return ann
        return None

    @staticmethod
    def _shifted_layout(layout: TextLayout, dx: float, dy: float) -> TextLayout:
        shifted_ann = replace(
            layout.annotation,
            x_mm=round(layout.annotation.x_mm + dx, 2),
            y_mm=round(layout.annotation.y_mm + dy, 2),
        )
        shifted_quad = tuple((x + dx, y + dy) for x, y in layout.quad_mm)
        min_x, min_y, max_x, max_y = layout.bounds_mm
        return replace(
            layout,
            annotation=shifted_ann,
            quad_mm=shifted_quad,
            bounds_mm=(min_x + dx, min_y + dy, max_x + dx, max_y + dy),
        )

    def mousePressEvent(self, event):
        point = QPointF(*self.viewport_to_source(event.position()))
        on_ruler = self.rulers_visible and (event.position().x() < 28 or event.position().y() < 24)
        if event.button() == Qt.MouseButton.LeftButton and not on_ruler:
            if self.placing_text:
                x = round(max(5.0, min(point.x(), A4_WIDTH_MM - 5.0)), 2)
                y = round(max(10.0, min(point.y(), A4_HEIGHT_MM - 10.0)), 2)
                self.set_placing_text(False)
                self.setFocus()
                self.annotationPlaceRequested.emit(x, y)
                event.accept()
                return
            hit_ann = self._hit_annotation(point)
            if hit_ann is not None and hit_ann.id in self.annotation_items:
                self.select_annotation(hit_ann.id)
                self._text_drag_id = hit_ann.id
                self._text_drag_start = point
                self._text_drag_origin = hit_ann
                self._text_drag_origin_layout = self.annotation_layouts.get(hit_ann.id)
                self._text_drag_current = hit_ann
                self._text_drag_base_pos = self.annotation_items[hit_ann.id].pos()
                self.setFocus()
                self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
                self.annotationDragStarted.emit(hit_ann.id)
                event.accept()
                return
            self.select_annotation(None)
            if any(
                QRectF(p.x_mm, p.y_mm, p.width_mm, p.height_mm).contains(point)
                for p in self.placements
            ):
                self._drag_start = point
                self._drag_layout = self.sheet_layout
                self.setFocus()
                self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
                self.layoutDragStarted.emit()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        point = QPointF(*self.viewport_to_source(event.position()))
        on_ruler = self.rulers_visible and (event.position().x() < 28 or event.position().y() < 24)
        if event.button() == Qt.MouseButton.LeftButton and not on_ruler:
            hit_ann = self._hit_annotation(point)
            if hit_ann is not None:
                self.cancel_text_drag(emit=False)
                self.select_annotation(hit_ann.id)
                self.annotationEditRequested.emit(hit_ann.id)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        if self.is_text_dragging:
            ann_id = self._text_drag_id
            delta = QPointF(*self.viewport_to_source(event.position())) - self._text_drag_start
            origin = self._text_drag_origin
            new_x = round(origin.x_mm + delta.x(), 2)
            new_y = round(origin.y_mm + delta.y(), 2)
            dx = new_x - origin.x_mm
            dy = new_y - origin.y_mm
            item = self.annotation_items.get(ann_id)
            if item is not None and self._text_drag_base_pos is not None:
                item.setPos(self._text_drag_base_pos.x() + dx, self._text_drag_base_pos.y() + dy)
            if self._text_drag_origin_layout is not None:
                shifted = self._shifted_layout(self._text_drag_origin_layout, dx, dy)
                self.annotation_layouts[ann_id] = shifted
                min_x, min_y, max_x, max_y = shifted.bounds_mm
                self.annotation_valid[ann_id] = (
                    min_x >= -1e-6
                    and min_y >= -1e-6
                    and max_x <= A4_WIDTH_MM + 1e-6
                    and max_y <= A4_HEIGHT_MM + 1e-6
                )
                self._text_drag_current = shifted.annotation
            else:
                self._text_drag_current = replace(origin, x_mm=new_x, y_mm=new_y)
            self.annotationMoved.emit(ann_id, new_x, new_y)
            self.viewport().update()
            event.accept()
            return
        if self.is_layout_dragging:
            delta = QPointF(*self.viewport_to_source(event.position())) - self._drag_start
            sizes = {p.side: (p.width_mm, p.height_mm) for p in self.placements}
            self._move_layout(
                position_group(
                    sizes,
                    self._drag_layout,
                    self._drag_layout.offset_x_mm + delta.x(),
                    self._drag_layout.offset_y_mm + delta.y(),
                )
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.is_text_dragging and event.button() == Qt.MouseButton.LeftButton:
            self.mouseMoveEvent(event)
            ann_id = self._text_drag_id
            changed = (
                self._text_drag_current is not None
                and self._text_drag_current != self._text_drag_origin
            )
            self._text_drag_id = None
            self._text_drag_start = None
            self._text_drag_origin = None
            self._text_drag_origin_layout = None
            self._text_drag_current = None
            self._text_drag_base_pos = None
            self.viewport().unsetCursor()
            self.annotationDragFinished.emit(ann_id, changed)
            self.viewport().update()
            event.accept()
            return
        if self.is_layout_dragging and event.button() == Qt.MouseButton.LeftButton:
            self.mouseMoveEvent(event)
            changed = self.sheet_layout != self._drag_layout
            self._drag_start = self._drag_layout = None
            self.viewport().unsetCursor()
            self.layoutDragFinished.emit(changed)
            self.viewport().update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def cancel_text_drag(self, *, emit: bool = True) -> bool:
        if not self.is_text_dragging:
            return False
        ann_id = self._text_drag_id
        origin = self._text_drag_origin
        origin_layout = self._text_drag_origin_layout
        base_pos = self._text_drag_base_pos
        self._text_drag_id = None
        self._text_drag_start = None
        self._text_drag_origin = None
        self._text_drag_origin_layout = None
        self._text_drag_current = None
        self._text_drag_base_pos = None
        item = self.annotation_items.get(ann_id)
        if item is not None and base_pos is not None:
            item.setPos(base_pos)
        if origin_layout is not None:
            self.annotation_layouts[ann_id] = origin_layout
            min_x, min_y, max_x, max_y = origin_layout.bounds_mm
            self.annotation_valid[ann_id] = (
                min_x >= -1e-6
                and min_y >= -1e-6
                and max_x <= A4_WIDTH_MM + 1e-6
                and max_y <= A4_HEIGHT_MM + 1e-6
            )
        if origin is not None and emit:
            self.annotationMoved.emit(ann_id, origin.x_mm, origin.y_mm)
        self.viewport().unsetCursor()
        if emit:
            self.annotationDragFinished.emit(ann_id, False)
        self.viewport().update()
        return True

    def cancel_layout_drag(self, *, emit=True):
        if not self.is_layout_dragging:
            return False
        origin = self._drag_layout
        self._drag_start = self._drag_layout = None
        self._move_layout(origin)
        self.viewport().unsetCursor()
        if emit:
            self.layoutDragFinished.emit(False)
        return True

    def keyPressEvent(self, event):
        if self.is_text_dragging and event.key() == Qt.Key.Key_Escape:
            self.cancel_text_drag()
            event.accept()
            return
        if self.placing_text and event.key() == Qt.Key.Key_Escape:
            self.set_placing_text(False)
            self.placingTextCancelled.emit()
            event.accept()
            return
        if self.is_layout_dragging and event.key() == Qt.Key.Key_Escape:
            self.cancel_layout_drag()
            event.accept()
            return
        if (
            event.key() == Qt.Key.Key_Delete
            and self.selected_annotation_id is not None
            and not self.is_text_dragging
        ):
            self.annotationDeleteRequested.emit(self.selected_annotation_id)
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        self.cancel_layout_drag()
        self.cancel_text_drag()
        super().focusOutEvent(event)

    def wheelEvent(self, event):
        if self.is_layout_dragging or self.is_text_dragging:
            event.accept()
        else:
            super().wheelEvent(event)

    def fit_image(self):
        if getattr(self, "rulers_visible", False) and self.image_item:
            # Keep the full paper clear of the viewport ruler strips even at 280×240.
            scale = min(
                self.viewport().width() / A4_WIDTH_MM, self.viewport().height() / A4_HEIGHT_MM
            )
            padding = 40 / max(scale, 0.01)
            self.fitInView(
                QRectF(0, 0, A4_WIDTH_MM, A4_HEIGHT_MM).adjusted(
                    -padding, -padding, padding, padding
                ),
                Qt.AspectRatioMode.KeepAspectRatio,
            )
            self.auto_fit = True
        else:
            super().fit_image()

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        if self.guides_visible:
            pen = QPen(QColor("#64748b"), 1, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            m = self.sheet_layout.margin_mm
            painter.drawRect(QRectF(m, m, A4_WIDTH_MM - 2 * m, A4_HEIGHT_MM - 2 * m))
        if self.is_layout_dragging:
            pen = QPen(self._accent_color, 1, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            if self.sheet_layout.offset_x_mm == 0:
                painter.drawLine(QPointF(105, 0), QPointF(105, 297))
            if self.sheet_layout.offset_y_mm == 0:
                painter.drawLine(QPointF(0, 148.5), QPointF(210, 148.5))
        for ann in self.annotations:
            layout = self.annotation_layouts.get(ann.id)
            if layout is None:
                continue
            valid = self.annotation_valid.get(ann.id, True)
            selected = ann.id == self.selected_annotation_id
            if not valid:
                pen = QPen(QColor("#dc2626"), 1.6, Qt.PenStyle.DashLine)
            elif selected:
                pen = QPen(self._accent_color, 1.5, Qt.PenStyle.DashLine)
            else:
                continue
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(QPolygonF([QPointF(*pt) for pt in layout.quad_mm]))

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.rulers_visible:
            return
        painter = QPainter(self.viewport())
        painter.fillRect(0, 0, self.viewport().width(), 24, self.palette().window())
        painter.fillRect(0, 0, 28, self.viewport().height(), self.palette().window())
        painter.setPen(self.palette().windowText().color())
        font = painter.font()
        font.setPixelSize(10)
        painter.setFont(font)
        scale = self.transform().m11()
        step = 1 if scale >= 4 else 5 if scale >= 1 else 10 if scale >= 0.5 else 50
        label_width = painter.fontMetrics().horizontalAdvance("297") + 8
        major = next((n for n in (10, 20, 50, 100) if n * scale >= label_width), 100)
        for mm in range(0, 211, step):
            x = self.mapFromScene(QPointF(mm, 0)).x()
            if 28 <= x < self.viewport().width():
                painter.drawLine(x, 24, x, 17 if mm % major == 0 else 21)
                if mm % major == 0:
                    painter.drawText(x + 2, 12, str(mm))
        for mm in range(0, 298, step):
            y = self.mapFromScene(QPointF(0, mm)).y()
            if 24 <= y < self.viewport().height():
                painter.drawLine(28, y, 22 if mm % major == 0 else 25, y)
                if mm % major == 0:
                    painter.drawText(1, y - 2, str(mm))
        painter.drawText(3, 13, "мм")

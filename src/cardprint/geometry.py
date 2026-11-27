"""Image coordinates (pixels) and print geometry (millimetres) are separate types."""

from dataclasses import dataclass, replace
from math import ceil, floor, hypot, isfinite

Point = tuple[float, float]
Quad = tuple[Point, Point, Point, Point]  # TL, TR, BR, BL in rotated source pixels
A4_WIDTH_MM = 210.0
A4_HEIGHT_MM = 297.0
MM_TO_PT = 72 / 25.4
CROP_MARK_WIDTH_MM = 0.15


@dataclass(frozen=True)
class ViewTransform:
    """Uniform source → editor transform, including pan, with exact inverse."""

    scale: float
    offset_x: float = 0
    offset_y: float = 0

    def __post_init__(self):
        if not isfinite(self.scale) or self.scale <= 0:
            raise ValueError("Масштаб должен быть положительным.")

    def to_view(self, point: Point) -> Point:
        return (point[0] * self.scale + self.offset_x, point[1] * self.scale + self.offset_y)

    def to_source(self, point: Point) -> Point:
        return ((point[0] - self.offset_x) / self.scale, (point[1] - self.offset_y) / self.scale)


def validate_quad(points, width: int, height: int) -> Quad:
    if len(points) != 4:
        raise ValueError("Укажите четыре угла по часовой стрелке, начиная с верхнего левого.")
    p = tuple((float(x), float(y)) for x, y in points)
    if any(not isfinite(v) for point in p for v in point):
        raise ValueError("Координаты углов некорректны.")
    if any(x < 0 or y < 0 or x > width - 1 or y > height - 1 for x, y in p):
        raise ValueError("Углы должны находиться внутри изображения.")
    crosses = []
    for i in range(4):
        a, b, c = p[i], p[(i + 1) % 4], p[(i + 2) % 4]
        crosses.append((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]))
    if not (all(c > 0.01 for c in crosses) or all(c < -0.01 for c in crosses)):
        raise ValueError("Выделение должно быть выпуклым, без пересечений и совпавших углов.")
    if all(c < 0 for c in crosses):
        raise ValueError(
            "Расположите углы по часовой стрелке, начиная с верхнего левого; "
            "иначе изображение будет зеркальным."
        )
    if any(hypot(p[i][0] - p[(i + 1) % 4][0], p[i][1] - p[(i + 1) % 4][1]) < 3 for i in range(4)):
        raise ValueError("Выделение слишком маленькое: разнесите углы.")
    return p


def quad_size(points: Quad) -> tuple[int, int]:
    a, b, c, d = points
    width = max(hypot(b[0] - a[0], b[1] - a[1]), hypot(c[0] - d[0], c[1] - d[1]))
    height = max(hypot(d[0] - a[0], d[1] - a[1]), hypot(c[0] - b[0], c[1] - b[1]))
    return max(2, round(width) + 1), max(2, round(height) + 1)


@dataclass(frozen=True)
class CardSize:
    mode: str = "id1"  # id1, aspect, custom
    width_mm: float = 85.60
    height_mm: float = 53.98

    def resolve(self, pixels: tuple[int, int]) -> tuple[float, float]:
        if self.mode == "id1":
            return 85.60, 53.98
        if self.mode == "aspect":
            return self.width_mm, self.width_mm * pixels[1] / pixels[0]
        if self.mode != "custom":
            raise ValueError("Неизвестный режим размера карточки.")
        return self.width_mm, self.height_mm


@dataclass(frozen=True)
class Layout:
    template: str = "vertical"
    gap_mm: float = 15
    margin_mm: float = 10
    offset_x_mm: float = 0
    offset_y_mm: float = 0
    crop_marks: bool = False


@dataclass(frozen=True)
class Placement:
    side: str
    x_mm: float
    y_mm: float  # measured from top of the A4 sheet
    width_mm: float
    height_mm: float


@dataclass(frozen=True)
class CropMark:
    start: Point
    end: Point
    width_mm: float = CROP_MARK_WIDTH_MM


@dataclass(frozen=True)
class CropMarks:
    segments: tuple[CropMark, ...] = ()
    omitted: int = 0

    @property
    def warning(self) -> str:
        return (
            f"⚠ Пропущено меток реза: {self.omitted}. Недостаточно места до края листа "
            "или соседней карточки. Размеры карточек сохранены."
            if self.omitted
            else ""
        )


def crop_marks(placements: list[Placement]) -> CropMarks:
    """3 mm marks, 1 mm away from corners; omit unsafe strokes in their entirety."""
    segments = []
    omitted = 0
    for card in placements:
        left, top = card.x_mm, card.y_mm
        right, bottom = left + card.width_mm, top + card.height_mm
        for x, direction_x in ((left, -1), (right, 1)):
            for y, direction_y in ((top, -1), (bottom, 1)):
                candidates = (
                    CropMark((x + direction_x, y), (x + 4 * direction_x, y)),
                    CropMark((x, y + direction_y), (x, y + 4 * direction_y)),
                )
                for segment in candidates:
                    half = segment.width_mm / 2
                    x0 = min(segment.start[0], segment.end[0]) - half
                    x1 = max(segment.start[0], segment.end[0]) + half
                    y0 = min(segment.start[1], segment.end[1]) - half
                    y1 = max(segment.start[1], segment.end[1]) + half
                    unsafe = x0 < 0 or y0 < 0 or x1 > A4_WIDTH_MM or y1 > A4_HEIGHT_MM
                    unsafe = unsafe or any(
                        x0 <= p.x_mm + p.width_mm
                        and x1 >= p.x_mm
                        and y0 <= p.y_mm + p.height_mm
                        and y1 >= p.y_mm
                        for p in placements
                    )
                    if unsafe:
                        omitted += 1
                    elif segment not in segments:
                        segments.append(segment)
    return CropMarks(tuple(segments), omitted)


def position_group(
    sizes: dict[str, tuple[float, float]],
    layout: Layout,
    x: float,
    y: float,
    *,
    snap_mm: float = 0.8,
) -> Layout:
    """Clamp a gesture to arrange's margins, in the controls' 0.01 mm precision."""
    if not all(isfinite(value) for value in (x, y, snap_mm)) or snap_mm < 0:
        raise ValueError("Некорректное смещение группы карточек.")
    centred = arrange(sizes, replace(layout, offset_x_mm=0, offset_y_mm=0))
    left = min(p.x_mm for p in centred)
    top = min(p.y_mm for p in centred)
    right = max(p.x_mm + p.width_mm for p in centred)
    bottom = max(p.y_mm + p.height_mm for p in centred)

    def clamp(value, low, high):
        if abs(value) <= snap_mm:
            value = 0.0
        low, high = ceil((low - 1e-7) * 100) / 100, floor((high + 1e-7) * 100) / 100
        return max(low, min(high, round(value, 2)))

    result = replace(
        layout,
        offset_x_mm=clamp(x, layout.margin_mm - left, A4_WIDTH_MM - layout.margin_mm - right),
        offset_y_mm=clamp(y, layout.margin_mm - top, A4_HEIGHT_MM - layout.margin_mm - bottom),
    )
    arrange(sizes, result)
    return result


def arrange(sizes: dict[str, tuple[float, float]], layout: Layout) -> list[Placement]:
    if not sizes:
        raise ValueError("Назначьте хотя бы одну сторону документа.")
    values = [layout.gap_mm, layout.margin_mm, layout.offset_x_mm, layout.offset_y_mm]
    if any(not isfinite(v) for v in values) or layout.gap_mm < 0 or layout.margin_mm < 0:
        raise ValueError("Промежуток и отступ должны быть неотрицательными.")
    ordered = [(side, sizes[side]) for side in ("front", "back") if side in sizes]
    if not ordered or len(ordered) != len(sizes):
        raise ValueError("Неизвестная сторона документа.")
    if any(not isfinite(v) or v <= 0 for _, size in ordered for v in size):
        raise ValueError("Размеры карточек должны быть положительными.")
    vertical = layout.template == "vertical"
    if layout.template not in ("vertical", "horizontal"):
        raise ValueError("Неизвестный шаблон компоновки.")
    group_w = max(w for _, (w, h) in ordered) if vertical else sum(w for _, (w, h) in ordered)
    group_h = sum(h for _, (w, h) in ordered) if vertical else max(h for _, (w, h) in ordered)
    if vertical:
        group_h += layout.gap_mm * (len(ordered) - 1)
    else:
        group_w += layout.gap_mm * (len(ordered) - 1)
    left = (A4_WIDTH_MM - group_w) / 2 + layout.offset_x_mm
    top = (A4_HEIGHT_MM - group_h) / 2 + layout.offset_y_mm
    result = []
    cursor = 0.0
    for side, (w, h) in ordered:
        x = left + (group_w - w) / 2 if vertical else left + cursor
        y = top + cursor if vertical else top + (group_h - h) / 2
        margin = layout.margin_mm
        if (
            x < margin - 1e-7
            or y < margin - 1e-7
            or x + w > A4_WIDTH_MM - margin + 1e-7
            or y + h > A4_HEIGHT_MM - margin + 1e-7
        ):
            raise ValueError(
                "Карточки выходят за поля A4. Уменьшите размер, промежуток, смещение или отступ."
            )
        result.append(Placement(side, x, y, w, h))
        cursor += (h if vertical else w) + layout.gap_mm
    return result

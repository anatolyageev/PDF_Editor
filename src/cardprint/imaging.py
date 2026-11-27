"""Pure processing: never edit the source array, never enhance document content."""

import warnings
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from .geometry import Quad, quad_size, validate_quad
from .pdf_worker import (
    DEFAULT_THUMBNAIL_BATCH_SIZE,
    MAX_PIXELS,
    PDF_DPI,
    PdfWorkerClient,
    get_default_pdf_worker,
)


@dataclass(frozen=True)
class Source:
    path: Path
    page: int | None
    thumbnail: Image.Image

    @property
    def label(self):
        prefix = f"Стр. {self.page + 1} · " if self.page is not None else ""
        return prefix + self.path.name


def _photo(path) -> Image.Image:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError("Изображение слишком большое (более 80 мегапикселей).")
                image = ImageOps.exif_transpose(image)
                if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                    rgba = image.convert("RGBA")
                    white = Image.new("RGBA", rgba.size, "white")
                    return Image.alpha_composite(white, rgba).convert("RGB")
                return image.convert("RGB")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(
            "Не удалось прочитать фотографию: файл повреждён или слишком велик."
        ) from exc


def discover_pdf_batches(
    path: str | Path,
    *,
    batch_size: int = DEFAULT_THUMBNAIL_BATCH_SIZE,
    worker: PdfWorkerClient | None = None,
):
    """Yield ``(batch_sources, total_pages)`` tuples via the isolated PDF worker."""
    resolved = Path(path).resolve()
    if resolved.suffix.lower() != ".pdf":
        raise ValueError("Ожидался PDF-файл.")
    client = worker or get_default_pdf_worker()
    info = client.inspect_pdf(resolved)
    for start in range(0, info.page_count, batch_size):
        batch = client.render_thumbnail_batch(resolved, start, count=batch_size)
        sources = [Source(resolved, page_idx, img.to_pil()) for page_idx, img in batch.items]
        yield sources, info.page_count


def discover(path: str | Path, *, worker: PdfWorkerClient | None = None) -> list[Source]:
    path = Path(path).resolve()
    if path.suffix.lower() == ".pdf":
        sources: list[Source] = []
        for batch_sources, _total in discover_pdf_batches(path, worker=worker):
            sources.extend(batch_sources)
        return sources
    if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        raise ValueError("Поддерживаются только PDF, JPG, JPEG и PNG.")
    thumb = _photo(path)
    thumb.thumbnail((180, 140))
    return [Source(path, None, thumb)]


def load_source(source: Source, *, worker: PdfWorkerClient | None = None) -> np.ndarray:
    if source.page is None:
        image = _photo(source.path)
        if min(image.size) < 4:
            raise ValueError("Изображение слишком маленькое для выделения карточки.")
        array = np.array(image, dtype=np.uint8)
        array.flags.writeable = False
        return array
    client = worker or get_default_pdf_worker()
    rendered = client.render_page(source.path, source.page, dpi=PDF_DPI)
    array = rendered.to_array()
    if min(array.shape[1], array.shape[0]) < 4:
        raise ValueError("Изображение слишком маленькое для выделения карточки.")
    return array


def rotated(source: np.ndarray, turns: int) -> np.ndarray:
    return np.ascontiguousarray(np.rot90(source, -(turns % 4)))


def full_quad(width: int, height: int) -> Quad:
    return ((0.0, 0.0), (width - 1.0, 0.0), (width - 1.0, height - 1.0), (0.0, height - 1.0))


def rotate_quad(points: Quad, old_height: int) -> Quad:
    # Clockwise rotation: the former BL becomes TL.
    moved = tuple((old_height - 1 - y, x) for x, y in points)
    return moved[3], moved[0], moved[1], moved[2]


def detect_quad(image: np.ndarray) -> Quad | None:
    height, width = image.shape[:2]
    scale = min(1.0, 1200 / max(width, height))
    small = cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))))
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 40, 140)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    area = small.shape[0] * small.shape[1]
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        if not 0.05 * area < cv2.contourArea(contour) < 0.995 * area:
            continue
        poly = cv2.approxPolyDP(contour, 0.025 * cv2.arcLength(contour, True), True)
        if len(poly) != 4 or not cv2.isContourConvex(poly):
            continue
        points = poly.reshape(4, 2).astype(float)
        center = points.mean(axis=0)
        points = points[np.argsort(np.arctan2(points[:, 1] - center[1], points[:, 0] - center[0]))]
        points = np.roll(points, -int(np.argmin(points.sum(axis=1))), axis=0)
        points[:, 0] *= width / small.shape[1]
        points[:, 1] *= height / small.shape[0]
        points[:, 0] = np.clip(points[:, 0], 0, width - 1)
        points[:, 1] = np.clip(points[:, 1], 0, height - 1)
        try:
            return validate_quad(points, width, height)
        except ValueError:
            continue
    return None


def rectify(source: np.ndarray, turns: int, points: Quad) -> Image.Image:
    image = rotated(source, turns)
    points = validate_quad(points, image.shape[1], image.shape[0])
    width, height = quad_size(points)
    if width * height > MAX_PIXELS:
        raise ValueError("Результат слишком велик (более 80 мегапикселей).")
    matrix = cv2.getPerspectiveTransform(np.float32(points), np.float32(full_quad(width, height)))
    output = cv2.warpPerspective(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )
    return Image.fromarray(output)


def round_corners(
    image: Image.Image, radius_mm: float, size_mm: tuple[float, float]
) -> Image.Image:
    """White only outside the rounded contour; preserve interior pixels exactly."""
    if radius_mm <= 0:
        return image
    w, h = image.size
    rx = min(w / 2, radius_mm / size_mm[0] * w)
    ry = min(h / 2, radius_mm / size_mm[1] * h)
    # Analytic ellipse coverage at pixel centres; bounded memory even for large images.
    corner_w, corner_h = int(np.ceil(rx)), int(np.ceil(ry))
    yy, xx = np.ogrid[:corner_h, :corner_w]
    distance = np.sqrt(((xx + 0.5 - rx) / rx) ** 2 + ((yy + 0.5 - ry) / ry) ** 2)
    alpha = np.clip((1 - distance) * min(rx, ry) + 0.5, 0, 1)[..., None]
    result = np.array(image)
    for sy, sx, mask in (
        (slice(0, corner_h), slice(0, corner_w), alpha),
        (slice(0, corner_h), slice(w - corner_w, w), alpha[:, ::-1]),
        (slice(h - corner_h, h), slice(0, corner_w), alpha[::-1]),
        (slice(h - corner_h, h), slice(w - corner_w, w), alpha[::-1, ::-1]),
    ):
        region = result[sy, sx]
        result[sy, sx] = np.rint(region * mask + 255 * (1 - mask)).astype(np.uint8)
    return Image.fromarray(result)

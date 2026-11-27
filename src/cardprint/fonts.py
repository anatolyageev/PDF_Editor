"""Packaged Cyrillic TrueType font resolver and shared millimeter text layout."""

import math
import threading
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from io import BytesIO

from PIL import ImageFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from .geometry import (
    MM_TO_PT,
    TEXT_LINE_ADVANCE_FACTOR,
    VALID_FONT_FAMILIES,
    Quad,
    TextAnnotation,
    TextLayout,
    TextLineLayout,
    validate_text_bounds,
)

MEASURE_DPI = 300.0


@dataclass(frozen=True)
class FontSpec:
    family: str
    display_name: str
    filename: str
    reportlab_name: str
    license_file: str = "LICENSE-DejaVu.txt"


FONT_FAMILIES: tuple[str, ...] = VALID_FONT_FAMILIES

FONT_SPECS: dict[str, FontSpec] = {
    "sans": FontSpec(
        family="sans",
        display_name="Без засечек",
        filename="DejaVuSans.ttf",
        reportlab_name="CardPrint-DejaVuSans",
    ),
    "sans-bold": FontSpec(
        family="sans-bold",
        display_name="Полужирный",
        filename="DejaVuSans-Bold.ttf",
        reportlab_name="CardPrint-DejaVuSans-Bold",
    ),
    "serif": FontSpec(
        family="serif",
        display_name="С засечками",
        filename="DejaVuSerif.ttf",
        reportlab_name="CardPrint-DejaVuSerif",
    ),
    "mono": FontSpec(
        family="mono",
        display_name="Моноширинный",
        filename="DejaVuSansMono.ttf",
        reportlab_name="CardPrint-DejaVuSansMono",
    ),
}

_REPORTLAB_LOCK = threading.Lock()


def get_font_spec(family: str) -> FontSpec:
    spec = FONT_SPECS.get(family)
    if spec is None:
        raise ValueError(f"Неизвестное семейство шрифта: {family}.")
    return spec


def _asset_path(filename: str):
    return resources.files("cardprint").joinpath("assets", "fonts", filename)


@lru_cache(maxsize=8)
def get_font_bytes(family: str) -> bytes:
    """Load packaged TrueType font bytes strictly from package resources."""
    spec = get_font_spec(family)
    resource = _asset_path(spec.filename)
    if not resource.is_file():
        raise FileNotFoundError(
            f"Не найден встроенный файл шрифта «{spec.display_name}» ({spec.filename})."
        )
    return resource.read_bytes()


@lru_cache(maxsize=4)
def get_font_license_text(family: str = "sans") -> str:
    """Return the bundled license text associated with packaged fonts."""
    spec = get_font_spec(family)
    resource = _asset_path(spec.license_file)
    if not resource.is_file():
        raise FileNotFoundError(f"Не найден файл лицензии шрифтов: {spec.license_file}.")
    return resource.read_text(encoding="utf-8")


@lru_cache(maxsize=8)
def get_font_cmap(family: str) -> frozenset[int]:
    """Return supported Unicode code points for ``family`` from its packaged TTF cmap."""
    spec = get_font_spec(family)
    ttfont = TTFont(spec.reportlab_name, BytesIO(get_font_bytes(family)))
    return frozenset(ttfont.face.charToGlyph.keys())


def unsupported_glyphs(text: str, family: str) -> tuple[str, ...]:
    """Return unique characters in ``text`` that are unsupported by ``family``."""
    cmap = get_font_cmap(family)
    missing: list[str] = []
    seen: set[str] = set()
    for ch in text:
        if ch in ("\n", "\r"):
            continue
        code = ord(ch)
        if code < 32 or code == 127 or code not in cmap:
            if ch not in seen:
                seen.add(ch)
                missing.append(ch)
    return tuple(missing)


def validate_text_glyphs(text: str, family: str) -> None:
    """Raise a Russian ``ValueError`` if ``family`` cannot render every character in ``text``."""
    spec = get_font_spec(family)
    missing = unsupported_glyphs(text, family)
    if missing:
        chars = ", ".join(f"«{ch}» (U+{ord(ch):04X})" for ch in missing)
        raise ValueError(
            f"Шрифт «{spec.display_name}» не содержит символы: {chars}. "
            "Замените символы или выберите другое семейство шрифта."
        )


def register_reportlab_font(family: str) -> str:
    """Thread-safely register the packaged TTF for ReportLab using shared font bytes."""
    spec = get_font_spec(family)
    font_bytes = get_font_bytes(family)
    with _REPORTLAB_LOCK:
        if spec.reportlab_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(spec.reportlab_name, BytesIO(font_bytes)))
    return spec.reportlab_name


def load_pil_font(family: str, size_pt: float, dpi: float) -> ImageFont.FreeTypeFont:
    """Construct a Pillow FreeTypeFont from the exact packaged TTF bytes at ``dpi``."""
    size_px = float(size_pt) * float(dpi) / 72.0
    return ImageFont.truetype(BytesIO(get_font_bytes(family)), size=size_px)


def layout_text_annotation(
    annotation: TextAnnotation, *, validate_bounds: bool = True
) -> TextLayout:
    """Compute backend-neutral millimeter line baselines, ink box, and rotated A4 bounds."""
    validate_text_glyphs(annotation.text, annotation.font_family)
    normalized = annotation.text.replace("\r\n", "\n").replace("\r", "\n")
    raw_lines = normalized.split("\n")

    pil_font = load_pil_font(annotation.font_family, annotation.font_size_pt, MEASURE_DPI)
    px_per_mm = MEASURE_DPI / 25.4
    line_advance_mm = TEXT_LINE_ADVANCE_FACTOR * float(annotation.font_size_pt) / MM_TO_PT
    ascent_px, descent_px = pil_font.getmetrics()
    ascent_mm = ascent_px / px_per_mm
    descent_mm = descent_px / px_per_mm

    measured_lines: list[TextLineLayout] = []
    min_x: float | None = None
    min_y: float | None = None
    max_x: float | None = None
    max_y: float | None = None

    for idx, line in enumerate(raw_lines):
        baseline_y_mm = idx * line_advance_mm
        advance_mm = pil_font.getlength(line) / px_per_mm if line else 0.0
        if annotation.align == "left":
            offset_x_mm = 0.0
        elif annotation.align == "center":
            offset_x_mm = -advance_mm / 2.0
        else:
            offset_x_mm = -advance_mm

        bbox = pil_font.getbbox(line, anchor="ls") if line else None
        if bbox is not None:
            left_px, top_px, right_px, bottom_px = bbox
            ink_left = offset_x_mm + left_px / px_per_mm
            ink_top = baseline_y_mm + top_px / px_per_mm
            ink_right = offset_x_mm + right_px / px_per_mm
            ink_bottom = baseline_y_mm + bottom_px / px_per_mm
        else:
            ink_left = offset_x_mm
            ink_top = baseline_y_mm - ascent_mm
            ink_right = offset_x_mm + advance_mm
            ink_bottom = baseline_y_mm + descent_mm

        measured_lines.append(
            TextLineLayout(
                text=line,
                offset_x_mm=offset_x_mm,
                baseline_y_mm=baseline_y_mm,
                advance_mm=advance_mm,
                ink_left_mm=ink_left,
                ink_top_mm=ink_top,
                ink_right_mm=ink_right,
                ink_bottom_mm=ink_bottom,
            )
        )
        if bbox is not None or not line.strip():
            min_x = ink_left if min_x is None else min(min_x, ink_left)
            min_y = ink_top if min_y is None else min(min_y, ink_top)
            max_x = ink_right if max_x is None else max(max_x, ink_right)
            max_y = ink_bottom if max_y is None else max(max_y, ink_bottom)

    assert min_x is not None and min_y is not None and max_x is not None and max_y is not None
    local_bounds = (min_x, min_y, max_x, max_y)

    theta = math.radians(float(annotation.angle_deg))
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    ax = float(annotation.x_mm)
    ay = float(annotation.y_mm)

    local_corners = (
        (min_x, min_y),
        (max_x, min_y),
        (max_x, max_y),
        (min_x, max_y),
    )
    rotated_corners: list[tuple[float, float]] = []
    for dx, dy in local_corners:
        rx = ax + dx * cos_t - dy * sin_t
        ry = ay + dx * sin_t + dy * cos_t
        rotated_corners.append((rx, ry))

    quad: Quad = (
        rotated_corners[0],
        rotated_corners[1],
        rotated_corners[2],
        rotated_corners[3],
    )
    xs = [pt[0] for pt in quad]
    ys = [pt[1] for pt in quad]
    bounds = (min(xs), min(ys), max(xs), max(ys))

    layout = TextLayout(
        annotation=annotation,
        lines=tuple(measured_lines),
        line_advance_mm=line_advance_mm,
        ascent_mm=ascent_mm,
        descent_mm=descent_mm,
        local_bounds_mm=local_bounds,
        quad_mm=quad,
        bounds_mm=bounds,
    )
    if validate_bounds:
        validate_text_bounds(layout)
    return layout

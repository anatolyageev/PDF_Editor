from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ICONS: dict[str, str] = {
    "add_files": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
        '<polyline points="14 2 14 8 20 8"/>'
        '<line x1="12" y1="12" x2="12" y2="18"/>'
        '<line x1="9" y1="15" x2="15" y2="15"/>'
        "</svg>"
    ),
    "undo": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="9 14 4 9 9 4"/>'
        '<path d="M20 20v-5a6 6 0 0 0-6-6H4"/>'
        "</svg>"
    ),
    "redo": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="15 14 20 9 15 4"/>'
        '<path d="M4 20v-5a6 6 0 0 1 6-6h10"/>'
        "</svg>"
    ),
    "preview": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="5" y="2" width="14" height="20" rx="2"/>'
        '<rect x="8" y="6" width="8" height="4.5" rx="0.8"/>'
        '<rect x="8" y="13.5" width="8" height="4.5" rx="0.8"/>'
        "</svg>"
    ),
    "save_pdf": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>'
        '<polyline points="17 21 17 13 7 13 7 21"/>'
        '<polyline points="7 3 7 8 15 8"/>'
        "</svg>"
    ),
    "save_png": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="3" y="4" width="18" height="16" rx="2"/>'
        '<circle cx="8.5" cy="9.5" r="1.5"/>'
        '<path d="m21 15-4.5-4.5L7 20"/>'
        "</svg>"
    ),
    "theme_dark": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/>'
        "</svg>"
    ),
    "theme_light": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="4"/>'
        '<line x1="12" y1="2" x2="12" y2="4"/>'
        '<line x1="12" y1="20" x2="12" y2="22"/>'
        '<line x1="4.9" y1="4.9" x2="6.3" y2="6.3"/>'
        '<line x1="17.7" y1="17.7" x2="19.1" y2="19.1"/>'
        '<line x1="2" y1="12" x2="4" y2="12"/>'
        '<line x1="20" y1="12" x2="22" y2="12"/>'
        '<line x1="4.9" y1="19.1" x2="6.3" y2="17.7"/>'
        '<line x1="17.7" y1="6.3" x2="19.1" y2="4.9"/>'
        "</svg>"
    ),
    "assign_front": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="3" y="5" width="18" height="14" rx="2"/>'
        '<line x1="7" y1="10" x2="17" y2="10"/>'
        '<line x1="7" y1="14" x2="13" y2="14"/>'
        "</svg>"
    ),
    "assign_back": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="3" y="5" width="18" height="14" rx="2"/>'
        '<line x1="3" y1="10" x2="21" y2="10"/>'
        '<line x1="7" y1="15" x2="12" y2="15"/>'
        "</svg>"
    ),
    "reload": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="23 4 23 10 17 10"/>'
        '<path d="M20.5 15a9 9 0 1 1-2.1-9.4L23 10"/>'
        "</svg>"
    ),
    "remove": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="3 6 5 6 21 6"/>'
        '<path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>'
        '<path d="M10 11v6"/>'
        '<path d="M14 11v6"/>'
        '<path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>'
        "</svg>"
    ),
    "autodetect": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 9V5a1 1 0 0 1 1-1h4"/>'
        '<path d="M15 4h4a1 1 0 0 1 1 1v4"/>'
        '<path d="M20 15v4a1 1 0 0 1-1 1h-4"/>'
        '<path d="M9 20H5a1 1 0 0 1-1-1v-4"/>'
        '<rect x="8" y="8" width="8" height="8" rx="1"/>'
        "</svg>"
    ),
    "pick_points": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="5" cy="6" r="2"/>'
        '<circle cx="19" cy="5" r="2"/>'
        '<circle cx="19" cy="18" r="2"/>'
        '<circle cx="6" cy="19" r="2"/>'
        '<path d="M7 6 17 5.2M19 7v9M17 18.2 8 18.9M5.8 17 5.2 8"/>'
        "</svg>"
    ),
    "rotate": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="21 4 21 10 15 10"/>'
        '<path d="M18.4 15A8 8 0 1 1 20 8.5L21 10"/>'
        "</svg>"
    ),
    "reset": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/>'
        '<polyline points="3 3 3 8 8 8"/>'
        "</svg>"
    ),
    "inspect": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="11" cy="11" r="7"/>'
        '<line x1="21" y1="21" x2="16.6" y2="16.6"/>'
        '<line x1="11" y1="8" x2="11" y2="14"/>'
        '<line x1="8" y1="11" x2="14" y2="11"/>'
        "</svg>"
    ),
    "open_external": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'
        '<polyline points="15 3 21 3 21 9"/>'
        '<line x1="10" y1="14" x2="21" y2="3"/>'
        "</svg>"
    ),
    "zoom_in": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<line x1="12" y1="5" x2="12" y2="19"/>'
        '<line x1="5" y1="12" x2="19" y2="12"/>'
        "</svg>"
    ),
    "zoom_out": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<line x1="5" y1="12" x2="19" y2="12"/>'
        "</svg>"
    ),
    "fit": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="15 3 21 3 21 9"/>'
        '<polyline points="9 21 3 21 3 15"/>'
        '<polyline points="21 15 21 21 15 21"/>'
        '<polyline points="3 9 3 3 9 3"/>'
        "</svg>"
    ),
}


def _render_pixmap(svg_bytes: bytes, pixel_size: int, dpr: float) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(svg_bytes))
    pm = QPixmap(pixel_size, pixel_size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    renderer.render(painter, QRectF(0, 0, pixel_size, pixel_size))
    painter.end()
    pm.setDevicePixelRatio(dpr)
    return pm


@lru_cache(maxsize=256)
def render_svg_icon(name: str, color: str, size: int = 18) -> QIcon:
    """Render a bundled inline SVG icon tinted to ``color`` at 1x and 2x DPI."""
    if name not in ICONS:
        raise KeyError(f"Unknown SVG icon: {name}")
    svg_bytes = ICONS[name].replace("currentColor", color).encode("utf-8")
    icon = QIcon()
    icon.addPixmap(_render_pixmap(svg_bytes, size, 1.0))
    icon.addPixmap(_render_pixmap(svg_bytes, size * 2, 2.0))
    return icon

"""Shared millimetre placements drive both preview and PDF/PNG output."""

import errno
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .geometry import A4_HEIGHT_MM, A4_WIDTH_MM, MM_TO_PT, Placement

PRINT_HINT = "Для сохранения размера печатайте в масштабе 100% / Фактический размер"


@dataclass(frozen=True)
class Sheet:
    images: dict[str, Image.Image]
    placements: list[Placement]


def render_sheet(sheet: Sheet, dpi: float = 120) -> Image.Image:
    scale = dpi / 25.4
    result = Image.new("RGB", (round(A4_WIDTH_MM * scale), round(A4_HEIGHT_MM * scale)), "white")
    for item in sheet.placements:
        x, y = round(item.x_mm * scale), round(item.y_mm * scale)
        right, bottom = (
            round((item.x_mm + item.width_mm) * scale),
            round((item.y_mm + item.height_mm) * scale),
        )
        resized = sheet.images[item.side].resize((right - x, bottom - y), Image.Resampling.LANCZOS)
        result.paste(resized, (x, y))
    return result


def _write_pdf(sheet: Sheet, path: Path):
    pdf = canvas.Canvas(
        str(path), pagesize=(A4_WIDTH_MM * MM_TO_PT, A4_HEIGHT_MM * MM_TO_PT), pageCompression=1
    )
    pdf.setTitle("Копии документов — A4")
    pdf.setFillColorRGB(1, 1, 1)
    pdf.rect(0, 0, A4_WIDTH_MM * MM_TO_PT, A4_HEIGHT_MM * MM_TO_PT, fill=1, stroke=0)
    for item in sheet.placements:
        pdf.drawImage(
            ImageReader(sheet.images[item.side]),
            item.x_mm * MM_TO_PT,
            (A4_HEIGHT_MM - item.y_mm - item.height_mm) * MM_TO_PT,
            item.width_mm * MM_TO_PT,
            item.height_mm * MM_TO_PT,
        )
    pdf.showPage()
    pdf.save()


def export_sheet(sheet: Sheet, target: str | Path, kind: str, *, overwrite: bool = False):
    """Stage beside destination, then commit atomically; no silent replacement."""
    target = Path(target)
    if target.exists() and not overwrite:
        raise FileExistsError("Файл уже существует. Выберите другое имя или подтвердите замену.")
    fd, staging = tempfile.mkstemp(prefix=".cardprint-", suffix=".tmp", dir=target.parent)
    os.close(fd)
    staging = Path(staging)
    try:
        if kind == "pdf":
            _write_pdf(sheet, staging)
        elif kind == "png":
            render_sheet(sheet, dpi=300).save(staging, format="PNG", dpi=(300, 300))
        else:
            raise ValueError("Неизвестный формат экспорта.")
        if overwrite:
            os.replace(staging, target)
        else:
            # Hard link is atomic and fails if a file appeared after the initial check.
            try:
                os.link(staging, target)
            except OSError as exc:
                if exc.errno not in {errno.EPERM, errno.EOPNOTSUPP, errno.ENOSYS, errno.EXDEV}:
                    raise
                # FAT/exFAT and some network drives cannot create hard links.
                # Exclusive creation still prevents overwriting an existing file.
                with target.open("xb") as destination:
                    try:
                        with staging.open("rb") as source:
                            shutil.copyfileobj(source, destination)
                    except BaseException:
                        destination.close()
                        target.unlink(missing_ok=True)
                        raise
    finally:
        staging.unlink(missing_ok=True)

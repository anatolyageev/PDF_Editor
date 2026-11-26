"""Generate artificial fixtures locally: uv run python scripts/make_demo.py [directory]."""

import sys
from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def make_demo(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    images = []
    for side, color in (("front", (214, 230, 240)), ("back", (226, 237, 215))):
        photo = Image.new("RGB", (1400, 1000), (125, 112, 98))
        draw = ImageDraw.Draw(photo)
        draw.rounded_rectangle((205, 235, 1205, 865), radius=30, fill=(90, 85, 80))
        draw.rectangle((180, 200, 1180, 830), fill=color, outline=(30, 60, 80), width=5)
        draw.text(
            (230, 245),
            f"SYNTHETIC {side.upper()} / NOT A REAL DOCUMENT",
            fill=(25, 45, 60),
            font_size=28,
        )
        if side == "front":
            draw.rectangle((230, 330, 450, 680), fill=(147, 174, 192))
            draw.ellipse((280, 365, 400, 485), fill=(72, 95, 110))
            draw.ellipse((255, 505, 425, 665), fill=(72, 95, 110))
        for y in range(340, 730, 55):
            draw.line((510 if side == "front" else 250, y, 1090, y), fill=(55, 80, 98), width=4)
        draw.text(
            (235, 760), "Artificial fixture. No personal data.", fill=(35, 65, 85), font_size=24
        )
        path = folder / f"{side}.png"
        if path.exists():
            raise FileExistsError(f"Already exists: {path}")
        photo.save(path)
        images.append(photo)
    pdf_path = folder / "two_pages.pdf"
    if pdf_path.exists():
        raise FileExistsError(f"Already exists: {pdf_path}")
    pdf = canvas.Canvas(str(pdf_path), pagesize=(504, 360))
    for image in images:
        pdf.drawImage(ImageReader(image), 0, 0, 504, 360)
        pdf.showPage()
    pdf.save()
    print(f"Created front.png, back.png, two_pages.pdf in {folder}")


if __name__ == "__main__":
    make_demo(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("demo"))

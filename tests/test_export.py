from io import BytesIO

import numpy as np
import pymupdf
import pytest
from PIL import Image

from cardprint.export import Sheet, export_sheet, render_sheet
from cardprint.geometry import MM_TO_PT, Layout, arrange


def test_pdf_has_a4_two_full_resolution_lossless_cards_and_white_background(
    tmp_path, synthetic_card
):
    back = synthetic_card.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    positions = arrange({"front": (85.6, 53.98), "back": (85.6, 53.98)}, Layout())
    sheet = Sheet({"front": synthetic_card, "back": back}, positions)
    target = tmp_path / "result.pdf"
    export_sheet(sheet, target, "pdf")
    with pymupdf.open(target) as doc:
        assert len(doc) == 1
        page = doc[0]
        assert page.rect.width == pytest.approx(210 * MM_TO_PT, abs=0.001)
        assert page.rect.height == pytest.approx(297 * MM_TO_PT, abs=0.001)
        images = page.get_images(full=True)
        assert len(images) == 2
        for xref, *_ in images:
            image = doc.extract_image(xref)
            assert image["width"] == 856 and image["height"] == 540
            assert image["ext"] != "jpeg"
            with Image.open(BytesIO(image["image"])) as decoded:
                pixels = np.array(decoded)
                assert any(
                    np.array_equal(pixels, np.array(card)) for card in (synthetic_card, back)
                )
            rect = page.get_image_rects(xref)[0]
            assert rect.width / MM_TO_PT == pytest.approx(85.6, abs=0.001)
            assert rect.height / MM_TO_PT == pytest.approx(53.98, abs=0.001)
            assert any(abs(rect.y0 / MM_TO_PT - p.y_mm) < 0.001 for p in positions)
        pix = page.get_pixmap(dpi=120, colorspace=pymupdf.csRGB, alpha=False)
        array = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
        mask = np.ones(array.shape[:2], dtype=bool)
        for p in positions:
            x0 = int(p.x_mm * 120 / 25.4) - 2
            y0 = int(p.y_mm * 120 / 25.4) - 2
            x1 = int((p.x_mm + p.width_mm) * 120 / 25.4) + 3
            y1 = int((p.y_mm + p.height_mm) * 120 / 25.4) + 3
            mask[y0:y1, x0:x1] = False
        assert np.all(array[mask] == 255)
        # Both cards occupy the same coordinates as the preview (sample central colour).
        preview = np.array(render_sheet(sheet))
        for p in positions:
            x, y = round((p.x_mm + 10) * 120 / 25.4), round((p.y_mm + 46) * 120 / 25.4)
            np.testing.assert_allclose(array[y, x], preview[y, x], atol=2)


def test_png_300dpi_and_collision_protection(tmp_path):
    image = Image.new("RGB", (856, 540), (40, 80, 120))
    positions = arrange({"front": (85.6, 53.98)}, Layout())
    sheet = Sheet({"front": image}, positions)
    target = tmp_path / "sheet.png"
    export_sheet(sheet, target, "png")
    before = target.read_bytes()
    with Image.open(target) as result:
        assert result.size == (2480, 3508)
        assert result.info["dpi"] == pytest.approx((300, 300), abs=0.01)
        assert result.getpixel((0, 0)) == (255, 255, 255)
    with pytest.raises(FileExistsError):
        export_sheet(sheet, target, "png")
    assert target.read_bytes() == before
    export_sheet(sheet, target, "png", overwrite=True)
    assert not list(tmp_path.glob(".cardprint-*"))


def test_failed_export_removes_staging_file(tmp_path):
    with pytest.raises(ValueError):
        export_sheet(Sheet({}, []), tmp_path / "wrong.dat", "unknown")
    assert list(tmp_path.iterdir()) == []


def test_export_on_filesystem_without_hardlinks(tmp_path, monkeypatch):
    import errno

    import cardprint.export as exporting

    image = Image.new("RGB", (100, 60), "blue")
    sheet = Sheet({"front": image}, arrange({"front": (85.6, 53.98)}, Layout()))
    target = tmp_path / "portable.pdf"

    def unsupported(*args):
        raise OSError(errno.EOPNOTSUPP, "Hard links unavailable")

    monkeypatch.setattr(exporting.os, "link", unsupported)
    export_sheet(sheet, target, "pdf")
    with pymupdf.open(target) as doc:
        assert len(doc) == 1
    before = target.read_bytes()
    with pytest.raises(FileExistsError):
        export_sheet(sheet, target, "pdf")
    assert target.read_bytes() == before
    assert not list(tmp_path.glob(".cardprint-*"))

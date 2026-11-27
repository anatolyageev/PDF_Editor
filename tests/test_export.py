import errno
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import numpy as np
import pymupdf
import pytest
from PIL import Image

from cardprint.export import (
    OriginalGuard,
    OriginalOverwriteError,
    Sheet,
    SourceChangedError,
    atomic_write_file,
    export_sheet,
    render_sheet,
)
from cardprint.fonts import layout_text_annotation
from cardprint.geometry import MM_TO_PT, Layout, TextAnnotation, arrange
from cardprint.imaging import discover


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


def test_atomic_write_file_protects_originals_symlinks_hardlinks_and_removed_paths(tmp_path):
    orig = tmp_path / "original.png"
    orig.write_bytes(b"original-bytes")
    guard = OriginalGuard()
    guard.register(orig)

    # Direct overwrite attempt fails before staging.
    with pytest.raises(OriginalOverwriteError):
        atomic_write_file(
            orig, lambda p: p.write_bytes(b"new"), overwrite=True, original_guard=guard
        )
    assert orig.read_bytes() == b"original-bytes"
    assert not list(tmp_path.glob(".cardprint-*"))

    # Hard-link alias attempt fails via device/inode identity.
    hardlink = tmp_path / "hardlink.png"
    try:
        hardlink.hardlink_to(orig)
    except OSError:
        pass
    else:
        with pytest.raises(OriginalOverwriteError):
            atomic_write_file(
                hardlink, lambda p: p.write_bytes(b"new"), overwrite=True, original_guard=guard
            )
        assert orig.read_bytes() == b"original-bytes"

    # Symlink alias attempt fails.
    symlink = tmp_path / "symlink.png"
    try:
        symlink.symlink_to(orig)
    except OSError:
        pass
    else:
        with pytest.raises(OriginalOverwriteError):
            atomic_write_file(
                symlink, lambda p: p.write_bytes(b"new"), overwrite=True, original_guard=guard
            )
        assert orig.read_bytes() == b"original-bytes"

    # Even after removing the original file, its path remains protected.
    orig.unlink()
    with pytest.raises(OriginalOverwriteError):
        atomic_write_file(
            orig, lambda p: p.write_bytes(b"recreated"), overwrite=True, original_guard=guard
        )
    assert not orig.exists()
    assert not list(tmp_path.glob(".cardprint-*"))


def test_atomic_write_file_late_collision_and_writer_or_validator_failure(tmp_path):
    target = tmp_path / "output.dat"
    guard = OriginalGuard()

    # Late target collision during writer execution raises FileExistsError and preserves late file.
    def collide_writer(staging):
        staging.write_bytes(b"staged")
        target.write_bytes(b"late-arrival")

    with pytest.raises(FileExistsError):
        atomic_write_file(target, collide_writer, overwrite=False, original_guard=guard)
    assert target.read_bytes() == b"late-arrival"
    assert not list(tmp_path.glob(".cardprint-*"))

    # Writer failure preserves existing non-original target and cleans up staging.
    def fail_writer(staging):
        staging.write_bytes(b"partial")
        raise RuntimeError("write failed")

    with pytest.raises(RuntimeError, match="write failed"):
        atomic_write_file(target, fail_writer, overwrite=True, original_guard=guard)
    assert target.read_bytes() == b"late-arrival"
    assert not list(tmp_path.glob(".cardprint-*"))

    # Validator failure preserves existing non-original target and cleans up staging.
    def fail_validator(_staging):
        raise ValueError("validation failed")

    with pytest.raises(ValueError, match="validation failed"):
        atomic_write_file(
            target,
            lambda p: p.write_bytes(b"bad-output"),
            overwrite=True,
            validator_fn=fail_validator,
            original_guard=guard,
        )
    assert target.read_bytes() == b"late-arrival"
    assert not list(tmp_path.glob(".cardprint-*"))


def test_atomic_write_file_fallback_copy_failure_cleans_partial_target(tmp_path, monkeypatch):
    import cardprint.export as exporting

    target = tmp_path / "fallback.dat"
    guard = OriginalGuard()

    def no_hardlink(*_args):
        raise OSError(errno.EOPNOTSUPP, "Hard links unavailable")

    def failing_copy(_src, dst):
        dst.write(b"partial-bytes")
        raise OSError("disk full")

    monkeypatch.setattr(exporting.os, "link", no_hardlink)
    monkeypatch.setattr(exporting.shutil, "copyfileobj", failing_copy)

    with pytest.raises(OSError, match="disk full"):
        atomic_write_file(target, lambda p: p.write_bytes(b"full"), original_guard=guard)
    assert not target.exists()
    assert not list(tmp_path.glob(".cardprint-*"))


def test_atomic_write_file_serializes_concurrent_writes_to_same_destination(tmp_path):
    target = tmp_path / "shared.dat"
    guard = OriginalGuard()
    active = 0
    max_active = 0
    counter_lock = threading.Lock()
    entered_first = threading.Event()

    def write_payload(label: bytes):
        def writer(staging):
            nonlocal active, max_active
            with counter_lock:
                active += 1
                max_active = max(max_active, active)
            entered_first.set()
            # Yield briefly without busy-waiting to allow second thread to contend on lock.
            threading.Event().wait(0.05)
            staging.write_bytes(label)
            with counter_lock:
                active -= 1

        return writer

    errors = []

    def worker(label: bytes):
        try:
            atomic_write_file(target, write_payload(label), overwrite=True, original_guard=guard)
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=(b"first",))
    t2 = threading.Thread(target=worker, args=(b"second",))
    t1.start()
    assert entered_first.wait(timeout=5)
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert not errors
    assert max_active == 1
    assert target.read_bytes() in (b"first", b"second")


def test_export_sheet_non_ui_protects_discovered_original_and_validates_png_and_changed_source(
    tmp_path, synthetic_card, monkeypatch
):
    orig = tmp_path / "orig.png"
    synthetic_card.save(orig)
    sources = discover(orig)
    fp = sources[0].fingerprint
    assert fp is not None

    sheet = Sheet({"front": synthetic_card}, arrange({"front": (85.6, 53.98)}, Layout()))

    # Headless export_sheet refuses to overwrite discovered original via default guard.
    with pytest.raises(OriginalOverwriteError):
        export_sheet(sheet, orig, "png", overwrite=True)

    # Corrupt staged PNG fails staged validation without publishing.
    bad_png = tmp_path / "bad.png"
    monkeypatch.setattr(
        "cardprint.export.render_sheet", lambda _s, dpi=300: Image.new("RGB", (50, 50))
    )
    with pytest.raises(ValueError, match="300 DPI"):
        export_sheet(sheet, bad_png, "png")
    assert not bad_png.exists()
    assert not list(tmp_path.glob(".cardprint-*"))

    # Modifying source on disk blocks export until reloaded.
    orig.write_bytes(orig.read_bytes() + b"\x00")
    out_pdf = tmp_path / "out.pdf"
    with pytest.raises(SourceChangedError, match="изменился"):
        export_sheet(sheet, out_pdf, "pdf", source_fingerprints=(fp,))
    assert not out_pdf.exists()
    # Keep unused shutil import used in module-level verification cleanly.
    assert shutil is not None


@pytest.mark.parametrize("enabled", [False, True])
def test_crop_marks_shared_preview_png_vector_pdf_and_snapshot(tmp_path, enabled):
    from cardprint.export import snapshot_sheet
    from cardprint.geometry import crop_marks

    layout = Layout(crop_marks=enabled)
    placements = arrange({"front": (85.6, 53.98), "back": (75, 45)}, layout)
    images = {
        "front": Image.new("RGB", (856, 540), (60, 120, 180)),
        "back": Image.new("RGB", (750, 450), (170, 80, 40)),
    }
    sheet = Sheet(images, placements, crop_marks=enabled)
    snapshot = snapshot_sheet(sheet)
    assert snapshot.crop_marks is enabled
    frozen = Sheet(snapshot.images, list(snapshot.placements), snapshot.crop_marks)
    png = tmp_path / "marks.png"
    pdf = tmp_path / "marks.pdf"
    export_sheet(frozen, png, "png")
    export_sheet(frozen, pdf, "pdf")
    marks = crop_marks(placements).segments
    assert len(marks) == 16
    with Image.open(png) as exported:
        for dpi, raster in ((120, render_sheet(frozen)), (300, exported)):
            for mark in marks:
                x = round((mark.start[0] + mark.end[0]) / 2 * dpi / 25.4)
                y = round((mark.start[1] + mark.end[1]) / 2 * dpi / 25.4)
                assert raster.getpixel((x, y)) == ((0, 0, 0) if enabled else (255, 255, 255))
            # Card pixels must be identical regardless of crop-mark setting.
            plain = render_sheet(Sheet(images, placements), dpi)
            for p in placements:
                rect = tuple(
                    round(v * dpi / 25.4)
                    for v in (p.x_mm, p.y_mm, p.x_mm + p.width_mm, p.y_mm + p.height_mm)
                )
                assert raster.crop(rect).tobytes() == plain.crop(rect).tobytes()
    with pymupdf.open(pdf) as doc:
        lines = [drawing for drawing in doc[0].get_drawings() if drawing["items"][0][0] == "l"]
        assert len(lines) == (len(marks) if enabled else 0)
        for line, mark in zip(lines, marks):
            assert line["width"] / MM_TO_PT == pytest.approx(mark.width_mm, abs=1e-5)
            _, start, end = line["items"][0]
            assert tuple(v / MM_TO_PT for v in start) == pytest.approx(mark.start, abs=1e-4)
            assert tuple(v / MM_TO_PT for v in end) == pytest.approx(mark.end, abs=1e-4)


def _ink_bbox_mm(array: np.ndarray, bounds_mm: tuple[float, float, float, float], dpi: float):
    scale = dpi / 25.4
    pad = round(2.5 * scale)
    x0 = max(0, round(bounds_mm[0] * scale) - pad)
    y0 = max(0, round(bounds_mm[1] * scale) - pad)
    x1 = min(array.shape[1], round(bounds_mm[2] * scale) + pad)
    y1 = min(array.shape[0], round(bounds_mm[3] * scale) + pad)
    region = array[y0:y1, x0:x1]
    mask = np.any(region < 252, axis=2)
    assert mask.any()
    ys, xs = np.where(mask)
    return (
        (x0 + xs.min()) / scale,
        (y0 + ys.min()) / scale,
        (x0 + xs.max()) / scale,
        (y0 + ys.max()) / scale,
    )


def test_text_annotations_pdf_vector_cyrillic_and_png_rendering_match_within_0_2mm(
    tmp_path, synthetic_card
):
    annotations = (
        TextAnnotation(
            id="caption",
            text="Копия верна\nІіЇїЄєҐґ · № 42",
            x_mm=20.0,
            y_mm=25.0,
            font_family="sans",
            font_size_pt=14.0,
            color_hex="#111827",
            align="left",
        ),
        TextAnnotation(
            id="serif-hdr",
            text="Для предъявления",
            x_mm=105.0,
            y_mm=24.0,
            font_family="serif",
            font_size_pt=15.0,
            color_hex="#1d4ed8",
            align="center",
        ),
        TextAnnotation(
            id="mono-date",
            text="Дата: 27.11.2026",
            x_mm=190.0,
            y_mm=275.0,
            font_family="mono",
            font_size_pt=12.0,
            color_hex="#334155",
            align="right",
        ),
        TextAnnotation(
            id="wm",
            text="КОПИЯ ВЕРНА ✓\nТолько для работы",
            x_mm=105.0,
            y_mm=225.0,
            font_family="sans-bold",
            font_size_pt=22.0,
            color_hex="#111827",
            angle_deg=-30.0,
            opacity=0.25,
            align="center",
            is_watermark=True,
        ),
    )
    placements = arrange({"front": (85.6, 53.98)}, Layout())
    sheet = Sheet({"front": synthetic_card}, placements, annotations=annotations)
    pdf_path = tmp_path / "annotated.pdf"
    png_path = tmp_path / "annotated.png"
    export_sheet(sheet, pdf_path, "pdf")
    export_sheet(sheet, png_path, "png")

    with Image.open(png_path) as exported_png:
        png_arr = np.array(exported_png)
        assert exported_png.getpixel((0, 0)) == (255, 255, 255)

    with pymupdf.open(pdf_path) as doc:
        page = doc[0]
        extracted = page.get_text()
        assert "Копия верна" in extracted
        assert "ІіЇїЄєҐґ · № 42" in extracted
        assert "Для предъявления" in extracted
        assert "Дата: 27.11.2026" in extracted
        assert "КОПИЯ ВЕРНА ✓" in extracted
        assert "Только для работы" in extracted
        assert len(page.get_fonts()) >= 4

        pix = page.get_pixmap(dpi=300, colorspace=pymupdf.csRGB, alpha=False)
        pdf_arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)

    for ann in annotations:
        bounds = layout_text_annotation(ann).bounds_mm
        pdf_box = _ink_bbox_mm(pdf_arr, bounds, 300.0)
        png_box = _ink_bbox_mm(png_arr, bounds, 300.0)
        for edge_pdf, edge_png in zip(pdf_box, png_box):
            assert abs(edge_pdf - edge_png) <= 0.2

    # Semi-transparent watermark must blend cleanly (~75% white + 25% dark), not solid black.
    wm_bounds = layout_text_annotation(annotations[-1]).bounds_mm
    scale = 300.0 / 25.4
    wm_crop = png_arr[
        round(wm_bounds[1] * scale) : round(wm_bounds[3] * scale),
        round(wm_bounds[0] * scale) : round(wm_bounds[2] * scale),
    ]
    assert int(wm_crop.min()) > 150


def test_text_annotation_stacking_order_and_invalid_export_blocking(tmp_path):
    blue_card = Image.new("RGB", (856, 540), (30, 90, 180))
    placements = arrange({"front": (85.6, 53.98)}, Layout())
    card_center_x = placements[0].x_mm + placements[0].width_mm / 2
    card_center_y = placements[0].y_mm + placements[0].height_mm / 2

    # Even with is_watermark=True, an annotation paints above placed cards in tuple order.
    over_card = TextAnnotation(
        id="over-card",
        text="ОБРАЗЕЦ",
        x_mm=card_center_x,
        y_mm=card_center_y,
        font_family="sans-bold",
        font_size_pt=20.0,
        color_hex="#ffffff",
        opacity=1.0,
        align="center",
        is_watermark=True,
    )
    sheet = Sheet({"front": blue_card}, placements, annotations=(over_card,))
    rendered = np.array(render_sheet(sheet, dpi=150))
    scale = 150.0 / 25.4
    sample = rendered[
        round((card_center_y - 6) * scale) : round((card_center_y + 2) * scale),
        round((card_center_x - 20) * scale) : round((card_center_x + 20) * scale),
    ]
    assert np.all(sample.max(axis=2) >= 245) or int(sample.max()) == 255

    # Out-of-bounds or missing-glyph annotations block PDF and PNG exports cleanly without staging files.
    out_of_bounds = TextAnnotation(id="oob", text="За краем листа", x_mm=195.0, y_mm=30.0)
    bad_glyph = TextAnnotation(
        id="bad-glyph", text="Текст ✓", x_mm=20.0, y_mm=30.0, font_family="serif"
    )
    for bad_ann in (out_of_bounds, bad_glyph):
        for kind in ("pdf", "png"):
            target = tmp_path / f"{bad_ann.id}.{kind}"
            with pytest.raises(ValueError):
                export_sheet(
                    Sheet({"front": blue_card}, placements, annotations=(bad_ann,)),
                    target,
                    kind,
                )
            assert not target.exists()
    assert not list(tmp_path.glob(".cardprint-*"))


def test_concurrent_text_exports_thread_safe(tmp_path, synthetic_card):
    placements = arrange({"front": (85.6, 53.98)}, Layout())

    def run_export(index: int) -> Path:
        family = ("sans", "sans-bold", "serif", "mono")[index % 4]
        kind = "pdf" if index % 2 == 0 else "png"
        ann = TextAnnotation(
            id=f"c-{index}",
            text=f"Поток {index} · Кириллица ЄєЇї",
            x_mm=25.0,
            y_mm=30.0,
            font_family=family,
            font_size_pt=13.0,
        )
        sheet = Sheet({"front": synthetic_card}, placements, annotations=(ann,))
        out = tmp_path / f"export_{index}.{kind}"
        return export_sheet(sheet, out, kind)

    with ThreadPoolExecutor(max_workers=4) as pool:
        outputs = list(pool.map(run_export, range(8)))

    assert len(outputs) == 8
    assert all(path.exists() for path in outputs)

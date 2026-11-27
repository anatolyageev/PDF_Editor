import hashlib

import cv2
import numpy as np
import pymupdf
import pytest
from PIL import Image

from cardprint.export import OriginalGuard, SourceChangedError
from cardprint.imaging import (
    detect_quad,
    discover,
    full_quad,
    load_source,
    rectify,
    rotate_quad,
    rotated,
    round_corners,
)


def test_identity_selection_preserves_every_pixel(original):
    result = rectify(original, 0, full_quad(856, 540))
    np.testing.assert_array_equal(np.array(result), original)


def test_perspective_corners_and_content():
    image = np.full((400, 600, 3), 240, np.uint8)
    points = ((80, 50), (500, 75), (530, 330), (55, 350))
    cv2.fillConvexPoly(image, np.array(points), (60, 110, 160))
    cv2.circle(image, (290, 200), 30, (200, 30, 40), -1)
    output = np.array(rectify(image, 0, points))
    assert output.shape[1] > 420 and output.shape[0] >= 255
    np.testing.assert_allclose(output[20, 20], (60, 110, 160), atol=1)
    assert ((output[:, :, 0] == 200) & (output[:, :, 1] == 30)).sum() > 1000
    # No surrounding grey paper survives well inside any output edge.
    for point in [(10, 10), (10, -11), (-11, 10), (-11, -11)]:
        np.testing.assert_allclose(output[point], (60, 110, 160), atol=1)


def test_contour_detection_and_empty_fallback():
    image = np.full((600, 900, 3), 35, np.uint8)
    corners = np.array(((140, 140), (760, 110), (790, 470), (100, 450)))
    cv2.fillConvexPoly(image, corners, (220, 220, 220))
    detected = detect_quad(image)
    assert detected is not None
    np.testing.assert_allclose(detected, corners, atol=5)
    assert detect_quad(np.full((300, 500, 3), 255, np.uint8)) is None


def test_rounding_only_removes_outside_pixels(synthetic_card):
    result = np.array(round_corners(synthetic_card, 5, (85.6, 53.98)))
    original = np.array(synthetic_card)
    assert tuple(result[0, 0]) == (255, 255, 255)
    np.testing.assert_array_equal(result[60:-60, :], original[60:-60, :])
    np.testing.assert_array_equal(result[:, 60:-60], original[:, 60:-60])


def test_original_file_and_array_are_unchanged(tmp_path, synthetic_card):
    path = tmp_path / "artificial.png"
    synthetic_card.save(path)
    before = hashlib.sha256(path.read_bytes()).digest()
    source = discover(path)[0]
    pixels = load_source(source)
    original = pixels.copy()
    for turns in range(4):
        image = rotated(pixels, turns)
        output = rectify(pixels, turns, full_quad(image.shape[1], image.shape[0]))
        round_corners(output, 4, (85.6, 53.98))
    assert not pixels.flags.writeable
    np.testing.assert_array_equal(pixels, original)
    assert hashlib.sha256(path.read_bytes()).digest() == before


def test_rotation_is_reversible_and_selection_follows(original):
    points = full_quad(856, 540)
    w, h = 856, 540
    for _ in range(4):
        points = rotate_quad(points, h)
        w, h = h, w
    assert points == full_quad(856, 540)
    np.testing.assert_array_equal(rotated(original, 4), original)
    output = rectify(original, 1, rotate_quad(full_quad(856, 540), 540))
    np.testing.assert_array_equal(np.array(output), np.rot90(original, -1))


def test_exif_orientation_and_transparency(tmp_path):
    image = Image.new("RGB", (80, 40), "red")
    exif = Image.Exif()
    exif[274] = 6
    path = tmp_path / "orientation.jpg"
    image.save(path, exif=exif)
    assert load_source(discover(path)[0]).shape[:2] == (80, 40)
    path = tmp_path / "alpha.png"
    Image.new("RGBA", (40, 40), (0, 0, 0, 0)).save(path)
    assert np.all(load_source(discover(path)[0]) == 255)


def test_pdf_pages_and_clear_import_errors(tmp_path):
    pdf = tmp_path / "two_pages.pdf"
    with pymupdf.open() as doc:
        doc.new_page(width=200, height=100)
        doc.new_page(width=100, height=200)
        doc.save(pdf)
    pages = discover(pdf)
    assert [p.page for p in pages] == [0, 1]
    assert load_source(pages[0]).shape[:2] == (417, 834)
    locked = tmp_path / "locked.pdf"
    with pymupdf.open(pdf) as doc:
        doc.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="test")
    with pytest.raises(ValueError, match="паролем"):
        discover(locked)
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    with pytest.raises(ValueError, match="повреждён"):
        discover(bad)
    bad_pdf = tmp_path / "bad.pdf"
    bad_pdf.write_bytes(b"broken pdf")
    with pytest.raises(ValueError, match="повреждён"):
        discover(bad_pdf)


def test_load_source_blocks_changed_source_until_explicit_reload(tmp_path, synthetic_card):
    card_path = tmp_path / "source.png"
    synthetic_card.save(card_path)
    guard = OriginalGuard()
    src = discover(card_path, original_guard=guard)[0]
    assert src.fingerprint is not None

    # Mutate file on disk -> loading stale Source must fail until explicitly reloaded.
    updated = Image.new("RGB", (200, 120), (10, 120, 40))
    updated.save(card_path)
    with pytest.raises(SourceChangedError, match="изменился"):
        load_source(src, original_guard=guard)

    reloaded = discover(card_path, original_guard=guard)[0]
    arr = load_source(reloaded, original_guard=guard)
    assert arr.shape[:2] == (120, 200)
    assert guard.is_protected(card_path)

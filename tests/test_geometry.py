import math

import pytest

from cardprint.geometry import CardSize, Layout, ViewTransform, arrange, validate_quad


@pytest.mark.parametrize("scale", [0.031, 0.4, 1, 2.75, 8])
def test_editor_transform_roundtrip_after_zoom_and_pan(scale):
    transform = ViewTransform(scale, -128.25, 349.7)
    point = (2345.123, 1567.89)
    view = transform.to_view(point)
    assert transform.to_source(view) == pytest.approx(point, abs=1e-10)


@pytest.mark.parametrize(
    "points",
    [
        ((0, 0), (90, 90), (90, 0), (0, 90)),  # crossing
        ((0, 0), (90, 0), (10, 10), (0, 90)),  # concave
        ((0, 0), (0, 0), (90, 90), (0, 90)),
        ((-1, 0), (90, 0), (90, 90), (0, 90)),
        ((0, 0), (0, 90), (90, 90), (90, 0)),  # would mirror the document
        ((0, 0), (math.nan, 0), (90, 90), (0, 90)),
    ],
)
def test_invalid_quadrilateral_rejected(points):
    with pytest.raises(ValueError):
        validate_quad(points, 100, 100)


def test_layout_centre_gap_and_physical_sizes():
    positions = arrange({"back": (85.6, 53.98), "front": (85.6, 53.98)}, Layout())
    a, b = positions
    assert a.side == "front" and b.side == "back"
    assert a.x_mm == b.x_mm == pytest.approx((210 - 85.6) / 2)
    assert b.y_mm - a.y_mm - a.height_mm == pytest.approx(15)
    assert a.y_mm == pytest.approx(297 - b.y_mm - b.height_mm)


def test_horizontal_layout_and_offsets():
    a, b = arrange({"front": (60, 50), "back": (70, 40)}, Layout("horizontal", 10, 5, 3, -7))
    assert b.x_mm - a.x_mm - a.width_mm == pytest.approx(10)
    assert (a.x_mm + b.x_mm + b.width_mm) / 2 == pytest.approx(108)
    assert a.y_mm + a.height_mm / 2 == pytest.approx(141.5)
    assert b.y_mm + b.height_mm / 2 == pytest.approx(141.5)


@pytest.mark.parametrize(
    "layout", [Layout(offset_x_mm=100), Layout(gap_mm=200), Layout(margin_mm=80)]
)
def test_out_of_bounds_are_not_silently_clipped(layout):
    with pytest.raises(ValueError, match="A4"):
        arrange({"front": (85.6, 53.98), "back": (85.6, 53.98)}, layout)


def test_size_modes():
    assert CardSize("aspect", 90).resolve((900, 600)) == (90, 60)
    assert CardSize("custom", 93, 42).resolve((900, 600)) == (93, 42)
    assert CardSize().resolve((900, 600)) == (85.6, 53.98)

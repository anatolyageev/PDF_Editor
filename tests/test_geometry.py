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


@pytest.mark.parametrize(
    "sizes,layout,omitted",
    [
        ({"front": (85.6, 53.98), "back": (85.6, 53.98)}, Layout(), False),
        ({"front": (85.6, 53.98), "back": (85.6, 53.98)}, Layout(gap_mm=0), True),
        ({"front": (70, 60), "back": (80, 40)}, Layout("horizontal", 1, 10), True),
        ({"front": (110, 60), "back": (75, 50)}, Layout(gap_mm=1), True),
        ({"front": (204, 100)}, Layout(margin_mm=3), True),
        ({"front": (210, 297)}, Layout(margin_mm=0), True),
    ],
)
def test_crop_marks_never_cross_cards_or_page_edges(sizes, layout, omitted):
    from cardprint.geometry import crop_marks

    placements = arrange(sizes, layout)
    before = tuple(placements)
    result = crop_marks(placements)
    assert bool(result.omitted) is omitted
    assert bool(result.warning) is omitted
    assert tuple(placements) == before
    for mark in result.segments:
        assert mark.width_mm == 0.15
        assert math.dist(mark.start, mark.end) == pytest.approx(3)
        half = mark.width_mm / 2
        x0, x1 = sorted((mark.start[0], mark.end[0]))
        y0, y1 = sorted((mark.start[1], mark.end[1]))
        assert x0 - half >= 0 and x1 + half <= 210
        assert y0 - half >= 0 and y1 + half <= 297
        for p in placements:
            assert (
                x1 + half < p.x_mm
                or x0 - half > p.x_mm + p.width_mm
                or y1 + half < p.y_mm
                or y0 - half > p.y_mm + p.height_mm
            )


@pytest.mark.parametrize("template", ["vertical", "horizontal"])
def test_group_position_clamps_to_arrange_and_snaps_in_control_precision(template):
    from cardprint.geometry import position_group

    sizes = {"front": (70.123, 51.234), "back": (65.678, 40.567)}
    initial = Layout(template, crop_marks=True)
    for x, y in ((10000, -10000), (-10000, 10000), (12.3456, 8.1234)):
        moved = position_group(sizes, initial, x, y)
        assert moved.crop_marks
        assert moved.offset_x_mm == round(moved.offset_x_mm, 2)
        assert moved.offset_y_mm == round(moved.offset_y_mm, 2)
        assert len(arrange(sizes, moved)) == 2
    snapped = position_group(sizes, initial, 0.79, -0.79)
    assert snapped.offset_x_mm == snapped.offset_y_mm == 0
    assert position_group(sizes, initial, 0.81, -0.81).offset_x_mm == 0.81
    with pytest.raises(ValueError):
        position_group(sizes, initial, math.nan, 0)

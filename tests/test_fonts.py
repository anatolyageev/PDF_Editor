import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from cardprint.fonts import (
    FONT_FAMILIES,
    get_font_bytes,
    get_font_license_text,
    layout_text_annotation,
    register_reportlab_font,
    unsupported_glyphs,
    validate_text_glyphs,
)
from cardprint.geometry import TextAnnotation


def test_fonts_module_imports_without_pyside6_or_pymupdf():
    code = (
        "import sys, cardprint.fonts; "
        "assert 'PySide6' not in sys.modules, list(sys.modules); "
        "assert 'pymupdf' not in sys.modules, list(sys.modules)"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


def test_bundled_fonts_bytes_licenses_and_cyrillic_ukrainian_stamp_glyphs():
    license_text = get_font_license_text()
    assert "Bitstream" in license_text and "DejaVu" in license_text

    cyrillic_and_ukrainian = (
        "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
        "ІіЇїЄєҐґ · № — «Копия верна»\nДата: 27.11.2026"
    )
    for family in FONT_FAMILIES:
        font_bytes = get_font_bytes(family)
        assert len(font_bytes) > 100_000
        validate_text_glyphs(cyrillic_and_ukrainian, family)
        assert register_reportlab_font(family)

    for stamp_family in ("sans", "sans-bold", "mono"):
        validate_text_glyphs("✓ ✕ × •", stamp_family)

    # Serif bundled face omits geometric checkmark ✓; must fail clearly instead of silent box.
    assert unsupported_glyphs("Копия ✓", "serif") == ("✓",)
    with pytest.raises(ValueError, match="С засечками"):
        validate_text_glyphs("Копия ✓", "serif")

    # CJK / non-covered emoji characters not in any bundled font must report exact missing chars.
    assert unsupported_glyphs("Текст 漢字 🚀 漢字", "sans") == ("漢", "字", "🚀")
    with pytest.raises(ValueError, match="U\\+6F22"):
        validate_text_glyphs("Текст 漢字", "sans")


def test_text_annotation_validation_and_bounds_rejection():
    with pytest.raises(ValueError):
        TextAnnotation(id="", text="Копия", x_mm=20, y_mm=20)
    with pytest.raises(ValueError):
        TextAnnotation(id="a1", text="   \n ", x_mm=20, y_mm=20)
    with pytest.raises(ValueError):
        TextAnnotation(id="a1", text="Копия", x_mm=float("nan"), y_mm=20)
    with pytest.raises(ValueError):
        TextAnnotation(id="a1", text="Копия", x_mm=20, y_mm=20, font_family="comic")
    with pytest.raises(ValueError):
        TextAnnotation(id="a1", text="Копия", x_mm=20, y_mm=20, align="justify")
    with pytest.raises(ValueError):
        TextAnnotation(id="a1", text="Копия", x_mm=20, y_mm=20, font_size_pt=5.0)
    with pytest.raises(ValueError):
        TextAnnotation(id="a1", text="Копия", x_mm=20, y_mm=20, angle_deg=181.0)
    with pytest.raises(ValueError):
        TextAnnotation(id="a1", text="Копия", x_mm=20, y_mm=20, opacity=0.01)
    with pytest.raises(ValueError):
        TextAnnotation(id="a1", text="Копия", x_mm=20, y_mm=20, color_hex="red")

    # Valid annotation inside A4 passes rotated layout validation.
    valid = TextAnnotation(
        id="ok",
        text="Копия верна\nТолько по требованию",
        x_mm=105.0,
        y_mm=140.0,
        font_family="sans-bold",
        font_size_pt=18.0,
        angle_deg=-30.0,
        opacity=0.25,
        align="center",
        is_watermark=True,
    )
    layout = layout_text_annotation(valid)
    min_x, min_y, max_x, max_y = layout.bounds_mm
    assert 0.0 <= min_x < max_x <= 210.0
    assert 0.0 <= min_y < max_y <= 297.0
    assert layout.lines[1].baseline_y_mm > layout.lines[0].baseline_y_mm

    # Annotation extending past right/bottom/top/left sheet boundaries must be rejected without clipping.
    out_right = TextAnnotation(
        id="edge-r", text="Очень длинная строка текста", x_mm=195.0, y_mm=40.0
    )
    with pytest.raises(ValueError, match="выходит за пределы листа A4"):
        layout_text_annotation(out_right)

    out_top = TextAnnotation(id="edge-t", text="Сверху", x_mm=20.0, y_mm=2.0, font_size_pt=24.0)
    with pytest.raises(ValueError, match="выходит за пределы листа A4"):
        layout_text_annotation(out_top)

    out_rotated = TextAnnotation(
        id="edge-rot", text="Повёрнутая строка", x_mm=20.0, y_mm=15.0, angle_deg=-90.0
    )
    with pytest.raises(ValueError, match="выходит за пределы листа A4"):
        layout_text_annotation(out_rotated)


def test_font_resources_load_from_installed_wheel_outside_checkout(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    dist_dir = tmp_path / "dist"
    build = subprocess.run(
        ["uv", "build", "--wheel", "--offline", "--out-dir", str(dist_dir)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    wheels = list(dist_dir.glob("cardprint-*.whl"))
    assert len(wheels) == 1

    wheel_site = tmp_path / "wheel_site"
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
        assert "cardprint/assets/fonts/DejaVuSans.ttf" in names
        assert "cardprint/assets/fonts/DejaVuSans-Bold.ttf" in names
        assert "cardprint/assets/fonts/DejaVuSerif.ttf" in names
        assert "cardprint/assets/fonts/DejaVuSansMono.ttf" in names
        assert "cardprint/assets/fonts/LICENSE-DejaVu.txt" in names
        archive.extractall(wheel_site)

    check_code = (
        "import sys; from pathlib import Path; "
        "src_root = str((Path(sys.argv[1]) / 'src').resolve()); "
        "sys.path = [p for p in sys.path if p and str(Path(p).resolve()) != src_root]; "
        "sys.path.insert(0, str(Path(sys.argv[2]).resolve())); "
        "import cardprint.fonts as fonts; "
        "from cardprint.geometry import TextAnnotation; "
        "assert str(Path(sys.argv[2]).resolve()) in str(Path(fonts.__file__).resolve()); "
        "assert 'Bitstream' in fonts.get_font_license_text(); "
        "[fonts.get_font_bytes(fam) for fam in fonts.FONT_FAMILIES]; "
        "lay = fonts.layout_text_annotation(TextAnnotation(id='w1', text='ІіЇїЄєҐґ Копия ✓', x_mm=30, y_mm=30)); "
        "assert lay.bounds_mm[2] <= 210.0"
    )
    isolated = tmp_path / "run_cwd"
    isolated.mkdir()
    proc = subprocess.run(
        [sys.executable, "-c", check_code, str(repo_root), str(wheel_site)],
        cwd=isolated,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr

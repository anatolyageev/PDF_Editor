from dataclasses import dataclass

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

SETTINGS_ORG = "CardPrint Local"
SETTINGS_APP = "Карточки на A4"
THEME_SETTINGS_KEY = "ui/theme"
THEME_LIGHT = "light"
THEME_DARK = "dark"


@dataclass(frozen=True)
class ThemePalette:
    """Semantic UI color tokens shared by QSS styling, icons, and canvas surfaces."""

    name: str
    window_bg: str
    panel_bg: str
    card_bg: str
    canvas_bg: str
    input_bg: str
    subtle_bg: str
    border: str
    border_focus: str
    divider: str
    text_primary: str
    text_secondary: str
    text_muted: str
    accent_text: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_subtle: str
    danger: str
    danger_hover: str
    danger_subtle: str
    warning: str
    warning_subtle: str
    success: str
    success_subtle: str


LIGHT_PALETTE = ThemePalette(
    name=THEME_LIGHT,
    window_bg="#f1f5f9",
    panel_bg="#f8fafc",
    card_bg="#ffffff",
    canvas_bg="#dbe1e8",
    input_bg="#ffffff",
    subtle_bg="#e2e8f0",
    border="#cbd5e1",
    border_focus="#1d4ed8",
    divider="#e2e8f0",
    text_primary="#0f172a",
    text_secondary="#334155",
    text_muted="#475569",
    accent_text="#ffffff",
    accent="#1d4ed8",
    accent_hover="#1e40af",
    accent_pressed="#1e3a8a",
    accent_subtle="#dbeafe",
    danger="#b91c1c",
    danger_hover="#991b1b",
    danger_subtle="#fee2e2",
    warning="#9a3412",
    warning_subtle="#ffedd5",
    success="#15803d",
    success_subtle="#dcfce7",
)

DARK_PALETTE = ThemePalette(
    name=THEME_DARK,
    window_bg="#11161d",
    panel_bg="#161c25",
    card_bg="#1d2531",
    canvas_bg="#26303d",
    input_bg="#131922",
    subtle_bg="#283344",
    border="#334155",
    border_focus="#60a5fa",
    divider="#293445",
    text_primary="#f1f5f9",
    text_secondary="#cbd5e1",
    text_muted="#94a3b8",
    accent_text="#ffffff",
    accent="#2563eb",
    accent_hover="#3b82f6",
    accent_pressed="#1d4ed8",
    accent_subtle="#1e3a8a",
    danger="#f87171",
    danger_hover="#fca5a5",
    danger_subtle="#451a1a",
    warning="#fbbf24",
    warning_subtle="#422006",
    success="#4ade80",
    success_subtle="#14532d",
)

PALETTES: dict[str, ThemePalette] = {
    THEME_LIGHT: LIGHT_PALETTE,
    THEME_DARK: DARK_PALETTE,
}


def normalize_theme_name(name: str | None) -> str:
    if name is not None and str(name).strip().lower() == THEME_DARK:
        return THEME_DARK
    return THEME_LIGHT


def get_palette(name: str | None) -> ThemePalette:
    return PALETTES[normalize_theme_name(name)]


def default_settings() -> QSettings:
    return QSettings(SETTINGS_ORG, SETTINGS_APP)


def load_saved_theme(settings: QSettings | None = None) -> str:
    store = settings if settings is not None else default_settings()
    return normalize_theme_name(store.value(THEME_SETTINGS_KEY, THEME_LIGHT))


def save_theme(theme_name: str, settings: QSettings | None = None) -> str:
    normalized = normalize_theme_name(theme_name)
    store = settings if settings is not None else default_settings()
    store.setValue(THEME_SETTINGS_KEY, normalized)
    store.sync()
    return normalized


def build_qpalette(palette: ThemePalette) -> QPalette:
    qpalette = QPalette()
    qpalette.setColor(QPalette.ColorRole.Window, QColor(palette.window_bg))
    qpalette.setColor(QPalette.ColorRole.WindowText, QColor(palette.text_primary))
    qpalette.setColor(QPalette.ColorRole.Base, QColor(palette.input_bg))
    qpalette.setColor(QPalette.ColorRole.AlternateBase, QColor(palette.panel_bg))
    qpalette.setColor(QPalette.ColorRole.ToolTipBase, QColor(palette.card_bg))
    qpalette.setColor(QPalette.ColorRole.ToolTipText, QColor(palette.text_primary))
    qpalette.setColor(QPalette.ColorRole.Text, QColor(palette.text_primary))
    qpalette.setColor(QPalette.ColorRole.Button, QColor(palette.card_bg))
    qpalette.setColor(QPalette.ColorRole.ButtonText, QColor(palette.text_primary))
    qpalette.setColor(QPalette.ColorRole.Highlight, QColor(palette.accent))
    qpalette.setColor(QPalette.ColorRole.HighlightedText, QColor(palette.accent_text))
    qpalette.setColor(QPalette.ColorRole.Mid, QColor(palette.border))
    qpalette.setColor(QPalette.ColorRole.Dark, QColor(palette.divider))
    qpalette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Text,
        QColor(palette.text_muted),
    )
    qpalette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.ButtonText,
        QColor(palette.text_muted),
    )
    return qpalette


def build_stylesheet(palette: ThemePalette) -> str:
    p = palette
    return f"""
QMainWindow, QDialog {{
    background-color: {p.window_bg};
    color: {p.text_primary};
}}
QWidget {{
    color: {p.text_primary};
}}
QScrollArea {{
    background-color: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background-color: transparent;
}}
QSplitter::handle {{
    background-color: {p.divider};
    width: 3px;
    margin: 4px 2px;
    border-radius: 1px;
}}
QSplitter::handle:hover {{
    background-color: {p.border_focus};
}}
QFrame[panel="card"], QGroupBox {{
    background-color: {p.card_bg};
    border: 1px solid {p.border};
    border-radius: 10px;
    margin-top: 12px;
    padding: 12px 10px 10px 10px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 6px;
    background-color: {p.card_bg};
    color: {p.text_primary};
    font-weight: 600;
}}
QLabel {{
    background-color: transparent;
    color: {p.text_primary};
}}
QLabel[role="sectionTitle"] {{
    font-weight: 600;
    color: {p.text_primary};
}}
QLabel[status="muted"] {{
    color: {p.text_secondary};
}}
QLabel[status="warning"] {{
    color: {p.warning};
    font-weight: 600;
}}
QLabel[status="error"] {{
    color: {p.danger};
    font-weight: 600;
}}
QLabel[status="ok"] {{
    color: {p.success};
    font-weight: 600;
}}
QPushButton {{
    background-color: {p.card_bg};
    color: {p.text_primary};
    border: 1px solid {p.border};
    border-radius: 7px;
    padding: 6px 12px;
    min-height: 20px;
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {p.subtle_bg};
}}
QPushButton:pressed {{
    background-color: {p.accent_subtle};
}}
QPushButton:focus {{
    border: 2px solid {p.border_focus};
    padding: 5px 11px;
}}
QPushButton:disabled {{
    background-color: {p.panel_bg};
    color: {p.text_muted};
    border-color: {p.divider};
}}
QPushButton[variant="primary"] {{
    background-color: {p.accent};
    color: {p.accent_text};
    border: 1px solid {p.accent};
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{
    background-color: {p.accent_hover};
    border-color: {p.accent_hover};
}}
QPushButton[variant="primary"]:pressed {{
    background-color: {p.accent_pressed};
    border-color: {p.accent_pressed};
}}
QPushButton[variant="primary"]:focus {{
    border: 2px solid {p.border_focus};
}}
QPushButton[variant="primary"]:disabled {{
    background-color: {p.subtle_bg};
    color: {p.text_muted};
    border-color: {p.divider};
}}
QPushButton[variant="secondary"] {{
    background-color: {p.card_bg};
    color: {p.text_primary};
    border: 1px solid {p.border};
}}
QPushButton[variant="secondary"]:hover {{
    background-color: {p.subtle_bg};
}}
QPushButton[variant="ghost"] {{
    background-color: transparent;
    color: {p.text_primary};
    border: 1px solid transparent;
    padding: 5px 9px;
}}
QPushButton[variant="ghost"]:hover {{
    background-color: {p.subtle_bg};
    border-color: {p.border};
}}
QPushButton[variant="ghost"]:focus {{
    border: 2px solid {p.border_focus};
    padding: 4px 8px;
}}
QPushButton[variant="ghost"]:disabled {{
    background-color: transparent;
    color: {p.text_muted};
    border-color: transparent;
}}
QPushButton[variant="danger"] {{
    background-color: {p.card_bg};
    color: {p.danger};
    border: 1px solid {p.border};
}}
QPushButton[variant="danger"]:hover {{
    background-color: {p.danger_subtle};
    border-color: {p.danger};
}}
QPushButton[variant="danger"]:focus {{
    border: 2px solid {p.border_focus};
}}
QPushButton[variant="danger"]:disabled {{
    background-color: {p.panel_bg};
    color: {p.text_muted};
    border-color: {p.divider};
}}
QComboBox, QDoubleSpinBox, QSpinBox, QLineEdit {{
    background-color: {p.input_bg};
    color: {p.text_primary};
    border: 1px solid {p.border};
    border-radius: 6px;
    padding: 4px 8px;
    min-height: 22px;
}}
QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus, QLineEdit:focus {{
    border: 2px solid {p.border_focus};
}}
QComboBox:disabled, QDoubleSpinBox:disabled, QSpinBox:disabled, QLineEdit:disabled {{
    background-color: {p.panel_bg};
    color: {p.text_muted};
    border-color: {p.divider};
}}
QComboBox QAbstractItemView {{
    background-color: {p.card_bg};
    color: {p.text_primary};
    border: 1px solid {p.border};
    selection-background-color: {p.accent_subtle};
    selection-color: {p.text_primary};
}}
QListWidget {{
    background-color: {p.panel_bg};
    color: {p.text_primary};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 4px;
}}
QListWidget:focus {{
    border: 2px solid {p.border_focus};
}}
QListWidget::item {{
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px;
    color: {p.text_primary};
}}
QListWidget::item:selected {{
    background-color: {p.accent_subtle};
    border: 1px solid {p.border_focus};
    color: {p.text_primary};
}}
QListWidget::item:hover:!selected {{
    background-color: {p.subtle_bg};
}}
QGraphicsView {{
    border: 1px solid {p.border};
    border-radius: 8px;
}}
QGraphicsView:focus {{
    border: 2px solid {p.border_focus};
}}
QTabWidget::pane {{
    background-color: {p.panel_bg};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 4px;
}}
QTabBar::tab {{
    background-color: {p.panel_bg};
    color: {p.text_secondary};
    border: 1px solid {p.border};
    border-bottom: none;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    padding: 6px 14px;
    margin-right: 4px;
    font-weight: 500;
}}
QTabBar::tab:selected {{
    background-color: {p.card_bg};
    color: {p.text_primary};
    border-color: {p.border};
    font-weight: 600;
}}
QTabBar::tab:focus {{
    border: 2px solid {p.border_focus};
}}
QProgressBar {{
    background-color: {p.subtle_bg};
    border: none;
    border-radius: 2px;
}}
QProgressBar::chunk {{
    background-color: {p.accent};
    border-radius: 2px;
}}
QStatusBar {{
    background-color: {p.panel_bg};
    color: {p.text_secondary};
    border-top: 1px solid {p.divider};
}}
QToolTip {{
    background-color: {p.card_bg};
    color: {p.text_primary};
    border: 1px solid {p.border};
    padding: 4px 6px;
}}
QScrollBar:vertical {{
    background-color: {p.panel_bg};
    width: 10px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical {{
    background-color: {p.border};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
"""


def set_widget_property(widget: QWidget, name: str, value: str) -> None:
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)


def apply_theme(target: QApplication | QWidget, theme_name: str) -> ThemePalette:
    palette = get_palette(theme_name)
    if isinstance(target, QApplication):
        target.setPalette(build_qpalette(palette))
    target.setStyleSheet(build_stylesheet(palette))
    return palette

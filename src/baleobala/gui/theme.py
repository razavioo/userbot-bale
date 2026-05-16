"""Theme tokens and QSS builder for the Qt GUI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

try:
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication
except ImportError:
    QPalette = None  # type: ignore[assignment]
    QApplication = None  # type: ignore[assignment]


Mode = Literal["light", "dark", "auto"]


@dataclass(frozen=True)
class Theme:
    name: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_soft: str
    ok: str
    err: str
    info: str
    card_border: str
    shadow: str
    bg_base: str = "#08090C"
    bg_surface: str = "#101114"
    bg_elevated: str = "#16181D"
    text_primary: str = "#F4F5F8"
    text_secondary: str = "#9BA1AE"
    text_tertiary: str = "#5C606A"
    font_family_ui: str = '"Inter", "SF Pro Text", "Segoe UI", system-ui, sans-serif'
    font_family_display: str = '"Inter Display", "SF Pro Display", "Inter", system-ui, sans-serif'
    font_family_mono: str = '"JetBrains Mono", "SF Mono", Menlo, Consolas, monospace'
    base_font_px: int = 14
    title_font_px: int = 30
    section_font_px: int = 11
    radius_card: int = 14
    radius_input: int = 10


LIGHT = Theme(
    name="light",
    accent="#6948E8",
    accent_hover="#7C5CFF",
    accent_pressed="#5A39D6",
    accent_soft="rgba(124, 92, 255, 0.10)",
    ok="#0EA47A",
    err="#E04565",
    info="#2563EB",
    card_border="#E8EAF0",
    shadow="rgba(15, 17, 25, 0.05)",
    bg_base="#FAFAFB",
    bg_surface="#FFFFFF",
    bg_elevated="#F4F5F8",
    text_primary="#0B0C10",
    text_secondary="#5A6072",
    text_tertiary="#9098A6",
)

DARK = Theme(
    name="dark",
    accent="#7C5CFF",
    accent_hover="#8E73FF",
    accent_pressed="#6948E8",
    accent_soft="rgba(124, 92, 255, 0.14)",
    ok="#3FE0A0",
    err="#FF5C7C",
    info="#7EC2FF",
    card_border="#1F232C",
    shadow="rgba(0, 0, 0, 0.45)",
    bg_base="#08090C",
    bg_surface="#101114",
    bg_elevated="#16181D",
    text_primary="#F4F5F8",
    text_secondary="#9BA1AE",
    text_tertiary="#5C606A",
)


def detect_os_theme() -> Literal["light", "dark"]:
    if QApplication is None:
        return "dark"
    app = QApplication.instance()
    if app is None:
        return "dark"
    palette = app.palette()
    window = palette.color(QPalette.ColorRole.Window)
    return "dark" if window.valueF() < 0.5 else "light"


def resolve_theme(mode: Mode) -> Theme:
    if mode == "auto":
        return DARK if detect_os_theme() == "dark" else LIGHT
    return DARK if mode == "dark" else LIGHT


def stylesheet(theme: Theme) -> str:
    return f"""
* {{
    font-family: {theme.font_family_ui};
    font-size: {theme.base_font_px}px;
    color: {theme.text_primary};
}}

QMainWindow, QWidget#container, LoginView, ConnectView, QScrollArea, QAbstractScrollArea {{
    background-color: {theme.bg_base};
}}

QScrollArea {{ border: none; }}
QScrollArea > QWidget > QWidget {{ background-color: {theme.bg_base}; }}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 4px 2px 4px 0;
}}
QScrollBar::handle:vertical {{
    background: {theme.card_border};
    border-radius: 4px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: {theme.text_tertiary}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0 4px 2px 4px;
}}
QScrollBar::handle:horizontal {{
    background: {theme.card_border};
    border-radius: 4px;
    min-width: 32px;
}}
QScrollBar::handle:horizontal:hover {{ background: {theme.text_tertiary}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

QWidget#card, QGroupBox, QFrame#banner-info, QFrame#banner-ok, QFrame#banner-err, QFrame#accent-panel, QFrame#hero-panel {{
    background: {theme.bg_surface};
    border: 1px solid {theme.card_border};
    border-radius: {theme.radius_card}px;
}}

QFrame#dashboard-panel, QFrame#gate-panel, QFrame#settings-panel {{
    background: {theme.bg_surface};
    border: 1px solid {theme.card_border};
    border-radius: {theme.radius_card}px;
}}

QFrame#accent-panel {{
    background: {theme.bg_elevated};
    border: 1px solid {theme.card_border};
}}
QFrame#hero-panel {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {theme.accent_soft},
        stop:1 {theme.bg_surface});
    border: 1px solid {theme.card_border};
}}

QGroupBox {{
    margin-top: 10px;
    padding: 22px 20px 20px 20px;
    font-weight: 600;
    color: {theme.text_primary};
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 16px;
    padding: 0 6px;
    color: {theme.text_secondary};
}}

QLabel {{ color: {theme.text_primary}; background: transparent; }}
QLabel#title {{
    font-family: {theme.font_family_display};
    font-size: {theme.title_font_px}px;
    font-weight: 700;
    letter-spacing: -0.5px;
    color: {theme.text_primary};
}}
QLabel#subtitle {{ color: {theme.text_secondary}; }}
QLabel#section, QLabel#eyebrow {{
    font-size: {theme.section_font_px}px;
    font-weight: 700;
    letter-spacing: 1.2px;
    text-transform: uppercase;
    color: {theme.text_tertiary};
}}
QLabel#summary {{ color: {theme.text_secondary}; }}
QLabel#accent-title {{ font-weight: 700; color: {theme.accent}; }}
QLabel#accent-body {{ color: {theme.text_primary}; }}
QLabel#readiness-ok {{
    color: {theme.text_primary};
    background: rgba(63, 224, 160, 0.10);
    border: 1px solid rgba(63, 224, 160, 0.28);
    border-radius: 10px;
    padding: 10px 12px;
}}
QLabel#readiness-pending {{
    color: {theme.text_secondary};
    background: {theme.bg_elevated};
    border: 1px solid {theme.card_border};
    border-radius: 10px;
    padding: 10px 12px;
}}

QLabel#banner-title {{ font-weight: 700; }}
QLabel#banner-body {{ color: {theme.text_secondary}; }}
QFrame#banner-info {{
    background: {theme.accent_soft};
    border: 1px solid {theme.accent};
}}
QFrame#banner-ok {{
    background: rgba(63, 224, 160, 0.10);
    border: 1px solid rgba(63, 224, 160, 0.32);
}}
QFrame#banner-err {{
    background: rgba(255, 92, 124, 0.10);
    border: 1px solid rgba(255, 92, 124, 0.32);
}}

QLabel#status-info {{ color: {theme.text_secondary}; }}
QLabel#status-ok {{ color: {theme.ok}; font-weight: 600; }}
QLabel#status-err {{ color: {theme.err}; font-weight: 600; }}

QLabel#step-done, QLabel#step-current, QLabel#step-pending {{
    border-radius: 10px;
    padding: 10px 12px;
    border: 1px solid {theme.card_border};
}}
QLabel#step-done {{
    background: rgba(63, 224, 160, 0.10);
    color: {theme.ok};
    font-weight: 700;
    border-color: rgba(63, 224, 160, 0.32);
}}
QLabel#step-current {{
    background: {theme.accent_soft};
    color: {theme.accent};
    font-weight: 700;
    border-color: {theme.accent};
}}
QLabel#step-pending {{
    color: {theme.text_tertiary};
    background: {theme.bg_surface};
}}

QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QAbstractSpinBox {{
    padding: 10px 12px;
    border: 1px solid {theme.card_border};
    border-radius: {theme.radius_input}px;
    background: {theme.bg_surface};
    color: {theme.text_primary};
    selection-background-color: {theme.accent};
    selection-color: white;
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QPlainTextEdit:hover {{
    border-color: {theme.text_tertiary};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{
    border: 1px solid {theme.accent};
    background: {theme.bg_surface};
}}
QPlainTextEdit {{
    font-family: {theme.font_family_mono};
    font-size: 12px;
}}
QLineEdit::placeholder {{ color: {theme.text_tertiary}; }}

QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {theme.bg_elevated};
    color: {theme.text_primary};
    border: 1px solid {theme.card_border};
    border-radius: 8px;
    padding: 4px;
    selection-background-color: {theme.accent};
    selection-color: white;
    outline: 0;
}}

QPushButton, QToolButton {{
    padding: 10px 18px;
    border-radius: {theme.radius_input}px;
    border: 1px solid {theme.card_border};
    background: {theme.bg_surface};
    color: {theme.text_primary};
    font-weight: 500;
}}
QPushButton:hover, QToolButton:hover {{
    background: {theme.bg_elevated};
    border-color: {theme.text_tertiary};
}}
QPushButton:pressed, QToolButton:pressed {{ background: {theme.bg_elevated}; }}
QPushButton:disabled, QToolButton:disabled {{
    color: {theme.text_tertiary};
    background: {theme.bg_surface};
}}

QPushButton#primary {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {theme.accent_hover},
        stop:1 {theme.accent});
    color: white;
    border: 1px solid {theme.accent};
    font-weight: 700;
}}
QPushButton#primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {theme.accent_hover},
        stop:1 {theme.accent_hover});
}}
QPushButton#primary:pressed {{
    background: {theme.accent_pressed};
    border-color: {theme.accent_pressed};
}}
QPushButton#primary:disabled {{
    background: {theme.bg_elevated};
    color: {theme.text_tertiary};
    border-color: {theme.card_border};
}}

QPushButton#hero-action {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {theme.accent_hover},
        stop:1 {theme.accent});
    color: white;
    border: 1px solid {theme.accent};
    font-weight: 700;
    font-size: 18px;
    letter-spacing: 0.2px;
    min-height: 60px;
    padding: 16px 28px;
    border-radius: 16px;
}}
QPushButton#hero-action:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {theme.accent_hover},
        stop:1 {theme.accent_hover});
}}
QPushButton#hero-action:pressed {{
    background: {theme.accent_pressed};
    border-color: {theme.accent_pressed};
}}
QPushButton#hero-action:checked {{ background: {theme.accent_pressed}; }}
QPushButton#hero-action:checked:hover {{ background: {theme.accent_hover}; }}

QPushButton#danger {{
    background: transparent;
    color: {theme.err};
    border: 1px solid {theme.err};
    font-weight: 700;
}}
QPushButton#danger:hover {{
    background: rgba(255, 92, 124, 0.10);
}}

QStatusBar {{
    color: {theme.text_tertiary};
    background: {theme.bg_base};
    border-top: 1px solid {theme.card_border};
}}

QToolTip {{
    background: {theme.bg_elevated};
    color: {theme.text_primary};
    border: 1px solid {theme.card_border};
    border-radius: 6px;
    padding: 6px 8px;
}}

QMenu {{
    background: {theme.bg_elevated};
    color: {theme.text_primary};
    border: 1px solid {theme.card_border};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 14px;
    border-radius: 6px;
}}
QMenu::item:selected {{ background: {theme.accent}; color: white; }}

QCheckBox, QRadioButton {{ spacing: 8px; color: {theme.text_primary}; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {theme.card_border};
    border-radius: 4px;
    background: {theme.bg_surface};
}}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {theme.accent};
    border-color: {theme.accent};
}}
"""


def apply_theme(app, mode: Mode = "auto") -> Theme:
    theme = resolve_theme(mode)
    app.setStyle("Fusion")
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(theme.bg_base))
    palette.setColor(QPalette.ColorRole.Base, QColor(theme.bg_surface))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(theme.bg_elevated))
    palette.setColor(QPalette.ColorRole.Button, QColor(theme.bg_surface))
    palette.setColor(QPalette.ColorRole.Text, QColor(theme.text_primary))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(theme.text_primary))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(theme.text_primary))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(theme.text_tertiary))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(theme.accent))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(theme.bg_elevated))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(theme.text_primary))
    palette.setColor(QPalette.ColorRole.Link, QColor(theme.accent))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet(theme))
    return theme

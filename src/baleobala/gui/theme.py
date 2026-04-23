"""Theme tokens and QSS builder for the Qt GUI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

try:
    from PySide6.QtGui import QPalette
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
    font_family_ui: str = '"Avenir Next", "Segoe UI", sans-serif'
    font_family_mono: str = '"SF Mono", Menlo, Consolas, monospace'
    base_font_px: int = 14
    title_font_px: int = 28
    section_font_px: int = 12
    radius_card: int = 16
    radius_input: int = 10


LIGHT = Theme(
    name="light",
    accent="#0f766e",
    accent_hover="#0b5f59",
    accent_pressed="#094e49",
    accent_soft="#d7f3ef",
    ok="#1f7a44",
    err="#b9384e",
    info="#185c9c",
    card_border="#d5ddd9",
    shadow="rgba(16, 24, 40, 0.08)",
)

DARK = Theme(
    name="dark",
    accent="#2ec4b6",
    accent_hover="#27aa9d",
    accent_pressed="#1f8d82",
    accent_soft="rgba(46, 196, 182, 0.15)",
    ok="#59c174",
    err="#ff6b81",
    info="#78b8ff",
    card_border="#2f3744",
    shadow="rgba(0, 0, 0, 0.35)",
)


def detect_os_theme() -> Literal["light", "dark"]:
    if QApplication is None:
        return "light"
    app = QApplication.instance()
    if app is None:
        return "light"
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
}}

QMainWindow, QWidget#container {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 palette(window),
        stop:1 palette(alternate-base));
}}

QWidget#card, QGroupBox, QFrame#banner-info, QFrame#banner-ok, QFrame#banner-err, QFrame#accent-panel, QFrame#hero-panel {{
    background: palette(base);
    border: 1px solid {theme.card_border};
    border-radius: {theme.radius_card}px;
}}

QFrame#dashboard-panel, QFrame#gate-panel, QFrame#settings-panel {{
    background: rgba(15, 118, 110, 0.03);
    border: 1px solid rgba(15, 118, 110, 0.12);
    border-radius: {theme.radius_card}px;
}}

QFrame#accent-panel {{
    background: rgba(15, 118, 110, 0.04);
}}
QFrame#hero-panel {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {theme.accent_soft},
        stop:1 rgba(15, 118, 110, 0.03));
    border-color: rgba(15, 118, 110, 0.20);
}}

QGroupBox {{
    margin-top: 8px;
    padding: 20px 18px 18px 18px;
    font-weight: 600;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
}}

QLabel#title {{ font-size: {theme.title_font_px}px; font-weight: 700; }}
QLabel#subtitle {{ color: palette(placeholder-text); }}
QLabel#section, QLabel#eyebrow {{
    font-size: {theme.section_font_px}px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: palette(placeholder-text);
}}
QLabel#summary {{ color: palette(placeholder-text); }}
QLabel#accent-title {{ font-weight: 700; }}
QLabel#accent-body {{ color: palette(text); }}
QLabel#readiness-ok {{
    color: palette(text);
    background: rgba(31, 122, 68, 0.08);
    border: 1px solid rgba(31, 122, 68, 0.18);
    border-radius: 10px;
    padding: 8px 10px;
}}
QLabel#readiness-pending {{
    color: palette(text);
    background: rgba(24, 92, 156, 0.08);
    border: 1px solid rgba(24, 92, 156, 0.18);
    border-radius: 10px;
    padding: 8px 10px;
}}

QLabel#banner-title {{ font-weight: 700; }}
QLabel#banner-body {{ color: palette(text); }}
QFrame#banner-info {{ background: {theme.accent_soft}; }}
QFrame#banner-ok {{ background: rgba(31, 122, 68, 0.10); }}
QFrame#banner-err {{ background: rgba(185, 56, 78, 0.10); }}

QLabel#status-info {{ color: palette(text); }}
QLabel#status-ok {{ color: {theme.ok}; font-weight: 600; }}
QLabel#status-err {{ color: {theme.err}; font-weight: 600; }}

QLabel#step-done, QLabel#step-current, QLabel#step-pending {{
    border-radius: 10px;
    padding: 8px 10px;
    border: 1px solid {theme.card_border};
}}
QLabel#step-done {{ background: rgba(31, 122, 68, 0.10); color: {theme.ok}; font-weight: 700; }}
QLabel#step-current {{ background: {theme.accent_soft}; color: {theme.accent}; font-weight: 700; border-color: {theme.accent}; }}
QLabel#step-pending {{ color: palette(placeholder-text); background: palette(base); }}

QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {{
    padding: 9px 11px;
    border: 1px solid palette(mid);
    border-radius: {theme.radius_input}px;
    background: palette(base);
    selection-background-color: {theme.accent};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{
    border-color: {theme.accent};
}}

QPushButton, QToolButton {{
    padding: 10px 16px;
    border-radius: {theme.radius_input}px;
    border: 1px solid palette(mid);
    background: palette(button);
}}
QPushButton:hover, QToolButton:hover {{ background: palette(midlight); }}
QPushButton:disabled, QToolButton:disabled {{ color: palette(placeholder-text); }}

QPushButton#primary {{
    background: {theme.accent};
    color: white;
    border-color: {theme.accent};
    font-weight: 700;
}}
QPushButton#primary:hover {{ background: {theme.accent_hover}; }}
QPushButton#primary:pressed {{ background: {theme.accent_pressed}; }}

QPushButton#hero-action {{
    background: {theme.accent};
    color: white;
    border-color: {theme.accent};
    font-weight: 800;
    font-size: 18px;
    min-height: 58px;
    padding: 14px 24px;
    border-radius: 16px;
}}
QPushButton#hero-action:hover {{ background: {theme.accent_hover}; }}
QPushButton#hero-action:pressed {{ background: {theme.accent_pressed}; }}
QPushButton#hero-action:checked {{ background: {theme.accent_pressed}; }}
QPushButton#hero-action:checked:hover {{ background: {theme.accent_hover}; }}

QPushButton#danger {{
    background: transparent;
    color: {theme.err};
    border-color: {theme.err};
    font-weight: 700;
}}
QPushButton#danger:hover {{ background: rgba(185, 56, 78, 0.08); }}

QStatusBar {{ color: palette(placeholder-text); }}
"""


def apply_theme(app, mode: Mode = "auto") -> Theme:
    theme = resolve_theme(mode)
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet(theme))
    return theme

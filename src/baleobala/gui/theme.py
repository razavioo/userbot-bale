"""Theme tokens and QSS builder for the Qt6 GUI.

Centralises color, spacing, and typography so views don't embed raw hex
literals and so light/dark switching is a single code path. The OS theme is
detected at apply time; users can override via the config system.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication
except ImportError:  # GUI extras not installed
    Qt = None  # type: ignore[assignment]
    QPalette = None  # type: ignore[assignment]
    QApplication = None  # type: ignore[assignment]


Mode = Literal["light", "dark", "auto"]


@dataclass(frozen=True)
class Theme:
    name: str
    primary: str
    primary_hover: str
    primary_pressed: str
    primary_disabled_bg: str
    primary_disabled_fg: str
    danger: str
    danger_bg_hover: str
    ok: str
    err: str
    font_family_mono: str = '"SF Mono", Menlo, Consolas, monospace'
    base_font_px: int = 13
    title_font_px: int = 22
    section_font_px: int = 11
    radius_card: int = 10
    radius_input: int = 6
    pad_input_v: int = 7
    pad_input_h: int = 10
    pad_button_v: int = 8
    pad_button_h: int = 16


LIGHT = Theme(
    name="light",
    primary="#2b6cb0",
    primary_hover="#2c5a94",
    primary_pressed="#234b7a",
    primary_disabled_bg="#c3cdd9",
    primary_disabled_fg="#6b7280",
    danger="#d64e66",
    danger_bg_hover="rgba(214, 78, 102, 0.08)",
    ok="#2ea043",
    err="#d64e66",
)

DARK = Theme(
    name="dark",
    primary="#2b6cb0",
    primary_hover="#2c5a94",
    primary_pressed="#234b7a",
    primary_disabled_bg="#3a4556",
    primary_disabled_fg="#8a95a5",
    danger="#f0506e",
    danger_bg_hover="rgba(240, 80, 110, 0.08)",
    ok="#2ea043",
    err="#f0506e",
)


def detect_os_theme() -> Literal["light", "dark"]:
    if QApplication is None:
        return "light"
    app = QApplication.instance()
    if app is None:
        return "light"
    palette = app.palette()
    window = palette.color(QPalette.ColorRole.Window)
    # Qt's color-valueF is 0.0–1.0; < 0.5 ≈ dark.
    return "dark" if window.valueF() < 0.5 else "light"


def resolve_theme(mode: Mode) -> Theme:
    if mode == "auto":
        return DARK if detect_os_theme() == "dark" else LIGHT
    return DARK if mode == "dark" else LIGHT


def stylesheet(theme: Theme) -> str:
    return f"""
* {{ font-size: {theme.base_font_px}px; }}

QMainWindow, QWidget#container {{ background: palette(window); }}

QWidget#card {{
    background: palette(base);
    border-radius: {theme.radius_card}px;
}}

QLabel#title    {{ font-size: {theme.title_font_px}px; font-weight: 600; }}
QLabel#subtitle {{ color: palette(placeholder-text); }}
QLabel#section  {{
    font-size: {theme.section_font_px}px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 1px;
    color: palette(placeholder-text);
}}

QLabel#status-info {{ color: palette(placeholder-text); }}
QLabel#status-ok   {{ color: {theme.ok}; font-weight: 600; }}
QLabel#status-err  {{ color: {theme.err}; font-weight: 600; }}

QLineEdit, QComboBox, QSpinBox {{
    padding: {theme.pad_input_v}px {theme.pad_input_h}px;
    border: 1px solid palette(mid);
    border-radius: {theme.radius_input}px;
    background: palette(base);
    selection-background-color: palette(highlight);
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: palette(highlight); }}

QPushButton {{
    padding: {theme.pad_button_v}px {theme.pad_button_h}px;
    border-radius: {theme.radius_input}px;
    border: 1px solid palette(mid);
    background: palette(button);
}}
QPushButton:hover    {{ background: palette(midlight); }}
QPushButton:pressed  {{ background: palette(mid); }}
QPushButton:disabled {{ color: palette(placeholder-text); }}

QPushButton#primary {{
    background: {theme.primary};
    color: white;
    border: 1px solid {theme.primary};
}}
QPushButton#primary:hover    {{ background: {theme.primary_hover}; }}
QPushButton#primary:pressed  {{ background: {theme.primary_pressed}; }}
QPushButton#primary:disabled {{
    background: {theme.primary_disabled_bg};
    color: {theme.primary_disabled_fg};
    border-color: {theme.primary_disabled_bg};
}}

QPushButton#danger {{
    background: transparent;
    color: {theme.danger};
    border-color: {theme.danger};
}}
QPushButton#danger:hover {{ background: {theme.danger_bg_hover}; }}

QPlainTextEdit {{
    font-family: {theme.font_family_mono};
    font-size: {theme.base_font_px - 2}px;
    background: palette(base);
    border: 1px solid palette(mid);
    border-radius: {theme.radius_input}px;
    padding: 8px;
}}

QStatusBar {{ background: palette(window); color: palette(placeholder-text); }}

QFrame#divider {{ background: palette(mid); max-height: 1px; min-height: 1px; }}
"""


def apply_theme(app, mode: Mode = "auto") -> Theme:
    """Install the stylesheet for ``mode`` on ``app`` and return the theme used."""
    theme = resolve_theme(mode)
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet(theme))
    return theme

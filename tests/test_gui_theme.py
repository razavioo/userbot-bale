"""Theme module is pure — no Qt runtime needed for unit tests."""

from __future__ import annotations

import pytest

theme = pytest.importorskip("userbot_bale.gui.theme")


def test_resolve_theme_light_vs_dark() -> None:
    light = theme.resolve_theme("light")
    dark = theme.resolve_theme("dark")
    assert light.name == "light"
    assert dark.name == "dark"
    assert light is not dark


def test_stylesheet_contains_tokens() -> None:
    t = theme.resolve_theme("dark")
    qss = theme.stylesheet(t)
    assert t.accent in qss
    assert f"{t.base_font_px}px" in qss
    assert "QPushButton#primary" in qss
    assert "QPushButton#danger" in qss
    assert "QFrame#banner-info" in qss
    assert "QLabel#readiness-ok" in qss


def test_stylesheet_is_deterministic() -> None:
    t = theme.resolve_theme("light")
    assert theme.stylesheet(t) == theme.stylesheet(t)

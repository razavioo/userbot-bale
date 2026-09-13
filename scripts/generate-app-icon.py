#!/usr/bin/env python3
"""Generate the project app icon in all sizes used by macOS, Android, and the README.

Run: python scripts/generate-app-icon.py
"""
from __future__ import annotations

import math
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "build" / "app-icon"
ANDROID_RES = REPO / "native" / "android" / "app" / "src" / "main" / "res"
MACOS_RES = REPO / "native" / "macos" / "UserbotBaleApp" / "Resources"
PROXY_RES = REPO / "native" / "macos" / "UserbotBaleProxyApp" / "Resources"
README_DIR = REPO / "docs" / "assets"

# Brand palette
TOP = (38, 198, 218)       # teal
BOTTOM = (63, 81, 181)     # indigo
ACCENT = (255, 224, 130)   # warm gold for the bolt

MASTER = 1024


def squircle_mask(size: int, radius_ratio: float = 0.225) -> Image.Image:
    """Apple-style squircle approximated with a high-radius rounded rect."""
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    r = int(size * radius_ratio)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=r, fill=255)
    return mask


def vertical_gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGB", (size, size), top)
    px = img.load()
    for y in range(size):
        t = y / (size - 1)
        # ease-out for a richer look
        t = 1 - (1 - t) ** 2
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(size):
            px[x, y] = (r, g, b)
    return img


def chat_bubble_path(size: int) -> list[tuple[float, float]]:
    """Rounded chat bubble centered in canvas, tail at lower-left."""
    cx, cy = size / 2, size / 2 - size * 0.02
    w, h = size * 0.58, size * 0.46
    x0, y0 = cx - w / 2, cy - h / 2
    x1, y1 = cx + w / 2, cy + h / 2
    r = size * 0.10
    pts: list[tuple[float, float]] = []
    # rounded rect via arc sampling
    def arc(cxx, cyy, start, end, steps=18):
        for i in range(steps + 1):
            a = math.radians(start + (end - start) * i / steps)
            pts.append((cxx + r * math.cos(a), cyy + r * math.sin(a)))
    arc(x1 - r, y0 + r, -90, 0)
    arc(x1 - r, y1 - r, 0, 90)
    # tail at bottom-left
    tail_anchor_x = x0 + w * 0.32
    tail_tip_x = x0 + w * 0.10
    tail_tip_y = y1 + size * 0.11
    pts.append((tail_anchor_x + size * 0.06, y1))
    pts.append((tail_tip_x, tail_tip_y))
    pts.append((tail_anchor_x - size * 0.04, y1 - size * 0.005))
    arc(x0 + r, y1 - r, 90, 180)
    arc(x0 + r, y0 + r, 180, 270)
    return pts


def bolt_path(size: int) -> list[tuple[float, float]]:
    """Stylized lightning bolt centered in canvas."""
    cx, cy = size / 2, size / 2 - size * 0.04
    s = size * 0.30
    # canonical bolt offsets (relative)
    raw = [
        (0.18, -0.55), (-0.32, 0.10), (-0.02, 0.10),
        (-0.18, 0.55), (0.32, -0.10), (0.02, -0.10),
    ]
    return [(cx + dx * s, cy + dy * s) for dx, dy in raw]


def render_master() -> Image.Image:
    size = MASTER
    bg = vertical_gradient(size, TOP, BOTTOM).convert("RGBA")

    # Subtle highlight ring
    overlay = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    od = ImageDraw.Draw(overlay)
    for i in range(8):
        alpha = int(18 * (1 - i / 8))
        od.ellipse(
            (
                size * 0.08 + i * 4,
                size * 0.05 + i * 4,
                size * 0.92 - i * 4,
                size * 0.55 - i * 4,
            ),
            fill=(255, 255, 255, alpha),
        )
    overlay = overlay.filter(ImageFilter.GaussianBlur(radius=size * 0.04))
    bg = Image.alpha_composite(bg, overlay)

    # Chat bubble (white, soft drop shadow)
    bubble_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bubble_layer)
    bubble = chat_bubble_path(size)
    bd.polygon(bubble, fill=(255, 255, 255, 255))

    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.polygon(bubble, fill=(0, 0, 0, 110))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=size * 0.025))
    shadow_offset = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_offset.paste(shadow, (0, int(size * 0.012)), shadow)

    # Bolt on top of bubble
    bolt_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ld = ImageDraw.Draw(bolt_layer)
    ld.polygon(bolt_path(size), fill=ACCENT + (255,))

    composed = Image.alpha_composite(bg, shadow_offset)
    composed = Image.alpha_composite(composed, bubble_layer)
    composed = Image.alpha_composite(composed, bolt_layer)

    # Mask to squircle
    masked = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    masked.paste(composed, (0, 0), squircle_mask(size))
    return masked


def render_full_bleed(master_squircle: Image.Image) -> Image.Image:
    """Variant without the squircle clip — used for adaptive-icon foreground & macOS .icns."""
    size = MASTER
    bg = vertical_gradient(size, TOP, BOTTOM).convert("RGBA")
    bubble_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bubble_layer)
    bd.polygon(chat_bubble_path(size), fill=(255, 255, 255, 255))
    bolt_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ld = ImageDraw.Draw(bolt_layer)
    ld.polygon(bolt_path(size), fill=ACCENT + (255,))
    out = Image.alpha_composite(bg, bubble_layer)
    out = Image.alpha_composite(out, bolt_layer)
    return out


def adaptive_foreground() -> Image.Image:
    """Android adaptive-icon foreground: 432x432 with content inside the 264x264 safe zone."""
    canvas = Image.new("RGBA", (432, 432), (0, 0, 0, 0))
    inner_size = 264
    bubble_layer = Image.new("RGBA", (inner_size, inner_size), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bubble_layer)
    bd.polygon(chat_bubble_path(inner_size), fill=(255, 255, 255, 255))
    bolt_layer = Image.new("RGBA", (inner_size, inner_size), (0, 0, 0, 0))
    ld = ImageDraw.Draw(bolt_layer)
    ld.polygon(bolt_path(inner_size), fill=ACCENT + (255,))
    inner = Image.alpha_composite(bubble_layer, bolt_layer)
    canvas.paste(inner, ((432 - inner_size) // 2, (432 - inner_size) // 2), inner)
    return canvas


def adaptive_background() -> Image.Image:
    return vertical_gradient(432, TOP, BOTTOM).convert("RGBA")


def round_mask(size: int) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).ellipse((0, 0, size - 1, size - 1), fill=255)
    return m


def save(img: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    master = render_master()
    full = render_full_bleed(master)

    # ---- Master assets ----
    save(master, OUT / "icon-1024-squircle.png")
    save(full, OUT / "icon-1024-fullbleed.png")
    save(master.resize((512, 512), Image.LANCZOS), OUT / "icon-512.png")
    save(master.resize((256, 256), Image.LANCZOS), OUT / "icon-256.png")

    # ---- README ----
    save(master.resize((256, 256), Image.LANCZOS), README_DIR / "icon.png")
    save(master.resize((512, 512), Image.LANCZOS), README_DIR / "icon@2x.png")

    # ---- macOS .icns ----
    iconset_sizes = [
        ("icon_16x16.png", 16),
        ("icon_16x16@2x.png", 32),
        ("icon_32x32.png", 32),
        ("icon_32x32@2x.png", 64),
        ("icon_128x128.png", 128),
        ("icon_128x128@2x.png", 256),
        ("icon_256x256.png", 256),
        ("icon_256x256@2x.png", 512),
        ("icon_512x512.png", 512),
        ("icon_512x512@2x.png", 1024),
    ]
    iconset_dir = OUT / "UserbotBale.iconset"
    if iconset_dir.exists():
        shutil.rmtree(iconset_dir)
    iconset_dir.mkdir(parents=True)
    for name, size in iconset_sizes:
        full.resize((size, size), Image.LANCZOS).save(iconset_dir / name)
    icns_path = OUT / "UserbotBale.icns"
    if shutil.which("iconutil") is None:
        print("warning: iconutil not on PATH; skipping .icns creation", file=sys.stderr)
    else:
        subprocess.run(
            ["iconutil", "-c", "icns", "-o", str(icns_path), str(iconset_dir)],
            check=True,
        )
        for dest in (MACOS_RES / "UserbotBale.icns", PROXY_RES / "UserbotBale.icns"):
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(icns_path, dest)

    # ---- Android mipmaps ----
    densities = [
        ("mdpi", 48),
        ("hdpi", 72),
        ("xhdpi", 96),
        ("xxhdpi", 144),
        ("xxxhdpi", 192),
    ]
    for d, px in densities:
        target = ANDROID_RES / f"mipmap-{d}"
        target.mkdir(parents=True, exist_ok=True)
        sq = master.resize((px, px), Image.LANCZOS)
        sq.save(target / "ic_launcher.png")
        # round variant
        circle = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        circle.paste(full.resize((px, px), Image.LANCZOS), (0, 0), round_mask(px))
        circle.save(target / "ic_launcher_round.png")
        # foreground for adaptive (uses density bucket sizes per Android docs)
        fg_size = int(px * 432 / 48)  # 432 at mdpi
        adaptive_foreground().resize((fg_size, fg_size), Image.LANCZOS).save(
            target / "ic_launcher_foreground.png"
        )

    # adaptive XML + background color
    anydpi = ANDROID_RES / "mipmap-anydpi-v26"
    anydpi.mkdir(parents=True, exist_ok=True)
    adaptive_xml = """<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@drawable/ic_launcher_background" />
    <foreground android:drawable="@mipmap/ic_launcher_foreground" />
</adaptive-icon>
"""
    (anydpi / "ic_launcher.xml").write_text(adaptive_xml)
    (anydpi / "ic_launcher_round.xml").write_text(adaptive_xml)

    # background drawable: vertical gradient via shape drawable
    drawable_dir = ANDROID_RES / "drawable"
    drawable_dir.mkdir(parents=True, exist_ok=True)
    bg_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<shape xmlns:android="http://schemas.android.com/apk/res/android"
    android:shape="rectangle">
    <gradient
        android:startColor="#{TOP[0]:02X}{TOP[1]:02X}{TOP[2]:02X}"
        android:endColor="#{BOTTOM[0]:02X}{BOTTOM[1]:02X}{BOTTOM[2]:02X}"
        android:angle="270" />
</shape>
"""
    (drawable_dir / "ic_launcher_background.xml").write_text(bg_xml)

    print(f"Generated icons under: {OUT}")
    print("macOS .icns copied into UserbotBaleApp/Resources and UserbotBaleProxyApp/Resources.")
    print("Android mipmaps written under native/android/app/src/main/res/mipmap-*.")
    print("README icon at docs/assets/icon.png.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

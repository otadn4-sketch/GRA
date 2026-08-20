from __future__ import annotations

"""Cover artwork for weekday palettes used on the Garaye bulletin first page.

Colors come from the weekday palette workbook/PDF
(``راهنمای_نهایی_پالت_رنگی_روزهای_هفته``). Operators can drop raster files
into ``web/assets/images/weekdays/`` named after the Persian weekday
(``شنبه.png`` … ``جمعه.png``) to replace the generated art.
"""

from io import BytesIO
from pathlib import Path
from typing import Any

from .persian_text import to_persian_digits


_WEEKDAY_FILES = {
    0: ("shanbeh", "شنبه"),
    1: ("yekshanbeh", "یکشنبه"),
    2: ("doshanbeh", "دوشنبه"),
    3: ("seshanbeh", "سه‌شنبه"),
    4: ("chaharshanbeh", "چهارشنبه"),
    5: ("panjshanbeh", "پنج‌شنبه"),
    6: ("jomeh", "جمعه"),
}


def _weekday_asset_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "web" / "assets" / "images" / "weekdays"


def weekday_cover_image_path(palette: dict[str, Any]) -> Path | None:
    weekday = int(palette.get("weekday") or 0)
    latin, persian = _WEEKDAY_FILES.get(weekday, _WEEKDAY_FILES[0])
    folder = _weekday_asset_dir()
    for name in (persian, latin, f"{weekday}"):
        for suffix in (".png", ".jpg", ".jpeg", ".webp", ".svg"):
            path = folder / f"{name}{suffix}"
            if path.is_file():
                return path
    return None


def weekday_cover_svg(palette: dict[str, Any]) -> str:
    dark = palette.get("dark", "#783B31")
    main = palette.get("main", "#B85C4A")
    accent = palette.get("accent", "#D58A79")
    light = palette.get("light", "#EBC4BA")
    ground = palette.get("ground", "#FAF3F1")
    name = to_persian_digits(palette.get("name") or "شنبه")
    label = to_persian_digits(palette.get("label") or "")
    weekday = int(palette.get("weekday") or 0)
    motif = _motif_svg(weekday, dark, main, accent, light)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640" role="img" aria-label="{name} {label}">
  <defs>
    <linearGradient id="sky" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{dark}"/>
      <stop offset="1" stop-color="{main}"/>
    </linearGradient>
    <radialGradient id="glow" cx="70%" cy="22%" r="48%">
      <stop offset="0" stop-color="{accent}" stop-opacity=".95"/>
      <stop offset="1" stop-color="{main}" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="640" height="640" rx="48" fill="url(#sky)"/>
  <rect width="640" height="640" rx="48" fill="url(#glow)"/>
  {motif}
  <rect x="36" y="36" width="568" height="568" rx="36" fill="none" stroke="{light}" stroke-width="3" opacity=".55"/>
  <text x="320" y="548" text-anchor="middle" fill="{ground}" font-family="IRZar,Tahoma,Arial" font-size="42" font-weight="700">{name}</text>
  <text x="320" y="592" text-anchor="middle" fill="{light}" font-family="IRZar,Tahoma,Arial" font-size="22">{label}</text>
</svg>"""


def _motif_svg(weekday: int, dark: str, main: str, accent: str, light: str) -> str:
    if weekday == 0:  # شنبه — شفق
        return f"""
<circle cx="455" cy="168" r="78" fill="{light}" opacity=".95"/>
<circle cx="455" cy="168" r="48" fill="{accent}"/>
<path d="M70 430 C170 310, 300 360, 390 300 C470 250, 540 290, 600 250 L600 560 L70 560 Z" fill="{accent}" opacity=".55"/>
<path d="M70 480 C190 370, 310 430, 430 370 C520 325, 580 360, 620 330 L620 580 L70 580 Z" fill="{light}" opacity=".35"/>"""
    if weekday == 1:  # یکشنبه — یشم
        return f"""
<circle cx="210" cy="250" r="92" fill="{light}" opacity=".7"/>
<circle cx="310" cy="210" r="78" fill="{accent}" opacity=".8"/>
<circle cx="390" cy="280" r="96" fill="{light}" opacity=".55"/>
<ellipse cx="320" cy="430" rx="170" ry="70" fill="{dark}" opacity=".28"/>"""
    if weekday == 2:  # دوشنبه — دود
        return f"""
<ellipse cx="220" cy="250" rx="130" ry="70" fill="{light}" opacity=".45"/>
<ellipse cx="360" cy="210" rx="150" ry="80" fill="{accent}" opacity=".5"/>
<ellipse cx="430" cy="300" rx="140" ry="74" fill="{light}" opacity=".35"/>
<rect x="90" y="390" width="460" height="18" rx="9" fill="{light}" opacity=".35"/>
<rect x="140" y="430" width="360" height="12" rx="6" fill="{accent}" opacity=".4"/>"""
    if weekday == 3:  # سه‌شنبه — سرمه
        return f"""
<circle cx="430" cy="180" r="46" fill="{light}"/>
<path d="M180 250 A150 150 0 1 0 470 420" fill="none" stroke="{accent}" stroke-width="22" stroke-linecap="round"/>
<path d="M210 290 A120 120 0 1 0 450 400" fill="none" stroke="{light}" stroke-width="10" opacity=".7"/>
<circle cx="200" cy="430" r="18" fill="{light}" opacity=".7"/>
<circle cx="250" cy="470" r="10" fill="{accent}"/>"""
    if weekday == 4:  # چهارشنبه — چوب
        return f"""
<ellipse cx="320" cy="300" rx="170" ry="170" fill="none" stroke="{light}" stroke-width="18"/>
<ellipse cx="320" cy="300" rx="126" ry="126" fill="none" stroke="{accent}" stroke-width="14"/>
<ellipse cx="320" cy="300" rx="84" ry="84" fill="none" stroke="{light}" stroke-width="10"/>
<ellipse cx="320" cy="300" rx="44" ry="44" fill="{accent}"/>
<rect x="312" y="140" width="16" height="320" rx="8" fill="{dark}" opacity=".25"/>"""
    if weekday == 5:  # پنج‌شنبه — پرتقال
        return f"""
<circle cx="320" cy="278" r="128" fill="{accent}"/>
<circle cx="320" cy="278" r="92" fill="{light}" opacity=".35"/>
<path d="M320 150 L320 406 M192 278 L448 278 M228 186 L412 370 M412 186 L228 370" stroke="{dark}" stroke-width="10" opacity=".35"/>
<circle cx="320" cy="278" r="18" fill="{dark}"/>"""
    return f"""
<path d="M320 150 L470 250 L420 430 L220 430 L170 250 Z" fill="{accent}" opacity=".85"/>
<path d="M320 190 L430 265 L395 395 L245 395 L210 265 Z" fill="{light}" opacity=".35"/>
<rect x="250" y="300" width="140" height="90" rx="12" fill="{dark}" opacity=".28"/>
<path d="M250 300 Q320 230 390 300" fill="none" stroke="{light}" stroke-width="10"/>"""


def weekday_cover_png_bytes(palette: dict[str, Any], *, size: int = 900) -> BytesIO:
    path = weekday_cover_image_path(palette)
    if path is not None and path.suffix.lower() != ".svg":
        return BytesIO(path.read_bytes())
    from PIL import Image, ImageDraw, ImageFont

    dark = _rgb(palette.get("dark", "#783B31"))
    main = _rgb(palette.get("main", "#B85C4A"))
    accent = _rgb(palette.get("accent", "#D58A79"))
    light = _rgb(palette.get("light", "#EBC4BA"))
    ground = _rgb(palette.get("ground", "#FAF3F1"))
    image = Image.new("RGB", (size, size), dark)
    draw = ImageDraw.Draw(image)
    for index in range(size):
        color = _blend(dark, main, (index / max(size - 1, 1)) * 0.72)
        draw.line([(0, index), (size, index)], fill=color)
    draw.ellipse((int(size * 0.42), -int(size * 0.12), int(size * 1.08), int(size * 0.52)), fill=accent)
    weekday = int(palette.get("weekday") or 0)
    _draw_motif(draw, weekday, size, dark, accent, light)
    inset = int(size * 0.06)
    draw.rounded_rectangle((inset, inset, size - inset, size - inset), radius=int(size * 0.05), outline=light, width=3)
    try:
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", int(size * 0.07))
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", int(size * 0.035))
    except OSError:
        font_large = ImageFont.load_default()
        font_small = font_large
    name = to_persian_digits(palette.get("name") or "")
    label = to_persian_digits(palette.get("label") or "")
    draw.text((size / 2, size * 0.84), name, fill=ground, font=font_large, anchor="mm")
    draw.text((size / 2, size * 0.91), label, fill=light, font=font_small, anchor="mm")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def _draw_motif(draw, weekday: int, size: int, dark, accent, light) -> None:
    if weekday == 0:
        draw.ellipse((int(size * 0.58), int(size * 0.12), int(size * 0.86), int(size * 0.4)), fill=light)
        draw.polygon(
            [(int(size * 0.08), int(size * 0.72)), (int(size * 0.42), int(size * 0.46)), (size, int(size * 0.58)), (size, int(size * 0.9)), (int(size * 0.08), int(size * 0.9))],
            fill=accent,
        )
    elif weekday == 1:
        draw.ellipse((int(size * 0.18), int(size * 0.22), int(size * 0.52), int(size * 0.56)), fill=light)
        draw.ellipse((int(size * 0.34), int(size * 0.18), int(size * 0.66), int(size * 0.5)), fill=accent)
    elif weekday == 2:
        draw.ellipse((int(size * 0.12), int(size * 0.28), int(size * 0.58), int(size * 0.5)), fill=light)
        draw.ellipse((int(size * 0.34), int(size * 0.22), int(size * 0.86), int(size * 0.48)), fill=accent)
    elif weekday == 3:
        draw.arc((int(size * 0.18), int(size * 0.22), int(size * 0.82), int(size * 0.78)), 30, 300, fill=accent, width=18)
        draw.ellipse((int(size * 0.62), int(size * 0.16), int(size * 0.76), int(size * 0.3)), fill=light)
    elif weekday == 4:
        cx = cy = size // 2
        for radius, color, width in ((int(size * 0.28), light, 14), (int(size * 0.2), accent, 10), (int(size * 0.12), light, 8)):
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=color, width=width)
    elif weekday == 5:
        draw.ellipse((int(size * 0.28), int(size * 0.18), int(size * 0.72), int(size * 0.62)), fill=accent)
        draw.ellipse((int(size * 0.47), int(size * 0.37), int(size * 0.53), int(size * 0.43)), fill=dark)
    else:
        draw.polygon(
            [
                (size * 0.5, size * 0.18),
                (size * 0.78, size * 0.36),
                (size * 0.68, size * 0.68),
                (size * 0.32, size * 0.68),
                (size * 0.22, size * 0.36),
            ],
            fill=accent,
        )


def _rgb(value: str) -> tuple[int, int, int]:
    text = str(value or "#20252B").lstrip("#")
    if len(text) != 6:
        text = "20252B"
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def _blend(left: tuple[int, int, int], right: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    amount = max(0.0, min(1.0, amount))
    return tuple(int(a + (b - a) * amount) for a, b in zip(left, right))

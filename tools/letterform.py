"""Очертание буквы как огибающая звука.

Буква рисуется шрифтом, затем для каждой колонки пикселей берутся верхний и нижний
край краски относительно оптического центра строчных. Эти два контура и становятся
верхней и нижней границей звуковой волны: волна буквально обводит букву.

Запуск напрямую печатает ASCII-превью всех букв — так видно, читается ли результат.
"""
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    ("/System/Library/Fonts/Supplemental/Futura.ttc", 1),      # Futura Medium
    ("/System/Library/Fonts/HelveticaNeue.ttc", 2),            # Neue Bold
    ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
]
EM = 220              # кегль для растеризации
FLOOR = 0.02          # тонкая линия вместо тишины вне краски
GAMMA = 0.75          # поджимает разницу громкостей, сохраняя перевес выносных
_cache = {}


def _font():
    if "font" not in _cache:
        last = None
        for path, index in FONT_CANDIDATES:
            try:
                _cache["font"] = ImageFont.truetype(path, EM, index=index)
                _cache["name"] = f"{path.split('/')[-1]}#{index}"
                return _cache["font"]
            except Exception as exc:                            # нет шрифта — пробуем следующий
                last = exc
        raise SystemExit(f"не нашёл шрифт для отрисовки букв: {last}")
    return _cache["font"]


def _metrics():
    """Оптический центр строчных и общий вертикальный размах — одни для всего алфавита."""
    if "metrics" in _cache:
        return _cache["metrics"]
    font = _font()
    ink = {}
    for letter in "abcdefghijklmnopqrstuvwxyz":
        img = Image.new("L", (EM * 2, EM * 3), 0)
        ImageDraw.Draw(img).text((EM // 2, EM), letter, font=font, fill=255)
        box = img.getbbox()
        ink[letter] = box
    baseline = max(b[3] for b in (ink["n"], ink["o"], ink["x"]))   # низ строчных без выносов
    x_top = min(b[1] for b in (ink["n"], ink["o"], ink["x"]))
    center = (baseline + x_top) / 2
    above = max(center - b[1] for b in ink.values())
    below = max(b[3] - center for b in ink.values())
    span = max(above, below)
    widths = {l: b[2] - b[0] for l, b in ink.items()}
    _cache["metrics"] = (center, span, widths)
    return _cache["metrics"]


def contours(letter, n):
    """Верхний и нижний контур буквы, растянутые на n отсчётов. Значения 0…1."""
    font = _font()
    center, span, widths = _metrics()
    img = Image.new("L", (EM * 2, EM * 3), 0)
    ImageDraw.Draw(img).text((EM // 2, EM), letter, font=font, fill=255)
    box = img.getbbox()
    a = np.array(img.crop((box[0], 0, box[2], img.height)), dtype=np.float64) / 255

    rows = np.arange(a.shape[0])[:, None]
    inked = a > 0.5
    has = inked.any(axis=0)
    top = np.where(has, np.where(inked, rows, a.shape[0]).min(axis=0), center)
    bottom = np.where(has, np.where(inked, rows, -1).max(axis=0), center)

    up = np.clip((center - top) / span, FLOOR, 1) ** GAMMA
    dn = np.clip((bottom - center) / span, FLOOR, 1) ** GAMMA
    up[~has] = FLOOR ** GAMMA
    dn[~has] = FLOOR ** GAMMA

    src = np.linspace(0, len(up) - 1, n)
    grid = np.arange(len(up))
    return np.interp(src, grid, up), np.interp(src, grid, dn), widths[letter] / EM


def width_ratio(letter):
    _, _, widths = _metrics()
    return widths[letter] / EM


def preview(letter, cols=56, rows=17):
    up, dn, w = contours(letter, cols)
    mid = rows // 2
    grid = [[" "] * cols for _ in range(rows)]
    for x in range(cols):
        hi = mid - int(round(up[x] * mid))
        lo = mid + int(round(dn[x] * mid))
        for y in range(hi, lo + 1):
            grid[max(0, min(rows - 1, y))][x] = "█"
    return f"{letter}  ширина {w:.2f}em\n" + "\n".join("".join(r) for r in grid)


if __name__ == "__main__":
    _font()
    print(f"шрифт: {_cache['name']}")
    center, span, widths = _metrics()
    print(f"центр {center:.0f}px, размах ±{span:.0f}px, ширины "
          f"{min(widths.values()) / EM:.2f}…{max(widths.values()) / EM:.2f}em\n")
    for letter in "abdeimopqtxz":
        print(preview(letter), "\n")

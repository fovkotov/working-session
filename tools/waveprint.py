"""Печатает в терминал форму волны готовых глифов — проверка, что буква читается в звуке.

  python3 tools/waveprint.py [гарнитура] [буквы]
"""
import pathlib
import sys
import wave

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONT = sys.argv[1] if len(sys.argv) > 1 else "rumble"
LETTERS = sys.argv[2] if len(sys.argv) > 2 else "abdgiklmopqtwz"
SAMPLES = ROOT / "web" / "fonts" / FONT / "samples"
ROWS = 19


def read(path):
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64) / 32768


def hull(x, cols):
    chunks = np.array_split(x, cols)
    up = np.array([max(c.max(), 0) for c in chunks])
    dn = np.array([max(-c.min(), 0) for c in chunks])
    scale = max(up.max(), dn.max())
    return up / scale, dn / scale


for letter in LETTERS:
    path = SAMPLES / f"{letter}.wav"
    if not path.exists():
        continue
    x = read(path)
    cols = max(18, int(round(len(x) / 48000 * 190)))   # ширина ∝ длительности глифа
    up, dn = hull(x, cols)
    mid = ROWS // 2
    grid = [[" "] * cols for _ in range(ROWS)]
    for i in range(cols):
        for y in range(mid - int(round(up[i] * mid)), mid + int(round(dn[i] * mid)) + 1):
            grid[max(0, min(ROWS - 1, y))][i] = "█"
    print(f"\n{letter}   {len(x) / 48000 * 1000:.0f} ms")
    print("\n".join("".join(row) for row in grid))

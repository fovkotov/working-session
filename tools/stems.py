"""Подготовить записи как дорожки для Ableton: выровнять громкость и убрать щелчки.

Исходники сняты на телефон с разбросом уровня в 9 dB, поэтому в сете они
складывались бы в кашу. Здесь каждая запись приводится к одной громкости по RMS
(не по пику — пик у гула случайный), с фейдами на концах. Эффекты не трогаем:
их добавляет пользователь уже в Live.

usage:
  python3 tools/stems.py raw/rec91.wav raw/rec92.wav ...
"""
import argparse
import pathlib
import tempfile

import numpy as np

import extract as E

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "ableton" / "samples"
TARGET_RMS = 0.075    # примерно -22 dBFS: оставляем запас под эффекты
PEAK = 0.89
FADE = 0.035


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="+")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        for name in args.src:
            path = pathlib.Path(name)
            x = E.decode(path, pathlib.Path(td))
            rms = np.sqrt((x ** 2).mean())
            x = x * (TARGET_RMS / (rms + 1e-9))
            # редкие выбросы гула мягко прижимаем: tanh даёт единичное усиление
            # на тихом и упирается в потолок на громком
            if np.abs(x).max() > PEAK:
                x = PEAK * np.tanh(x / PEAK)
            n = int(FADE * E.SR)
            x[:n] *= np.linspace(0, 1, n)
            x[-n:] *= np.linspace(1, 0, n)
            dst = OUT / f"tex-{path.stem.replace('rec', '')}.wav"
            E.write_wav(dst, x)
            print(f"  {dst.name}  {len(x)/E.SR:6.2f}s  rms {rms:.4f} -> {TARGET_RMS}"
                  f"  peak {np.abs(x).max():.2f}")

    print(f"\n-> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

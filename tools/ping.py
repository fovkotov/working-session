"""Вытащить из записи настоящие удары-пинги — ритмическую основу трека.

Удар лежит под стационарным гулом комнаты: в полосе 5–16 кГц он выше фона на
20 дБ, а в низу — всего на 3–9 дБ. Поэтому ни один простой фильтр не годится.
Срежешь низ от 700 Гц — останется тонкий писк без корпуса; не срежешь ничего —
слайс превратится в 300 мс гула, в котором удара не слышно.

Здесь гул убирается спектральным вычитанием: фон оценивается по тихому
перцентилю каждого бина (он стационарный), а на слайсе гасятся те бины, где
сигнал не поднялся над фоном. Остаётся удар со своим спектром — и щелчок, и
низкий корпус, — но без гудящего ковра под ним.

usage:
  python3 tools/ping.py --src raw/ping.wav
"""
import argparse
import pathlib
import tempfile

import numpy as np
from scipy.signal import butter, sosfilt

import extract as E

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "ableton" / "samples"
BAND = (1200, 12000)   # в этой полосе удар виден над гулом
RUMBLE = 120           # ниже — только инфранизкий бубнёж микрофона
PRE = 0.015            # захватываем атаку целиком
TAIL = 0.18            # сам удар звенит ~50 мс, остальное — воздух вокруг него
RELEASE = 0.09         # последняя часть хвоста уходит плавно
MIN_GAP = 0.25
PEAK = 0.82
DENOISE = 1.0          # во сколько раз вычитаем фон (1 = ровно по оценке)
FLOOR = 0.25           # четверть фона оставляем: с ней удар звучит как в комнате
N = 1024
HOP = N // 4


def highpass(x, hz, order=2):
    return sosfilt(butter(order, hz / (E.SR / 2), btype="high", output="sos"), x)


def frames(x):
    count = 1 + max(0, len(x) - N) // HOP
    idx = np.arange(N)[None, :] + HOP * np.arange(count)[:, None]
    return np.fft.rfft(x[idx] * np.hanning(N), axis=1)


def unframe(X, length):
    win = np.hanning(N)
    out = np.zeros(length + N)
    norm = np.zeros(length + N) + 1e-9
    blocks = np.fft.irfft(X, axis=1) * win
    for k, block in enumerate(blocks):
        out[k * HOP: k * HOP + N] += block
        norm[k * HOP: k * HOP + N] += win ** 2
    return (out / norm)[:length]


def bed_spectrum(x):
    """Стационарный фон записи — тихий перцентиль каждого бина."""
    return np.percentile(np.abs(frames(x)), 20, axis=0)


def denoise(g, bed):
    X = frames(g)
    mag = np.abs(X)
    gain = np.clip(1.0 - DENOISE * bed[None, :] / (mag + 1e-9), FLOOR, 1.0)
    return unframe(X * gain, len(g))


def detect(x, floor_db=9.0):
    """Кадры, где верхняя полоса резко поднимается над своим же фоном."""
    S = E.stft(x)
    freqs = np.fft.rfftfreq(E.N_FFT, 1 / E.SR)
    e = S[:, (freqs >= BAND[0]) & (freqs < BAND[1])].mean(1)
    step = E.FFT_HOP / E.SR
    db = 20 * np.log10(e / np.percentile(e, 20) + 1e-9)
    hits, i = [], 1
    while i < len(db) - 1:
        if db[i] > floor_db and db[i] >= db[i - 1] and db[i] >= db[i + 1]:
            hits.append(dict(t=i * step, db=float(db[i])))
            i += int(MIN_GAP / step)
        else:
            i += 1
    return hits


def cut(x, hit, bed):
    i0 = max(0, int((hit["t"] - PRE) * E.SR))
    g = x[i0:i0 + int((PRE + TAIL) * E.SR)].copy()
    if len(g) < int(0.1 * E.SR):
        return None
    g = highpass(denoise(g, bed), RUMBLE)
    n_in = int(0.004 * E.SR)
    g[:n_in] *= np.linspace(0, 1, n_in)
    rel = min(len(g) - n_in, int(RELEASE * E.SR))
    # косинусный спад: хвост уходит в тишину, но не схлопывается рывком
    g[-rel:] *= (np.cos(np.linspace(0, np.pi, rel)) + 1) / 2
    peak = np.abs(g).max()
    return g / peak * PEAK if peak > 1e-6 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="raw/ping.wav")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        x = E.decode(pathlib.Path(args.src), pathlib.Path(td))

    hits = detect(x)
    bed = bed_spectrum(x)
    print(f"ударов в записи: {len(hits)}  (длина {len(x)/E.SR:.2f}s)")
    good = []
    for h in sorted(hits, key=lambda h: -h["db"]):
        g = cut(x, h, bed)
        if g is None:
            print(f"  {h['t']:6.3f}s  +{h['db']:4.1f}dB  — у края файла, пропускаю")
            continue
        good.append((h, g))

    for n, (h, g) in enumerate(good[:3], 1):
        E.write_wav(OUT / f"ping-hit-{n}.wav", g)
        S = np.abs(np.fft.rfft(g[: 4096] * np.hanning(min(4096, len(g)))))
        freqs = np.fft.rfftfreq(min(4096, len(g)) * 2 - 2, 1 / E.SR)[: len(S)]
        cen = (freqs * S).sum() / S.sum()
        print(f"  ping-hit-{n}.wav  из {h['t']:.3f}s  +{h['db']:4.1f}dB  "
              f"{len(g)/E.SR*1000:3.0f} ms  центроид {cen:5.0f} Hz")

    print(f"\n-> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

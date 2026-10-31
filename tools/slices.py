"""Нарезать из записей короткие удары для ритма и собрать шумовую подложку.

Удары ищутся по широкополосному росту энергии и чистятся тем же спектральным
вычитанием, что и пинг (tools/ping.py): иначе каждый слайс тащит за собой
гудящий ковёр комнаты и в ритме слышно только «бум-бум».

Подложка — наоборот, сам этот гул, но ровный. Петлять живой кусок нельзя: в
нём свои всплески и провалы, и на повторе слышно пульсацию. Поэтому берётся
усреднённый спектр комнаты из всех записей и по нему синтезируется шум —
тот же тембр помещения, но без событий. Длина 20.000 с, ровно 8 тактов на
96 BPM, так что Live не станет её варпить.

usage:
  python3 tools/slices.py
"""
import pathlib
import tempfile

import numpy as np
from scipy.signal import butter, sosfilt

import extract as E
import ping as P

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "ableton" / "samples"
PEAK = 0.8

# что берём из каждой записи: (файл, сколько ударов, длина слайса, срез низа)
HITS = [
    ("raw/rec92.wav", 2, 0.26, 180),   # сухие щелчки — верхний слой ритма
    ("raw/rec94.wav", 2, 0.30, 90),    # низкие толчки — на сильные доли
    ("raw/rec91.wav", 1, 0.24, 140),   # шипящий тик — подголосок
]
BED_SRC = ["raw/rec91.wav", "raw/rec93.wav", "raw/rec94.wav"]
BED_LEN = 20.0                         # 8 тактов на 96 BPM
BED_TOP = 2500                         # выше — только шипение микрофона


def lowpass(x, hz, order=4):
    return sosfilt(butter(order, hz / (E.SR / 2), btype="low", output="sos"), x)


def onsets(x, count, gap=0.35):
    """Самые заметные широкополосные атаки записи."""
    S = E.stft(x)
    step = E.FFT_HOP / E.SR
    e = S.mean(1)
    prev = np.convolve(e, np.ones(12) / 12, mode="same")
    rise = 20 * np.log10(e / (prev + 1e-9) + 1e-9)
    order = np.argsort(-rise)
    picked = []
    for i in order:
        t = i * step
        if t < 0.1 or t > len(x) / E.SR - 0.35:
            continue
        if all(abs(t - p) > gap for p in picked):
            picked.append(t)
        if len(picked) == count:
            break
    return picked


def cut(x, t, bed, dur, hp):
    i0 = max(0, int((t - 0.012) * E.SR))
    g = x[i0:i0 + int(dur * E.SR)].copy()
    g = sosfilt(butter(2, hp / (E.SR / 2), btype="high", output="sos"),
                P.denoise(g, bed))
    n = int(0.004 * E.SR)
    g[:n] *= np.linspace(0, 1, n)
    rel = int(min(len(g) - n, 0.6 * len(g)))
    g[-rel:] *= (np.cos(np.linspace(0, np.pi, rel)) + 1) / 2
    peak = np.abs(g).max()
    return g / peak * PEAK if peak > 1e-6 else None


def make_bed(tmp):
    """Ровный room tone: шум, которому навязан усреднённый спектр комнаты."""
    target = np.mean([P.bed_spectrum(E.decode(ROOT / s, tmp)) for s in BED_SRC], axis=0)
    want = int(BED_LEN * E.SR)
    noise = np.random.default_rng(11).standard_normal(want + P.N)
    X = P.frames(noise)
    # фаза остаётся шумовой, модуль берём от комнаты — тембр тот же, событий нет
    X = target[None, :] * np.exp(1j * np.angle(X))
    out = lowpass(P.unframe(X, len(noise)), BED_TOP)[:want]
    fade = int(0.4 * E.SR)
    out[:fade] *= np.linspace(0, 1, fade)
    out[-fade:] *= np.linspace(1, 0, fade)
    peak = np.abs(out).max()
    return out / peak * 0.6 if peak > 1e-6 else out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        for src, count, dur, hp in HITS:
            path = ROOT / src
            x = E.decode(path, tmp)
            bed = P.bed_spectrum(x)
            tag = path.stem.replace("rec", "")
            for n, t in enumerate(onsets(x, count), 1):
                g = cut(x, t, bed, dur, hp)
                if g is None:
                    continue
                name = f"hit-{tag}-{n}.wav"
                E.write_wav(OUT / name, g)
                S = np.abs(np.fft.rfft(g * np.hanning(len(g))))
                f = np.fft.rfftfreq(len(g), 1 / E.SR)
                print(f"  {name:<14} из {src} @{t:6.3f}s  "
                      f"{len(g)/E.SR*1000:3.0f} ms  центроид {(f*S).sum()/S.sum():5.0f} Hz")

        bed = make_bed(tmp)
        E.write_wav(OUT / "bed-noise.wav", bed)
        rms = np.sqrt((bed ** 2).mean())
        print(f"  bed-noise.wav  {len(bed)/E.SR:6.3f}s  RMS {20*np.log10(rms):.1f} dBFS "
              f"(в миксе ещё тише)")

    print(f"\n-> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

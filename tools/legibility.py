"""Насколько буквы шрифта различимы между собой: матрица тембровых расстояний."""
import pathlib
import sys
import wave

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONT = sys.argv[1] if len(sys.argv) > 1 else "rumble"
SAMPLES = ROOT / "web" / "fonts" / FONT / "samples"
N_FFT, HOP, SR, BANDS = 1024, 256, 48000, 24


def mel_fb():
    freqs = np.fft.rfftfreq(N_FFT, 1 / SR)
    to_mel = lambda f: 2595 * np.log10(1 + f / 700)
    pts = np.linspace(to_mel(40), to_mel(SR / 2 * 0.9), BANDS + 2)
    hz = 700 * (10 ** (pts / 2595) - 1)
    fb = np.zeros((BANDS, len(freqs)))
    for b in range(BANDS):
        l, c, r = hz[b], hz[b + 1], hz[b + 2]
        up, dn = (freqs >= l) & (freqs <= c), (freqs >= c) & (freqs <= r)
        fb[b, up] = (freqs[up] - l) / max(c - l, 1e-6)
        fb[b, dn] = (r - freqs[dn]) / max(r - c, 1e-6)
    return fb


def profile(path):
    with wave.open(str(path)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64) / 32768
    count = 1 + (len(x) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(count)[:, None]
    S = np.abs(np.fft.rfft(x[idx] * np.hanning(N_FFT), axis=1)) + 1e-9
    mel = np.log(S @ mel_fb().T)
    v = mel.mean(axis=0)
    return v - v.mean()


def envelope_vector(path, n=48):
    """Форма буквы, измеренная из волны: верхний и нижний контур плюс длительность."""
    with wave.open(str(path)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64) / 32768
    chunks = np.array_split(x, n)
    up = np.array([max(c.max(), 0) for c in chunks])
    dn = np.array([max(-c.min(), 0) for c in chunks])
    return np.concatenate([up, dn, [len(x) / 48000 * 6]])


letters = sorted(p.stem for p in SAMPLES.glob("*.wav"))
V = np.array([profile(SAMPLES / f"{l}.wav") for l in letters])
spectral = np.sqrt(((V[:, None, :] - V[None]) ** 2).sum(-1) / BANDS)

E = np.array([envelope_vector(SAMPLES / f"{l}.wav") for l in letters])
shape = np.sqrt(((E[:, None, :] - E[None]) ** 2).mean(-1))
off = np.triu_indices(len(letters), 1)
shape *= spectral[off].mean() / shape[off].mean()

D = np.sqrt(spectral ** 2 + (0.5 * shape) ** 2)
for m in (spectral, shape, D):
    np.fill_diagonal(m, np.inf)

print(f"гарнитура {FONT}: {len(letters)} глифов")
for name, m in (("спектр", spectral), ("форма", shape), ("вместе", D)):
    print(f"  {name:7s} среднее {m[np.isfinite(m)].mean():.3f}  минимум {m.min():.3f}")

pairs = sorted(((D[i, j], letters[i], letters[j])
                for i in range(len(letters)) for j in range(i + 1, len(letters))))
print("\nсамые похожие пары (риск спутать на слух):")
for d, a, b in pairs[:8]:
    print(f"  {a}–{b}  {d:.3f}")
print("\nсамые контрастные пары:")
for d, a, b in pairs[-5:]:
    print(f"  {a}–{b}  {d:.3f}")

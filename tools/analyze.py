"""Scan raw recordings: envelope, onsets, spectral stats. Prints a report only."""
import sys, wave, pathlib
import numpy as np

RAW = pathlib.Path(__file__).resolve().parent.parent / "raw"


def read_wav(path):
    with wave.open(str(path)) as w:
        sr = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return data.astype(np.float32) / 32768.0, sr


def frames(x, n, hop):
    count = 1 + (len(x) - n) // hop
    idx = np.arange(n)[None, :] + hop * np.arange(count)[:, None]
    return x[idx]


def report(path):
    x, sr = read_wav(path)
    n, hop = 1024, 256
    fr = frames(x, n, hop)
    rms = np.sqrt((fr ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    win = np.hanning(n)
    spec = np.abs(np.fft.rfft(fr * win, axis=1))
    freqs = np.fft.rfftfreq(n, 1 / sr)
    centroid = (spec * freqs).sum(axis=1) / (spec.sum(axis=1) + 1e-9)
    t = np.arange(len(rms)) * hop / sr
    print(f"\n### {path.name}  dur={len(x)/sr:.2f}s")
    print(f"  peak={20*np.log10(np.abs(x).max()):.1f}dB  noisefloor={np.percentile(db,10):.1f}dB  loud95={np.percentile(db,95):.1f}dB")
    # coarse envelope sketch, 40 buckets
    buckets = np.array_split(db, 40)
    cb = np.array_split(centroid, 40)
    chars = " .:-=+*#%@"
    line = "".join(chars[min(9, max(0, int((b.max() + 60) / 6)))] for b in buckets)
    print("  env |" + line + "|")
    print("  cen |" + "".join(chars[min(9, int(c.mean() / 1200))] for c in cb) + "|")
    # onsets: rms rises above gate
    gate = np.percentile(db, 10) + 12
    above = db > gate
    events = []
    i = 0
    while i < len(above):
        if above[i]:
            j = i
            while j < len(above) and (above[j] or (db[j] > gate - 6)):
                j += 1
            if (j - i) * hop / sr > 0.03:
                events.append((t[i], t[min(j, len(t) - 1)], db[i:j].max(), centroid[i:j].mean()))
            i = j
        else:
            i += 1
    print(f"  gate={gate:.1f}dB  events={len(events)}")
    for k, (a, b, pk, c) in enumerate(events[:60]):
        print(f"    {k:3d} {a:6.3f}–{b:6.3f} ({b-a:5.3f}s) pk={pk:6.1f} cen={c:6.0f}Hz")


for p in sorted(RAW.glob("*.wav")):
    report(p)

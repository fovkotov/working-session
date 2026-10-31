"""Cut field recordings into 26 maximally distinct grains — the letters of a sound typeface.

candidate grains -> timbre features -> k-means (k=26) -> medoid per cluster
-> roles (tonal grains become vowels) -> trimmed, loudness-matched WAV + font.json

usage:
  python3 tools/extract.py --in <folder with audio> --id rumble --name "RUMBLE" [--about "..."]
"""
import argparse
import json
import pathlib
import subprocess
import tempfile
import wave

import numpy as np

import letterform as letter_form

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONTS = ROOT / "web" / "fonts"
SR = 48000
GRAIN = 0.30          # candidate grain length, seconds
HOP = 0.05
VOWEL_LEN = 0.26      # базовая длина гласной, дальше правится шириной буквы
CONSONANT_LEN = 0.17
MEAN_WIDTH = 0.45     # средняя ширина строчной в em
N_FFT = 1024
FFT_HOP = 256
MEL_BANDS = 18
PEAK_CEILING = 0.93   # потолок для самой громкой буквы алфавита
VOWELS = "aeiou"
ALPHABET = "abcdefghijklmnopqrstuvwxyz"

# Второе измерение различимости: материал однородный, поэтому только тембра мало.
# Гласные расставлены по высоте от тёмной u к светлой i, высоты согласных
# подбираются так, чтобы спектрально похожие буквы разъехались по высоте.
VOWEL_PITCH = {"u": -7, "o": -3, "a": 0, "e": 3, "i": 7}
PITCH_RANGE = range(-7, 8)
PITCH_REUSE = 2       # сколько букв максимум могут делить одну высоту
PITCH_WEIGHT = 0.085  # вклад одного полутона в «различимость» на фоне тембра
AUDIO_EXT = {".wav", ".m4a", ".mp3", ".aif", ".aiff", ".flac", ".ogg", ".caf", ".mp4"}
rng = np.random.default_rng(7)


def decode(path, tmp):
    """Any input format -> mono float64 at SR."""
    if path.suffix.lower() != ".wav":
        dst = tmp / (path.stem + ".wav")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(path), "-ac", "1",
             "-ar", str(SR), "-c:a", "pcm_s16le", str(dst)], check=True)
        path = dst
    with wave.open(str(path)) as w:
        if w.getframerate() != SR or w.getnchannels() != 1:
            dst = tmp / (path.stem + f".{SR}.wav")
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-i", str(path), "-ac", "1",
                 "-ar", str(SR), "-c:a", "pcm_s16le", str(dst)], check=True)
            return decode(dst, tmp)
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return data.astype(np.float64) / 32768.0


def write_wav(path, x):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def envelope(x, n=256):
    x = x[: len(x) // n * n]
    return np.sqrt((x.reshape(-1, n) ** 2).mean(axis=1) + 1e-12)


def stft(x):
    count = 1 + max(0, len(x) - N_FFT) // FFT_HOP
    idx = np.arange(N_FFT)[None, :] + FFT_HOP * np.arange(count)[:, None]
    return np.abs(np.fft.rfft(x[idx] * np.hanning(N_FFT), axis=1)) + 1e-9


_FB = None


def mel_filterbank():
    global _FB
    if _FB is None:
        freqs = np.fft.rfftfreq(N_FFT, 1 / SR)
        to_mel = lambda f: 2595 * np.log10(1 + f / 700)
        pts = np.linspace(to_mel(40), to_mel(SR / 2 * 0.9), MEL_BANDS + 2)
        hz = 700 * (10 ** (pts / 2595) - 1)
        fb = np.zeros((MEL_BANDS, len(freqs)))
        for b in range(MEL_BANDS):
            l, c, r = hz[b], hz[b + 1], hz[b + 2]
            up = (freqs >= l) & (freqs <= c)
            dn = (freqs >= c) & (freqs <= r)
            fb[b, up] = (freqs[up] - l) / max(c - l, 1e-6)
            fb[b, dn] = (r - freqs[dn]) / max(r - c, 1e-6)
        _FB = fb
    return _FB


def features(g):
    S = stft(g)
    freqs = np.fft.rfftfreq(N_FFT, 1 / SR)
    mel = np.log(S @ mel_filterbank().T + 1e-9)
    shape = mel.mean(axis=0)
    shape = shape - shape.mean()                                  # loudness-invariant spectrum
    avg = S.mean(0)
    cen = float((avg * freqs).sum() / avg.sum())
    flat = float(np.exp(np.log(avg).mean()) / avg.mean())         # 0 tonal .. 1 noisy
    env = envelope(g)
    slope = float(np.polyfit(np.arange(len(env)), 20 * np.log10(env), 1)[0])
    zcr = float((np.diff(np.sign(g)) != 0).mean())
    vec = np.concatenate([shape, [np.log(cen), flat * 6, slope, zcr * 10, mel.std(axis=0).mean()]])
    return vec, dict(centroid=cen, flatness=flat, slope=slope, zcr=zcr,
                     rms=float(np.sqrt((g ** 2).mean())))


def kmeans(X, k, iters=160, restarts=14):
    best = None
    for _ in range(restarts):
        c = X[rng.choice(len(X), k, replace=False)].copy()
        for _ in range(iters):
            lab = ((X[:, None, :] - c[None]) ** 2).sum(-1).argmin(1)
            for j in range(k):
                m = lab == j
                c[j] = X[m].mean(0) if m.any() else X[rng.integers(len(X))]
        inertia = ((X - c[lab]) ** 2).sum()
        if best is None or inertia < best[0]:
            best = (inertia, lab.copy(), c.copy())
    return best[1], best[2]


def harvest(src_dir, tmp):
    """All loud-enough candidate grains from every recording in the folder."""
    files = sorted(p for p in src_dir.iterdir() if p.suffix.lower() in AUDIO_EXT)
    if not files:
        raise SystemExit(f"no audio in {src_dir}")
    audio, grains, meta, sources = {}, [], [], []
    glen, ghop = int(GRAIN * SR), int(HOP * SR)
    for path in files:
        x = decode(path, tmp)
        audio[path.stem] = x
        sources.append(dict(file=path.name, dur=round(len(x) / SR, 2)))
        gate = np.percentile(20 * np.log10(envelope(x)), 35)
        for start in range(0, max(1, len(x) - glen), ghop):
            g = x[start : start + glen]
            if len(g) < glen or 20 * np.log10(np.sqrt((g ** 2).mean()) + 1e-12) < gate:
                continue
            grains.append(g)
            meta.append(dict(src=path.stem, t=start / SR, i0=start))
    return audio, grains, meta, sources


def hull(values):
    """Огибающая для отрисовки: лёгкое сглаживание убирает шумовую дрожь контура."""
    v = np.array(values, dtype=float)
    k = np.array([0.25, 0.5, 0.25])
    v = np.convolve(np.pad(v, (1, 1), mode="edge"), k, mode="same")[1 : 1 + len(v)]
    return [round(float(x), 3) for x in v]


def glyph_length(letter, is_vowel):
    """Длительность буквы пропорциональна её ширине — шрифт получается пропорциональным."""
    base = VOWEL_LEN if is_vowel else CONSONANT_LEN
    factor = np.clip((letter_form.width_ratio(letter) / MEAN_WIDTH) ** 0.6, 0.65, 1.5)
    return int(base * factor * SR)


def smooth(v, ms):
    """Сглаживание огибающей: ступеньки контура дали бы щелчки."""
    n = max(1, int(ms / 1000 * SR))
    k = np.ones(n) / n
    return np.convolve(np.pad(v, (n, n), mode="edge"), k, mode="same")[n : n + len(v)]


def flatten(g, ms=2):
    """Снять собственную огибающую гранулы, чтобы её место занял контур буквы.

    Делим на сглаженную пиковую огибающую: пики выравниваются по единице, тембр
    и спектр при этом не трогаются (в отличие от ограничителя).
    """
    n = max(1, int(ms / 1000 * SR))
    blocks = max(1, len(g) // n)
    peaks = np.array([np.abs(g[i * n : (i + 1) * n]).max() for i in range(blocks)])
    env = np.interp(np.arange(len(g)), (np.arange(blocks) + 0.5) * n, peaks)
    env = smooth(env, ms * 1.5)
    g = g / np.maximum(env, 0.25 * env.max() + 1e-9)
    return np.tanh(1.25 * g) / np.tanh(1.25)   # лёгкое ограничение: контур держится ровнее


def shape_glyph(x, start, letter, is_vowel, semitones):
    """Вырезать букву на своей высоте и обвести волну контуром самой буквы."""
    keep = glyph_length(letter, is_vowel)
    rate = 2 ** (semitones / 12)
    if not is_vowel:
        # согласная начинается с пика громкости — так слышна атака
        head = int(envelope(x[start : start + int(GRAIN * SR)]).argmax()) * 256
        start = max(0, start + head - 1024)
    need = int(np.ceil(keep * rate)) + 2
    seg = x[start : start + need]
    if len(seg) < need:                                  # у конца записи добираем назад
        start = max(0, len(x) - need)
        seg = x[start : start + need]
    seg = np.pad(seg, (0, max(0, need - len(seg))))
    g = np.interp(np.arange(keep) * rate, np.arange(len(seg)), seg)
    g = flatten(g - g.mean())

    # верх волны идёт по верхнему контуру буквы, низ — по нижнему
    up, dn, _ = letter_form.contours(letter, keep)
    up, dn = smooth(up, 2.5), smooth(dn, 2.5)
    g = np.where(g >= 0, g * up, g * dn)

    edge = int(0.004 * SR)
    g[:edge] *= np.linspace(0, 1, edge)
    g[-edge:] *= np.linspace(1, 0, edge)
    return g


def assign_pitches(letters, timbre, fixed):
    """Подобрать высоты так, чтобы самая похожая пара букв стала как можно различимее.

    letters — порядок букв, timbre — матрица тембровых расстояний,
    fixed — буквы с заранее заданной высотой (гласные).
    """
    free = [i for i, l in enumerate(letters) if l not in fixed]
    pool = [s for s in PITCH_RANGE for _ in range(PITCH_REUSE)]

    def score(semi):
        d = timbre + PITCH_WEIGHT * np.abs(np.subtract.outer(semi, semi))
        pairs = d[np.triu_indices(len(semi), 1)]
        order = np.sort(pairs)
        return order[0], order[:10].mean()   # сначала худшая пара, потом десять худших

    best = None
    for _ in range(40):
        semi = np.array([fixed.get(l, 0) for l in letters], dtype=float)
        picks = rng.permutation(pool)[: len(free)]
        for k, i in enumerate(free):
            semi[i] = picks[k]
        cur = score(semi)
        improved = True
        while improved:
            improved = False
            for i in free:
                for value in PITCH_RANGE:
                    if list(semi).count(value) >= PITCH_REUSE and semi[i] != value:
                        continue
                    old = semi[i]
                    semi[i] = value
                    trial = score(semi)
                    if trial > cur:
                        cur, improved = trial, True
                    else:
                        semi[i] = old
        if best is None or cur > best[0]:
            best = (cur, semi.copy())
    return {l: int(s) for l, s in zip(letters, best[1])}, best[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True, help="folder with field recordings")
    ap.add_argument("--id", required=True, help="font id, used as folder name")
    ap.add_argument("--name", required=True, help="display name of the typeface")
    ap.add_argument("--about", default="", help="one-line description of the sound source")
    args = ap.parse_args()

    out = FONTS / args.id
    (out / "samples").mkdir(parents=True, exist_ok=True)
    for f in (out / "samples").glob("*.wav"):
        f.unlink()

    with tempfile.TemporaryDirectory() as td:
        audio, grains, meta, sources = harvest(pathlib.Path(args.src).expanduser(), pathlib.Path(td))
    print(f"candidates: {len(grains)} grains from {len(sources)} recordings")

    feats, stats = zip(*(features(g) for g in grains))
    X = np.array(feats)
    X = (X - X.mean(0)) / (X.std(0) + 1e-9)
    lab, cent = kmeans(X, 26)

    picks = []
    for j in range(26):
        idx = np.where(lab == j)[0]
        if len(idx) == 0:
            continue
        medoid = int(idx[((X[idx] - cent[j]) ** 2).sum(1).argmin()])
        picks.append(dict(i=medoid, size=int(len(idx)), **stats[medoid], **meta[medoid]))
    if len(picks) < 26:
        raise SystemExit(f"only {len(picks)} distinct clusters; feed me more material")

    # the most tonal grains carry the vowels, the rest fill consonants by brightness
    picks.sort(key=lambda p: p["flatness"])
    mapping = {}
    for letter, p in zip(VOWELS, sorted(picks[:5], key=lambda p: p["centroid"])):
        mapping[letter] = p
    for letter, p in zip((c for c in ALPHABET if c not in VOWELS),
                         sorted(picks[5:], key=lambda p: p["centroid"])):
        mapping[letter] = p

    letters = list(ALPHABET)
    V = np.array([feats[mapping[l]["i"]][:MEL_BANDS] for l in letters])
    timbre = np.sqrt(((V[:, None, :] - V[None]) ** 2).sum(-1) / MEL_BANDS)

    # буква слышна не только тембром, но и своей формой: контур задаёт громкость во времени
    F = []
    for l in letters:
        up, dn, _ = letter_form.contours(l, 48)
        F.append(np.concatenate([up, dn, [glyph_length(l, l in VOWELS) / SR * 6]]))
    F = np.array(F)
    form = np.sqrt(((F[:, None, :] - F[None]) ** 2).mean(-1))
    off = np.triu_indices(len(letters), 1)
    form *= timbre[off].mean() / form[off].mean()
    pitch, (worst, worst10) = assign_pitches(
        letters, np.sqrt(timbre ** 2 + (0.5 * form) ** 2), VOWEL_PITCH)
    print(f"высоты подобраны: худшая пара {worst:.3f}, десять худших в среднем {worst10:.3f}")

    # масштаб один на весь алфавит, иначе овалы поднимутся до выносных и буквы перестанут
    # читаться в волне: выносные элементы должны звучать громче, а не выравниваться
    shaped = {l: shape_glyph(audio[mapping[l]["src"]], mapping[l]["i0"], l,
                             l in VOWELS, pitch[l]) for l in ALPHABET}
    gain = PEAK_CEILING / max(np.abs(g).max() for g in shaped.values())

    glyphs = {}
    for letter in ALPHABET:
        p = mapping[letter]
        is_vowel = letter in VOWELS
        g = shaped[letter] * gain
        write_wav(out / "samples" / f"{letter}.wav", g)
        # шаг измерения ~4.5 мс: короче — и пик начинает скакать по фазе самого гула
        chunks = np.array_split(g, int(np.clip(round(len(g) / SR / 0.0045), 24, 80)))
        glyphs[letter] = dict(
            file=f"samples/{letter}.wav",
            dur=round(len(g) / SR, 4),
            kind="vowel" if is_vowel else "consonant",
            src=p["src"],
            at=round(p["t"], 3),
            semitones=pitch[letter],
            width=round(letter_form.width_ratio(letter), 3),
            centroid=round(p["centroid"]),
            flatness=round(p["flatness"], 4),
            # измеренная огибающая волны: она же контур буквы
            up=hull([max(c.max(), 0) for c in chunks]),
            dn=hull([max(-c.min(), 0) for c in chunks]),
        )
        print(f"  {letter}  {glyphs[letter]['kind'][:4]}  {p['src']} @{p['t']:6.2f}s  "
              f"cen={p['centroid']:5.0f}Hz pitch={pitch[letter]:+d}st  "
              f"{letter_form.width_ratio(letter):.2f}em -> {len(g) / SR * 1000:3.0f}ms")

    font = dict(id=args.id, name=args.name, about=args.about, sampleRate=SR,
                sources=sources, glyphs=glyphs)
    (out / "font.json").write_text(json.dumps(font, indent=1, ensure_ascii=False))

    index_path = FONTS / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else []
    index = [e for e in index if e["id"] != args.id]
    index.append(dict(id=args.id, name=args.name, about=args.about, dir=f"fonts/{args.id}"))
    index.sort(key=lambda e: e["id"])
    index_path.write_text(json.dumps(index, indent=1, ensure_ascii=False))
    print(f"\n{args.name}: 26 glyphs -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

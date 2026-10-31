"""Свести компиляцию из ableton/arrangement.json в один файл — чтобы послушать вне Live.

Это офлайн-копия того, что лежит на дорожках в сете: те же файлы на тех же долях.
Длинные текстуры Live при импорте подтянул варпом под сетку (span в долях), поэтому
здесь они растягиваются тем же способом — по времени, без смены высоты (atempo),
иначе звучало бы не как в сете. Короткие удары варпу не подвергались и идут как есть.

Реверб синтезируется здесь же: короткая комната на ритм, чтобы удары стояли в
помещении, но не размазывались, и длинный зал на воздушные текстуры. Сухой
сигнал остаётся в центре, отражения разведены по сторонам — отсюда объём.

usage:
  python3 tools/mixdown.py [--layout ableton/arrangement.json] [--peak -1]
"""
import argparse
import json
import pathlib
import subprocess
import tempfile
import wave

import numpy as np
from scipy.signal import butter, fftconvolve, sosfilt

import extract as E

ROOT = pathlib.Path(__file__).resolve().parent.parent
TAIL = 2.5        # запас на хвост реверберации
FADE = 0.03
rng = np.random.default_rng(5)


def stretch(src, target, tmp):
    """Привести файл к длительности target секунд, сохранив высоту."""
    x = E.decode(src, tmp)
    have = len(x) / E.SR
    want = int(round(target * E.SR))
    if abs(have - target) < 0.005:
        return np.pad(x, (0, max(0, want - len(x))))[:want]
    dst = tmp / f"{src.stem}-{target:.3f}.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(src),
         "-filter:a", f"atempo={have / target:.9f}", "-ar", str(E.SR), "-ac", "1",
         "-c:a", "pcm_s16le", str(dst)], check=True)
    y = E.decode(dst, tmp)
    # atempo округляет размер блока — подрезаем ровно под сетку
    return np.pad(y, (0, max(0, want - len(y))))[:want]


def impulse(decay, top, predelay):
    """Стерео-импульс: затухающий шум с ранними отражениями, каналы раскоррелированы."""
    n = int(decay * E.SR)
    env = np.exp(-np.linspace(0, 6.5, n))
    sos = butter(2, top / (E.SR / 2), btype="low", output="sos")
    ir = np.stack([sosfilt(sos, rng.standard_normal(n) * env) for _ in range(2)])
    for t, g in ((0.011, 0.5), (0.023, 0.38), (0.041, 0.26), (0.067, 0.18)):
        i = int(t * E.SR)
        if i < n:
            ir[0, i] += g
            ir[1, int(i * 1.17) % n] += g * 0.9
    pre = int(predelay * E.SR)
    ir = np.pad(ir, ((0, 0), (pre, 0)))
    # нормируем по энергии, а не по пику: иначе длинный зал прибавляет громкость
    # и забивает ритм, хотя по проценту wet кажется тихим
    return ir / np.sqrt((ir ** 2).sum(axis=1).mean())


def write_stereo(path, stereo, sr=E.SR):
    frames = np.clip(stereo.T, -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((frames * 32767).astype("<i2").tobytes())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", default="ableton/arrangement.json")
    ap.add_argument("--peak", type=float, default=-1.0, help="потолок микса в dBFS")
    args = ap.parse_args()

    doc = json.loads((ROOT / args.layout).read_text())
    beat = 60.0 / doc["bpm"]
    total = doc["bars"] * doc["beats_per_bar"] * beat
    length = int((total + TAIL) * E.SR)
    irs = {k: impulse(**v) for k, v in doc["reverbs"].items()}
    mix = np.zeros((2, length))
    print(f'{doc["bpm"]} BPM, {doc["bars"]} такта, {total:.1f} s')

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        cache = {}
        for track in doc["tracks"]:
            buf = np.zeros(length)
            for clip in track["clips"]:
                span = clip.get("span")
                key = (clip["file"], span)
                if key not in cache:
                    src = ROOT / clip["file"]
                    cache[key] = (stretch(src, span * beat, tmp) if span
                                  else E.decode(src, tmp))
                g = cache[key]
                i = int(round(clip["at"] * beat * E.SR))
                n = min(len(g), length - i)
                buf[i:i + n] += g[:n]

            buf *= track.get("gain", 1.0)
            pan = track.get("pan", 0.0)
            gains = np.array([1 - max(0, pan), 1 + min(0, pan)])
            wet_name, wet = track.get("reverb", (None, 0.0))
            out = np.outer(gains, buf) * (1 - wet * 0.5)
            if wet_name:
                tail = fftconvolve(np.stack([buf, buf]), irs[wet_name], mode="full",
                                   axes=1)[:, :length]
                out += tail * wet
            mix += out
            print(f"  {track['name']:<20} {len(track['clips']):2d} клипов  "
                  f"gain {track.get('gain', 1.0):.2f}  pan {pan:+.2f}  "
                  f"{wet_name or 'сухо':<4} {wet:.0%}")

    n = int(FADE * E.SR)
    mix[:, :n] *= np.linspace(0, 1, n)
    mix[:, -int(0.4 * E.SR):] *= np.linspace(1, 0, int(0.4 * E.SR))

    raw_peak = np.abs(mix).max()
    mix *= 10 ** (args.peak / 20) / raw_peak
    rms = np.sqrt((mix ** 2).mean())
    print(f"\nсумма: пик {20*np.log10(raw_peak):+.1f} dBFS -> нормировано к "
          f"{args.peak:+.1f} dBFS, RMS {20*np.log10(rms):.1f} dBFS")

    out = ROOT / "ableton" / "compilation.wav"
    write_stereo(out, mix)
    mp3 = out.with_suffix(".mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(out),
                    "-codec:a", "libmp3lame", "-b:a", "192k", str(mp3)], check=True)
    print(f"{out.relative_to(ROOT)}  {mix.shape[1]/E.SR:.2f}s")
    print(f"{mp3.relative_to(ROOT)}  {mp3.stat().st_size/1024:.0f} KB")


if __name__ == "__main__":
    main()

/* Specimen: the word is drawn with the sound font, the case pairs Wremena with it. */

const ALPHABET = "abcdefghijklmnopqrstuvwxyz";

const TRANSLIT = {
  а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "yo", ж: "zh", з: "z",
  и: "i", й: "y", к: "k", л: "l", м: "m", н: "n", о: "o", п: "p", р: "r",
  с: "s", т: "t", у: "u", ф: "f", х: "h", ц: "ts", ч: "ch", ш: "sh", щ: "sch",
  ъ: "", ы: "y", ь: "", э: "e", ю: "yu", я: "ya",
};
const DIGITS = { 0: "o", 1: "i", 2: "z", 3: "e", 4: "a", 5: "s", 6: "b", 7: "t", 8: "h", 9: "g" };
const RESTS = {
  " ": 1.6, "\u00a0": 1.6, ",": 2.2, ":": 2.2, ";": 2.6,
  ".": 3.2, "!": 3.0, "?": 3.0, "-": 1.15, "—": 1.5, "–": 1.15,
};

const fineQuery = window.matchMedia("(pointer: fine)");
const el = (id) => document.getElementById(id);

const state = {
  ctx: null,
  bus: null,
  font: null,
  buffers: new Map(),
  fontPeak: 1,
  nx: 0.5,
  ny: 0.5,
  heightT: 0.5,
  widthT: 0.5,
  prevText: "working-session",
  phrase: null,
  speakUntil: new Map(),
  glyphBody: new Map(),
  pendingCaret: null,
};

function soundLetters(raw) {
  const low = raw.toLowerCase();
  if (ALPHABET.includes(low)) return low;
  if (low in TRANSLIT) return TRANSLIT[low];
  if (low in DIGITS) return DIGITS[low];
  return "";
}

function isDash(ch) {
  return ch === "-" || ch === "—" || ch === "–";
}

function limitCurve() {
  const n = 2048;
  const curve = new Float32Array(n);
  const knee = 0.55;
  const ceil = 0.95;
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * 2 - 1;
    const a = Math.abs(x);
    curve[i] = Math.sign(x) * (a <= knee
      ? a
      : knee + (ceil - knee) * Math.tanh((a - knee) / (ceil - knee)));
  }
  return curve;
}

function ensureAudio() {
  if (state.ctx) return;
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const bus = ctx.createGain();
  const comp = ctx.createDynamicsCompressor();
  comp.threshold.value = -12;
  comp.knee.value = 18;
  comp.ratio.value = 3.5;
  comp.attack.value = 0.004;
  comp.release.value = 0.16;
  const limiter = ctx.createWaveShaper();
  limiter.curve = limitCurve();
  limiter.oversample = "2x";
  const master = ctx.createGain();
  master.gain.value = 0.92;
  bus.connect(comp).connect(limiter).connect(master).connect(ctx.destination);
  state.ctx = ctx;
  state.bus = bus;
}

async function wake() {
  ensureAudio();
  if (state.ctx.state !== "running") await state.ctx.resume();
}

function glyphBody(letter) {
  if (state.glyphBody.has(letter)) return state.glyphBody.get(letter);
  const g = state.font.glyphs[letter];
  if (!g) return null;
  const vbH = 100;
  const vbW = Math.max(6, g.width * vbH);
  const bars = g.up.length;
  const bw = vbW / bars;
  const mid = vbH / 2;
  const amp = (vbH * 0.46) / state.fontPeak;
  let rects = "";
  for (let i = 0; i < bars; i++) {
    const hi = Math.max(0.35, g.up[i] * amp);
    const lo = Math.max(0.35, g.dn[i] * amp);
    const w = Math.max(0.12, bw * 0.86);
    const x = i * bw + (bw - w) / 2;
    rects += `<rect x="${x.toFixed(3)}" y="${(mid - hi).toFixed(3)}" width="${w.toFixed(3)}" height="${(hi + lo).toFixed(3)}"/>`;
  }
  const body = {
    aspect: (vbW / vbH).toFixed(4),
    viewBox: `0 0 ${vbW.toFixed(3)} ${vbH}`,
    rects,
  };
  state.glyphBody.set(letter, body);
  return body;
}

function glyphSvg(letter, cls) {
  const body = glyphBody(letter);
  if (!body) return "";
  return `<svg class="${cls}" viewBox="${body.viewBox}" preserveAspectRatio="none" style="--aspect:${body.aspect}" focusable="false" aria-hidden="true">${body.rects}</svg>`;
}

function slotHtml(ch, i) {
  if (ch === " " || ch === "\u00a0") {
    return `<span class="slot space" data-i="${i}"></span>`;
  }
  if (isDash(ch)) {
    return `<span class="slot dash" data-i="${i}"><i class="dash-mark"></i></span>`;
  }
  const letters = soundLetters(ch);
  if (!letters) return `<span class="slot gap" data-i="${i}"></span>`;
  const svgs = [...letters].map((letter) => glyphSvg(letter, "sound-svg")).join("");
  return `<span class="slot letter" data-i="${i}">${svgs}</span>`;
}

function renderWord() {
  const text = el("textInput").value;
  const parts = [];
  for (let i = 0; i < text.length; i++) parts.push(slotHtml(text[i], i));
  el("wordDisplay").innerHTML = parts.join("") + '<span class="caret" aria-hidden="true"></span>';
  placeCaret();
  markSelection();
  paintHeldSlots();
}

function caretIndex() {
  const input = el("textInput");
  if (document.activeElement !== input && state.pendingCaret == null) return input.value.length;
  if (input.selectionDirection === "backward") return input.selectionStart;
  return input.selectionEnd;
}

function placeCaret() {
  const word = el("wordDisplay");
  const caret = word.querySelector(".caret");
  if (!caret) return;
  const slots = [...word.querySelectorAll(".slot")];
  let at = state.pendingCaret != null ? state.pendingCaret : caretIndex();
  at = Math.max(0, Math.min(slots.length, at));
  if (at >= slots.length) word.appendChild(caret);
  else word.insertBefore(caret, slots[at]);
}

function markSelection() {
  const input = el("textInput");
  const a = input.selectionStart;
  const b = input.selectionEnd;
  el("wordDisplay").querySelectorAll(".slot").forEach((slot) => {
    const i = Number(slot.dataset.i);
    slot.classList.toggle("selected", b > a && i >= a && i < b);
  });
}

function indexFromX(x) {
  const slots = [...el("wordDisplay").querySelectorAll(".slot")];
  if (!slots.length) return 0;
  for (let i = 0; i < slots.length; i++) {
    const r = slots[i].getBoundingClientRect();
    if (x < r.left + r.width / 2) return i;
  }
  return slots.length;
}

function setSize(fs, wx, hy = 1) {
  const word = el("wordDisplay");
  word.style.setProperty("--fs", fs.toFixed(2));
  word.style.setProperty("--wx", wx.toFixed(4));
  word.style.setProperty("--hy", hy.toFixed(4));
}

function baseSize() {
  const min = 34;
  const max = Math.max(min + 8, window.innerHeight * 0.58);
  return min * Math.pow(max / min, 0.5);
}

function heightStretch(t) {
  if (t <= 0.5) return 0.35 + (t / 0.5) * 0.65;
  return 1 + ((t - 0.5) / 0.5) * 1.2;
}

function emWidth(text) {
  let em = 0;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (ch === " " || ch === "\u00a0") { em += 0.34; continue; }
    if (isDash(ch)) { em += 0.36; continue; }
    const letters = soundLetters(ch);
    if (!letters) { em += 0.2; continue; }
    for (const letter of letters) {
      const g = state.font.glyphs[letter];
      if (g) em += g.width + 0.02;
    }
    em += 0.03;
  }
  return Math.max(em, 0.4);
}

function applyPointer() {
  if (!state.font) return;
  if (!fineQuery.matches) {
    state.heightT = 0.45;
    state.widthT = 0.35;
    const wx = 0.92;
    const avail = window.innerWidth * 0.88;
    const fs = Math.max(32, Math.min(window.innerHeight * 0.24, avail / (emWidth(el("textInput").value) * wx)));
    setSize(fs, wx, 1);
    return;
  }
  state.heightT = Math.min(1, Math.max(0, 1 - state.ny));
  state.widthT = Math.min(1, Math.max(0, state.nx));
  const wx = 0.42 + state.widthT * (1.75 - 0.42);
  setSize(baseSize(), wx, heightStretch(state.heightT));
}

function renderCharset() {
  const grid = el("charsetGrid");
  grid.innerHTML = "";
  for (const letter of ALPHABET) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "char-pair";
    btn.dataset.letter = letter;
    btn.innerHTML =
      `<span class="base-letter">${letter}</span>` +
      `<span class="sound-glyph">${glyphSvg(letter, "charset-svg")}</span>`;
    btn.addEventListener("mousedown", (e) => e.preventDefault());
    btn.addEventListener("click", () => playLetter(letter));
    grid.appendChild(btn);
  }
}

function voiceOf(letter, index, isUpper) {
  const glyph = state.font.glyphs[letter];
  const rate = (1.08 - state.heightT * 0.2) * (isUpper ? 0.94 : 1);
  const gain = Math.min(1.15, (0.4 + state.heightT * 0.55) * (isUpper ? 1.28 : 1));
  const pan = Math.sin(index * 1.7) * state.widthT * 0.7;
  return { letter, rate, gain, pan, dur: glyph.dur / rate };
}

function buildSchedule(text) {
  const step = 0.05 + state.widthT * 0.28;
  const notes = [];
  let t = 0;
  let i = 0;
  for (let c = 0; c < text.length; c++) {
    const raw = text[c];
    const low = raw.toLowerCase();
    if (low in RESTS) {
      t += step * RESTS[low];
      continue;
    }
    const letters = soundLetters(raw);
    if (!letters) {
      if (raw.trim()) t += step;
      continue;
    }
    const isUpper = raw !== low && raw === raw.toUpperCase();
    for (const letter of letters) {
      if (!state.font.glyphs[letter]) continue;
      const voice = voiceOf(letter, i, isUpper);
      notes.push({ ...voice, t, charIndex: c });
      t += Math.max(0.035, step);
      i++;
    }
  }
  const end = notes.reduce((m, n) => Math.max(m, n.t + n.dur), 0);
  return { notes, end };
}

function startVoice(note, when) {
  const buf = state.buffers.get(note.letter);
  if (!buf || !state.ctx) return null;
  const src = state.ctx.createBufferSource();
  src.buffer = buf;
  src.playbackRate.value = note.rate;
  const gain = state.ctx.createGain();
  gain.gain.value = note.gain;
  const pan = state.ctx.createStereoPanner();
  pan.pan.value = Math.max(-1, Math.min(1, note.pan || 0));
  src.connect(gain).connect(pan).connect(state.bus);
  src.start(when);
  return src;
}

function setPlaying(on) {
  const btn = el("playBtn");
  btn.classList.toggle("busy", on);
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.setAttribute("aria-label", on ? "Stop" : "Play");
}

function stopPhrase() {
  const phrase = state.phrase;
  if (!phrase) {
    setPlaying(false);
    return;
  }
  state.phrase = null;
  clearTimeout(phrase.timer);
  if (phrase.raf) cancelAnimationFrame(phrase.raf);
  for (const src of phrase.sources) {
    try { src.stop(); } catch (e) { /* already ended */ }
  }
  state.speakUntil.clear();
  el("wordDisplay").querySelectorAll(".slot.speaking").forEach((n) => n.classList.remove("speaking"));
  document.querySelectorAll(".char-pair.playing").forEach((n) => n.classList.remove("playing"));
  setPlaying(false);
}

function follow() {
  const phrase = state.phrase;
  if (!phrase) return;
  const now = state.ctx.currentTime - phrase.at;
  const chars = new Set();
  const letters = new Set();
  for (const note of phrase.sched.notes) {
    if (now >= note.t && now < note.t + note.dur) {
      chars.add(note.charIndex);
      letters.add(note.letter);
    }
  }
  el("wordDisplay").querySelectorAll(".slot").forEach((slot) => {
    slot.classList.toggle("speaking", chars.has(Number(slot.dataset.i)));
  });
  document.querySelectorAll(".char-pair").forEach((card) => {
    card.classList.toggle("playing", letters.has(card.dataset.letter));
  });
  if (now > phrase.sched.end + 0.2) stopPhrase();
  else phrase.raf = requestAnimationFrame(follow);
}

async function playPhrase() {
  await wake();
  stopPhrase();
  state.speakUntil.clear();
  if (!state.font || state.buffers.size === 0) return;
  const sched = buildSchedule(el("textInput").value);
  if (!sched.notes.length) return;
  const at = state.ctx.currentTime + 0.05;
  const sources = [];
  for (const note of sched.notes) {
    const src = startVoice(note, at + note.t);
    if (src) sources.push(src);
  }
  if (!sources.length) return;
  state.phrase = {
    sched,
    at,
    sources,
    raf: 0,
    timer: setTimeout(() => stopPhrase(), (sched.end + 0.4) * 1000),
  };
  setPlaying(true);
  follow();
}

function togglePhrase() {
  if (state.phrase) stopPhrase();
  else playPhrase();
}

function holdSlot(index, ms) {
  if (state.phrase || index == null || index < 0) return;
  const text = el("textInput").value;
  const ch = text[index];
  if (ch == null) return;
  const until = performance.now() + ms;
  const prev = state.speakUntil.get(index);
  const kept = prev && prev.ch === ch ? prev.until : 0;
  state.speakUntil.set(index, { until: Math.max(kept, until), ch });
  const slot = el("wordDisplay").querySelector(`.slot[data-i="${index}"]`);
  if (slot) slot.classList.add("speaking");
  setTimeout(() => releaseSlot(index), ms);
}

function releaseSlot(index) {
  if (state.phrase) return;
  const hold = state.speakUntil.get(index);
  if (!hold || hold.until > performance.now() + 20) return;
  state.speakUntil.delete(index);
  if (el("textInput").value[index] !== hold.ch) return;
  const slot = el("wordDisplay").querySelector(`.slot[data-i="${index}"]`);
  if (slot) slot.classList.remove("speaking");
}

function paintHeldSlots() {
  if (state.phrase) return;
  const text = el("textInput").value;
  const now = performance.now();
  for (const [index, hold] of [...state.speakUntil]) {
    if (!hold || hold.until <= now || text[index] !== hold.ch) {
      state.speakUntil.delete(index);
      continue;
    }
    const slot = el("wordDisplay").querySelector(`.slot[data-i="${index}"]`);
    if (slot) slot.classList.add("speaking");
  }
}

async function playLetter(letter, charIndex) {
  await wake();
  if (!state.buffers.has(letter)) return;
  const note = voiceOf(letter, 0, false);
  startVoice(note, state.ctx.currentTime + 0.01);
  if (state.phrase) return;
  const ms = (state.buffers.get(letter).duration / note.rate) * 1000 + 50;
  const card = document.querySelector(`.char-pair[data-letter="${letter}"]`);
  if (card) {
    card.classList.add("playing");
    setTimeout(() => {
      if (!state.phrase) card.classList.remove("playing");
    }, ms);
  }
  if (charIndex != null) holdSlot(charIndex, ms);
}

function playTyped(chunk, startIndex) {
  let delay = 0;
  for (let n = 0; n < chunk.length; n++) {
    const letters = soundLetters(chunk[n]);
    const charIndex = startIndex + n;
    for (const letter of letters) {
      const wait = delay;
      setTimeout(() => playLetter(letter, charIndex), wait);
      delay += 55;
    }
    if (delay > 700) break;
  }
}

function onType() {
  const input = el("textInput");
  const next = input.value;
  const prev = state.prevText;
  const caret = input.selectionStart;
  state.prevText = next;
  renderWord();
  if (!fineQuery.matches) applyPointer();
  if (next.length > prev.length) {
    const added = next.length - prev.length;
    const start = Math.max(0, caret - added);
    const chunk = next.slice(start, caret);
    if (chunk && added <= 24) playTyped(chunk, start);
  }
}

function onSelection() {
  if (document.activeElement !== el("textInput")) return;
  state.pendingCaret = null;
  placeCaret();
  markSelection();
}

function focusInput(index) {
  const input = el("textInput");
  const at = index == null ? input.value.length : index;
  state.pendingCaret = at;
  input.focus({ preventScroll: true });
  input.setSelectionRange(at, at);
  placeCaret();
}

function bindUI() {
  const input = el("textInput");
  const hero = el("hero");

  hero.addEventListener("pointerdown", (e) => {
    if (e.target.closest("button")) return;
    const idx = indexFromX(e.clientX);
    state.pendingCaret = idx;
    if (fineQuery.matches) e.preventDefault();
    input.focus({ preventScroll: true });
    input.setSelectionRange(idx, idx);
    placeCaret();
    markSelection();
  });

  input.addEventListener("input", onType);
  input.addEventListener("keyup", onSelection);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      togglePhrase();
    }
  });
  document.addEventListener("selectionchange", onSelection);

  const play = el("playBtn");
  play.addEventListener("mousedown", (e) => e.preventDefault());
  play.addEventListener("click", togglePhrase);

  window.addEventListener("resize", applyPointer);
  const onMove = (e) => {
    state.nx = e.clientX / window.innerWidth;
    state.ny = e.clientY / window.innerHeight;
    applyPointer();
  };
  const bindPointer = () => {
    window.removeEventListener("mousemove", onMove);
    if (fineQuery.matches) window.addEventListener("mousemove", onMove);
    applyPointer();
  };
  if (fineQuery.addEventListener) fineQuery.addEventListener("change", bindPointer);
  bindPointer();
}

async function loadBuffers() {
  await Promise.all(
    ALPHABET.split("").map(async (letter) => {
      const glyph = state.font.glyphs[letter];
      if (!glyph) return;
      const bytes = await (await fetch(`fonts/rumble/${glyph.file}`)).arrayBuffer();
      state.buffers.set(letter, await state.ctx.decodeAudioData(bytes.slice(0)));
    })
  );
}

async function boot() {
  bindUI();
  ensureAudio();
  const font = await (await fetch("fonts/rumble/font.json")).json();
  state.font = font;
  state.fontPeak = Math.max(
    ...Object.values(font.glyphs).flatMap((glyph) => [...glyph.up, ...glyph.dn])
  );
  renderCharset();
  renderWord();
  applyPointer();
  focusInput(el("textInput").value.length);
  await loadBuffers();
}

boot().catch((err) => console.error(err));

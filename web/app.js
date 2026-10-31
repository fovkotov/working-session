/* Звуковой шрифт — набор текста гранулами полевых записей. */

const ALPHABET = "abcdefghijklmnopqrstuvwxyz";

// кириллица на вводе транслитерируется в буквы гарнитуры
const TRANSLIT = {
  а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "yo", ж: "zh", з: "z",
  и: "i", й: "y", к: "k", л: "l", м: "m", н: "n", о: "o", п: "p", р: "r",
  с: "s", т: "t", у: "u", ф: "f", х: "h", ц: "ts", ч: "ch", ш: "sh", щ: "sch",
  ъ: "", ы: "y", ь: "", э: "e", ю: "yu", я: "ya",
};
const DIGITS = { 0: "o", 1: "i", 2: "z", 3: "e", 4: "a", 5: "s", 6: "b", 7: "t", 8: "h", 9: "g" };
const RESTS = { " ": 1.6, "\u00a0": 1.6, ",": 2.2, ":": 2.2, ";": 2.6, ".": 3.2, "!": 3.0, "?": 3.0, "-": 1.2, "—": 1.6 };

const PRESETS = {
  "Specimen":  { tempo: 190, track: 0,   pitch: 0,  weight: 50, width: 35, space: 22, human: 18 },
  "Шёпот":     { tempo: 300, track: 25,  pitch: 7,  weight: 16, width: 70, space: 55, human: 35 },
  "Объявление":{ tempo: 150, track: -10, pitch: -3, weight: 85, width: 15, space: 12, human: 8 },
  "Телеграф":  { tempo: 85,  track: -5,  pitch: 4,  weight: 60, width: 25, space: 5,  human: 25 },
  "Тягучий":   { tempo: 520, track: -35, pitch: -8, weight: 70, width: 85, space: 85, human: 12 },
  "Машинопись":{ tempo: 135, track: 40,  pitch: 0,  weight: 45, width: 0,  space: 0,  human: 0 },
};

const el = (id) => document.getElementById(id);
const SLIDERS = ["tempo", "track", "pitch", "weight", "width", "space", "human"];

const state = {
  ctx: new (window.AudioContext || window.webkitAudioContext)(),
  font: null,
  buffers: new Map(),
  sched: null,
  live: null,     // текущее воспроизведение фразы
  irCache: new Map(),
};

/* ---------------- разбор текста ---------------- */

function tokenize(text) {
  const tokens = [];
  for (const raw of text) {
    const low = raw.toLowerCase();
    const isUpper = raw !== low && raw === raw.toUpperCase();
    if (low in RESTS) {
      tokens.push({ show: raw === " " ? "·" : raw, rest: RESTS[low] });
      continue;
    }
    let letters = "";
    if (ALPHABET.includes(low)) letters = low;
    else if (low in TRANSLIT) letters = TRANSLIT[low];
    else if (low in DIGITS) letters = DIGITS[low];
    if (letters) tokens.push({ show: raw, letters, isUpper });
    else if (raw.trim()) tokens.push({ show: raw, rest: 1.2 });
  }
  return tokens;
}

/* ---------------- расписание ---------------- */

function readParams() {
  const p = {};
  for (const id of SLIDERS) p[id] = Number(el(id).value);
  return p;
}

function buildSchedule(text, p) {
  const step = p.tempo / 1000;
  const tracking = p.track / 100;
  const baseRate = Math.pow(2, p.pitch / 12);
  const h = p.human / 100;
  const spread = p.width / 100;
  const jitter = (amt) => (Math.random() - 0.5) * amt * h;

  const notes = [];
  const cells = [];
  let t = 0;
  let i = 0;

  for (const tok of tokenize(text)) {
    if (tok.rest) {
      cells.push({ show: tok.show, rest: true, t });
      t += step * tok.rest * (1 + tracking * 0.5);
      continue;
    }
    const cell = { show: tok.show, t, from: notes.length, to: notes.length };
    cells.push(cell);
    for (const letter of tok.letters) {
      const glyph = state.font.glyphs[letter];
      if (!glyph) continue;
      const rate = baseRate * (tok.isUpper ? 0.94 : 1) * (1 + jitter(0.12));
      const note = {
        letter,
        t: Math.max(0, t + jitter(step * 0.5)),
        rate,
        dur: glyph.dur / rate,
        gain: (tok.isUpper ? 1.35 : 1) * (1 + jitter(0.3)),
        pan: Math.sin(i * 1.7) * spread * 0.9,
        kind: glyph.kind,
      };
      notes.push(note);
      const weightOfLetter = glyph.kind === "vowel" ? 1.15 : 0.85;
      t += Math.max(0.03, step * weightOfLetter + step * tracking);
      cell.to = notes.length;
      i++;
    }
  }

  const end = notes.reduce((m, n) => Math.max(m, n.t + n.dur), 0);
  return { notes, cells, end, tail: 0.5 + 2.6 * (p.space / 100) };
}

/* ---------------- звуковой тракт ---------------- */

function impulseResponse(ctx, amount) {
  const key = `${ctx.sampleRate}:${Math.round(amount * 20)}`;
  if (state.irCache.has(key)) return state.irCache.get(key);
  const sr = ctx.sampleRate;
  const len = Math.floor(sr * (0.45 + 2.4 * amount));
  const ir = ctx.createBuffer(2, len, sr);
  const pre = Math.floor(sr * 0.012);
  for (let c = 0; c < 2; c++) {
    const d = ir.getChannelData(c);
    for (let n = pre; n < len; n++) {
      const x = (n - pre) / (len - pre);
      d[n] = (Math.random() * 2 - 1) * Math.pow(1 - x, 2.6);
    }
  }
  state.irCache.set(key, ir);
  return ir;
}

// насыщение: полная шкала остаётся на месте, растёт плотность середины
function driveCurve(amount) {
  const n = 2048;
  const curve = new Float32Array(n);
  const g = 1 + amount * 2.5;
  const norm = Math.tanh(g);
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * 2 - 1;
    curve[i] = Math.tanh(g * x) / norm;
  }
  return curve;
}

// страховочный лимитер: линеен до 0.6, дальше мягко загибается и никогда не выходит за 0.97
function limitCurve() {
  const n = 2048;
  const curve = new Float32Array(n);
  const knee = 0.6;
  const ceil = 0.97;
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * 2 - 1;
    const a = Math.abs(x);
    curve[i] = Math.sign(x) * (a <= knee
      ? a
      : knee + (ceil - knee) * Math.tanh((a - knee) / (ceil - knee)));
  }
  return curve;
}

function buildGraph(ctx, p) {
  const w = p.weight / 100;
  const space = p.space / 100;

  const bus = ctx.createGain();
  const hp = ctx.createBiquadFilter();
  hp.type = "highpass";
  hp.frequency.value = 40 + 1150 * Math.pow(1 - w, 1.7);
  const shelf = ctx.createBiquadFilter();
  shelf.type = "lowshelf";
  shelf.frequency.value = 190;
  shelf.gain.value = (w - 0.45) * 13;
  const drive = ctx.createWaveShaper();
  drive.curve = driveCurve(Math.max(0, w - 0.55) * 2);
  drive.oversample = "2x";

  const comp = ctx.createDynamicsCompressor();
  comp.threshold.value = -14;
  comp.knee.value = 20;
  comp.ratio.value = 3;
  comp.attack.value = 0.004;
  comp.release.value = 0.2;

  const dry = ctx.createGain();
  dry.gain.value = 1 - 0.4 * space;
  const wet = ctx.createGain();
  wet.gain.value = space * 1.1;
  const verb = ctx.createConvolver();
  verb.buffer = impulseResponse(ctx, space);

  const master = ctx.createGain();
  // частично компенсируем подъём нижней полки: жирное должно быть громче, но не в перегруз
  master.gain.value = 1.5 * Math.pow(10, -Math.max(0, (w - 0.45) * 13) * 0.45 / 20);
  const limiter = ctx.createWaveShaper();
  limiter.curve = limitCurve();
  limiter.oversample = "4x";

  bus.connect(hp).connect(shelf).connect(drive);
  drive.connect(dry).connect(comp);
  drive.connect(wet).connect(verb).connect(comp);
  comp.connect(master).connect(limiter).connect(ctx.destination);
  return bus;
}

function schedule(ctx, bus, sched, at) {
  const sources = [];
  for (const note of sched.notes) {
    const buf = state.buffers.get(note.letter);
    if (!buf) continue;
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.playbackRate.value = note.rate;
    const g = ctx.createGain();
    g.gain.value = note.gain;
    const pan = ctx.createStereoPanner();
    pan.pan.value = note.pan;
    src.connect(g).connect(pan).connect(bus);
    src.start(at + note.t);
    sources.push(src);
  }
  return sources;
}

/* ---------------- воспроизведение ---------------- */

async function wake() {
  if (state.ctx.state !== "running") await state.ctx.resume();
}

function stopAll() {
  if (state.live) {
    clearTimeout(state.live.timer);
    for (const s of state.live.sources) { try { s.stop(); } catch (e) {} }
    state.live = null;
  }
  el("play").classList.remove("busy");
  el("play").textContent = "Произнести";
  document.querySelectorAll(".glyph.on, .cells span.on").forEach((n) => n.classList.remove("on"));
  drawScore();
}

async function playText() {
  await wake();
  stopAll();
  const p = readParams();
  const sched = buildSchedule(el("text").value, p);
  state.sched = sched;
  drawScore();
  if (!sched.notes.length) return;
  const bus = buildGraph(state.ctx, p);
  const at = state.ctx.currentTime + 0.08;
  state.live = { sched, at, sources: schedule(state.ctx, bus, sched, at) };
  el("play").classList.add("busy");
  el("play").textContent = "Звучит…";
  // rAF засыпает в фоновой вкладке, поэтому финал подстрахован таймером
  state.live.timer = setTimeout(stopAll, (sched.end + 0.4) * 1000);
  requestAnimationFrame(follow);
}

async function playLetter(letter) {
  await wake();
  const buf = state.buffers.get(letter);
  if (!buf) return;
  const p = readParams();
  const bus = buildGraph(state.ctx, p);
  const src = state.ctx.createBufferSource();
  src.buffer = buf;
  src.playbackRate.value = Math.pow(2, p.pitch / 12);
  src.connect(bus);
  src.start();
  const card = document.querySelector(`.glyph[data-letter="${letter}"]`);
  if (card) {
    card.classList.add("on");
    setTimeout(() => card.classList.remove("on"), buf.duration * 1000 + 90);
  }
}

function follow() {
  if (!state.live) return;
  const now = state.ctx.currentTime - state.live.at;
  const { sched } = state.live;
  const active = new Set();
  sched.notes.forEach((n) => {
    if (now >= n.t && now < n.t + n.dur) active.add(n.letter);
  });
  document.querySelectorAll(".glyph").forEach((card) => {
    card.classList.toggle("on", active.has(card.dataset.letter));
  });
  // в строке текста буква горит от своей атаки до атаки следующей — так читается курсор набора
  let cursor = -1;
  sched.notes.forEach((n, idx) => { if (now >= n.t) cursor = idx; });
  const cellNodes = el("cells").children;
  sched.cells.forEach((c, idx) => {
    const node = cellNodes[idx];
    if (!node) return;
    node.classList.toggle("on", !c.rest && cursor >= c.from && cursor < c.to);
  });
  drawScore(now);
  if (now > sched.end + 0.25) stopAll();
  else requestAnimationFrame(follow);
}

/* ---------------- отрисовка ---------------- */

// одна и та же отрисовка идёт и на экран, и в PNG — отличается только масштабом
function paintScore(g, w, h, { playhead, scale = 1, labels = true } = {}) {
  const sched = state.sched;
  const pad = 18 * scale;
  const mid = h * 0.46;
  const amp = (h * 0.33) / state.fontPeak;

  if (!sched || !sched.notes.length) return;

  const span = Math.max(sched.end, 0.4);
  const x = (t) => pad + (t / span) * (w - pad * 2);

  for (const note of sched.notes) {
    const glyph = state.font.glyphs[note.letter];
    const x0 = x(note.t);
    const x1 = Math.max(x0 + 1.5 * scale, x(note.t + note.dur));
    const bars = glyph.up.length;
    const bw = Math.max(0.7 * scale, (x1 - x0) / bars);
    const lit = playhead !== undefined && playhead >= note.t && playhead < note.t + note.dur;
    g.fillStyle = note.kind === "vowel"
      ? (lit ? "#eaffa0" : "#d8ff3e")
      : (lit ? "#e8e6e0" : "#7f8792");
    g.globalAlpha = lit ? 1 : 0.78;
    for (let i = 0; i < bars; i++) {
      const hi = Math.max(0.5 * scale, glyph.up[i] * amp * note.gain);
      const lo = Math.max(0.5 * scale, glyph.dn[i] * amp * note.gain);
      g.fillRect(x0 + i * bw, mid - hi, Math.max(0.7 * scale, bw * 0.9), hi + lo);
    }
    g.globalAlpha = 1;
    if (labels && x1 - x0 > 9 * scale) {
      g.fillStyle = lit ? "#f1efea" : "#5a5a63";
      g.font = `${11 * scale}px ui-monospace, "SF Mono", Menlo, monospace`;
      g.fillText(note.letter, x0, h - 10 * scale);
    }
  }

  if (playhead !== undefined && playhead >= 0) {
    const px = x(Math.min(playhead, span));
    g.strokeStyle = "#d8ff3e";
    g.lineWidth = 1.5 * scale;
    g.beginPath();
    g.moveTo(px, 10 * scale);
    g.lineTo(px, h - 22 * scale);
    g.stroke();
  }
}

function drawScore(playhead) {
  const cvs = el("score");
  const dpr = window.devicePixelRatio || 1;
  const w = cvs.clientWidth;
  const h = cvs.clientHeight;
  if (cvs.width !== Math.floor(w * dpr)) {
    cvs.width = Math.floor(w * dpr);
    cvs.height = Math.floor(h * dpr);
  }
  const g = cvs.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  paintScore(g, w, h, { playhead });
}

function renderCells() {
  const sched = state.sched;
  const box = el("cells");
  box.innerHTML = "";
  if (!sched) return;
  for (const c of sched.cells) {
    const span = document.createElement("span");
    span.textContent = c.show;
    if (c.rest) span.className = "rest";
    box.appendChild(span);
  }
}

function renderGrid() {
  const grid = el("grid");
  grid.innerHTML = "";
  for (const letter of ALPHABET) {
    const glyph = state.font.glyphs[letter];
    if (!glyph) continue;
    const card = document.createElement("button");
    card.className = `glyph ${glyph.kind}`;
    card.dataset.letter = letter;
    card.innerHTML =
      `<div class="letter">${letter}</div>` +
      `<canvas></canvas>` +
      `<div class="meta">${glyph.centroid}Hz ${Math.round(glyph.dur * 1000)}ms ` +
      `${glyph.semitones > 0 ? "+" : ""}${glyph.semitones}st</div>`;
    card.addEventListener("click", () => playLetter(letter));
    grid.appendChild(card);
    drawGlyph(card.querySelector("canvas"), glyph);
  }
}

function drawGlyph(cvs, glyph) {
  const dpr = window.devicePixelRatio || 1;
  const w = cvs.clientWidth || 110;
  const h = cvs.clientHeight || 46;
  cvs.width = Math.floor(w * dpr);
  cvs.height = Math.floor(h * dpr);
  const g = cvs.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  const mid = h / 2;
  const bars = glyph.up.length;
  // ширина глифа на карточке пропорциональна его длительности — шрифт пропорциональный
  const bw = (w * (glyph.dur / state.fontLongest)) / bars;
  const amp = (h * 0.46) / state.fontPeak;
  g.fillStyle = "#7f8792";
  for (let i = 0; i < bars; i++) {
    const hi = Math.max(0.5, glyph.up[i] * amp);
    const lo = Math.max(0.5, glyph.dn[i] * amp);
    g.fillRect(i * bw, mid - hi, Math.max(0.6, bw * 0.9), hi + lo);
  }
}

/* ---------------- экспорт ---------------- */

function encodeWav(buffer) {
  const ch = buffer.numberOfChannels;
  const frames = buffer.length;
  const bytes = 44 + frames * ch * 2;
  const view = new DataView(new ArrayBuffer(bytes));
  const str = (off, s) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); };
  str(0, "RIFF");
  view.setUint32(4, bytes - 8, true);
  str(8, "WAVEfmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, ch, true);
  view.setUint32(24, buffer.sampleRate, true);
  view.setUint32(28, buffer.sampleRate * ch * 2, true);
  view.setUint16(32, ch * 2, true);
  view.setUint16(34, 16, true);
  str(36, "data");
  view.setUint32(40, frames * ch * 2, true);
  const data = [];
  for (let c = 0; c < ch; c++) data.push(buffer.getChannelData(c));
  let off = 44;
  for (let i = 0; i < frames; i++) {
    for (let c = 0; c < ch; c++) {
      const s = Math.max(-1, Math.min(1, data[c][i]));
      view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
      off += 2;
    }
  }
  return new Blob([view], { type: "audio/wav" });
}

function download(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

function fileName(ext) {
  const text = el("text").value.trim().slice(0, 32).replace(/\s+/g, "_");
  return `${state.font.id}-${text || "specimen"}.${ext}`;
}

function exportPng() {
  if (!state.sched || !state.sched.notes.length) return;
  const onscreen = el("score");
  const scale = 2400 / onscreen.clientWidth;
  const w = 2400;
  const h = Math.round(onscreen.clientHeight * scale);
  const cvs = document.createElement("canvas");
  cvs.width = w;
  cvs.height = h;
  const g = cvs.getContext("2d");
  g.fillStyle = "#0a0a0b";
  g.fillRect(0, 0, w, h);
  paintScore(g, w, h, { scale });
  cvs.toBlob((blob) => download(blob, fileName("png")), "image/png");
}

// та же геометрия, что у paintScore (ширина 2400, как у PNG), но только столбики волны
const SVG_FILL = "#d8ff3e";

function svgNum(n) {
  return Number(n.toFixed(2)).toString();
}

function buildScoreSvg() {
  const sched = state.sched;
  if (!sched || !sched.notes.length || !state.font) return "";
  const onscreen = el("score");
  const scale = 2400 / onscreen.clientWidth;
  const w = 2400;
  const h = Math.round(onscreen.clientHeight * scale);
  const pad = 18 * scale;
  const mid = h * 0.46;
  const amp = (h * 0.33) / state.fontPeak;
  const span = Math.max(sched.end, 0.4);
  const x = (t) => pad + (t / span) * (w - pad * 2);
  const rects = [];
  for (const note of sched.notes) {
    const glyph = state.font.glyphs[note.letter];
    const x0 = x(note.t);
    const x1 = Math.max(x0 + 1.5 * scale, x(note.t + note.dur));
    const bars = glyph.up.length;
    const bw = Math.max(0.7 * scale, (x1 - x0) / bars);
    for (let i = 0; i < bars; i++) {
      const hi = Math.max(0.5 * scale, glyph.up[i] * amp * note.gain);
      const lo = Math.max(0.5 * scale, glyph.dn[i] * amp * note.gain);
      rects.push(
        `<rect x="${svgNum(x0 + i * bw)}" y="${svgNum(mid - hi)}" ` +
        `width="${svgNum(Math.max(0.7 * scale, bw * 0.9))}" height="${svgNum(hi + lo)}" fill="${SVG_FILL}"/>`
      );
    }
  }
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}">${rects.join("")}</svg>`;
}

function typingField(node) {
  if (!node || node.nodeType !== 1) return false;
  const tag = node.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || node.isContentEditable;
}

async function exportSvg() {
  const svg = buildScoreSvg();
  if (!svg) return;
  const btn = el("exportSvg");
  try {
    await navigator.clipboard.writeText(svg);
    const prev = btn.textContent;
    btn.textContent = "скопировано";
    setTimeout(() => {
      if (btn.textContent === "скопировано") btn.textContent = prev;
    }, 1000);
  } catch {
    download(new Blob([svg], { type: "image/svg+xml" }), fileName("svg"));
  }
}

async function exportWav() {
  const text = el("text").value;
  const p = readParams();
  const sched = buildSchedule(text, p);
  if (!sched.notes.length) return;
  const sr = state.font.sampleRate || 48000;
  const btn = el("exportWav");
  btn.disabled = true;
  btn.textContent = "…";
  try {
    const off = new OfflineAudioContext(2, Math.ceil((sched.end + sched.tail) * sr), sr);
    const bus = buildGraph(off, p);
    schedule(off, bus, sched, 0);
    download(encodeWav(await off.startRendering()), fileName("wav"));
  } finally {
    btn.disabled = false;
    btn.textContent = "WAV";
  }
}

/* ---------------- загрузка гарнитуры ---------------- */

async function loadFont(entry) {
  stopAll();
  const base = entry.dir;
  const font = await (await fetch(`${base}/font.json`)).json();
  const buffers = new Map();
  await Promise.all(
    ALPHABET.split("").map(async (letter) => {
      const glyph = font.glyphs[letter];
      if (!glyph) return;
      const bytes = await (await fetch(`${base}/${glyph.file}`)).arrayBuffer();
      buffers.set(letter, await state.ctx.decodeAudioData(bytes));
    })
  );
  state.font = font;
  state.buffers = buffers;
  // общая шкала рисования: самая высокая буква заполняет карточку, остальные ниже неё
  state.fontPeak = Math.max(...Object.values(font.glyphs)
    .flatMap((glyph) => [...glyph.up, ...glyph.dn]));
  state.fontLongest = Math.max(...Object.values(font.glyphs).map((glyph) => glyph.dur));
  renderGrid();
  refresh();
}

function refresh() {
  if (!state.font) return;
  state.sched = buildSchedule(el("text").value, readParams());
  renderCells();
  drawScore();
}

function syncLabels() {
  const p = readParams();
  el("vTempo").textContent = `${p.tempo} ms`;
  el("vTrack").textContent = `${p.track > 0 ? "+" : ""}${p.track}`;
  el("vPitch").textContent = `${p.pitch > 0 ? "+" : ""}${p.pitch} st`;
  el("vWeight").textContent = p.weight;
  el("vWidth").textContent = p.width;
  el("vSpace").textContent = p.space;
  el("vHuman").textContent = p.human;
}

function applyPreset(name) {
  const preset = PRESETS[name];
  for (const [k, v] of Object.entries(preset)) el(k).value = v;
  document.querySelectorAll("#presets button").forEach((b) => b.classList.toggle("on", b.textContent === name));
  syncLabels();
  refresh();
}

function initUI() {
  const presets = el("presets");
  for (const name of Object.keys(PRESETS)) {
    const b = document.createElement("button");
    b.textContent = name;
    b.addEventListener("click", () => applyPreset(name));
    presets.appendChild(b);
  }
  presets.firstChild.classList.add("on");

  for (const id of SLIDERS) {
    el(id).addEventListener("input", () => {
      document.querySelectorAll("#presets button").forEach((b) => b.classList.remove("on"));
      syncLabels();
      refresh();
    });
  }
  syncLabels();

  el("text").addEventListener("input", refresh);
  el("text").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); playText(); }
  });
  el("play").addEventListener("click", playText);
  el("stop").addEventListener("click", stopAll);
  el("exportWav").addEventListener("click", exportWav);
  el("exportPng").addEventListener("click", exportPng);
  el("exportSvg").addEventListener("click", exportSvg);

  // живая игра с клавиатуры, когда фокус не в поле ввода
  window.addEventListener("keydown", (e) => {
    // физическая клавиша «/»: на кириллице она даёт «.» / «,», поэтому смотрим code, не key
    if (
      e.code === "Slash" && !e.repeat &&
      !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey &&
      !typingField(e.target)
    ) {
      e.preventDefault();
      exportSvg();
      return;
    }
    if (e.target.tagName === "INPUT" || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === " ") { e.preventDefault(); playText(); return; }
    const low = e.key.toLowerCase();
    const letters = ALPHABET.includes(low) ? low : TRANSLIT[low] || "";
    if (letters) playLetter(letters[0]);
  });

  window.addEventListener("resize", () => {
    drawScore();
    document.querySelectorAll(".glyph").forEach((card) => {
      drawGlyph(card.querySelector("canvas"), state.font.glyphs[card.dataset.letter]);
    });
  });
}

async function boot() {
  initUI();
  const index = await (await fetch("fonts/index.json")).json();
  const sel = el("fontSel");
  index.forEach((entry) => {
    const o = document.createElement("option");
    o.value = entry.id;
    o.textContent = entry.name;
    sel.appendChild(o);
  });
  sel.addEventListener("change", () => loadFont(index.find((e) => e.id === sel.value)));
  await loadFont(index[0]);
}

boot();

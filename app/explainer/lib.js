// Tiny immediate-mode SVG toolkit for the explainer. Every scene is a pure function
// (t, B) => SVG string, where t is seconds since the scene started and B[i] = {start, end}
// is when narration beat i is spoken. Key every animation off B, never off fixed seconds.
// Canvas: 1920x1080. The header (y < 130) and subtitles (y > 940) belong to the player:
// keep scene content inside x 80..1840, y 150..920.
(function () {
  const C = {
    bg: "#0f1218", panel: "#171c25", panel2: "#1e2430", line: "#2c3442",
    ink: "#e8ecf2", muted: "#8a93a3", faint: "#4a5262",
    blue: "#5b9cff", green: "#3ecf8e", amber: "#f5b041", red: "#ef5a5a",
    darkred: "#a33a3a", purple: "#a78bfa", teal: "#2dd4bf",
  };
  const SANS = "-apple-system, 'Helvetica Neue', Helvetica, Arial, 'Liberation Sans', 'DejaVu Sans', sans-serif";
  const MONO = "Menlo, 'SF Mono', Consolas, 'DejaVu Sans Mono', monospace";
  const col = (c) => C[c] || c || C.blue;
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
  const ease = (x) => { x = clamp(x); return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; };
  // eased 0..1 progress of an animation that starts at `start` and lasts `dur` seconds
  const p = (t, start, dur = 0.6) => ease((t - start) / dur);
  // a moment inside beat i: f = 0 its start, f = 1 its end
  const frac = (B, i, f = 0) => B[i].start + f * (B[i].end - B[i].start);
  const lerp = (a, b, k) => a + (b - a) * k;
  const typed = (str, k) => String(str).slice(0, Math.round(String(str).length * clamp(k)));
  const textW = (str, size, mono) => String(str).length * size * (mono ? 0.6 : 0.54);
  // break a string into lines of at most `max` characters (for text({t}) inside a fixed-width box)
  const wrap = (str, max) => {
    const out = [];
    for (const para of String(str).split("\n")) {
      let line = "";
      for (const w of para.split(/\s+/)) {
        if (line && (line + " " + w).length > max) { out.push(line); line = w; } else line = line ? line + " " + w : w;
      }
      out.push(line);
    }
    return out.join("\n");
  };

  function group(inner, { o = 1, dx = 0, dy = 0, s = 1, cx = 0, cy = 0 } = {}) {
    if (o <= 0.001) return "";
    const tr = `translate(${dx + cx} ${dy + cy}) scale(${s}) translate(${-cx} ${-cy})`;
    return `<g opacity="${clamp(o)}" transform="${tr}">${inner}</g>`;
  }
  // fade + slight rise, the default entrance
  const enter = (inner, t, start, dur = 0.5) => { const k = p(t, start, dur); return group(inner, { o: k, dy: (1 - k) * 14 }); };

  function rect({ x, y, w, h, r = 14, fill = C.panel, stroke = C.line, sw = 2, o = 1, dash }) {
    return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${col(fill)}" stroke="${col(stroke)}" stroke-width="${sw}" opacity="${o}"${dash ? ` stroke-dasharray="${dash}"` : ""}/>`;
  }

  function text({ x, y, t: str, size = 28, color = C.ink, weight = 500, anchor = "start", o = 1, mono = false, lh = 1.3, italic = false }) {
    const lines = String(str).split("\n");
    const spans = lines.map((l, i) => `<tspan x="${x}" dy="${i ? size * lh : 0}">${esc(l)}</tspan>`).join("");
    return `<text x="${x}" y="${y}" font-family="${mono ? MONO : SANS}" font-size="${size}" font-weight="${weight}" fill="${col(color)}" text-anchor="${anchor}" opacity="${o}" dominant-baseline="middle"${italic ? ' font-style="italic"' : ""}>${spans}</text>`;
  }

  // card with a coloured top accent; title (and optional sub) centred
  function box({ x, y, w, h, title = "", sub = "", color = "blue", o = 1, fill = C.panel, size = 28, dash, glow = 0 }) {
    const c = col(color);
    let s = "";
    if (glow > 0) s += rect({ x: x - 6, y: y - 6, w: w + 12, h: h + 12, r: 18, fill: "none", stroke: c, sw: 3, o: glow * 0.6 });
    s += rect({ x, y, w, h, fill, stroke: dash ? c : C.line, dash });
    s += `<rect x="${x}" y="${y}" width="${w}" height="6" rx="3" fill="${c}"/>`;
    const cy = y + h / 2 + 3;
    if (sub) {
      s += text({ x: x + w / 2, y: cy - size * 0.45, t: title, size, weight: 650, anchor: "middle" });
      s += text({ x: x + w / 2, y: cy + size * 0.65, t: sub, size: size * 0.7, color: C.muted, anchor: "middle" });
    } else s += text({ x: x + w / 2, y: cy, t: title, size, weight: 650, anchor: "middle" });
    return group(s, { o });
  }

  // pill-shaped status chip; returns {svg, w} via chip.w helper when you need its width
  function chip({ x, y, t: str, color = "blue", o = 1, size = 22, solid = false, anchor = "start" }) {
    const c = col(color), w = textW(str, size) + size * 1.2, h = size * 1.6;
    const x0 = anchor === "middle" ? x - w / 2 : anchor === "end" ? x - w : x;
    const s = `<rect x="${x0}" y="${y - h / 2}" width="${w}" height="${h}" rx="${h / 2}" fill="${solid ? c : c + "26"}" stroke="${c}" stroke-width="1.5"/>`
      + text({ x: x0 + w / 2, y: y + 1, t: str, size, color: solid ? C.bg : c, weight: 650, anchor: "middle" });
    return group(s, { o });
  }
  chip.width = (str, size = 22) => textW(str, size) + size * 1.2;

  // arrow drawn progressively (k: 0..1); optional label at the midpoint; bend curves it
  function arrow({ x1, y1, x2, y2, k = 1, color = C.muted, label = "", sw = 3, dash, o = 1, bend = 0, size = 20, head = true }) {
    if (k < 0.03 || o <= 0.001) return "";  // a zero-length round-capped line renders as a stray dot
    const c = col(color);
    const mx = (x1 + x2) / 2 - bend * (y2 - y1) / 2, my = (y1 + y2) / 2 + bend * (x2 - x1) / 2;
    const pt = (u) => ({ x: (1 - u) ** 2 * x1 + 2 * (1 - u) * u * mx + u * u * x2, y: (1 - u) ** 2 * y1 + 2 * (1 - u) * u * my + u * u * y2 });
    const n = 24, pts = [];
    for (let i = 0; i <= n * k; i++) pts.push(pt(i / n));
    const e = pt(k); pts.push(e);
    let s = `<polyline points="${pts.map((q) => `${q.x},${q.y}`).join(" ")}" fill="none" stroke="${c}" stroke-width="${sw}" stroke-linecap="round"${dash ? ` stroke-dasharray="${dash}"` : ""}/>`;
    if (head && k > 0.9) {
      const b = pt(Math.max(0, k - 0.04)), a = Math.atan2(e.y - b.y, e.x - b.x), L = 16;
      s += `<polygon points="${e.x},${e.y} ${e.x - L * Math.cos(a - 0.45)},${e.y - L * Math.sin(a - 0.45)} ${e.x - L * Math.cos(a + 0.45)},${e.y - L * Math.sin(a + 0.45)}" fill="${c}"/>`;
    }
    if (label && k > 0.5) {
      const m = pt(0.5), w = textW(label, size) + 16;
      s += `<rect x="${m.x - w / 2}" y="${m.y - size * 0.8}" width="${w}" height="${size * 1.6}" rx="6" fill="${C.bg}"/>`
        + text({ x: m.x, y: m.y + 1, t: label, size, color: c, weight: 600, anchor: "middle" });
    }
    return group(s, { o });
  }
  // a point travelling along a straight segment (for "packets" moving through a pipeline)
  const along = (x1, y1, x2, y2, k) => ({ x: lerp(x1, x2, clamp(k)), y: lerp(y1, y2, clamp(k)) });
  const dot = ({ x, y, r = 9, color = "blue", o = 1 }) => `<circle cx="${x}" cy="${y}" r="${r}" fill="${col(color)}" opacity="${o}"/>`;

  // database table card: name header + column rows revealed by k; hl = column names to highlight
  function table({ x, y, w = 420, name, cols, k = 1, o = 1, color = "purple", hl = [], note = "" }) {
    const rowH = 40, head = 56, shown = Math.ceil(cols.length * clamp(k));
    const h = head + cols.length * rowH + (note ? 46 : 12);
    let s = rect({ x, y, w, h, fill: C.panel });
    s += `<rect x="${x}" y="${y}" width="${w}" height="${head}" rx="14" fill="${col(color)}22"/>`;
    s += text({ x: x + 22, y: y + head / 2, t: name, size: 26, weight: 700, color, mono: true });
    cols.slice(0, shown).forEach((c, i) => {
      const yy = y + head + i * rowH + rowH / 2 + 4, on = hl.includes(c);
      if (on) s += `<rect x="${x + 8}" y="${yy - rowH / 2 + 3}" width="${w - 16}" height="${rowH - 6}" rx="6" fill="${C.amber}22"/>`;
      s += text({ x: x + 22, y: yy, t: c, size: 22, mono: true, color: on ? C.amber : C.ink });
    });
    if (note && k >= 1) s += text({ x: x + 22, y: y + h - 26, t: note, size: 19, mono: true, color: C.amber, weight: 650 });
    return group(s, { o });
  }
  table.height = (cols, note) => 56 + cols.length * 40 + (note ? 46 : 12);

  // code panel: lines revealed by k (whole lines); hl = index of a line to highlight
  function code({ x, y, w = 760, lines, k = 1, o = 1, title = "", hl = -1, size = 22, color = "blue" }) {
    const lh = size * 1.55, top = title ? 58 : 22, h = top + lines.length * lh + 18;
    let s = rect({ x, y, w, h, fill: "#11151c" });
    if (title) s += text({ x: x + 22, y: y + 30, t: title, size: 22, color, weight: 700, mono: true });
    const shown = Math.ceil(lines.length * clamp(k));
    lines.slice(0, shown).forEach((l, i) => {
      const yy = y + top + i * lh + lh / 2;
      if (i === hl) s += `<rect x="${x + 8}" y="${yy - lh / 2}" width="${w - 16}" height="${lh}" rx="6" fill="${C.amber}22"/>`;
      s += text({ x: x + 22, y: yy, t: l, size, mono: true, color: i === hl ? C.amber : C.ink });
    });
    return group(s, { o });
  }
  code.height = (n, title = true, size = 22) => (title ? 58 : 22) + n * size * 1.55 + 18;

  const strike = ({ x1, x2, y, k = 1, color = "red", sw = 4 }) =>
    k > 0.03 ? `<line x1="${x1}" y1="${y}" x2="${lerp(x1, x2, clamp(k))}" y2="${y}" stroke="${col(color)}" stroke-width="${sw}" stroke-linecap="round"/>` : "";

  function bar({ x, y, w, h = 18, v = 0, color = "blue", o = 1, track = C.panel2 }) {
    return group(`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${h / 2}" fill="${track}"/>`
      + `<rect x="${x}" y="${y}" width="${Math.max(0, w * clamp(v))}" height="${h}" rx="${h / 2}" fill="${col(color)}"/>`, { o });
  }

  // simple line icons centred on (x, y), size s
  function icon(name, { x, y, s = 48, color = "ink", o = 1, sw = 3.5 }) {
    const c = col(color), r = s / 2;
    const st = `fill="none" stroke="${c}" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round"`;
    const P = {
      check: `<polyline points="${x - r * .6},${y} ${x - r * .15},${y + r * .45} ${x + r * .65},${y - r * .5}" ${st}/>`,
      cross: `<path d="M${x - r * .5} ${y - r * .5} L${x + r * .5} ${y + r * .5} M${x + r * .5} ${y - r * .5} L${x - r * .5} ${y + r * .5}" ${st}/>`,
      user: `<circle cx="${x}" cy="${y - r * .35}" r="${r * .3}" ${st}/><path d="M${x - r * .65} ${y + r * .7} Q${x} ${y - r * .1} ${x + r * .65} ${y + r * .7}" ${st}/>`,
      clock: `<circle cx="${x}" cy="${y}" r="${r * .75}" ${st}/><polyline points="${x},${y - r * .45} ${x},${y} ${x + r * .35},${y + r * .2}" ${st}/>`,
      lock: `<rect x="${x - r * .55}" y="${y - r * .1}" width="${r * 1.1}" height="${r * .8}" rx="4" ${st}/><path d="M${x - r * .32} ${y - r * .1} V${y - r * .4} A${r * .32} ${r * .32} 0 0 1 ${x + r * .32} ${y - r * .4} V${y - r * .1}" ${st}/>`,
      bell: `<path d="M${x - r * .55} ${y + r * .35} Q${x - r * .5} ${y - r * .7} ${x} ${y - r * .7} Q${x + r * .5} ${y - r * .7} ${x + r * .55} ${y + r * .35} Z" ${st}/><path d="M${x - r * .15} ${y + r * .6} Q${x} ${y + r * .75} ${x + r * .15} ${y + r * .6}" ${st}/>`,
      warn: `<path d="M${x} ${y - r * .7} L${x + r * .75} ${y + r * .6} L${x - r * .75} ${y + r * .6} Z" ${st}/><path d="M${x} ${y - r * .2} V${y + r * .15} M${x} ${y + r * .38} V${y + r * .4}" ${st}/>`,
      moon: `<path d="M${x + r * .35} ${y - r * .6} A${r * .7} ${r * .7} 0 1 0 ${x + r * .6} ${y + r * .3} A${r * .55} ${r * .55} 0 0 1 ${x + r * .35} ${y - r * .6} Z" ${st}/>`,
      tab: `<rect x="${x - r * .75}" y="${y - r * .55}" width="${r * 1.5}" height="${r * 1.1}" rx="5" ${st}/><line x1="${x - r * .75}" y1="${y - r * .25}" x2="${x + r * .75}" y2="${y - r * .25}" ${st}/>`,
      db: `<ellipse cx="${x}" cy="${y - r * .5}" rx="${r * .65}" ry="${r * .2}" ${st}/><path d="M${x - r * .65} ${y - r * .5} V${y + r * .5} A${r * .65} ${r * .2} 0 0 0 ${x + r * .65} ${y + r * .5} V${y - r * .5}" ${st}/>`,
      bolt: `<polyline points="${x + r * .1},${y - r * .75} ${x - r * .4},${y + r * .1} ${x + r * .05},${y + r * .1} ${x - r * .1},${y + r * .75} ${x + r * .45},${y - r * .1} ${x},${y - r * .1} ${x + r * .1},${y - r * .75}" ${st}/>`,
    };
    return group(P[name] || "", { o });
  }

  window.L = { C, SANS, MONO, col, esc, clamp, ease, p, frac, lerp, typed, textW, wrap, group, enter, rect, text, box, chip, arrow, along, dot, table, code, strike, bar, icon };
  window.SCENES = {};
  window.scene = (id, fn) => { window.SCENES[id] = fn; };
})();

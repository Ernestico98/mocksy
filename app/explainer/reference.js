// Reference scene: shows the conventions every scene follows. Topic: the read path of a URL shortener.
//   beat 1: "A redirect is a lookup: a short code comes in, a long URL goes out."
//   beat 2: "Reads outnumber writes about a hundred to one, so a cache sits in front of the database."
//   beat 3: "A hit answers in about a millisecond. A miss falls through to the database and fills the cache."
// Pure function of t; everything is keyed off the beats B[i].start / B[i].end, never absolute seconds.
scene("ref", (t, B) => {
  const { C, p, frac, enter, group, text, box, chip, arrow, table, dot, along, lerp } = L;
  let s = "";

  // beat 1: client -> service, with the short URL travelling along the arrow
  s += enter(box({ x: 120, y: 420, w: 300, h: 140, title: "Browser", sub: "GET /x7Kq2", color: "muted" }), t, B[0].start);
  s += enter(box({ x: 620, y: 420, w: 340, h: 140, title: "Redirect service", sub: "stateless", color: "blue" }), t, frac(B, 0, 0.3));
  s += arrow({ x1: 420, y1: 490, x2: 620, y2: 490, k: p(t, frac(B, 0, 0.45), 0.6), color: "blue" });
  const reply = p(t, frac(B, 0, 0.8), 0.5);
  s += group(chip({ x: 520, y: 620, t: "301 → long URL", color: "green", anchor: "middle" }), { o: reply });

  // beat 2: the cache appears in front of the database; the ratio is said, so it is shown
  s += enter(box({ x: 1120, y: 250, w: 320, h: 130, title: "Cache", sub: "code → URL", color: "teal" }), t, B[1].start + 0.2);
  s += enter(table({ x: 1120, y: 560, w: 420, name: "links", cols: ["code  (PK)", "long_url", "created_at"], k: p(t, frac(B, 1, 0.4), 0.8) }), t, frac(B, 1, 0.35));
  s += enter(chip({ x: 1480, y: 315, t: "reads : writes ≈ 100 : 1", color: "amber" }), t, frac(B, 1, 0.6));
  s += arrow({ x1: 960, y1: 460, x2: 1120, y2: 330, k: p(t, frac(B, 1, 0.3), 0.5), color: "teal" });
  s += arrow({ x1: 960, y1: 520, x2: 1120, y2: 640, k: p(t, frac(B, 1, 0.5), 0.5), color: "purple", dash: "10 8" });

  // beat 3: a hit (fast, green) then a miss that falls through and fills the cache
  const hitK = p(t, frac(B, 2, 0.05), 0.8);
  if (hitK > 0 && hitK < 1) { const q = along(960, 460, 1120, 330, hitK); s += dot({ ...q, color: "green" }); }
  s += enter(chip({ x: 1040, y: 230, t: "hit ≈ 1 ms", color: "green", anchor: "middle" }), t, frac(B, 2, 0.2));
  const missK = p(t, frac(B, 2, 0.5), 0.8);
  if (missK > 0 && missK < 1) { const q = along(960, 520, 1120, 640, missK); s += dot({ ...q, color: "amber" }); }
  s += enter(chip({ x: 820, y: 760, t: "miss → read DB, then fill cache", color: "amber", anchor: "middle" }), t, frac(B, 2, 0.6));
  const fill = p(t, frac(B, 2, 0.75), 0.6);
  s += arrow({ x1: 1300, y1: 560, x2: 1300, y2: 380, k: fill, color: "amber", label: "fill", bend: 0 });

  // a caption that slides in last and stays (cumulative build)
  const cy = lerp(900, 880, p(t, frac(B, 2, 0.85), 0.5));
  s += enter(text({ x: 960, y: cy, t: "Most redirects never touch the database", size: 30, color: C.muted, anchor: "middle" }), t, frac(B, 2, 0.85));
  return s;
});

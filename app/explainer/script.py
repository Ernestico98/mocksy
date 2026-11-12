"""The model-answer script: schema validation, the neutrality check, and everything code derives
from it without a model (markdown write-up, YouTube text, the solution diagram as SVG)."""

import html
import math
import re

SCENE_ID = re.compile(r"^s\d{2}$")
COLORS = {"blue", "green", "amber", "red", "purple", "teal", "muted"}
WORDS_PER_SCENE = 95  # ~750 words for 8 scenes: about 5-6 minutes of narration


def words(script: dict) -> int:
    return sum(len(b.get("say", "").split()) for s in script.get("scenes", []) for b in s.get("beats", []))


# ------------------------------------------------------------------ schema

def validate(script, scenes: int) -> list[str]:
    """Problems with script.json's shape and length; empty when it's usable. `scenes` is the target
    count: scenes-1..scenes are accepted."""
    if not isinstance(script, dict):
        return ["script.json must be a JSON object"]
    errs: list[str] = []

    def text(v, where, max_len=None, required=True):
        if not isinstance(v, str) or (required and not v.strip()):
            errs.append(f"{where} must be a non-empty string")
        elif max_len and len(v) > max_len:
            errs.append(f"{where} is too long ({len(v)} > {max_len} characters)")

    text(script.get("title"), "title", 100)
    text(script.get("description"), "description", 700)
    phases = script.get("phases")
    keys: set[str] = set()
    if not isinstance(phases, list) or not 1 <= len(phases) <= 5:
        errs.append("phases must be a list of 1-5 {key, label}")
    else:
        for i, p in enumerate(phases):
            if not isinstance(p, dict):
                errs.append(f"phases[{i}] must be an object")
                continue
            text(p.get("key"), f"phases[{i}].key", 30)
            text(p.get("label"), f"phases[{i}].label", 24)
            keys.add(p.get("key"))

    lo = max(1, scenes - 1)
    sc = script.get("scenes")
    if not isinstance(sc, list) or not lo <= len(sc) <= scenes:
        n = len(sc) if isinstance(sc, list) else 0
        errs.append(f"scenes must be a list of {lo}-{scenes} scenes (got {n})")
        sc = sc if isinstance(sc, list) else []
    seen = set()
    for i, s in enumerate(sc):
        where = f"scenes[{i}]"
        if not isinstance(s, dict):
            errs.append(f"{where} must be an object")
            continue
        sid = s.get("id")
        if not isinstance(sid, str) or not SCENE_ID.match(sid) or sid in seen:
            errs.append(f'{where}.id must be a unique id like "s01"')
        seen.add(sid)
        if keys and s.get("phase") not in keys:
            errs.append(f"{where}.phase must be one of the phase keys ({', '.join(sorted(k for k in keys if k))})")
        text(s.get("title"), f"{where}.title", 48)
        beats = s.get("beats")
        if not isinstance(beats, list) or not 2 <= len(beats) <= 7:
            errs.append(f"{where}.beats must be a list of 2-7 beats")
            continue
        for j, b in enumerate(beats):
            if not isinstance(b, dict):
                errs.append(f"{where}.beats[{j}] must be an object")
                continue
            text(b.get("say"), f"{where}.beats[{j}].say", 600)
            text(b.get("show"), f"{where}.beats[{j}].show", 800)

    target = scenes * WORDS_PER_SCENE
    n = words(script)
    if sc and n > target * 1.25:
        errs.append(f"the narration has {n} words; keep it under {round(target * 1.15)} (target ~{target})")

    a = script.get("assumptions", [])
    if not isinstance(a, list) or not all(isinstance(x, str) for x in a) or len(a) > 15:
        errs.append("assumptions must be a list of at most 15 strings")
    errs += validate_diagram(script.get("diagram"))
    return errs


def validate_diagram(d) -> list[str]:
    if not isinstance(d, dict):
        return ["diagram must be an object with title, nodes and edges"]
    errs = []
    if not isinstance(d.get("title"), str) or not d["title"].strip():
        errs.append("diagram.title must be a non-empty string")
    nodes = d.get("nodes")
    if not isinstance(nodes, list) or not 2 <= len(nodes) <= 18:
        return errs + ["diagram.nodes must be a list of 2-18 nodes"]
    ids, cells = set(), set()
    for i, n in enumerate(nodes):
        where = f"diagram.nodes[{i}]"
        if not isinstance(n, dict):
            errs.append(f"{where} must be an object")
            continue
        if not isinstance(n.get("id"), str) or n["id"] in ids:
            errs.append(f"{where}.id must be a unique string")
        ids.add(n.get("id"))
        if not isinstance(n.get("label"), str) or not n["label"].strip() or len(n["label"]) > 32:
            errs.append(f"{where}.label must be a string of 1-32 characters")
        if "sub" in n and (not isinstance(n["sub"], str) or len(n["sub"]) > 44):
            errs.append(f"{where}.sub must be a string of at most 44 characters")
        col, row = n.get("col"), n.get("row")
        if not isinstance(col, int) or not isinstance(row, int) or not (0 <= col <= 5 and 0 <= row <= 4):
            errs.append(f"{where} needs integer col 0-5 and row 0-4")
        elif (col, row) in cells:
            errs.append(f"{where} shares its cell ({col}, {row}) with another node")
        cells.add((col, row))
        if "color" in n and n["color"] not in COLORS:
            errs.append(f"{where}.color must be one of {', '.join(sorted(COLORS))}")
    edges = d.get("edges", [])
    if not isinstance(edges, list) or len(edges) > 30:
        return errs + ["diagram.edges must be a list of at most 30 edges"]
    for i, e in enumerate(edges):
        if not isinstance(e, dict) or e.get("from") not in ids or e.get("to") not in ids:
            errs.append(f"diagram.edges[{i}] must connect two node ids (from, to)")
        elif "label" in e and (not isinstance(e["label"], str) or len(e["label"]) > 28):
            errs.append(f"diagram.edges[{i}].label must be a string of at most 28 characters")
    return errs


# ------------------------------------------------------------------ neutrality

# The video is published as a stand-alone tutorial: it must never refer to the person who practised,
# the practice itself or how it went. Technical uses of the same words are allowed explicitly.
# (pattern, why, technical words allowed right before it, technical words allowed right after it)
TECH_SESSION_BEFORE = {"user", "http", "login", "sticky", "browser", "web", "chat", "client", "streaming", "checkout",
                       "tcp", "tls", "ssh", "db", "database", "per", "authenticated", "active", "anonymous", "user's"}
TECH_SESSION_AFTER = r"(token|store|cookie|id|state|affinity|key|data|storage|timeout|management|layer|cache|table|replay|ticket|service|expir\w*)s?"
TECH_SCORE_BEFORE = {"relevance", "ranking", "similarity", "risk", "credit", "trust", "quality", "confidence", "anomaly",
                     "fraud", "match", "spam", "health", "popularity", "reputation", "priority", "z", "f1", "bm25", "engagement"}
FEEDBACK_BEFORE = {"user", "customer", "client", "player", "listener", "reader", "viewer", "rider", "driver", "seller",
                   "buyer", "explicit", "implicit", "haptic", "visual", "instant", "immediate"}

FORBIDDEN = [
    (r"\bcandidates?\b|\binterviewees?\b|\binterviewers?\b", "refers to a candidate or an interviewer", None, None),
    (r"\b(this|that|the|your|our|today'?s|last|previous|mock|practice) interviews?\b", "refers to a specific interview", None, None),
    (r"\bmock(s|ed)?\b", "mentions a mock", None, r"(server|object|service|data|api|implementation)s?"),
    (r"\bsessions?\b", "mentions a session (non-technical use)", TECH_SESSION_BEFORE, TECH_SESSION_AFTER),
    (r"\bmistakes?\b|\bmissed\b|\bforgot(ten)?\b|\boverlooked\b", "talks about mistakes or omissions", None, None),
    (r"\bverdicts?\b|\bhire[sd]?\b|\bhiring\b|\bno[- ]hire\b|\bpass(ed)? the (round|interview)\b",
     "mentions a verdict or hiring", None, None),
    (r"\bscor(e[sd]?|ing)\b", "mentions a score", TECH_SCORE_BEFORE, r"(function|threshold|model)s?"),
    (r"\bfeedback\b", "mentions feedback (non-technical use)", FEEDBACK_BEFORE, r"loops?"),
    (r"\byou (said|say|didn'?t|did not|missed|forgot|mentioned|chose|drew|proposed|suggested|skipped|"
     r"should have|could have|got|were asked|answered|struggled)\b", "speaks to the candidate about what they did", None, None),
    (r"\byour (answer|whiteboard|diagram|sketch|interview|mistakes?|scores?|performance|attempt|solution was)\b",
     "speaks to the candidate about their attempt", None, None),
    # Spanish
    (r"\bcandidat[oa]s?\b|\bentrevistad[oa]s?\b|\bentrevistador(a|es)?\b", "refers to a candidate or an interviewer (es)", None, None),
    (r"\bsimulacros?\b|\bveredictos?\b|\bcontrataci[oó]n\b|\bpuntuaci[oó]n\b|\bolvidaste\b|\bolvid[oó]\b|"
     r"\bdijiste\b|\bno hiciste\b|\btus errores\b|\btu respuesta\b|\btu diagrama\b",
     "refers to the practice or the candidate (es)", None, None),
]


def _technical(text: str, m: re.Match, before: set | None, after: str | None) -> bool:
    prev = re.findall(r"[\w'-]+", text[:m.start()])
    if before and prev and prev[-1].lower() in before:
        return True
    return bool(after and re.match(r"[\s-]+" + after + r"\b", text[m.end():], flags=re.I))


def _company_terms(company: str) -> list[str]:
    company = (company or "").strip()
    if len(company) < 2 or company.lower() in {"none", "generic"}:
        return []
    joined = re.sub(r"\W+", "", company)
    return list({company, joined} if len(joined) >= 3 else {company})


def check_text(text: str, company: str = "") -> list[str]:
    out = []
    for pattern, why, before, after in FORBIDDEN:
        m = next((m for m in re.finditer(pattern, text, flags=re.I) if not _technical(text, m, before, after)), None)
        if m:
            out.append(f'"{m.group(0)}" ({why})')
    for term in _company_terms(company):
        m = re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, flags=re.I)
        if m:
            out.append(f'"{m.group(0)}" (names the target company)')
            break
    return out


def script_texts(script: dict) -> list[tuple[str, str]]:
    """Every piece of script.json that ends up in the video, the write-up or the YouTube text."""
    out = [("title", script.get("title", "")), ("description", script.get("description", ""))]
    for i, a in enumerate(script.get("assumptions", []) or []):
        out.append((f"assumptions[{i}]", a))
    for s in script.get("scenes", []) or []:
        sid = s.get("id", "?")
        out.append((f"{sid}.title", s.get("title", "")))
        for j, b in enumerate(s.get("beats", []) or []):
            out.append((f"{sid}.beats[{j}].say", b.get("say", "")))
            out.append((f"{sid}.beats[{j}].show", b.get("show", "")))
    d = script.get("diagram") or {}
    out.append(("diagram.title", d.get("title", "")))
    for n in d.get("nodes", []) or []:
        out.append((f"diagram node {n.get('id')}", f"{n.get('label', '')} {n.get('sub', '')}"))
    for e in d.get("edges", []) or []:
        out.append(("diagram edge", e.get("label", "")))
    return [(w, t) for w, t in out if isinstance(t, str) and t]


def neutrality(script: dict, company: str = "") -> list[str]:
    """Violations of the publishing rules, as "where: what" lines."""
    return [f"{where}: {v}" for where, text in script_texts(script) for v in check_text(text, company)]


STRING_LITERAL = re.compile(r"\"((?:[^\"\\\n]|\\.)*)\"|'((?:[^'\\\n]|\\.)*)'|`((?:[^`\\]|\\.)*)`")


def scene_literals(source: str) -> list[str]:
    """Human-readable string literals of a scene file (what can end up on screen)."""
    out = []
    for m in STRING_LITERAL.finditer(source):
        s = next(g for g in m.groups() if g is not None)
        if re.search(r"[A-Za-zÀ-ÿ]{3,}\s+[A-Za-zÀ-ÿ]", s) and "<" not in s:  # words, not markup or colours
            out.append(s)
    return out


# ------------------------------------------------------------------ derived text

def clock(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def chapters(script: dict, timings: dict) -> list[tuple[str, str]]:
    t, out = 0.0, []
    for s in script["scenes"]:
        out.append((clock(t), s["title"]))
        t += timings[s["id"]]["duration"]
    return out


def markdown(script: dict, diagram_file: str | None = "diagram.svg") -> str:
    out = [f"# {script['title']}", "", script.get("description", "").strip(), ""]
    if diagram_file:
        out += [f"![{script.get('diagram', {}).get('title', 'Solution diagram')}]({diagram_file})", ""]
    for i, s in enumerate(script["scenes"], 1):
        out += [f"## {i}. {s['title']}", "", " ".join(b["say"].strip() for b in s["beats"]), ""]
    if script.get("assumptions"):
        out += ["## Assumed figures", "", *[f"- {a}" for a in script["assumptions"]], ""]
    return "\n".join(out).rstrip() + "\n"


def youtube(script: dict, timings: dict) -> str:
    out = ["TITLE", script["title"], "", "DESCRIPTION", script.get("description", "").strip(), ""]
    out += [f"{t} {title}" for t, title in chapters(script, timings)]
    if script.get("assumptions"):
        out += ["", "Assumed figures (illustrative, for the estimates):", *[f"- {a}" for a in script["assumptions"]]]
    return "\n".join(out).rstrip() + "\n"


# ------------------------------------------------------------------ diagram

PALETTE = {
    "bg": "#0f1218", "panel": "#171c25", "line": "#2c3442", "ink": "#e8ecf2", "muted": "#8a93a3",
    "blue": "#5b9cff", "green": "#3ecf8e", "amber": "#f5b041", "red": "#ef5a5a", "purple": "#a78bfa", "teal": "#2dd4bf",
}
FONT = "-apple-system, 'Helvetica Neue', Helvetica, Arial, 'Liberation Sans', 'DejaVu Sans', sans-serif"
CELL_W, CELL_H, BOX_W, BOX_H, MARGIN, TOP = 470, 240, 270, 116, 60, 110


def _wrap(text: str, width: int) -> list[str]:
    lines, line = [], ""
    for w in text.split():
        if line and len(line) + 1 + len(w) > width:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    return (lines + [line])[:3]


def _border_point(cx, cy, tx, ty, pad=4):
    """Where the segment from a box centre towards (tx, ty) leaves the box (plus a little gap)."""
    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0:
        return cx, cy
    k = min((BOX_W / 2 + pad) / abs(dx) if dx else math.inf, (BOX_H / 2 + pad) / abs(dy) if dy else math.inf)
    return cx + dx * k, cy + dy * k


def _hits_box(p0, p1, boxes) -> bool:
    for i in range(1, 20):
        x = p0[0] + (p1[0] - p0[0]) * i / 20
        y = p0[1] + (p1[1] - p0[1]) * i / 20
        if any(abs(x - bx) < BOX_W / 2 + 6 and abs(y - by) < BOX_H / 2 + 6 for bx, by in boxes):
            return True
    return False


def diagram_svg(d: dict) -> str:
    """Renders the script's structured diagram (nodes on a grid, labelled arrows) in the video's style.
    Empty rows and columns are dropped; an edge that would cross another box bends around it."""
    nodes = d["nodes"]
    cols = sorted({n["col"] for n in nodes})
    rows = sorted({n["row"] for n in nodes})
    w, h = MARGIN * 2 + len(cols) * CELL_W, TOP + MARGIN + len(rows) * CELL_H
    pos = {n["id"]: (MARGIN + cols.index(n["col"]) * CELL_W + CELL_W / 2, TOP + rows.index(n["row"]) * CELL_H + CELL_H / 2)
           for n in nodes}
    e = html.escape
    P = PALETTE
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" font-family="{e(FONT)}">',
           "<defs>" + "".join(
               f'<marker id="a-{c}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
               f'<path d="M0,0 L10,5 L0,10 z" fill="{P[c]}"/></marker>' for c in ("muted", "blue", "green", "amber", "red", "purple", "teal")) + "</defs>",
           f'<rect width="{w}" height="{h}" fill="{P["bg"]}"/>',
           f'<text x="{MARGIN}" y="{MARGIN + 6}" font-size="34" font-weight="700" fill="{P["ink"]}">{e(d["title"])}</text>',
           f'<line x1="{MARGIN}" y1="{MARGIN + 30}" x2="{w - MARGIN}" y2="{MARGIN + 30}" stroke="{P["line"]}" stroke-width="2"/>']
    labels = []
    for n_edge, ed in enumerate(d.get("edges", [])):
        if ed["from"] not in pos or ed["to"] not in pos or ed["from"] == ed["to"]:
            continue
        a, b = pos[ed["from"]], pos[ed["to"]]
        others = [p for k, p in pos.items() if k not in (ed["from"], ed["to"])]
        c = ed.get("color") if ed.get("color") in P else "muted"
        style = f' stroke="{P[c]}" stroke-width="2.5" fill="none" marker-end="url(#a-{c})"'
        style += f' marker-start="url(#a-{c})"' if ed.get("both") else ""
        style += ' stroke-dasharray="9 7"' if ed.get("dashed") else ""
        if _hits_box(a, b, others):  # bend around whatever is in the way
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            length = math.hypot(b[0] - a[0], b[1] - a[1])
            side = -1 if n_edge % 2 == 0 else 1
            qx = mx + side * (b[1] - a[1]) / length * CELL_H * 0.75
            qy = my - side * (b[0] - a[0]) / length * CELL_H * 0.75
            sx, sy = _border_point(*a, qx, qy)
            tx, ty = _border_point(*b, qx, qy)
            out.append(f'<path d="M{sx:.1f},{sy:.1f} Q{qx:.1f},{qy:.1f} {tx:.1f},{ty:.1f}"{style}/>')
            lx, ly = 0.25 * sx + 0.5 * qx + 0.25 * tx, 0.25 * sy + 0.5 * qy + 0.25 * ty
        else:
            sx, sy = _border_point(*a, *b)
            tx, ty = _border_point(*b, *a)
            out.append(f'<line x1="{sx:.1f}" y1="{sy:.1f}" x2="{tx:.1f}" y2="{ty:.1f}"{style}/>')
            lx, ly = (sx + tx) / 2, (sy + ty) / 2
        if ed.get("label"):
            lines = _wrap(ed["label"], 18)[:2]
            lw = max(len(t) for t in lines) * 9 + 16
            lh = 22 * len(lines) + 6
            if abs(ty - sy) < abs(tx - sx) * 0.3:  # mostly horizontal: sit above the line
                ly -= lh / 2 + 6
            labels.append(f'<rect x="{lx - lw / 2:.1f}" y="{ly - lh / 2:.1f}" width="{lw:.1f}" height="{lh}" rx="6" fill="{P["bg"]}" opacity="0.92"/>')
            for i, t in enumerate(lines):
                labels.append(f'<text x="{lx:.1f}" y="{ly - lh / 2 + 20 + i * 22:.1f}" font-size="16" font-weight="600" '
                              f'fill="{P[c]}" text-anchor="middle">{e(t)}</text>')
    for n in nodes:
        cx, cy = pos[n["id"]]
        c = P.get(n.get("color") or "blue", P["blue"])
        x, y = cx - BOX_W / 2, cy - BOX_H / 2
        out.append(f'<rect x="{x}" y="{y}" width="{BOX_W}" height="{BOX_H}" rx="14" fill="{P["panel"]}" stroke="{P["line"]}" stroke-width="2"/>'
                   f'<rect x="{x}" y="{y}" width="{BOX_W}" height="6" rx="3" fill="{c}"/>')
        title = _wrap(n["label"], 19)[:2]
        sub = _wrap(n.get("sub", ""), 30)[:2] if n.get("sub") else []
        lines = [(t, 24, 650, P["ink"]) for t in title] + [(t, 16, 500, P["muted"]) for t in sub]
        total = sum(s * 1.25 for _, s, _, _ in lines)
        yy = cy + 3 - total / 2
        for t, size, weight, color in lines:
            yy += size * 1.25
            out.append(f'<text x="{cx}" y="{yy - size * 0.3:.1f}" font-size="{size}" font-weight="{weight}" fill="{color}" text-anchor="middle">{e(t)}</text>')
    out += labels + ["</svg>"]
    return "\n".join(out)

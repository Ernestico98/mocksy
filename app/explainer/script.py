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
# the practice itself or how it went. The rules match phrases and contexts (interview or practice
# words, second person about what someone did, verdict language), not bare words, so technical uses
# ("a Raft candidate", "session token", "relevance score", "user feedback", "mock the payment API",
# "forgot password", "missed heartbeat") stay allowed.

# A sentence with one of these is technical: "candidate" there is a Raft node, a key, a search result...
TECH_CANDIDATE = re.compile(
    r"\b(raft|leader|election|vote[sd]?|votes|term|nodes?|follower|quorum|keys?|index|generation|replicas?|"
    r"servers?|search|ranking|ranker|retrieval|matches|match|query|queries|recommendations?|items?|solutions?|"
    r"líder|elección|votos?|nodos?|claves?|réplicas?|servidor(es)?)\b", re.I)

YOU_DID = (r"went|talked|mentioned|said|chose|drew|proposed|suggested|skipped|missed|forgot|jumped|focused|"
           r"spent|answered|struggled|explained|described|covered|ignored|overlooked|glossed|handled|did")

FORBIDDEN: list[tuple[str, str]] = [
    # interview / practice context
    (r"\binterview(s|er|ers|ee|ees|ing)?\b(?! (schedul\w*|booking|slots?|platform|calendar)s?\b)",
     "mentions an interview"),
    (r"\b(mock|practice|training|coaching) (round|interview|session|exam|run)s?\b", "mentions a practice round"),
    (r"\b(this|that|today'?s|our|your|the last|last|previous) (mock|practice|round)\b(?! (server|object|service|data|api|"
     r"implementation|endpoint|client)s?\b)", "mentions a practice round"),
    (r"\b(this|that|today'?s|our|your|last|previous) sessions?\b(?! (token|store|cookie|id|state|affinity|key|data|storage|"
     r"timeout|layer|cache|table|replay|ticket|service|expir\w*)s?\b)", "mentions the session"),
    (r"\brecap of (the|this|your) (round|session|answer)\b", "recaps a practice round"),
    # people being assessed
    (r"\binterviewees?\b|\bcandidat(e|es|o|os|a|as)\b", "refers to a candidate"),  # filtered by TECH_CANDIDATE below
    # second person about what someone did ("you said", "earlier you talked about", "you never mentioned")
    (r"(?<!\bif )(?<!\bwhen )(?<!\bonce )(?<!\bas )\byou (just |really |also |then |first |quickly |already )?(" + YOU_DID
     + r")\b", "speaks to someone about what they did"),
    (r"\byou (didn'?t|did not|never|forgot to|failed to) (mention|say|ask|explain|consider|discuss|cover|talk|address|get)\w*\b",
     "speaks to someone about what they did"),
    (r"\b(earlier|before|here|there|then|at first),? you (" + YOU_DID + r"|were|had)\b", "speaks to someone about what they did"),
    (r"\byour (answer|whiteboard|diagram|sketch|interview|mistakes?|scores?|performance|attempt|approach was|design was|"
     r"solution was)\b", "speaks to someone about their attempt"),
    # mistakes, verdicts, scores, feedback about a person
    (r"\bmistakes?\b", "talks about mistakes"),
    (r"\bverdicts?\b|\b(strong|lean|weak)[- ](yes|no|hire|no[- ]hire)\b|\bno[- ]hire\b|\b(hire|hiring) (decision|signal|bar|"
     r"committee|recommendation)s?\b|\b(pass(ed)?|fail(ed)?|ace[sd]?) the (round|interview|loop|onsite)\b", "mentions a verdict"),
    (r"\bscored (a |an )?\d|\bscore of \d( out of \d+|/\d+)\b|\brubric\b|\byour scores?\b", "mentions a score"),
    (r"\b(your|my|the interviewer'?s|this|that|interview) feedback\b|\bthe feedback (said|says|was|is|noted|mentioned)\b",
     "mentions feedback on an attempt"),
    # Spanish
    (r"\bentrevist(a|as|ador|adora|adores|ado|ada|ados|adas)\b", "mentions an interview (es)"),
    (r"\bsimulacros?\b|\bveredictos?\b|\btu (puntuación|nota|respuesta|diagrama|intento)\b|\bdecisión de contratación\b",
     "refers to the practice or a verdict (es)"),
    (r"\b(no )?(mencionaste|dijiste|olvidaste|elegiste|dibujaste|propusiste|saltaste|hablaste|explicaste|hiciste)\b|"
     r"\bte (faltó|olvidaste|saltaste)\b|\bfuiste directo\b", "speaks to someone about what they did (es)"),
    (r"\b(un |los |el )?errores? comunes?\b|\berror común\b|\btus errores\b", "talks about mistakes (es)"),
]

# Exact on-screen labels that only make sense in a practice context
BARE_LABELS = re.compile(r"^\s*(mock|practice|practice round|mock round|verdict|recap|feedback|score)\s*[.:!]?\s*$", re.I)


def _company_pattern(company: str) -> re.Pattern | None:
    core = re.sub(r"[^0-9a-z]", "", (company or "").lower())
    if len(core) < 3 or core in {"none", "generic"}:
        return None
    # "AcmeCorp", "Acme Corp", "acme-corp", "Acme.Corp" all match
    return re.compile(r"(?<![0-9a-z])" + r"[\s\-_.·']*".join(re.escape(c) for c in core) + r"(?![0-9a-z])", re.I)


def check_text(text: str, company: str = "") -> list[str]:
    out = []
    for pattern, why in FORBIDDEN:
        for m in re.finditer(pattern, text, flags=re.I):
            if why == "refers to a candidate" and not m.group(0).lower().startswith("interviewee"):
                sentence = re.split(r"(?<=[.!?])\s+", text[max(0, m.start() - 300):m.end() + 300])
                around = next((x for x in sentence if m.group(0) in x), text)
                if TECH_CANDIDATE.search(around):
                    continue
            out.append(f'"{m.group(0)}" ({why})')
            break
    if BARE_LABELS.match(text):
        out.append(f'"{text.strip()}" (practice-context label)')
    pat = _company_pattern(company)
    if pat and (m := pat.search(text)):
        out.append(f'"{m.group(0)}" (names the target company)')
    return out


def script_texts(script: dict) -> list[tuple[str, str]]:
    """Every piece of script.json that ends up in the video, the write-up or the YouTube text."""
    out = [("title", script.get("title", "")), ("description", script.get("description", ""))]
    for i, p in enumerate(script.get("phases", []) or []):
        if isinstance(p, dict):
            out.append((f"phases[{i}].label", p.get("label", "")))
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
        if re.search(r"[A-Za-zÀ-ÿ]{3,}", s) and "<" not in s and not re.fullmatch(r"#?[0-9a-fA-F]{3,8}", s):
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


def _point(path, u):
    (sx, sy), q, (tx, ty) = path
    if q is None:
        return sx + (tx - sx) * u, sy + (ty - sy) * u
    qx, qy = q
    return ((1 - u) ** 2 * sx + 2 * (1 - u) * u * qx + u * u * tx, (1 - u) ** 2 * sy + 2 * (1 - u) * u * qy + u * u * ty)


def _rect_hits(x, y, w, h, rects, pad=6):
    return any(x < rx + rw + pad and rx - pad < x + w and y < ry + rh + pad and ry - pad < y + h for rx, ry, rw, rh in rects)


def _path_hits(path, boxes) -> bool:
    for i in range(1, 40):
        x, y = _point(path, i / 40)
        if _rect_hits(x, y, 0, 0, boxes, pad=8):
            return True
    return False


def diagram_svg(d: dict) -> str:
    """Renders the script's structured diagram (nodes on a grid, labelled arrows) in the video's style.
    Empty rows and columns are dropped, gaps widen to fit edge labels, an edge that would cross a box bends
    around it, and labels go where they cover neither a box nor another label."""
    nodes = d["nodes"]
    edges = [e for e in d.get("edges", []) if isinstance(e, dict)]
    cols = sorted({n["col"] for n in nodes})
    rows = sorted({n["row"] for n in nodes})
    label_w = lambda t: max(len(x) for x in _wrap(t, 18)[:2]) * 9 + 20  # noqa: E731
    longest = max([label_w(e["label"]) for e in edges if e.get("label")] or [0])
    cell_w = BOX_W + max(200, longest + 60)
    cell_h = BOX_H + 130
    w, h = MARGIN * 2 + len(cols) * cell_w, TOP + MARGIN + len(rows) * cell_h
    pos = {n["id"]: (MARGIN + cols.index(n["col"]) * cell_w + cell_w / 2, TOP + rows.index(n["row"]) * cell_h + cell_h / 2)
           for n in nodes}
    boxes = {k: (x - BOX_W / 2, y - BOX_H / 2, BOX_W, BOX_H) for k, (x, y) in pos.items()}
    e = html.escape
    P = PALETTE
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" font-family="{e(FONT)}">',
           "<defs>" + "".join(
               f'<marker id="a-{c}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
               f'<path d="M0,0 L10,5 L0,10 z" fill="{P[c]}"/></marker>' for c in ("muted", "blue", "green", "amber", "red", "purple", "teal")) + "</defs>",
           f'<rect width="{w}" height="{h}" fill="{P["bg"]}"/>',
           f'<text x="{MARGIN}" y="{MARGIN + 6}" font-size="34" font-weight="700" fill="{P["ink"]}">{e(d["title"])}</text>',
           f'<line x1="{MARGIN}" y1="{MARGIN + 30}" x2="{w - MARGIN}" y2="{MARGIN + 30}" stroke="{P["line"]}" stroke-width="2"/>']
    for n in nodes:  # boxes first: edges and labels are drawn above them
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

    taken: list[tuple] = list(boxes.values())
    labels = []
    for n_edge, ed in enumerate(edges):
        if ed.get("from") not in pos or ed.get("to") not in pos or ed["from"] == ed["to"]:
            continue
        a, b = pos[ed["from"]], pos[ed["to"]]
        others = [r for k, r in boxes.items() if k not in (ed["from"], ed["to"])]
        path = (_border_point(*a, *b), None, _border_point(*b, *a))
        if _path_hits(path, others):  # bend around whatever is in the way: try both sides, growing
            length = math.hypot(b[0] - a[0], b[1] - a[1])
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            for k in (0.55, 0.8, 1.1, 1.45, 1.9):
                for side in ((-1, 1) if n_edge % 2 == 0 else (1, -1)):
                    q = (mx + side * (b[1] - a[1]) / length * cell_h * k, my - side * (b[0] - a[0]) / length * cell_h * k)
                    cand = (_border_point(*a, *q), q, _border_point(*b, *q))
                    if not _path_hits(cand, others):
                        path = cand
                        break
                else:
                    continue
                break
        c = ed.get("color") if ed.get("color") in P else "muted"
        style = f' stroke="{P[c]}" stroke-width="2.5" fill="none" marker-end="url(#a-{c})"'
        style += f' marker-start="url(#a-{c})"' if ed.get("both") else ""
        style += ' stroke-dasharray="9 7"' if ed.get("dashed") else ""
        (sx, sy), q, (tx, ty) = path
        if q is None:
            out.append(f'<line x1="{sx:.1f}" y1="{sy:.1f}" x2="{tx:.1f}" y2="{ty:.1f}"{style}/>')
        else:
            out.append(f'<path d="M{sx:.1f},{sy:.1f} Q{q[0]:.1f},{q[1]:.1f} {tx:.1f},{ty:.1f}"{style}/>')
        if ed.get("label"):
            lines = _wrap(ed["label"], 18)[:2]
            lw, lh = label_w(ed["label"]), 22 * len(lines) + 8
            spots = [(u, off) for u in (0.5, 0.4, 0.6, 0.3, 0.7) for off in (0, -(lh / 2 + 8), lh / 2 + 8)]
            best = None
            for u, off in spots:
                x, y = _point(path, u)
                rect = (x - lw / 2, y + off - lh / 2, lw, lh)
                if not _rect_hits(*rect, taken, pad=4):
                    best = rect
                    break
            if best is None:
                x, y = _point(path, 0.5)
                best = (x - lw / 2, y - lh / 2, lw, lh)
            taken.append(best)
            x, y, _, _ = best
            labels.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{lw:.1f}" height="{lh}" rx="6" fill="{P["bg"]}" '
                          f'stroke="{P["line"]}" stroke-width="1"/>')
            for i, t in enumerate(lines):
                labels.append(f'<text x="{x + lw / 2:.1f}" y="{y + 21 + i * 22:.1f}" font-size="16" font-weight="600" '
                              f'fill="{P[c]}" text-anchor="middle">{e(t)}</text>')
    out += labels + ["</svg>"]
    return "\n".join(out)

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
# the practice itself or how it went. Rules match phrases in an interview-performance context, not bare
# words, so technical and product language stays allowed ("a Raft candidate", "user interviews", "session
# token", "relevance score", "common mistakes when sharding", "this round of retries").
# Two tiers: HARD rules are unambiguous and must be fixed (the script is bounced once, then the stage fails);
# SOFT rules are worth a second look and only become warnings for the script revision and the QA pass.

# A sentence with one of these is technical: "candidate" there is a Raft node, a key, a search result...
TECH_CANDIDATE = re.compile(
    r"\b(raft|leader|election|vote[sd]?|votes|term|nodes?|follower|quorum|keys?|index|generation|replicas?|"
    r"servers?|search|rank\w*|re-?rank\w*|retriev\w*|filter\w*|scor\w*|matches|match|query|queries|"
    r"recommendations?|items?|solutions?|líder|elección|votos?|nodos?|claves?|réplicas?|servidor(es)?)\b", re.I)

# What someone did or left out, said to them: always about an attempt (hypotheticals are excluded below)
YOU_DID = (r"went|talked|mentioned|said|chose|drew|proposed|suggested|skipped|missed|forgot|jumped|focused|"
           r"spent|answered|struggled|ignored|overlooked|glossed|left out|nailed|got right|got wrong")
ES_YOU_DID = ["pusiste", "elegiste", "escogiste", "empezaste", "comenzaste", "añadiste", "agregaste", "usaste", "diseñaste",
              "construiste", "propusiste", "dibujaste", "mencionaste", "dijiste", "olvidaste", "respondiste", "hablaste",
              "saltaste", "explicaste", "decidiste", "planteaste", "hiciste", "acertaste", "omitiste", "ignoraste",
              "preguntaste", "calculaste", "separaste", "dividiste", "escribiste", "pensaste", "optaste"]
NOT_HYPOTHETICAL = r"(?<!\bif )(?<!\bwhen )(?<!\bonce )(?<!\bas )(?<!\bsay )(?<!\bsuppose )(?<!\bimagine )(?<!\bassume )"

PRACTICE = r"(mock|practice|design|coding|onsite|phone|system design|technical|behaviou?ral|hiring|job|interview)"

# (pattern, why, hard)
FORBIDDEN: list[tuple[str, str, bool]] = [
    # interviews and rounds, as the setting of a performance
    (r"\binterviewees?\b|\binterviewers?\b", "refers to an interviewer or interviewee", True),
    (r"\b(this|that|your|our|today'?s|last|previous|mock|practice|system design|technical|coding|design|job|onsite|"
     r"phone|hiring|behaviou?ral|real) interviews?\b", "mentions an interview as a setting", True),
    (r"\b(this|that|the|your|previous|last|next|each) (kind of |type of |sort of )?" + PRACTICE + r"? ?rounds?\b(?! of\b)",
     "mentions a round of an interview", True),
    (r"\b(mock|practice) (round|interview|exam)s?\b|\bmock (session|run)s?\b", "mentions a practice round", True),
    (r"\b(interview|hiring|onsite) loops?\b|\basked (this|that|it) in (a|an|the|their|your) (loop|onsite|interview|round)\b",
     "mentions an interview loop", True),
    (r"\bhiring (panel|committee|manager'?s? (decision|bar))s?\b|\bassessors?\b", "mentions the people assessing", True),
    (r"\binterviewees?\b|\bcandidat(e|es|o|os|a|as)\b", "refers to a candidate", True),  # filtered by TECH_CANDIDATE
    (r"\bverdicts?\b|\b(strong|lean|weak)[- ](yes|no|hire|no[- ]hire)\b|\bno[- ]hire\b|\b(hire|hiring) (decision|signal|bar|"
     r"recommendation)s?\b|\b(pass(ed)?|fail(ed)?|ace[sd]?) the (round|interview|loop|onsite)\b", "mentions a verdict", True),
    (r"\brubrics?\b|\byour scores?\b|\bscored (a |an )?\d", "mentions a score", True),
    (r"\byour (earlier|previous|first|original) (design|answer|diagram|approach|attempt|sketch)\b|\byour (answer|whiteboard|"
     r"interview|mistakes?|performance|attempt)\b", "speaks to someone about their attempt", True),
    # Spanish
    (r"\bentrevistador(a|es|as)?\b|\bentrevistad[oa]s?\b", "refers to an interviewer or interviewee (es)", True),
    (r"\b(esta|tu|nuestra|la última|la anterior) entrevista\b(?! (de|con) (usuari|client))|\bentrevistas? (técnica|de diseño|"
     r"de trabajo|de sistemas)s?\b", "mentions an interview as a setting (es)", True),
    (r"\b(la|esta|tu) ronda (anterior|pasada)\b|\ben (esta|la) ronda\b(?! de\b)", "mentions a round of an interview (es)", True),
    (r"\bsimulacros?\b|\bveredictos?\b|\btu (puntuación|nota|respuesta|diagrama|intento)\b|\bdecisión de contratación\b",
     "refers to the practice or a verdict (es)", True),
    # remarks about what the viewer did or left out
    (NOT_HYPOTHETICAL + r"\byou (just |really |also |first |quickly |already )?(" + YOU_DID + r")\b",
     "speaks to someone about what they did", True),
    (r"\byou (didn'?t|did not|never|forgot to|failed to) (mention|say|ask|explain|consider|discuss|talk|address|cover|draw)\w*\b",
     "speaks to someone about what they did", True),
    (r"\b(earlier|before|here|there|at first|a moment ago|last time|earlier in this session|earlier on),? you\b",
     "speaks to someone about what they did", True),
    (r"\bas you saw\b.{0,40}\byou (put|" + YOU_DID + r")\b", "speaks to someone about what they did", True),
    (r"\blast time\b.{0,60}\b(left out|missed|forgot(ten)?|skipped|omitted|overlooked)\b", "talks about a previous attempt", True),
    (r"\b(this|our|that|your|the last|last|previous|today'?s) mock\b(?! (server|object|service|data|api|implementation|"
     r"endpoint|client)s?\b)", "mentions a mock round", True),
    (r"\b(this|the|our|your|today'?s|last) session (went|got|was going|ended|started|derailed)\b", "talks about the session", True),
    (r"\bscore:?\s*\d+(\.\d+)?\s*(out of|/)\s*\d+", "mentions a score", True),
    (r"\b(in|during|at) (an |the |your |a |their )?interviews?\b(?! (with|of|for) )|\bmistakes? (people|engineers|candidates) "
     r"make\b", "mentions interviews or people's mistakes in them", True),
    (NOT_HYPOTHETICAL + r"\byou put\b(?<!\bthen you put)(?<!\bnow you put)", "speaks to someone about what they did", True),
    (r"\bif you had (\w+ed|built|drawn|chosen|taken|written|done|gone|begun|put|made|thought|said)\b|\bremember when you\b|"
     r"\byour (first |initial )?instinct\b|\byou were (right|wrong|correct|smart|wise) (to|about|when)\b|\bgood call on\b|"
     + NOT_HYPOTHETICAL + r"\byou (started|began|opened|correctly|rightly|wisely|cleverly|smartly|nicely)\b|\bwhat you (built|drew|wrote|designed|"
     r"proposed|sketched|came up with|had)\b", "speaks to someone about what they did", True),
    # Spanish: 2nd-person preterite of the verbs that describe someone's attempt (a list, not the -aste/-iste
    # suffix: "consiste", "existe" are fine). Hypotheticals ("si eliges", "supongamos que pones") are present tense.
    (r"\b(no )?(" + "|".join(ES_YOU_DID) + r")\b|\bte (faltó|olvidaste|saltaste|equivocaste)\b|\bfaltó (hablar|mencionar|"
     r"explicar|dibujar)\b|\bfuiste directo\b|\bhiciste bien en\b|\besta vez,? no olvides\b|\brecordemos que (tú )?\w+aste\b",
     "speaks to someone about what they did (es)", True),
    # soft: ambiguous coaching and attempt language
    (r"\b(then|now),? you put\b", "speaks to someone about what they did", False),
    (r"\ba (stronger|better|weaker|weak|strong|great|solid|complete|perfect) answer\b|\bthe (best|ideal|right) answer\b",
     "frames the content as an answer to be judged", False),
    (r"\bmost (people|engineers|folks|candidates) (jump|go|rush|start|forget|miss|skip)\w*\b", "coaching language", False),
    (r"\b(on|to|from) the (white)?board\b|\bdrawn on the board\b", "refers to a whiteboard", False),
    (r"\binterviewing\b", "mentions interviews", False),
    (r"\b(a|the) (common|typical|classic|frequent|big|biggest) mistake (is|here|people)\b|\bpeople'?s? mistakes?\b",
     "talks about people's mistakes", False),
    (r"\b(this|that|today'?s|our|your|last|previous) sessions?\b(?! (token|store|cookie|id|state|affinity|key|data|storage|"
     r"timeout|layer|cache|table|replay|ticket|service|feature|expir\w*)s?\b)", "mentions the session", False),
    (r"\b(your|my|the interviewer'?s|interview) feedback\b|\bthe feedback (said|says|was|noted|mentioned)\b",
     "mentions feedback on an attempt", False),
    (r"\buna (mejor|buena|gran|peor) respuesta\b|\bla respuesta (ideal|correcta)\b", "frames the content as an answer (es)", False),
    (r"\b(un|el) error (común|típico|habitual) (es|aquí)\b|\btus errores\b", "talks about people's mistakes (es)", False),
]

# Exact on-screen labels that only make sense in a practice context
BARE_LABELS = re.compile(r"^\s*(mock|practice|practice round|mock round|verdict|recap|feedback|score)\s*[.:!]?\s*$", re.I)


def _company_pattern(company: str) -> re.Pattern | None:
    core = re.sub(r"[^0-9a-z]", "", (company or "").lower())
    if len(core) < 3 or core in {"none", "generic"}:
        return None
    # "AcmeCorp", "Acme Corp", "acme-corp", "Acme.Corp" all match
    return re.compile(r"(?<![0-9a-z])" + r"[\s\-_.·']*".join(re.escape(c) for c in core) + r"(?![0-9a-z])", re.I)


def findings(text: str, company: str = "") -> list[tuple[bool, str]]:
    """(hard, message) for every rule the text breaks."""
    out = []
    for pattern, why, hard in FORBIDDEN:
        for m in re.finditer(pattern, text, flags=re.I):
            word = m.group(0).lower()
            if why == "refers to a candidate":
                sentence = next((x for x in re.split(r"(?<=[.!?])\s+", text) if m.group(0) in x), text)
                if TECH_CANDIDATE.search(sentence):
                    continue
            if why.startswith("mentions a round") and re.search(r"\b(retr|compact|test|review|vot|rollout)", text[m.end():m.end() + 30], re.I):
                continue
            out.append((hard, f'"{m.group(0)}" ({why})'))
            break
    if BARE_LABELS.match(text):
        out.append((True, f'"{text.strip()}" (practice-context label)'))
    for name in ([company] if isinstance(company, str) else list(company)):
        pat = _company_pattern(name)
        if pat and (m := pat.search(text)):
            out.append((True, f'"{m.group(0)}" (names the target company)'))
            break
    return out


def check_text(text: str, company="") -> list[str]:
    """Every finding, hard and soft. `company`: a name or a list of names (company, product names)."""
    return [msg for _, msg in findings(text, company)]


def hard_findings(text: str, company="") -> list[str]:
    return [msg for hard, msg in findings(text, company) if hard]


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


def neutrality(script: dict, company="", hard: bool = True) -> list[str]:
    """Breaks of the publishing rules, as "where: what" lines: the hard ones (must be fixed), or with
    hard=False the soft ones (warnings for a second look)."""
    return [f"{where}: {msg}" for where, text in script_texts(script) for h, msg in findings(text, company) if h == hard]


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


def markdown(script: dict, diagram_file: str | None = "diagram.svg", review: list[str] | None = None) -> str:
    out = [f"# {script['title']}", "", script.get("description", "").strip(), ""]
    if diagram_file:
        out += [f"![{script.get('diagram', {}).get('title', 'Solution diagram')}]({diagram_file})", ""]
    for i, s in enumerate(script["scenes"], 1):
        out += [f"## {i}. {s['title']}", "", " ".join(b["say"].strip() for b in s["beats"]), ""]
    if script.get("assumptions"):
        out += ["## Assumed figures", "", *[f"- {a}" for a in script["assumptions"]], ""]
    if review:
        out += ["## Review before publishing", "", "Flagged by the automatic checks; edit or confirm before sharing.", "",
                *[f"- {r}" for r in review], ""]
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

"""Interview packs: Markdown files with a TOML header between +++ lines, then the interviewer brief.

Built-in packs live in prompts/packs/ (read-only for the app). Personal packs (e.g. one tailored
to a specific company) go in data/packs/, which is gitignored; the pack editor writes only there.
"""

import json
import os
import re
import tomllib
from pathlib import Path

from . import config

# Known header fields, in the order they're written back. Anything else is kept as is.
FIELDS = ("title", "description", "company", "default_language", "whiteboard", "phases", "rubric")
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
PHASE_KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
MAX_MINUTES = 180


class PackError(ValueError):
    """Invalid pack: `errors` maps a field path (e.g. "phases.1.key") to a message."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def personal_dir() -> Path:
    return config.DATA / "packs"  # read at call time: follows MOCKSY_DATA_DIR (and tests)


# ------------------------------------------------------------------ parse / serialize

def parse(text: str, pack_id: str = "") -> dict:
    """Markdown file → pack dict (header fields + `body`; `whiteboard` defaults to true)."""
    parts = text.split("+++", 2)
    if len(parts) != 3 or parts[0].strip():
        raise PackError({"raw": "The file must start with a TOML header between two +++ lines."})
    try:
        meta = tomllib.loads(parts[1])
    except tomllib.TOMLDecodeError as e:
        raise PackError({"raw": f"Invalid TOML header: {e}"}) from None
    meta.setdefault("whiteboard", True)
    meta["id"] = pack_id
    meta["body"] = parts[2].strip()
    return meta


def _value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        # JSON string escapes are valid TOML basic-string escapes; also keep "+++" (the header
        # delimiter) and DEL out of the raw text.
        s = json.dumps(v, ensure_ascii=False).replace("\x7f", "\\u007F")
        return s.replace("+++", "\\u002B++")
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_value(x)}" for k, x in v.items()) + " }"
    if isinstance(v, list):
        if v and all(isinstance(x, dict) for x in v):
            return "[\n" + "".join(f"  {_value(x)},\n" for x in v) + "]"
        return "[" + ", ".join(_value(x) for x in v) + "]"
    raise PackError({"raw": f"Can't write a {type(v).__name__} value to the pack header."})


def _key(k: str) -> str:
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else json.dumps(k)


def serialize(pack: dict) -> str:
    """Pack dict → Markdown file, in the same shape as the hand-written packs."""
    keys = [k for k in FIELDS if k in pack] + [k for k in pack if k not in FIELDS and k not in ("id", "body", "builtin")]
    header = "".join(f"{_key(k)} = {_value(pack[k])}\n" for k in keys)
    return f"+++\n{header}+++\n{pack.get('body', '').strip()}\n"


# ------------------------------------------------------------------ validation

def validate(pack: dict) -> dict:
    """Checks a pack from the editor and returns it normalized; raises PackError."""
    errors: dict[str, str] = {}
    out = {k: v for k, v in pack.items() if k not in ("id", "builtin")}
    pid = str(pack.get("id") or "")
    if not ID_RE.fullmatch(pid):
        errors["id"] = "Use 1-64 lowercase letters, digits or dashes, starting with a letter or digit."
    for k in ("title", "description", "company", "default_language", "body"):
        if not isinstance(out.get(k, ""), str):
            errors[k] = "Must be text."
    out["title"] = str(out.get("title") or "").strip()
    if not out["title"]:
        errors["title"] = "A title is required."
    out["description"] = str(out.get("description") or "").strip()
    out["company"] = str(out.get("company") or "").strip()
    out["default_language"] = out.get("default_language") or "en"
    if out["default_language"] not in config.LANGUAGES:
        errors["default_language"] = "Unknown language."
    if not isinstance(out.get("whiteboard", True), bool):
        errors["whiteboard"] = "Must be true or false."
    out["whiteboard"] = out.get("whiteboard", True) is not False

    phases = out.get("phases")
    if not isinstance(phases, list) or not phases:
        errors["phases"] = "Add at least one phase."
        phases = []
    seen: set[str] = set()
    clean = []
    for i, ph in enumerate(phases):
        if not isinstance(ph, dict):
            errors[f"phases.{i}"] = "Each phase needs a key, a label and minutes."
            continue
        key, label, minutes = str(ph.get("key") or "").strip(), str(ph.get("label") or "").strip(), ph.get("minutes")
        if not PHASE_KEY_RE.fullmatch(key):
            errors[f"phases.{i}.key"] = "Lowercase letters, digits and underscores, starting with a letter."
        elif key in seen:
            errors[f"phases.{i}.key"] = f'Duplicate key "{key}".'
        seen.add(key)
        if not label:
            errors[f"phases.{i}.label"] = "A label is required."
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 0 < minutes <= MAX_MINUTES:
            errors[f"phases.{i}.minutes"] = f"Whole minutes between 1 and {MAX_MINUTES}."
        clean.append({**ph, "key": key, "label": label, "minutes": minutes})
    out["phases"] = clean

    rubric = out.get("rubric")
    if not isinstance(rubric, list):
        rubric = []
    out["rubric"] = [str(r).strip() for r in rubric if str(r).strip()]
    if not out["rubric"]:
        errors["rubric"] = "Add at least one rubric dimension."
    elif len(set(out["rubric"])) != len(out["rubric"]):
        errors["rubric"] = "Rubric dimensions must be unique."

    out["body"] = str(out.get("body") or "").strip()
    if not out["body"]:
        errors["body"] = "The interviewer brief is required."
    if errors:
        raise PackError(errors)
    try:  # anything the form doesn't know about must still be writable
        serialize(out)
    except PackError as e:
        raise PackError({"raw": str(e.errors["raw"])}) from None
    return {"id": pid, **out}


# ------------------------------------------------------------------ load / store

def _read(path: Path, builtin: bool) -> dict:
    pack = parse(path.read_text(), path.stem)
    pack["builtin"] = builtin
    return pack


def load_packs() -> list[dict]:
    packs = []
    for builtin, folder in ((True, config.PACKS), (False, personal_dir())):
        for path in sorted(folder.glob("*.md")):
            try:
                packs.append(_read(path, builtin))
            except PackError as e:  # a broken hand-edited file shouldn't take the app down
                print(f"skipping pack {path}: {e}")
    return packs


def get_pack(pack_id: str) -> dict:
    for p in load_packs():
        if p["id"] == pack_id:
            return p
    raise KeyError(pack_id)


def _personal_path(pack_id: str) -> Path:
    if not ID_RE.fullmatch(pack_id or ""):
        raise KeyError(pack_id)
    base = personal_dir().resolve()
    path = (base / f"{pack_id}.md").resolve()
    if path.parent != base:
        raise KeyError(pack_id)
    return path


def is_builtin(pack_id: str) -> bool:
    return bool(ID_RE.fullmatch(pack_id or "")) and (config.PACKS / f"{pack_id}.md").exists()


def _write(pack: dict):
    path = _personal_path(pack["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(serialize(pack))
    os.replace(tmp, path)


def save_pack(pack: dict, old_id: str | None = None) -> dict:
    """Creates (old_id None) or updates a personal pack; changing the id renames the file."""
    pack = validate(pack)
    taken = {p["id"] for p in load_packs()} - {old_id}
    if pack["id"] in taken or is_builtin(pack["id"]):
        raise PackError({"id": f'A pack called "{pack["id"]}" already exists.'})
    _write(pack)
    if old_id and old_id != pack["id"]:
        _personal_path(old_id).unlink(missing_ok=True)
    return _read(_personal_path(pack["id"]), False)


def delete_pack(pack_id: str):
    path = _personal_path(pack_id)
    if not path.exists():
        raise KeyError(pack_id)
    path.unlink()

"""Config: config.toml defaults, overridden by data/settings.json (written by the UI)."""

import json
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"
PACKS = PROMPTS / "packs"
MODELS = ROOT / "models"
LANGUAGES = {"en": "English", "es": "Spanish"}

with open(ROOT / "config.toml", "rb") as f:
    DEFAULTS = tomllib.load(f)

# MOCKSY_DATA_DIR lets tests (or a second instance) use a separate data folder.
DATA = (ROOT / os.environ.get("MOCKSY_DATA_DIR", DEFAULTS["app"]["data_dir"])).resolve()
SESSIONS = DATA / "sessions"
SESSIONS.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = DATA / "settings.json"

# The few knobs the UI may change, flattened.
EDITABLE = {
    "model": DEFAULTS["interviewer"]["model"],
    "feedback_model": DEFAULTS["interviewer"]["feedback_model"],
    "feedback_language": DEFAULTS["interviewer"].get("feedback_language", "en"),
    "effort": DEFAULTS["interviewer"].get("effort", "default"),
    "speed": DEFAULTS["speech"]["speed"],
    "voice_en": DEFAULTS["speech"]["voices"]["en"],
    "voice_es": DEFAULTS["speech"]["voices"]["es"],
}


def load_settings() -> dict:
    s = dict(EDITABLE)
    try:
        s.update({k: v for k, v in json.loads(SETTINGS_FILE.read_text()).items() if k in EDITABLE})
    except (OSError, ValueError):
        pass
    return s


def save_settings(patch: dict) -> dict:
    s = load_settings()
    s.update({k: v for k, v in patch.items() if k in EDITABLE and v not in (None, "")})
    SETTINGS_FILE.write_text(json.dumps(s, indent=2))
    return s

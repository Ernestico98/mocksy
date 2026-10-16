"""Interview packs and prompt templates."""

import re

import pytest

from app.config import PROMPTS, load_packs
from app.sessions import render

PACKS = load_packs()


def test_there_are_packs():
    assert {p["id"] for p in PACKS} >= {"acme-product-decomposition", "product-decomposition", "system-design"}


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p["id"])
def test_pack_is_well_formed(pack):
    for key in ("title", "description", "phases", "rubric", "body"):
        assert pack.get(key), f"{pack['id']} is missing {key}"
    keys = [p["key"] for p in pack["phases"]]
    assert len(keys) == len(set(keys)), "phase keys must be unique"
    for p in pack["phases"]:
        assert re.fullmatch(r"[a-z_]+", p["key"])
        assert isinstance(p["minutes"], int) and p["minutes"] > 0
        assert p["label"]


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p["id"])
@pytest.mark.parametrize("template", ["interviewer.md", "feedback.md"])
def test_templates_render_without_leftover_placeholders(pack, template):
    meta = {"pack": pack["id"], "phases": pack["phases"], "rubric": pack["rubric"], "lang": "en",
            "company": "", "level": "Mid", "focus": ""}
    text = render(PROMPTS / template, meta, "es" if template == "feedback.md" else None)
    assert not re.search(r"\{[a-z_]+\}", text), "unfilled {placeholder}"
    assert pack["body"][:40] in text
    if template == "feedback.md":
        assert "Write the evaluation in Spanish" in text
        for dim in pack["rubric"]:
            assert f'"{dim}"' in text
    else:
        assert "Speak only in this language" in text and "English" in text
        for p in pack["phases"]:
            assert p["key"] in text

"""Interview packs: parsing, serialization, validation, the CRUD API and prompt templates."""

import re

import pytest
from fastapi.testclient import TestClient

from app import config, packs, server
from app.config import PROMPTS
from app.packs import PackError, load_packs, parse, serialize, validate
from app.sessions import render

PACKS = load_packs()


def test_there_are_packs():
    assert {p["id"] for p in PACKS} >= {"product-decomposition", "system-design"}
    assert all(p["builtin"] and p["whiteboard"] is True for p in PACKS)


def test_personal_packs_load_from_the_data_dir(monkeypatch, tmp_path):
    (tmp_path / "packs").mkdir()
    (tmp_path / "packs" / "mine.md").write_text('+++\ntitle = "Mine"\nphases = []\n+++\nBrief.')
    monkeypatch.setattr(config, "DATA", tmp_path)
    mine = [p for p in load_packs() if p["id"] == "mine"]
    assert mine and mine[0]["body"] == "Brief." and mine[0]["builtin"] is False


def test_a_broken_personal_pack_is_skipped(monkeypatch, tmp_path):
    (tmp_path / "packs").mkdir()
    (tmp_path / "packs" / "broken.md").write_text("no header here")
    monkeypatch.setattr(config, "DATA", tmp_path)
    assert "broken" not in {p["id"] for p in load_packs()}


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p["id"])
def test_pack_is_well_formed(pack):
    for key in ("title", "description", "phases", "rubric", "body"):
        assert pack.get(key), f"{pack['id']} is missing {key}"
    assert validate(pack)  # the editor's rules accept every built-in
    keys = [p["key"] for p in pack["phases"]]
    assert len(keys) == len(set(keys)), "phase keys must be unique"
    for p in pack["phases"]:
        assert re.fullmatch(r"[a-z_]+", p["key"])
        assert isinstance(p["minutes"], int) and p["minutes"] > 0
        assert p["label"]


@pytest.mark.parametrize("template", ["interviewer.md", "feedback.md"])
@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p["id"])
def test_templates_render_without_leftover_placeholders(pack, template):
    meta = {"pack": pack["id"], "phases": pack["phases"], "rubric": pack["rubric"], "lang": "en",
            "company": "", "level": "Mid", "focus": ""}
    text = render(PROMPTS / template, meta, "es" if template == "feedback.md" else None)
    assert not re.search(r"\{[a-z_]+\}", text), "unfilled {placeholder}"
    assert "<!--" not in text, "unprocessed conditional block"
    assert pack["body"][:40] in text
    if template == "feedback.md":
        assert "Write the evaluation in Spanish" in text and "canvas.png" in text
        for dim in pack["rubric"]:
            assert f'"{dim}"' in text
    else:
        assert "Speak only in this language" in text and "English" in text
        assert "[canvas]" in text and "[drawing silently]" in text
        for p in pack["phases"]:
            assert p["key"] in text


@pytest.mark.parametrize("template", ["interviewer.md", "feedback.md"])
def test_templates_without_whiteboard_never_mention_it(template):
    meta = {"pack": "x", "pack_body": "Talk about leadership.", "phases": [{"key": "talk", "label": "Talk", "minutes": 5}],
            "rubric": ["Ownership"], "lang": "en", "whiteboard": False}
    text = render(PROMPTS / template, meta)
    assert "Talk about leadership." in text and "conversation only" in text
    for word in ("whiteboard", "canvas", "Excalidraw", "[drawing silently]", "<!--"):
        assert word.lower() not in text.lower(), word
    assert not re.search(r"\n{3,}", text)


def test_render_uses_the_snapshot_not_the_current_file():
    meta = {"pack": "system-design", "pack_body": "Frozen brief.", "phases": [{"key": "a", "label": "A", "minutes": 1}],
            "rubric": ["R"], "lang": "en"}
    assert "Frozen brief." in render(PROMPTS / "interviewer.md", meta)


# ---------------------------------------------------------------- serialization

def sample(**kw) -> dict:
    return {"id": "my-pack", "title": "Behavioural", "description": "Stories.", "company": "",
            "default_language": "es", "whiteboard": False,
            "phases": [{"key": "intro", "label": "Intro", "minutes": 5}, {"key": "stories", "label": "Stories", "minutes": 30}],
            "rubric": ["Ownership", "Impact"], "body": "You are a hiring manager.\n\nAsk for STAR stories."} | kw


@pytest.mark.parametrize("pack", PACKS + [sample()], ids=lambda p: p["id"])
def test_serialize_round_trips(pack):
    clean = {k: v for k, v in pack.items() if k != "builtin"}
    assert {k: v for k, v in parse(serialize(clean), clean["id"]).items()} == clean


def test_round_trip_keeps_tricky_strings_and_unknown_fields():
    tricky = 'Quotes " and \\ backslash, +++ delimiter, tab\t, ñ, \x7f, {braces}'
    pack = sample(title=tricky, rubric=[tricky], extra_field={"a": [1, 2]}, ratio=1.5)
    text = serialize(pack)
    assert text.count("+++") == 2  # the delimiter inside strings is escaped
    assert parse(text, "my-pack") == pack


def test_serialize_matches_the_hand_written_shape():
    text = serialize(sample())
    assert text.startswith('+++\ntitle = "Behavioural"\n')
    assert '  { key = "intro", label = "Intro", minutes = 5 },\n' in text
    assert "whiteboard = false\n" in text
    assert text.endswith("+++\nYou are a hiring manager.\n\nAsk for STAR stories.\n")


def test_whiteboard_defaults_to_true():
    assert parse('+++\ntitle = "T"\n+++\nB')["whiteboard"] is True


@pytest.mark.parametrize("text", ["no header", '+++\ntitle = \n+++\nB', 'junk\n+++\ntitle = "T"\n+++\nB'])
def test_parse_rejects_bad_files(text):
    with pytest.raises(PackError) as e:
        parse(text)
    assert "raw" in e.value.errors


# ---------------------------------------------------------------- validation

@pytest.mark.parametrize("change, field", [
    ({"id": "../evil"}, "id"),
    ({"id": "Has Caps"}, "id"),
    ({"id": ""}, "id"),
    ({"title": "  "}, "title"),
    ({"body": ""}, "body"),
    ({"rubric": []}, "rubric"),
    ({"rubric": ["A", "A"]}, "rubric"),
    ({"phases": []}, "phases"),
    ({"default_language": "xx"}, "default_language"),
    ({"phases": [{"key": "a", "label": "A", "minutes": 5}, {"key": "a", "label": "B", "minutes": 5}]}, "phases.1.key"),
    ({"phases": [{"key": "Bad Key", "label": "A", "minutes": 5}]}, "phases.0.key"),
    ({"phases": [{"key": "a", "label": "", "minutes": 5}]}, "phases.0.label"),
    ({"phases": [{"key": "a", "label": "A", "minutes": 0}]}, "phases.0.minutes"),
    ({"phases": [{"key": "a", "label": "A", "minutes": -3}]}, "phases.0.minutes"),
    ({"phases": [{"key": "a", "label": "A", "minutes": 2.5}]}, "phases.0.minutes"),
    ({"phases": [{"key": "a", "label": "A", "minutes": True}]}, "phases.0.minutes"),
])
def test_validation_errors(change, field):
    with pytest.raises(PackError) as e:
        validate(sample(**change))
    assert field in e.value.errors


def test_validation_trims_and_drops_empty_rubric_lines():
    v = validate(sample(title="  Spaced  ", rubric=["A", " ", "B "]))
    assert v["title"] == "Spaced" and v["rubric"] == ["A", "B"]


# ---------------------------------------------------------------- API

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    with TestClient(server.app) as c:
        yield c


def body(**kw) -> dict:
    return {k: v for k, v in sample(**kw).items()}


def test_crud(client, tmp_path):
    r = client.post("/api/packs", json=body())
    assert r.status_code == 200, r.text
    assert r.json()["builtin"] is False and r.json()["whiteboard"] is False
    path = tmp_path / "packs" / "my-pack.md"
    assert path.exists() and parse(path.read_text(), "my-pack")["title"] == "Behavioural"

    listed = {p["id"]: p for p in client.get("/api/packs").json()}
    assert listed["my-pack"]["builtin"] is False and listed["system-design"]["builtin"] is True
    meta = {p["id"]: p for p in client.get("/api/meta").json()["packs"]}
    assert meta["my-pack"]["whiteboard"] is False and meta["system-design"]["whiteboard"] is True

    assert client.post("/api/packs", json=body()).status_code == 422  # already exists
    r = client.put("/api/packs/my-pack", json=body(title="Renamed", id="renamed"))
    assert r.status_code == 200 and r.json()["id"] == "renamed"
    assert not path.exists() and (tmp_path / "packs" / "renamed.md").exists()
    assert client.get("/api/packs/renamed").json()["title"] == "Renamed"
    assert client.get("/api/packs/my-pack").status_code == 404

    assert client.delete("/api/packs/renamed").status_code == 200
    assert client.get("/api/packs/renamed").status_code == 404
    assert client.delete("/api/packs/renamed").status_code == 404


def test_unknown_header_fields_survive_an_edit(client, tmp_path):
    (tmp_path / "packs").mkdir()
    (tmp_path / "packs" / "hand.md").write_text(serialize(sample(id="hand", custom="keep me")))
    p = client.get("/api/packs/hand").json()
    assert p["extra"] == {"custom": "keep me"}
    assert client.put("/api/packs/hand", json=p | {"title": "Edited"}).status_code == 200
    assert parse((tmp_path / "packs" / "hand.md").read_text())["custom"] == "keep me"


def test_validation_errors_come_back_per_field(client):
    r = client.post("/api/packs", json=body(title="", phases=[{"key": "a", "label": "A", "minutes": 0}]))
    assert r.status_code == 422
    assert set(r.json()["errors"]) == {"title", "phases.0.minutes"}


def test_builtins_are_immutable(client, tmp_path):
    before = (packs.config.PACKS / "system-design.md").read_text()
    sd = client.get("/api/packs/system-design").json()
    assert client.put("/api/packs/system-design", json=sd | {"title": "Hacked"}).status_code == 403
    assert client.delete("/api/packs/system-design").status_code == 403
    assert client.post("/api/packs", json=sd | {"title": "Copy"}).status_code == 422  # id taken by a built-in
    assert (packs.config.PACKS / "system-design.md").read_text() == before
    assert not (tmp_path / "packs" / "system-design.md").exists()
    # duplicating a built-in into a personal pack works
    assert client.post("/api/packs", json=sd | {"id": "system-design-copy"}).status_code == 200


@pytest.mark.parametrize("pid", ["../x", "..", "a/b", "../../prompts/packs/system-design", "x.md", "%2e%2e"])
def test_path_traversal_is_rejected(client, tmp_path, pid):
    r = client.post("/api/packs", json=body(id=pid))
    assert r.status_code == 422 and "id" in r.json()["errors"]
    assert client.get(f"/api/packs/{pid}").status_code == 404
    assert client.delete(f"/api/packs/{pid}").status_code in (404, 405)
    assert client.put("/api/packs/my-pack", json=body(id=pid)).status_code in (404, 422)
    assert not list(tmp_path.rglob("*.md"))
    with pytest.raises(KeyError):
        packs.delete_pack(pid)


def test_raw_markdown_conversion(client):
    raw = client.post("/api/pack-markdown", json=body()).json()["raw"]
    assert raw == serialize(sample())
    back = client.post("/api/pack-markdown/parse", json={"raw": raw}).json()
    assert back["title"] == "Behavioural" and back["phases"][1]["minutes"] == 30 and back["whiteboard"] is False
    r = client.post("/api/pack-markdown/parse", json={"raw": "nope"})
    assert r.status_code == 422 and "raw" in r.json()["errors"]


def test_hand_made_pack_with_an_odd_file_name_can_be_deleted(client, tmp_path):
    (tmp_path / "packs").mkdir()
    (tmp_path / "packs" / "My_Pack.md").write_text('+++\ntitle = "Mine"\nphases = []\n+++\nBrief.')
    assert client.delete("/api/packs/My_Pack").status_code == 200
    assert not (tmp_path / "packs" / "My_Pack.md").exists()

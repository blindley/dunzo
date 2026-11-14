import json

import pytest

from dunzo import schema_form as sf

SCHEMA = json.dumps(
    {
        "type": "object",
        "required": ["front"],
        "properties": {
            "front": {"type": "string", "title": "Front side", "description": "The question"},
            "age": {"type": "integer"},
            "score": {"type": "number", "default": 1.5},
            "hard": {"type": "boolean"},
            "level": {"enum": ["easy", "hard", 3]},
            "tags": {"type": "array", "items": {"type": "string"}},
            "extra": {"type": "object"},
            "either": {"type": ["string", "null"]},
        },
    }
)


def fields():
    return {f.name: f for f in sf.build_fields(SCHEMA)}


def test_build_fields_kinds_and_metadata():
    f = fields()
    assert {n: x.kind for n, x in f.items()} == {
        "front": "string",
        "age": "integer",
        "score": "number",
        "hard": "boolean",
        "level": "choice",
        "tags": "lines",
        "extra": "json",
        "either": "json",
    }
    assert f["front"].label == "Front side" and f["front"].description == "The question"
    assert f["front"].required and not f["age"].required
    assert f["age"].label == "age"
    assert f["front"].key == "prop__front"
    assert f["level"].options == (('"easy"', "easy"), ('"hard"', "hard"), ("3", "3"))
    assert list(f) == list(json.loads(SCHEMA)["properties"])  # schema order


@pytest.mark.parametrize(
    "schema",
    [None, "", '{"type": "string"}', '{"type": "object"}', '{"type": "object", "properties": {}}',
     '{"oneOf": [{"type": "object"}]}'],
)
def test_no_form_for_unsupported_schemas(schema):
    assert sf.build_fields(schema) is None


def test_initial_values_new_note_uses_defaults():
    v = sf.initial_values(sf.build_fields(SCHEMA), None)
    assert v["prop__score"] == "1.5"
    assert v["prop__front"] == "" and v["prop__hard"] is False


def test_initial_values_from_properties():
    props = {"front": "hi", "age": 4, "hard": True, "level": 3, "tags": ["a", "b"], "extra": {"x": 1}}
    v = sf.initial_values(sf.build_fields(SCHEMA), props)
    assert v["prop__front"] == "hi" and v["prop__age"] == "4" and v["prop__hard"] is True
    assert v["prop__level"] == "3" and v["prop__tags"] == "a\nb"
    assert json.loads(v["prop__extra"]) == {"x": 1}
    assert v["prop__score"] == ""  # existing note: defaults do not apply


def test_parse_form_all_kinds():
    form = {
        "prop__front": "Q\r\nline2",
        "prop__age": " 7 ",
        "prop__score": "2.5",
        "prop__hard": "on",
        "prop__level": '"hard"',
        "prop__tags": "a\n\n  b  \r\n",
        "prop__extra": '{"k": [1]}',
        "prop__either": '"x"',
    }
    props, problems = sf.parse_form(sf.build_fields(SCHEMA), form)
    assert problems == []
    assert props == {
        "front": "Q\nline2",
        "age": 7,
        "score": 2.5,
        "hard": True,
        "level": "hard",
        "tags": ["a", "b"],
        "extra": {"k": [1]},
        "either": "x",
    }


def test_parse_form_blank_removes_keys_and_unchecked_is_false():
    base = {"front": "old", "age": 3, "unknown": "keep me"}
    props, problems = sf.parse_form(sf.build_fields(SCHEMA), {"prop__front": "new", "prop__age": ""}, base)
    assert problems == []
    assert props == {"front": "new", "hard": False, "unknown": "keep me"}
    assert base == {"front": "old", "age": 3, "unknown": "keep me"}  # input not mutated


@pytest.mark.parametrize(
    "key, value, message",
    [
        ("prop__age", "1.5", "age: must be a whole number."),
        ("prop__age", "x", "age: must be a whole number."),
        ("prop__score", "abc", "score: must be a number."),
        ("prop__score", "nan", "score: must be a number."),
        ("prop__score", "inf", "score: must be a number."),
        ("prop__extra", "{bad", "extra: is not valid JSON."),
        ("prop__level", "hard", "level: is not valid JSON."),
    ],
)
def test_parse_form_reports_bad_input(key, value, message):
    _, problems = sf.parse_form(sf.build_fields(SCHEMA), {key: value})
    assert problems == [message]


def test_submitted_values_echo_input():
    v = sf.submitted_values(sf.build_fields(SCHEMA), {"prop__front": "x", "prop__hard": "on"})
    assert v["prop__front"] == "x" and v["prop__hard"] is True and v["prop__age"] == ""


def test_parse_properties_json():
    assert sf.parse_properties_json("  ") == {}
    assert sf.parse_properties_json('{"a": 1}') == {"a": 1}
    with pytest.raises(ValueError, match="not valid JSON"):
        sf.parse_properties_json("{bad")
    with pytest.raises(ValueError, match="JSON object"):
        sf.parse_properties_json("[1]")

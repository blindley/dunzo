"""Turn a player's note_schema (JSON Schema) into a form description, and form input back
into note properties. Pure: no database, no web imports.

Supported per property: string, integer, number, boolean, enum (a select), an array of
strings (one per line), and, for anything else, a raw JSON box. A schema that is not an
object with properties gets no generated form at all (`build_fields` returns None) and the
note is edited as raw JSON. Whatever the form produces is still checked against the full
schema on save (content_repo.validate_properties).
"""

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

KEY_PREFIX = "prop__"


@dataclass(frozen=True)
class Field:
    name: str  # property name in the schema
    kind: str  # string | integer | number | boolean | choice | lines | json
    label: str
    description: str | None
    required: bool
    options: tuple[tuple[str, str], ...] = ()  # choice only: (json text, display text)
    has_default: bool = False
    default: Any = None

    @property
    def key(self) -> str:
        """The HTML form field name."""
        return KEY_PREFIX + self.name


def _json_text(value: Any, indent: int | None = None) -> str:
    return json.dumps(value, ensure_ascii=False, indent=indent)


def _field_for(name: str, sub: Any, required: bool) -> Field:
    kind = "json"
    options: tuple[tuple[str, str], ...] = ()
    label, description = name, None
    has_default, default = False, None
    if isinstance(sub, dict):
        if isinstance(sub.get("title"), str) and sub["title"].strip():
            label = sub["title"]
        if isinstance(sub.get("description"), str):
            description = sub["description"]
        has_default, default = "default" in sub, sub.get("default")
        enum, type_ = sub.get("enum"), sub.get("type")
        if isinstance(enum, list) and enum:
            kind = "choice"
            options = tuple((_json_text(v), v if isinstance(v, str) else _json_text(v)) for v in enum)
        elif type_ in ("string", "integer", "number", "boolean"):
            kind = type_
        elif type_ == "array":
            items = sub.get("items")
            if isinstance(items, dict) and items.get("type") == "string" and "enum" not in items:
                kind = "lines"
    return Field(name, kind, label, description, required, options, has_default, default)


def build_fields(note_schema: str | None) -> list[Field] | None:
    """Describe the form for a schema, or None if no form can be generated."""
    if not note_schema:
        return None
    schema = json.loads(note_schema)
    properties = schema.get("properties")
    if schema.get("type") != "object" or not isinstance(properties, dict) or not properties:
        return None
    required = schema.get("required")
    required_names = set(required) if isinstance(required, list) else set()
    return [_field_for(name, sub, name in required_names) for name, sub in properties.items()]


def _display(field: Field, value: Any) -> str | bool:
    if field.kind == "boolean":
        return bool(value)
    if field.kind == "string":
        return value if isinstance(value, str) else _json_text(value)
    if field.kind in ("integer", "number"):
        numeric = isinstance(value, int | float) and not isinstance(value, bool)
        return str(value) if numeric else _json_text(value)
    if field.kind == "choice":
        return _json_text(value)
    if field.kind == "lines":
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return "\n".join(value)
        return _json_text(value)
    return _json_text(value, indent=2)


def initial_values(fields: list[Field], properties: dict[str, Any] | None) -> dict[str, str | bool]:
    """Form values (by field key) for existing properties, or for a new note (schema defaults)
    when `properties` is None."""
    values: dict[str, str | bool] = {}
    for f in fields:
        if properties is not None and f.name in properties:
            values[f.key] = _display(f, properties[f.name])
        elif properties is None and f.has_default:
            values[f.key] = _display(f, f.default)
        else:
            values[f.key] = False if f.kind == "boolean" else ""
    return values


def submitted_values(fields: list[Field], form: Mapping[str, Any]) -> dict[str, str | bool]:
    """Form values exactly as submitted, for re-rendering the form after an error."""
    values: dict[str, str | bool] = {}
    for f in fields:
        if f.kind == "boolean":
            values[f.key] = f.key in form
        else:
            values[f.key] = str(form.get(f.key, ""))
    return values


def _parse_number(text: str, integer: bool) -> int | float:
    try:
        return int(text)
    except ValueError:
        if integer:
            raise
    number = float(text)
    if not math.isfinite(number):
        raise ValueError(text)
    return number


def parse_form(
    fields: list[Field], form: Mapping[str, Any], base: dict[str, Any] | None = None
) -> tuple[dict[str, Any], list[str]]:
    """Build note properties from submitted form values.

    Starts from a copy of `base` (the note's existing properties) so keys the form does not
    know about survive an edit. A blank field removes its key; a required one is then
    reported by schema validation. Returns (properties, problems).
    """
    properties = dict(base or {})
    problems: list[str] = []
    for f in fields:
        if f.kind == "boolean":
            properties[f.name] = f.key in form
            continue
        raw = str(form.get(f.key, "")).replace("\r\n", "\n").replace("\r", "\n")
        if not raw.strip():
            properties.pop(f.name, None)
            continue
        try:
            if f.kind == "string":
                properties[f.name] = raw
            elif f.kind in ("integer", "number"):
                properties[f.name] = _parse_number(raw.strip(), f.kind == "integer")
            elif f.kind == "choice":
                properties[f.name] = json.loads(raw)
            elif f.kind == "lines":
                properties[f.name] = [line.strip() for line in raw.split("\n") if line.strip()]
            else:
                properties[f.name] = json.loads(raw)
        except ValueError:
            if f.kind == "integer":
                problems.append(f"{f.label}: must be a whole number.")
            elif f.kind == "number":
                problems.append(f"{f.label}: must be a number.")
            else:
                problems.append(f"{f.label}: is not valid JSON.")
    return properties, problems


def parse_properties_json(text: str) -> dict[str, Any]:
    """Parse the raw JSON editor. Blank means {}. Raises ValueError with a user-facing message."""
    if not text.strip():
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Properties are not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})."
        ) from None
    if not isinstance(value, dict):
        raise ValueError("Properties must be a JSON object.")  # noqa: TRY004 (user-facing)
    return value

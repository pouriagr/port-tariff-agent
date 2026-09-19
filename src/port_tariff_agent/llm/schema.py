"""Pydantic models rendered as the JSON schema a function declaration accepts.

Providers take a restricted subset of JSON Schema: no `$ref`, no `$defs`, and unknown
keywords are rejected rather than ignored. Tools therefore declare their arguments through
this module instead of by hand, so the schema the model is shown and the model the handler
validates against cannot drift apart.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

KEPT_KEYWORDS = frozenset(
    {
        "type",
        "description",
        "enum",
        "format",
        "items",
        "properties",
        "required",
        "nullable",
        "minimum",
        "maximum",
    }
)


def json_schema_for(model: type[BaseModel]) -> dict[str, Any]:
    raw = model.model_json_schema()
    definitions = raw.get("$defs", {})
    return _clean(raw, definitions)


def _clean(node: Any, definitions: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_clean(item, definitions) for item in node]
    if not isinstance(node, dict):
        return node

    resolved = _resolve(node, definitions)
    cleaned: dict[str, Any] = {}
    for key, value in resolved.items():
        if key not in KEPT_KEYWORDS:
            continue
        if key == "properties":
            cleaned[key] = {name: _clean(schema, definitions) for name, schema in value.items()}
        elif key in ("enum", "required"):
            cleaned[key] = value
        else:
            cleaned[key] = _clean(value, definitions)
    return _with_type(cleaned, resolved, definitions)


def _resolve(node: dict[str, Any], definitions: dict[str, Any]) -> dict[str, Any]:
    """Replace a reference by what it points at, keeping any sibling keywords."""
    reference = node.get("$ref")
    if not isinstance(reference, str):
        return node
    name = reference.rsplit("/", 1)[-1]
    target = definitions.get(name, {})
    return {**target, **{k: v for k, v in node.items() if k != "$ref"}}


def _with_type(
    cleaned: dict[str, Any], resolved: dict[str, Any], definitions: dict[str, Any]
) -> dict[str, Any]:
    """Collapse the union Pydantic emits for an optional field into type plus nullable."""
    if "type" in cleaned:
        return cleaned

    branches = [_clean(branch, definitions) for branch in resolved.get("anyOf", [])]
    concrete = [branch for branch in branches if branch.get("type") not in (None, "null")]
    if not concrete:
        return cleaned

    merged = {**concrete[0], **cleaned}
    if len(branches) > len(concrete):
        merged["nullable"] = True
    return merged

from __future__ import annotations

from typing import Any

DESIGN_INTENT_SCHEMA = "crab-archi-design-design-intent-v1"
EDIT_INTENT_SCHEMA = "crab-archi-design-edit-intent-v1"
ALLOWED_INTENT_SCHEMAS = {DESIGN_INTENT_SCHEMA, EDIT_INTENT_SCHEMA}


def validate_intent_schema(intent: dict[str, Any]) -> list[str]:
    schema = intent.get("schema")
    if schema not in ALLOWED_INTENT_SCHEMAS:
        return [f"Unsupported intent schema: {schema}"]
    if schema == DESIGN_INTENT_SCHEMA and not isinstance(intent.get("goals", []), list):
        return ["DesignIntent goals must be a list."]
    if schema == EDIT_INTENT_SCHEMA and not isinstance(intent.get("ops", []), list):
        return ["EditIntent ops must be a list."]
    return []

"""Agent tool schemas for Hermes Exchange.

Only draft/read operations are tools. Human Telegram messages own every
security-sensitive transition.
"""

PREPARE = {
    "name": "exchange_prepare",
    "description": (
        "Prepare a human-reviewed exchange draft for a configured Hermes peer. "
        "This never sends the draft; the owner must approve it in Telegram."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["peer", "kind", "subject", "summary", "payload"],
        "properties": {
            "peer": {"type": "string", "minLength": 1},
            "kind": {
                "type": "string",
                "pattern": "^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$",
            },
            "subject": {"type": "string", "minLength": 1, "maxLength": 160},
            "summary": {"type": "string", "minLength": 1, "maxLength": 800},
            "payload": {"type": "string", "minLength": 1},
            "constraints": {
                "type": "array",
                "items": {"type": "string", "maxLength": 500},
                "maxItems": 20,
                "default": [],
            },
            "artifact_refs": {
                "type": "array",
                "items": {"type": "string", "maxLength": 500},
                "maxItems": 20,
                "default": [],
            },
            "execution_hint": {"type": "string", "maxLength": 100},
        },
    },
}

STATUS = {
    "name": "exchange_status",
    "description": "Read one exchange and its approval/delivery audit trail.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["exchange_id"],
        "properties": {
            "exchange_id": {"type": "string", "minLength": 1},
        },
    },
}

LIST = {
    "name": "exchange_list",
    "description": "List pending Hermes Exchange owner actions with bounded output.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "state": {"type": "string", "maxLength": 64},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
        },
    },
}

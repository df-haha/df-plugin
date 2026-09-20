"""Versioned JSON contracts plus semantic cross-field checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import jsonschema


CONTRACT_DIR = Path(__file__).resolve().parent


def load_schema(name: str) -> dict[str, Any]:
    path = CONTRACT_DIR / f"{name}.schema.json"
    if not path.is_file():
        raise ValueError(f"unknown contract: {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_contract(name: str, document: Mapping[str, Any]) -> None:
    schema = load_schema(name)
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(document)
    if name == "executor-request":
        capability = document.get("capability")
        if capability == "pr_review" and not document.get("candidate_revision"):
            raise ValueError("pr_review requires candidate identity")
        if capability in {"merge", "release", "deploy"}:
            raise ValueError("forbidden Executor capability")
    if name == "peer-delivery":
        delivery_type = document.get("delivery_type")
        fragment = document.get("fragment")
        if (delivery_type == "content_fragment") != (fragment is not None):
            raise ValueError("fragment/type mismatch")

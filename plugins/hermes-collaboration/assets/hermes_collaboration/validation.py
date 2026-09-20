from __future__ import annotations

import json
import os
import re
import hashlib
from pathlib import Path
from typing import Any

import jsonschema


class ValidationError(RuntimeError): pass


def validate_install(plugin_dir: str | Path, *, schema_source: str | Path | None = None) -> dict[str, Any]:
    root = Path(plugin_dir)
    required = [root / "plugin.yaml", root / "__init__.py", root / "contracts"]
    missing = [str(path) for path in required if not path.exists()]
    if missing: raise ValidationError("missing installed files: " + ", ".join(missing))
    manifest = (root / "plugin.yaml").read_text(encoding="utf-8")
    if "name: hermes-collaboration" not in manifest: raise ValidationError("invalid plugin manifest")
    schemas = sorted((root / "contracts").glob("*.schema.json"))
    if len(schemas) != 11: raise ValidationError("exactly 11 installed schemas are required")
    for path in schemas: jsonschema.Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))
    if schema_source is not None:
        source = Path(schema_source)
        expected = sorted(source.glob("*.schema.json"))
        if [path.name for path in expected] != [path.name for path in schemas] or any(left.read_bytes() != right.read_bytes() for left, right in zip(expected, schemas, strict=True)):
            raise ValidationError("installed schemas do not match reviewed sources")
    forbidden = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"(?m)^\s*(?:from\s+hermes_exchange\b|import\s+hermes_exchange\b)", text):
            forbidden.append(str(path))
    if forbidden: raise ValidationError("forbidden hermes_exchange dependency: " + ", ".join(forbidden))
    mode = os.stat(root).st_mode & 0o777
    digest = hashlib.sha256()
    for path in schemas:
        digest.update(path.name.encode()); digest.update(path.read_bytes())
    return {"status": "PASS", "schemas": len(schemas), "schema_hash": "sha256:" + digest.hexdigest(), "mode": oct(mode), "protocol": "HERMES_CASE_V1"}

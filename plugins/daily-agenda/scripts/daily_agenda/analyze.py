"""Tool-less analysis process (plan D-12).

Untrusted bundle content is fed to ``claude -p`` over stdin in an empty temporary
directory with every tool, MCP server, customization and the Chrome integration
disabled. The ``init`` event is checked and the run is refused unless both the
tool list and the MCP server list are empty (Phase 0 0.2 showed ``--no-chrome`` is
required: without it a dynamically injected MCP server survives
``--strict-mcp-config`` and ``--safe-mode``).

Error messages carry only codes — never model output or bundle content.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ISOLATION_FLAGS: tuple[str, ...] = (
    "-p",
    "--tools", "",
    "--strict-mcp-config",
    "--safe-mode",
    "--no-session-persistence",
    "--no-chrome",
    "--output-format", "stream-json",
    "--verbose",
)
TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
PROMPT_FILES = {
    "agenda": "analyze-prompt.md",
    "criteria": "criteria-prompt.md",
    "criteria_observe": "criteria-observe-prompt.md",
    "criteria_reduce": "criteria-reduce-prompt.md",
    "refine": "refine-prompt.md",
}
_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)

# A .cmd/.bat target runs through cmd.exe, which cuts an argument at its first
# newline — the multi-line prompt would arrive as just its title line.
_BATCH_SHIM_SUFFIXES = (".cmd", ".bat")

Runner = Callable[..., subprocess.CompletedProcess]


class AnalyzerError(Exception):
    """Analysis failed; ``code`` maps to a plain-language message in ``messages``."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}{': ' + detail if detail else ''}")
        self.code = code


@dataclass(frozen=True)
class AnalyzerResult:
    output: dict[str, Any]
    ms: int
    usage: dict[str, Any] = field(default_factory=dict)


def resolve_claude(which: Callable[[str], str | None] = shutil.which) -> str:
    """Absolute path of the ``claude`` executable.

    Prefers a native ``claude.exe`` anywhere on PATH; a lone npm ``claude.cmd``
    shim is refused (``CLAUDE_NPM_SHIM``) because cmd.exe truncates the prompt.
    """
    found = which("claude.exe") or which("claude")
    if not found:
        raise AnalyzerError("ANALYZER_FAILED", "claude executable not found")
    if found.lower().endswith(_BATCH_SHIM_SUFFIXES):
        raise AnalyzerError("CLAUDE_NPM_SHIM")
    return found


def build_command(prompt: str, *, claude: str, model: str | None = None) -> list[str]:
    cmd = [claude, *ISOLATION_FLAGS]
    if model:
        cmd += ["--model", model]
    return [*cmd, prompt]


def load_prompt(kind: str) -> str:
    return (TEMPLATES_DIR / PROMPT_FILES[kind]).read_text(encoding="utf-8")


def _parse_events(stdout: bytes) -> list[dict[str, Any]]:
    events = []
    for line in stdout.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _assert_no_tools(events: list[dict[str, Any]]) -> None:
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None)
    if init is None or init.get("tools") != [] or init.get("mcp_servers") != []:
        raise AnalyzerError("ANALYZER_TOOLS_NOT_EMPTY")
    for event in events:
        if event.get("type") != "assistant":
            continue
        content = (event.get("message") or {}).get("content") or []
        if any(isinstance(c, dict) and c.get("type") == "tool_use" for c in content):
            raise AnalyzerError("ANALYZER_TOOLS_NOT_EMPTY")


def extract_json(text: str) -> dict[str, Any]:
    """Parse the model's JSON object, tolerating a surrounding code fence or prose."""
    candidates = [text.strip()]
    fenced = _FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise AnalyzerError("MODEL_OUTPUT_INVALID")


def run_analyzer(stdin_bytes: bytes, prompt: str, *, model: str | None = None,
                 timeout_s: int = 600, runner: Runner = subprocess.run,
                 claude: str | None = None) -> AnalyzerResult:
    """Run one isolated analysis and return the parsed JSON object."""
    cmd = build_command(prompt, claude=claude or resolve_claude(), model=model)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="da-analyze-") as workdir:
        try:
            proc = runner(cmd, input=stdin_bytes, cwd=workdir, capture_output=True,
                          timeout=timeout_s, shell=False)
        except subprocess.TimeoutExpired:
            raise AnalyzerError("ANALYZER_TIMEOUT") from None
        except OSError:
            raise AnalyzerError("ANALYZER_FAILED", "could not start claude") from None
    ms = int((time.monotonic() - started) * 1000)
    events = _parse_events(proc.stdout or b"")
    _assert_no_tools(events)
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if result is None or result.get("is_error") or not isinstance(result.get("result"), str):
        raise AnalyzerError("ANALYZER_FAILED", f"exit={proc.returncode}")
    return AnalyzerResult(output=extract_json(result["result"]), ms=ms,
                          usage=dict(result.get("usage") or {}))


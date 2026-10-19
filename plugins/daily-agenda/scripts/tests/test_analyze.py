"""Tests for the tool-less analysis process (plan D-12, Phase 0 0.2 findings)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from daily_agenda import analyze


def _events(*, tools=(), mcp=(), result_text='{"schema_version":1,"items":[]}',
            is_error=False, tool_use=False) -> bytes:
    lines = [
        {"type": "system", "subtype": "init", "tools": list(tools), "mcp_servers": list(mcp),
         "plugins": [], "model": "fake"},
    ]
    if tool_use:
        lines.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {}}]}})
    lines.append({"type": "result", "subtype": "success", "is_error": is_error,
                  "result": result_text, "usage": {"input_tokens": 10}})
    return ("\n".join(json.dumps(x) for x in lines) + "\n").encode("utf-8")


class FakeRunner:
    def __init__(self, stdout: bytes = b"", returncode: int = 0, exc: Exception | None = None):
        self.stdout = stdout
        self.returncode = returncode
        self.exc = exc
        self.calls: list[dict] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append({"cmd": cmd, **kwargs})
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(cmd, self.returncode, stdout=self.stdout, stderr=b"")


def test_command_has_all_isolation_flags():
    cmd = analyze.build_command("PROMPT", claude="claude")
    assert cmd[0] == "claude"
    i = cmd.index("--tools")
    assert cmd[i + 1] == ""
    for flag in ("-p", "--strict-mcp-config", "--safe-mode", "--no-session-persistence",
                 "--no-chrome", "--verbose"):
        assert flag in cmd
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert "--mcp-config" not in cmd and "--plugin-dir" not in cmd
    assert cmd[-1] == "PROMPT"


def test_model_flag_optional():
    assert "--model" not in analyze.build_command("P", claude="claude")
    cmd = analyze.build_command("P", claude="claude", model="sonnet")
    assert cmd[cmd.index("--model") + 1] == "sonnet"


def test_runs_in_empty_temp_dir_with_stdin(tmp_path: Path):
    runner = FakeRunner(_events())
    analyze.run_analyzer(b"bundle", "P", runner=runner, claude="claude")
    call = runner.calls[0]
    assert call["input"] == b"bundle"
    cwd = Path(call["cwd"])
    assert cwd.name.startswith("da-analyze-")
    assert call.get("shell") in (None, False)


def test_returns_parsed_json():
    runner = FakeRunner(_events(result_text='{"schema_version":1,"items":[{"action":"skip"}]}'))
    res = analyze.run_analyzer(b"x", "P", runner=runner, claude="claude")
    assert res.output == {"schema_version": 1, "items": [{"action": "skip"}]}


def test_strips_code_fences():
    text = 'Here:\n```json\n{"schema_version":1,"items":[]}\n```\n'
    res = analyze.run_analyzer(b"x", "P", runner=FakeRunner(_events(result_text=text)), claude="claude")
    assert res.output == {"schema_version": 1, "items": []}


@pytest.mark.parametrize("kwargs", [
    {"tools": ["Read"]},
    {"mcp": [{"name": "claude-in-chrome", "status": "connected"}]},
    {"tools": ["mcp__claude-in-chrome__navigate"]},
])
def test_refuses_when_tools_or_mcp_loaded(kwargs):
    with pytest.raises(analyze.AnalyzerError) as e:
        analyze.run_analyzer(b"x", "P", runner=FakeRunner(_events(**kwargs)), claude="claude")
    assert e.value.code == "ANALYZER_TOOLS_NOT_EMPTY"


def test_refuses_when_init_missing():
    stdout = (json.dumps({"type": "result", "is_error": False, "result": "{}"}) + "\n").encode()
    with pytest.raises(analyze.AnalyzerError) as e:
        analyze.run_analyzer(b"x", "P", runner=FakeRunner(stdout), claude="claude")
    assert e.value.code == "ANALYZER_TOOLS_NOT_EMPTY"


def test_refuses_on_any_tool_use_block():
    with pytest.raises(analyze.AnalyzerError) as e:
        analyze.run_analyzer(b"x", "P", runner=FakeRunner(_events(tool_use=True)), claude="claude")
    assert e.value.code == "ANALYZER_TOOLS_NOT_EMPTY"


def test_error_result():
    runner = FakeRunner(_events(is_error=True, result_text="Prompt is too long"), returncode=1)
    with pytest.raises(analyze.AnalyzerError) as e:
        analyze.run_analyzer(b"x", "P", runner=runner, claude="claude")
    assert e.value.code == "ANALYZER_FAILED"


def test_invalid_json():
    with pytest.raises(analyze.AnalyzerError) as e:
        analyze.run_analyzer(b"x", "P", runner=FakeRunner(_events(result_text="not json")), claude="claude")
    assert e.value.code == "MODEL_OUTPUT_INVALID"


def test_timeout():
    runner = FakeRunner(exc=subprocess.TimeoutExpired(cmd="claude", timeout=1))
    with pytest.raises(analyze.AnalyzerError) as e:
        analyze.run_analyzer(b"x", "P", runner=runner, claude="claude", timeout_s=1)
    assert e.value.code == "ANALYZER_TIMEOUT"


def test_error_message_never_contains_model_text():
    secret = "客戶甲的報價 123456"
    with pytest.raises(analyze.AnalyzerError) as e:
        analyze.run_analyzer(b"x", "P", runner=FakeRunner(_events(result_text=secret)), claude="claude")
    assert secret not in str(e.value)


def test_prompt_templates_exist_and_forbid_tools():
    agenda = analyze.load_prompt("agenda")
    criteria = analyze.load_prompt("criteria")
    for text in (agenda, criteria):
        assert "untrusted" in text
        assert "JSON" in text
    assert '"items"' in agenda
    assert '"criteria_markdown"' in criteria

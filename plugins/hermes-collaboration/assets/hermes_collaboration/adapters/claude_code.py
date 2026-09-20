from typing import Any, Callable, Mapping

from .executors import ExecutorAdapter, SafetyViolation


class ClaudeCodeAdapter(ExecutorAdapter):
    def __init__(self, runner: Callable[[Mapping[str, Any], Mapping[str, str]], Mapping[str, Any]]) -> None:
        super().__init__("claude_code", runner)

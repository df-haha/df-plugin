"""Progress reporting for long-running commands.

Appends JSONL events to a file so a watcher (``DA progress watch``) or
poller (``DA progress show``) can follow execution.  When no progress file
is configured, every method is a silent no-op — callers never need to
guard with ``if progress:``.

Events carry only machine-readable fields: ``at``, ``stage``, ``i``, ``n``,
``code``.  **No untrusted content** (chat names, senders, subjects) ever
appears in a progress event.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .fsutil import jsonl_line


class Progress:
    """Append-only JSONL progress writer.  Null-safe: ``Progress(None)`` is a no-op."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._last_throttled_at: float = 0.0

    # ------------------------------------------------------------------
    # Core
    # ------------------------------------------------------------------

    def event(
        self,
        stage: str,
        *,
        i: int | None = None,
        n: int | None = None,
        code: str | None = None,
    ) -> None:
        """Append one progress event.  Silent on any I/O error."""
        if self._path is None:
            return
        obj: dict[str, Any] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "stage": stage,
        }
        if i is not None:
            obj["i"] = i
        if n is not None:
            obj["n"] = n
        if code is not None:
            obj["code"] = code
        self._write(obj)

    # ------------------------------------------------------------------
    # Convenience: throttled chat progress
    # ------------------------------------------------------------------

    def throttled_chat(self, i: int, n: int) -> None:
        """Emit ``line_chat`` only at interesting points: first, last, every
        10th, or when >=5 s have elapsed since the last emission."""
        if self._path is None:
            return
        now = time.monotonic()
        if i == 1 or i == n or i % 10 == 0 or (now - self._last_throttled_at) >= 5.0:
            self.event("line_chat", i=i, n=n)
            self._last_throttled_at = now

    # ------------------------------------------------------------------
    # Heartbeat context manager
    # ------------------------------------------------------------------

    @contextmanager
    def heartbeat(self, interval_s: float = 20.0) -> Iterator[None]:
        """Emit ``heartbeat`` events at *interval_s* while the body runs.

        No thread is started when the progress file is ``None``.
        """
        if self._path is None:
            yield
            return
        stop = threading.Event()
        beat_count = 0

        def _beat() -> None:
            nonlocal beat_count
            while not stop.wait(interval_s):
                beat_count += 1
                self.event("heartbeat")

        t = threading.Thread(target=_beat, daemon=True)
        t.start()
        try:
            yield
        finally:
            stop.set()
            t.join(timeout=5.0)

    # ------------------------------------------------------------------
    # Terminal events (called from emit())
    # ------------------------------------------------------------------

    def done(self, code: str | None = None) -> None:
        self.event("done", code=code)

    def failed(self, code: str | None = None) -> None:
        self.event("failed", code=code)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _write(self, obj: dict[str, Any]) -> None:
        """Open-append-flush-close per write.  Tolerates all OS errors."""
        if self._path is None:
            return
        try:
            with self._lock:
                line = jsonl_line(obj) + "\n"
                with open(self._path, "a", encoding="utf-8") as fh:
                    fh.write(line)
                    fh.flush()
        except OSError:
            pass

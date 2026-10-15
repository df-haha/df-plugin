"""Entry point: ``python -m daily_agenda <cmd>``."""

from __future__ import annotations

import sys


def _reconfigure_stdout() -> None:
    """Ensure stdout is UTF-8 on Windows (cp950 garbles Traditional Chinese)."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _main() -> None:
    _reconfigure_stdout()
    from daily_agenda.cli import main
    sys.exit(main())


if __name__ == "__main__":
    _main()

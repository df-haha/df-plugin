#!/usr/bin/env python3
"""Install the reviewed Hermes Exchange package into one Hermes profile."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import uuid
from pathlib import Path


PLUGIN_DIR_NAME = "hermes_exchange"
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE = PLUGIN_ROOT / "assets" / PLUGIN_DIR_NAME
REQUIRED_FILES = ("plugin.yaml", "__init__.py")


class InstallError(RuntimeError):
    """Raised when the plugin cannot be installed safely."""


def _default_hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
    except ModuleNotFoundError:
        raise InstallError(
            "Cannot resolve the active Hermes profile: hermes_constants is unavailable. "
            "Run inside the Hermes environment or pass --hermes-home explicitly."
        ) from None

    return get_hermes_home()


def _validate_source(source: Path) -> None:
    if not source.is_dir():
        raise InstallError(f"Plugin source directory does not exist: {source}")

    missing = [name for name in REQUIRED_FILES if not (source / name).is_file()]
    if missing:
        raise InstallError(f"Plugin source is missing required file(s): {', '.join(missing)}")

    manifest = (source / "plugin.yaml").read_text(encoding="utf-8")
    if not any(line.strip() == "name: hermes-exchange" for line in manifest.splitlines()):
        raise InstallError("plugin.yaml must declare name: hermes-exchange")


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _reserve_backup_path(plugins_dir: Path) -> Path:
    while True:
        candidate = plugins_dir / f"{PLUGIN_DIR_NAME}.backup-{uuid.uuid4().hex}"
        if not _path_exists(candidate):
            return candidate


def install(source: Path, hermes_home: Path, *, replace: bool = False) -> Path:
    source = source.resolve()
    hermes_home = hermes_home.expanduser().resolve()
    _validate_source(source)

    plugins_dir = hermes_home / "plugins"
    target = plugins_dir / PLUGIN_DIR_NAME
    target_exists = _path_exists(target)
    if target_exists and not replace:
        raise InstallError(f"Target already exists: {target}")

    plugins_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{PLUGIN_DIR_NAME}.staging-", dir=plugins_dir))
    backup: Path | None = None
    try:
        shutil.copytree(
            source,
            staging,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        _validate_source(staging)
        try:
            if target_exists:
                backup = _reserve_backup_path(plugins_dir)
                target.rename(backup)
            staging.rename(target)
        except BaseException:
            if backup is not None and _path_exists(backup) and not _path_exists(target):
                backup.rename(target)
            raise
    finally:
        if _path_exists(staging):
            shutil.rmtree(staging)

    return target


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Atomically install Hermes Exchange into a Hermes user profile."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help="Plugin source directory (defaults to the marketplace-bundled runtime).",
    )
    parser.add_argument(
        "--hermes-home",
        type=Path,
        help="Hermes profile home; defaults to profile-aware get_hermes_home().",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing install after moving it to a retained backup.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        hermes_home = args.hermes_home or _default_hermes_home()
        target = install(args.source, hermes_home, replace=args.replace)
    except (InstallError, OSError, UnicodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Installed Hermes Exchange user plugin at {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

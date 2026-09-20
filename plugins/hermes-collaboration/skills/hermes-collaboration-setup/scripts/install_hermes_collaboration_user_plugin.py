#!/usr/bin/env python3
"""Atomically install, toggle, or roll back Hermes Collaboration in one profile."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import yaml


PLUGIN_DIR_NAME = "hermes_collaboration"
PLUGIN_CONFIG_NAME = "hermes-collaboration"
SKILL_DIR = Path(__file__).resolve().parents[1]
ASSETS_ROOT = SKILL_DIR.parents[1] / "assets"
DEFAULT_SOURCE = ASSETS_ROOT / PLUGIN_DIR_NAME
if str(ASSETS_ROOT) not in sys.path:
    sys.path.insert(0, str(ASSETS_ROOT))


class InstallError(RuntimeError): pass


def _exists(path: Path) -> bool: return path.exists() or path.is_symlink()


def _validate(source: Path) -> None:
    if not source.is_dir(): raise InstallError(f"source does not exist: {source}")
    from hermes_collaboration.validation import validate_install
    validate_install(source)


def _set_enabled(target: Path, enabled: bool) -> None:
    enabled_path, disabled_path = target / ".enabled", target / ".disabled"
    for path in (enabled_path, disabled_path):
        if path.exists(): path.unlink()
    (enabled_path if enabled else disabled_path).touch(mode=0o600)


def _write_profile_config(home: Path, *, enabled: bool, transaction_id: str) -> Path:
    path = home / "config.yaml"
    backup = home / f"config.backup-hermes-collaboration-{transaction_id}.yaml"
    existed = path.exists()
    if existed:
        shutil.copy2(path, backup)
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    else:
        backup.touch(mode=0o600)
        raw = {}
    if not isinstance(raw, dict):
        raise InstallError("profile config must be a mapping")
    plugins = raw.setdefault("plugins", {})
    if not isinstance(plugins, dict):
        raise InstallError("profile plugins config must be a mapping")
    enabled_items = plugins.setdefault("enabled", [])
    disabled_items = plugins.setdefault("disabled", [])
    if not isinstance(enabled_items, list) or not isinstance(disabled_items, list):
        raise InstallError("profile plugin lists must be arrays")
    enabled_items[:] = [item for item in enabled_items if item != PLUGIN_CONFIG_NAME]
    disabled_items[:] = [item for item in disabled_items if item != PLUGIN_CONFIG_NAME]
    (enabled_items if enabled else disabled_items).append(PLUGIN_CONFIG_NAME)
    staged = home / f".config.staging-hermes-collaboration-{transaction_id}.yaml"
    staged.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    os.chmod(staged, 0o600)
    staged.replace(path)
    if not existed:
        (home / f"config.absent-hermes-collaboration-{transaction_id}").touch(mode=0o600)
    return backup


def _restore_profile_config(home: Path, transaction_id: str) -> None:
    path = home / "config.yaml"
    backup = home / f"config.backup-hermes-collaboration-{transaction_id}.yaml"
    absent = home / f"config.absent-hermes-collaboration-{transaction_id}"
    if absent.exists():
        if path.exists():
            path.unlink()
        absent.unlink()
        if backup.exists():
            backup.unlink()
    elif backup.exists():
        backup.replace(path)


def _backup_root(home: Path) -> Path:
    return home / "backups" / "hermes-collaboration"


def _relocate_legacy_backups(home: Path, plugins: Path) -> None:
    root = _backup_root(home)
    for legacy in plugins.glob(f"{PLUGIN_DIR_NAME}.backup-*"):
        transaction_id = legacy.name.removeprefix(f"{PLUGIN_DIR_NAME}.backup-")
        destination = root / transaction_id / "plugin"
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if _exists(destination):
            raise InstallError(f"duplicate legacy backup transaction: {transaction_id}")
        legacy.rename(destination)


def install(source: Path, hermes_home: Path, *, replace: bool = False, enable: bool = False, config_source: Path | None = None) -> Path:
    source, home = source.resolve(), hermes_home.expanduser().resolve()
    _validate(source)
    plugins = home / "plugins"; plugins.mkdir(parents=True, exist_ok=True, mode=0o700)
    _relocate_legacy_backups(home, plugins)
    target = plugins / PLUGIN_DIR_NAME
    if _exists(target) and not replace: raise InstallError(f"target already exists: {target}")
    staging = Path(tempfile.mkdtemp(prefix=f".{PLUGIN_DIR_NAME}.staging-", dir=plugins))
    backup = None
    transaction_id = uuid.uuid4().hex
    state_dir = home / "state" / "hermes-collaboration"
    state_config = state_dir / "config.yaml"
    config_backup = None
    profile_backup = None
    swapped = False
    try:
        shutil.copytree(source, staging, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".enabled", ".disabled"))
        _set_enabled(staging, enable)
        _validate(staging)
        if _exists(target):
            backup = _backup_root(home) / transaction_id / "plugin"
            backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.rename(backup)
        try: staging.rename(target)
        except BaseException:
            if backup and _exists(backup) and not _exists(target): backup.rename(target)
            raise
        swapped = True
        if config_source is not None:
            config_source = config_source.resolve()
            if not config_source.is_file():
                raise InstallError(f"config source does not exist: {config_source}")
            from hermes_collaboration.config import load_config
            load_config(config_source)
            state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            if state_config.exists():
                config_backup = state_dir / f"config.backup-{transaction_id}.yaml"
                state_config.rename(config_backup)
            staged_config = state_dir / f".config.staging-{transaction_id}.yaml"
            shutil.copy2(config_source, staged_config)
            os.chmod(staged_config, 0o600)
            staged_config.rename(state_config)
        profile_backup = _write_profile_config(home, enabled=enable, transaction_id=transaction_id)
        (target / ".install-transaction").write_text(transaction_id, encoding="ascii")
    except BaseException:
        if profile_backup is not None:
            _restore_profile_config(home, transaction_id)
        if config_backup is not None and config_backup.exists():
            if state_config.exists():
                state_config.unlink()
            config_backup.rename(state_config)
        if swapped and _exists(target):
            shutil.rmtree(target)
        if backup is not None and _exists(backup):
            backup.rename(target)
        raise
    finally:
        if _exists(staging): shutil.rmtree(staging)
    return target


def rollback(hermes_home: Path, backup: Path) -> Path:
    home, backup = hermes_home.expanduser().resolve(), backup.resolve()
    plugins = home / "plugins"; target = plugins / PLUGIN_DIR_NAME
    root = _backup_root(home).resolve()
    if backup.name != "plugin" or backup.parent.parent != root: raise InstallError("backup is outside the exact profile backup directory")
    _validate(backup)
    displaced = _backup_root(home) / f"failed-{uuid.uuid4().hex}" / "plugin"
    displaced.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    transaction_id = backup.parent.name
    state_dir = home / "state" / "hermes-collaboration"
    state_config = state_dir / "config.yaml"
    config_backup = state_dir / f"config.backup-{transaction_id}.yaml"
    if _exists(target): target.rename(displaced)
    try: backup.rename(target)
    except BaseException:
        if _exists(displaced) and not _exists(target): displaced.rename(target)
        raise
    if config_backup.exists():
        if state_config.exists():
            state_config.rename(state_dir / f"config.failed-{uuid.uuid4().hex}.yaml")
        config_backup.rename(state_config)
    _restore_profile_config(home, transaction_id)
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--hermes-home", type=Path, required=True)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--disable", action="store_true")
    parser.add_argument("--config-source", type=Path)
    parser.add_argument(
        "--rollback-backup",
        type=Path,
        help="restore one exact <hermes-home>/backups/hermes-collaboration/<transaction>/plugin backup",
    )
    args = parser.parse_args(argv)
    if args.enable and args.disable: parser.error("choose only one of --enable/--disable")
    if args.rollback_backup and (args.replace or args.enable or args.disable or args.config_source):
        parser.error("--rollback-backup cannot be combined with install options")
    try:
        target = (
            rollback(args.hermes_home, args.rollback_backup)
            if args.rollback_backup
            else install(
                args.source,
                args.hermes_home,
                replace=args.replace,
                enable=args.enable and not args.disable,
                config_source=args.config_source,
            )
        )
    except (InstallError, OSError, ValueError) as exc:
        print(f"error: {exc}"); return 1
    print(target); return 0


if __name__ == "__main__": raise SystemExit(main())

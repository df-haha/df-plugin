from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (
    PLUGIN_ROOT
    / "skills"
    / "hermes-exchange-setup"
    / "scripts"
    / "install_hermes_exchange_user_plugin.py"
)


def _run_installer(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(INSTALLER), *args],
        capture_output=True,
        check=False,
        text=True,
    )


def _staging_paths(plugins_dir: Path) -> list[Path]:
    return list(plugins_dir.glob(".hermes_exchange.staging-*"))


def _make_source(root: Path, *, marker: str = "new") -> Path:
    source = root / "source"
    source.mkdir()
    (source / "plugin.yaml").write_text(
        "name: hermes-exchange\nversion: 0.1.0\n",
        encoding="utf-8",
    )
    (source / "__init__.py").write_text(
        "def register(ctx):\n    return None\n",
        encoding="utf-8",
    )
    (source / "marker.txt").write_text(marker, encoding="utf-8")
    return source


class UserScopeInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_default_bundled_source_installs_without_touching_profile_config(self) -> None:
        hermes_home = self.root / "profile"

        result = _run_installer("--hermes-home", str(hermes_home))

        target = hermes_home / "plugins" / "hermes_exchange"
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((target / "plugin.yaml").is_file())
        self.assertTrue((target / "envelope.py").is_file())
        self.assertFalse((hermes_home / "config.yaml").exists())
        self.assertFalse(
            (hermes_home / "state" / "hermes-exchange" / "config.yaml").exists()
        )

    def test_existing_install_is_not_overwritten_without_replace(self) -> None:
        hermes_home = self.root / "profile"
        target = hermes_home / "plugins" / "hermes_exchange"
        target.mkdir(parents=True)
        marker = target / "owner-data.txt"
        marker.write_text("keep", encoding="utf-8")

        result = _run_installer("--hermes-home", str(hermes_home))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Target already exists", result.stderr)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertEqual(_staging_paths(target.parent), [])

    def test_replace_preserves_existing_plugin_as_backup(self) -> None:
        source = _make_source(self.root, marker="new")
        hermes_home = self.root / "profile"
        target = hermes_home / "plugins" / "hermes_exchange"
        target.mkdir(parents=True)
        (target / "marker.txt").write_text("old", encoding="utf-8")

        result = _run_installer(
            "--source",
            str(source),
            "--hermes-home",
            str(hermes_home),
            "--replace",
        )

        backups = list(target.parent.glob("hermes_exchange.backup-*"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((target / "marker.txt").read_text(encoding="utf-8"), "new")
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "marker.txt").read_text(encoding="utf-8"), "old")
        self.assertEqual(_staging_paths(target.parent), [])

    def test_missing_manifest_fails_without_partial_install(self) -> None:
        source = self.root / "source"
        source.mkdir()
        (source / "__init__.py").write_text(
            "def register(ctx): pass\n", encoding="utf-8"
        )
        hermes_home = self.root / "profile"

        result = _run_installer(
            "--source", str(source), "--hermes-home", str(hermes_home)
        )

        plugins_dir = hermes_home / "plugins"
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("plugin.yaml", result.stderr)
        self.assertFalse((plugins_dir / "hermes_exchange").exists())
        self.assertEqual(_staging_paths(plugins_dir), [])

    def test_missing_plugin_module_fails_without_partial_install(self) -> None:
        source = self.root / "source"
        source.mkdir()
        (source / "plugin.yaml").write_text(
            "name: hermes-exchange\n", encoding="utf-8"
        )
        hermes_home = self.root / "profile"

        result = _run_installer(
            "--source", str(source), "--hermes-home", str(hermes_home)
        )

        plugins_dir = hermes_home / "plugins"
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("__init__.py", result.stderr)
        self.assertFalse((plugins_dir / "hermes_exchange").exists())
        self.assertEqual(_staging_paths(plugins_dir), [])


if __name__ == "__main__":
    unittest.main()

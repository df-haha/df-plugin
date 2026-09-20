from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS = PLUGIN_ROOT / "assets"
INSTALLER = (
    PLUGIN_ROOT
    / "skills"
    / "hermes-collaboration-setup"
    / "scripts"
    / "install_hermes_collaboration_user_plugin.py"
)


def _load_installer():
    spec = importlib.util.spec_from_file_location("hermes_collaboration_installer", INSTALLER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class PackagedRuntimeTests(unittest.TestCase):
    def test_bundled_runtime_validates_all_contracts(self) -> None:
        import sys

        sys.path.insert(0, str(ASSETS))
        try:
            from hermes_collaboration.validation import validate_install

            report = validate_install(ASSETS / "hermes_collaboration")
        finally:
            sys.path.pop(0)

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["schemas"], 11)
        self.assertEqual(report["protocol"], "HERMES_CASE_V1")

    def test_installer_uses_bundled_source_and_rolls_back_exact_backup(self) -> None:
        installer = _load_installer()
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary) / "profile"
            target = installer.install(installer.DEFAULT_SOURCE, profile, enable=True)
            self.assertEqual(target, profile / "plugins" / "hermes_collaboration")
            self.assertTrue((target / ".enabled").is_file())
            (target / "marker").write_text("old", encoding="utf-8")

            installer.install(installer.DEFAULT_SOURCE, profile, replace=True, enable=False)
            backup = next((profile / "backups" / "hermes-collaboration").glob("*/plugin"))
            restored = installer.rollback(profile, backup)

            self.assertEqual(restored, target)
            self.assertEqual((restored / "marker").read_text(encoding="utf-8"), "old")
            self.assertTrue((restored / ".enabled").is_file())

    def test_installed_state_is_private_and_contains_no_generated_cache(self) -> None:
        installer = _load_installer()
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary) / "profile"
            target = installer.install(installer.DEFAULT_SOURCE, profile)

            self.assertEqual(os.stat(profile / "plugins").st_mode & 0o077, 0)
            self.assertFalse(any(path.name == "__pycache__" for path in target.rglob("*")))
            self.assertFalse(any(path.suffix == ".pyc" for path in target.rglob("*")))


if __name__ == "__main__":
    unittest.main()

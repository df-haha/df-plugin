import unicodedata
import unittest

import runner
from tests.helpers import make_job


class PathValidationTests(unittest.TestCase):
    def test_normalize_literal_prefix(self) -> None:
        self.assertEqual(runner.normalize_prefix("src/api"), "src/api")

    def test_rejects_unsafe_prefixes(self) -> None:
        for value in (
            "",
            ".",
            "..",
            "../src",
            "/tmp/x",
            ".git",
            ".git/config",
            ":(glob)src/*",
            "src\\api",
            ":/x",
            ":!x",
            ":^x",
            "::x",
            "a//b",
            "a/./b",
        ):
            with self.subTest(value=value):
                with self.assertRaises(runner.PilotfishError):
                    runner.normalize_prefix(value)

    def test_rejects_control_characters_and_surrogates(self) -> None:
        for value in ("src/\x00api", "src/\napi", "src/\x7fapi", "src/\ud800api"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(runner.PilotfishError):
                    runner.normalize_prefix(value)

    def test_rejects_c1_control_characters(self) -> None:
        for character in ("\u0085", "\u009f"):
            with self.subTest(codepoint=f"U+{ord(character):04X}"):
                self.assertEqual(unicodedata.category(character), "Cc")
                with self.assertRaises(runner.PilotfishError):
                    runner.normalize_prefix(f"src/{character}api")

    def test_accepts_nfc_unicode(self) -> None:
        self.assertEqual(runner.normalize_prefix("caf\u00e9"), "caf\u00e9")

    def test_rejects_non_nfc_unicode(self) -> None:
        non_nfc = unicodedata.normalize("NFD", "caf\u00e9")
        self.assertNotEqual(non_nfc, "caf\u00e9")

        with self.assertRaisesRegex(runner.PilotfishError, "NFC"):
            runner.normalize_prefix(non_nfc)

    def test_path_is_within_uses_complete_components(self) -> None:
        self.assertTrue(runner.path_is_within("src/api/routes.py", "src/api"))
        self.assertFalse(runner.path_is_within("src/apiv2/routes.py", "src/api"))

    def test_accepts_nested_denied_prefix(self) -> None:
        job = runner.parse_job(
            make_job(
                allowed_paths=("src",),
                denied_paths=("src/generated",),
            )
        )

        self.assertEqual(job.allowed_paths, ("src",))
        self.assertEqual(job.denied_paths, ("src/generated",))

    def test_rejects_denied_prefix_outside_every_allowed_prefix(self) -> None:
        with self.assertRaisesRegex(runner.PilotfishError, "outside"):
            runner.parse_job(
                make_job(
                    allowed_paths=("src", "docs"),
                    denied_paths=("tests/generated",),
                )
            )

    def test_rejects_denied_allowed_collision(self) -> None:
        with self.assertRaisesRegex(runner.PilotfishError, "entire allowed prefix"):
            runner.parse_job(
                make_job(
                    allowed_paths=("src",),
                    denied_paths=("src",),
                )
            )

    def test_rejects_overlapping_executor_prefixes(self) -> None:
        jobs = (
            runner.parse_job(make_job("a", "executor", ("src",))),
            runner.parse_job(make_job("b", "executor", ("src/api",))),
        )

        with self.assertRaisesRegex(runner.PilotfishError, "overlap"):
            runner.validate_disjoint_writer_paths(jobs)

    def test_allows_overlap_with_a_read_only_role(self) -> None:
        jobs = (
            runner.parse_job(make_job("a", "scout", ("src",))),
            runner.parse_job(make_job("b", "executor", ("src/api",))),
        )

        runner.validate_disjoint_writer_paths(jobs)


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from src.config import Settings
from src.exclusions import ExclusionMatcher


class ExclusionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_config_rejects_string_exclude_patterns(self) -> None:
        with self.assertRaises(ValueError):
            Settings.from_payload({
                "target_dir": str(self.root / "backup"),
                "exclude_patterns": "__pycache__",
            })

    def test_name_pattern_matches_any_path_segment(self) -> None:
        matcher = ExclusionMatcher(self.root, [], [".venv"])

        self.assertTrue(matcher.skip(self.root / "project" / ".venv" / "Scripts" / "python.exe"))
        self.assertFalse(matcher.skip(self.root / "project" / "src" / "app.py"))

    def test_path_pattern_is_segment_aware(self) -> None:
        matcher = ExclusionMatcher(self.root, [], ["src/*.py"])

        self.assertTrue(matcher.skip(self.root / "src" / "app.py"))
        self.assertFalse(matcher.skip(self.root / "src" / "pkg" / "app.py"))
        self.assertTrue(ExclusionMatcher(self.root, [], ["src/**/*.py"]).skip(
            self.root / "src" / "pkg" / "app.py"
        ))

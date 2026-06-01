import tempfile
import unittest
from pathlib import Path

from src.config import Settings
from src.exclusions import DEFAULT_DEV_PATTERNS, ExclusionMatcher


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

    def test_typical_defaults_cover_os_editor_build_and_temp_junk(self) -> None:
        matcher = ExclusionMatcher(self.root, [], DEFAULT_DEV_PATTERNS)

        ignored = [
            self.root / "Thumbs.db",
            self.root / ".DS_Store",
            self.root / ".idea" / "workspace.xml",
            self.root / "frontend" / "node_modules" / "pkg" / "index.js",
            self.root / "app" / "dist" / "bundle.js",
            self.root / "scratch.tmp",
        ]
        for path in ignored:
            with self.subTest(path=path):
                self.assertTrue(matcher.skip(path))

        keep = [
            self.root / "game.log",
            self.root / "config.db",
            self.root / ".git" / "HEAD",
            self.root / "bin" / "tool.exe",
        ]
        for path in keep:
            with self.subTest(path=path):
                self.assertFalse(matcher.skip(path))

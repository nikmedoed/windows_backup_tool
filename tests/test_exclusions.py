import tempfile
import unittest
from pathlib import Path

from src.config import Settings
from src.exclusions import DEFAULT_DEV_PATTERNS, ExclusionMatcher, gitignore_excludes


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

    def test_gitignore_excludes_are_relative_and_minimal(self) -> None:
        (self.root / ".gitignore").write_text("*.log\nbuild/\n", encoding="utf-8")
        (self.root / "debug.log").write_text("ignored", encoding="utf-8")
        (self.root / "keep.txt").write_text("kept", encoding="utf-8")
        (self.root / "build").mkdir()
        (self.root / "build" / "artifact.bin").write_bytes(b"x")

        self.assertEqual(gitignore_excludes(self.root), ["build", "debug.log"])

    def test_nested_gitignore_rules_are_relative_to_their_directory(self) -> None:
        package = self.root / "package"
        package.mkdir()
        (package / ".gitignore").write_text("cache/\n*.tmp\n", encoding="utf-8")
        (package / "cache").mkdir()
        (package / "cache" / "item.bin").write_bytes(b"x")
        (package / "scratch.tmp").write_text("ignored", encoding="utf-8")
        (self.root / "scratch.tmp").write_text("kept", encoding="utf-8")

        self.assertEqual(
            gitignore_excludes(self.root),
            ["package/cache", "package/scratch.tmp"],
        )

    def test_gitignore_negation_reincludes_a_path(self) -> None:
        (self.root / ".gitignore").write_text("*.log\n!important.log\n", encoding="utf-8")
        (self.root / "debug.log").write_text("ignored", encoding="utf-8")
        (self.root / "important.log").write_text("kept", encoding="utf-8")

        self.assertEqual(gitignore_excludes(self.root), ["debug.log"])

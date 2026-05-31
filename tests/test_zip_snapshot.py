import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from src.config import PathRule, Settings
from src.zip_snapshot import create_zip_snapshots


class ZipSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.target = self.root / "backup"
        self.source_one = self.root / "project-one"
        self.source_two = self.root / "project-two"
        self.source_one.mkdir()
        self.source_two.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_creates_one_relative_archive_per_source(self) -> None:
        (self.source_one / "src").mkdir()
        (self.source_one / "src" / "app.py").write_text("print('one')", encoding="utf-8")
        (self.source_two / "README.md").write_text("two", encoding="utf-8")
        cfg = Settings(
            target_dir=str(self.target),
            sources=[
                PathRule(source=str(self.source_one)),
                PathRule(source=str(self.source_two)),
            ],
            wait_on_finish=False,
        )

        result = create_zip_snapshots(cfg, progress_cb=lambda _i, _t: None, log_cb=lambda _m: None)

        self.assertEqual(result.errors, [])
        self.assertEqual(result.snapshot_dir.parent.name, "zip_snapshots")
        self.assertEqual(len(result.archives), 2)
        archive_names = {item.archive_path.name for item in result.archives}
        self.assertEqual(archive_names, {"project-one.zip", "project-two.zip"})
        entries_by_archive = {}
        for item in result.archives:
            with zipfile.ZipFile(item.archive_path) as zf:
                entries_by_archive[item.source.name] = sorted(zf.namelist())
        self.assertEqual(entries_by_archive["project-one"], ["src/app.py"])
        self.assertEqual(entries_by_archive["project-two"], ["README.md"])

    def test_applies_source_excludes_and_global_patterns(self) -> None:
        keep = self.source_one / "src" / "app.py"
        cache = self.source_one / "src" / "__pycache__" / "app.pyc"
        local_secret = self.source_one / "local.secret"
        for path in (keep, cache, local_secret):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(path.name, encoding="utf-8")
        cfg = Settings(
            target_dir=str(self.target),
            sources=[PathRule(source=str(self.source_one), excludes=["local.secret"])],
            wait_on_finish=False,
            exclude_patterns=["__pycache__", "*.pyc"],
        )

        result = create_zip_snapshots(cfg, progress_cb=lambda _i, _t: None, log_cb=lambda _m: None)

        self.assertEqual(result.errors, [])
        with zipfile.ZipFile(result.archives[0].archive_path) as zf:
            self.assertEqual(zf.namelist(), ["src/app.py"])

    def test_skips_target_when_target_is_inside_source(self) -> None:
        (self.source_one / "src").mkdir()
        (self.source_one / "src" / "app.py").write_text("app", encoding="utf-8")
        nested_target = self.source_one / "backup-target"
        nested_target.mkdir()
        (nested_target / "old.zip").write_text("old", encoding="utf-8")
        cfg = Settings(
            target_dir=str(nested_target),
            sources=[PathRule(source=str(self.source_one))],
            wait_on_finish=False,
        )

        result = create_zip_snapshots(cfg, progress_cb=lambda _i, _t: None, log_cb=lambda _m: None)

        self.assertEqual(result.errors, [])
        with zipfile.ZipFile(result.archives[0].archive_path) as zf:
            self.assertEqual(zf.namelist(), ["src/app.py"])

    def test_writes_manifest_next_to_archives(self) -> None:
        (self.source_one / "file.txt").write_text("content", encoding="utf-8")
        cfg = Settings(
            target_dir=str(self.target),
            sources=[PathRule(source=str(self.source_one))],
            wait_on_finish=False,
        )

        result = create_zip_snapshots(cfg, progress_cb=lambda _i, _t: None, log_cb=lambda _m: None)

        manifest = json.loads((result.snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["archives"]), 1)
        self.assertEqual(manifest["archives"][0]["files"], 1)
        self.assertEqual(manifest["errors"], [])

    def test_manifest_write_error_is_reported(self) -> None:
        (self.source_one / "file.txt").write_text("content", encoding="utf-8")
        cfg = Settings(
            target_dir=str(self.target),
            sources=[PathRule(source=str(self.source_one))],
            wait_on_finish=False,
        )

        with mock.patch("src.zip_snapshot._write_manifest", side_effect=OSError("disk full")):
            result = create_zip_snapshots(cfg, progress_cb=lambda _i, _t: None, log_cb=lambda _m: None)

        self.assertEqual(len(result.archives), 1)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("manifest", result.errors[0])

    def test_writes_restore_settings_to_target_without_leftover_tmp_archives(self) -> None:
        (self.source_one / "file.txt").write_text("content", encoding="utf-8")
        cfg = Settings(
            target_dir=str(self.target),
            sources=[PathRule(source=str(self.source_one), excludes=["local.secret"])],
            wait_on_finish=False,
            exclude_patterns=["__pycache__", "*.pyc"],
        )

        result = create_zip_snapshots(cfg, progress_cb=lambda _i, _t: None, log_cb=lambda _m: None)

        self.assertEqual(result.errors, [])
        loaded = Settings.load_from_target(self.target)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.sources[0].excludes, ["local.secret"])
        self.assertEqual(loaded.exclude_patterns, ["__pycache__", "*.pyc"])
        self.assertEqual(list(result.snapshot_dir.glob("*.tmp")), [])

    def test_archive_name_preserves_folder_name_unicode(self) -> None:
        source = self.root / "проект тест"
        source.mkdir()
        (source / "file.txt").write_text("content", encoding="utf-8")
        cfg = Settings(
            target_dir=str(self.target),
            sources=[PathRule(source=str(source))],
            wait_on_finish=False,
        )

        result = create_zip_snapshots(cfg, progress_cb=lambda _i, _t: None, log_cb=lambda _m: None)

        self.assertEqual(result.errors, [])
        self.assertEqual(result.archives[0].archive_path.name, "проект тест.zip")

    def test_archive_name_adds_suffix_only_for_duplicate_folder_names(self) -> None:
        left = self.root / "left" / "project"
        right = self.root / "right" / "project"
        left.mkdir(parents=True)
        right.mkdir(parents=True)
        (left / "one.txt").write_text("one", encoding="utf-8")
        (right / "two.txt").write_text("two", encoding="utf-8")
        cfg = Settings(
            target_dir=str(self.target),
            sources=[PathRule(source=str(left)), PathRule(source=str(right))],
            wait_on_finish=False,
        )

        result = create_zip_snapshots(cfg, progress_cb=lambda _i, _t: None, log_cb=lambda _m: None)

        self.assertEqual(result.errors, [])
        self.assertEqual(
            [item.archive_path.name for item in result.archives],
            ["project.zip", "project_2.zip"],
        )

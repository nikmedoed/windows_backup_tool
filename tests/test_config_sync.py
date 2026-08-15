import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest import mock

from src.config import PathRule, Settings, merge_source_rule


class ConfigSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.local_file = self.root / "local" / "config.json"
        self.target = self.root / "cloud-target"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_repeated_source_is_merged_case_insensitively(self) -> None:
        sources = [PathRule(source=str(self.root / "Project"), excludes=["cache", "build"])]

        merged = merge_source_rule(
            sources,
            str(self.root / "project"),
            ["BUILD", "logs"],
        )

        self.assertEqual(len(sources), 1)
        self.assertIs(merged, sources[0])
        self.assertEqual(sources[0].excludes, ["cache", "build", "logs"])

    def test_payload_duplicate_sources_are_merged(self) -> None:
        source = str(self.root / "project")
        settings = Settings.from_payload({
            "target_dir": str(self.target),
            "sources": [
                {"source": source, "excludes": ["cache"]},
                {"source": source.upper(), "excludes": ["logs", "CACHE"]},
            ],
        })

        self.assertEqual(len(settings.sources), 1)
        self.assertEqual(settings.sources[0].excludes, ["cache", "logs"])

    def test_load_uses_settings_from_active_target(self) -> None:
        local = Settings(
            target_dir=str(self.target),
            sources=[PathRule(source="C:/stale")],
        )
        portable = Settings(
            target_dir=str(self.target),
            sources=[PathRule(source="C:/from-cloud", excludes=["cache"])],
            scheduled_zip_snapshots=True,
        )
        self.local_file.parent.mkdir(parents=True)
        self.local_file.write_text(json.dumps(asdict(local)), encoding="utf-8")
        portable.save_to_target()

        with mock.patch("src.config.CONFIG_FILE", self.local_file):
            loaded = Settings.load()

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual([rule.source for rule in loaded.sources], ["C:/from-cloud"])
        self.assertEqual(loaded.sources[0].excludes, ["cache"])
        self.assertTrue(loaded.scheduled_zip_snapshots)

    def test_load_falls_back_to_local_when_target_is_unavailable(self) -> None:
        local = Settings(
            target_dir=str(self.target),
            sources=[PathRule(source="C:/local")],
        )
        self.local_file.parent.mkdir(parents=True)
        self.local_file.write_text(json.dumps(asdict(local)), encoding="utf-8")

        with mock.patch("src.config.CONFIG_FILE", self.local_file):
            loaded = Settings.load()

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual([rule.source for rule in loaded.sources], ["C:/local"])

    def test_old_payload_defaults_scheduled_zip_to_false(self) -> None:
        loaded = Settings.from_payload({"target_dir": "C:/backup", "sources": []})

        self.assertFalse(loaded.scheduled_zip_snapshots)

import time
import unittest

from src.app_version import VERSION
from src import updater


class UpdaterTests(unittest.TestCase):
    def test_app_version_is_detected(self) -> None:
        self.assertRegex(VERSION, r"\d+\.\d+\.\d+")

    def test_version_comparison_accepts_v_tags(self) -> None:
        self.assertTrue(updater.is_newer_version("v0.1.1", "0.1.0"))
        self.assertTrue(updater.is_newer_version("1.2", "1.1.9"))
        self.assertFalse(updater.is_newer_version("v0.1.0", "0.1.0"))
        self.assertFalse(updater.is_newer_version("not-a-version", "0.1.0"))

    def test_release_payload_selects_backup_tool_exe(self) -> None:
        release = updater.release_from_payload(
            {
                "tag_name": "v0.2.0",
                "html_url": "https://github.com/nikmedoed/windows_backup_tool/releases/tag/v0.2.0",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": "notes.txt",
                        "browser_download_url": "https://example.invalid/notes.txt",
                    },
                    {
                        "name": "BackupTool.exe",
                        "browser_download_url": "https://example.invalid/BackupTool.exe",
                        "size": 10,
                    },
                ],
            },
            "0.1.0",
        )

        self.assertIsNotNone(release)
        assert release is not None
        self.assertEqual(release.version, "v0.2.0")
        self.assertEqual(release.asset.name, "BackupTool.exe")

    def test_release_payload_ignores_old_or_prerelease_versions(self) -> None:
        base = {
            "tag_name": "v0.1.0",
            "assets": [{"name": "BackupTool.exe", "browser_download_url": "https://example.invalid/app.exe"}],
        }
        self.assertIsNone(updater.release_from_payload({**base, "prerelease": False}, "0.1.0"))
        self.assertIsNone(updater.release_from_payload({**base, "tag_name": "v0.2.0", "prerelease": True}, "0.1.0"))

    def test_check_interval_uses_error_backoff(self) -> None:
        now = time.time()
        original_read_json = updater._read_json
        try:
            updater._read_json = lambda _path: {"last_check_at": now - 120, "last_error_at": now - 60}
            self.assertFalse(updater.should_check_for_update(now))
            updater._read_json = lambda _path: {"last_check_at": now - 3700, "last_error_at": now - 3600}
            self.assertTrue(updater.should_check_for_update(now))
        finally:
            updater._read_json = original_read_json

    def test_download_is_skipped_when_state_already_applied_release(self) -> None:
        original_fetch = updater.fetch_latest_release
        original_read_json = updater._read_json
        original_write_state = updater._write_state
        original_download = updater._download_asset
        writes: list[dict[str, object]] = []
        try:
            updater.fetch_latest_release = lambda _current: updater.ReleaseInfo(
                version="v0.2.0",
                page_url="https://example.invalid/release",
                asset=updater.ReleaseAsset("BackupTool.exe", "https://example.invalid/BackupTool.exe"),
            )
            updater._read_json = lambda _path: {"installed_version": "v0.2.0"}
            updater._write_state = lambda **updates: writes.append(updates)
            updater._download_asset = lambda _release: self.fail("download should not run")

            self.assertIsNone(updater._check_and_download("0.1.0"))
            self.assertIn({"last_error_at": None}, writes)
        finally:
            updater.fetch_latest_release = original_fetch
            updater._read_json = original_read_json
            updater._write_state = original_write_state
            updater._download_asset = original_download


if __name__ == "__main__":
    unittest.main()

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.config import PathRule, Settings
from src.copier import run_backup
from src.restore import RestorePlan, apply_restore_plan, build_restore_plan, build_restore_plan_for_sources
from src.version_store import VersionStore, mirror_path_for_source, mirror_relative_for_source


class VersionedBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.target = self.root / "backup"
        self.source.mkdir()
        self.cfg = Settings(
            target_dir=str(self.target),
            sources=[PathRule(source=str(self.source))],
            wait_on_finish=False,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_backup(self) -> None:
        self.assertTrue(run_backup(self.cfg, progress_cb=lambda _i, _t: None, log_cb=lambda _m: None))

    def restore_safety_files(self) -> list[Path]:
        root = self.target / ".backup_versions" / "restore_safety"
        if not root.exists():
            return []
        return [p for p in root.rglob("*") if p.is_file()]

    def archive_files(self) -> list[Path]:
        root = self.target / ".backup_versions" / "files"
        if not root.exists():
            return []
        return [p for p in root.rglob("*") if p.is_file()]

    def test_first_backup_creates_plain_mirror_and_index(self) -> None:
        source_file = self.source / "save.txt"
        source_file.write_text("version one", encoding="utf-8")

        self.run_backup()

        mirror_file = mirror_path_for_source(self.target, source_file)
        self.assertEqual(mirror_file.read_text(encoding="utf-8"), "version one")
        self.assertTrue((self.target / ".backup_versions" / "index.sqlite3").exists())

        store = VersionStore(self.target)
        try:
            runs = store.list_successful_runs()
            self.assertEqual(len(runs), 1)
            snapshot = store.snapshot_for_scope(runs[0].id, source_file)
            self.assertEqual(len(snapshot), 1)
            self.assertEqual(snapshot[0].state, "present")
        finally:
            store.close()

    def test_changed_file_archives_old_version_and_restore_file_uses_it(self) -> None:
        source_file = self.source / "save.txt"
        source_file.write_text("version one", encoding="utf-8")
        self.run_backup()

        source_file.write_text("version two with a different size", encoding="utf-8")
        self.run_backup()

        mirror_file = mirror_path_for_source(self.target, source_file)
        self.assertEqual(mirror_file.read_text(encoding="utf-8"), "version two with a different size")

        store = VersionStore(self.target)
        try:
            runs = sorted(store.list_successful_runs(), key=lambda r: r.id)
            old_plan = build_restore_plan(store, runs[0].id, source_file, mode="original")
            self.assertEqual(old_plan.copy_count, 1)
            self.assertTrue(old_plan.actions[0].content_path.exists())
            self.assertTrue(apply_restore_plan(old_plan, store, log_cb=lambda _m: None))
        finally:
            store.close()

        self.assertEqual(source_file.read_text(encoding="utf-8"), "version one")
        safety_files = self.restore_safety_files()
        self.assertEqual(len(safety_files), 1)
        self.assertEqual(safety_files[0].read_text(encoding="utf-8"), "version two with a different size")

        mirror_file = mirror_path_for_source(self.target, source_file)
        self.assertEqual(mirror_file.read_text(encoding="utf-8"), "version two with a different size")

        store = VersionStore(self.target)
        try:
            runs = sorted(store.list_successful_runs(), key=lambda r: r.id)
            self.assertEqual(len(runs), 2)
            restore_v2 = build_restore_plan(store, runs[1].id, source_file, mode="original")
            self.assertEqual(restore_v2.copy_count, 1)
            self.assertTrue(apply_restore_plan(restore_v2, store, log_cb=lambda _m: None))
        finally:
            store.close()

        self.assertEqual(source_file.read_text(encoding="utf-8"), "version two with a different size")

    def test_previous_successful_version_survives_crash_after_archive_before_indexing_new_run(self) -> None:
        source_file = self.source / "save.txt"
        source_file.write_text("version one", encoding="utf-8")
        self.run_backup()
        mirror_file = mirror_path_for_source(self.target, source_file)

        store = VersionStore(self.target)
        try:
            first_run = store.list_successful_runs()[0]
            failed_run_id = store.begin_run()
            archived_rel = store.prepare_archive_current(str(source_file.resolve()), mirror_file, failed_run_id)
            self.assertIsNotNone(archived_rel)
            mirror_file.write_text("partial failed mirror update", encoding="utf-8")
            store.finish_run(failed_run_id, False)

            export_root = self.root / "export_after_failed_run"
            plan = build_restore_plan(store, first_run.id, source_file, mode="export", export_root=export_root)
            self.assertEqual(plan.export_count, 1)
            self.assertTrue(apply_restore_plan(plan, store, log_cb=lambda _m: None))
        finally:
            store.close()

        exported = self.root / "export_after_failed_run" / mirror_relative_for_source(source_file)
        self.assertEqual(exported.read_text(encoding="utf-8"), "version one")

    def test_restore_does_not_create_version_when_action_no_longer_changes_data(self) -> None:
        source_file = self.source / "save.txt"
        source_file.write_text("version one", encoding="utf-8")
        self.run_backup()
        source_file.write_text("version two with a different size", encoding="utf-8")
        self.run_backup()

        store = VersionStore(self.target)
        try:
            runs = sorted(store.list_successful_runs(), key=lambda r: r.id)
            stale_plan = build_restore_plan(store, runs[0].id, source_file, mode="original")
            source_file.write_text("version one", encoding="utf-8")
            before_count = store.count_runs()
            self.assertTrue(apply_restore_plan(stale_plan, store, log_cb=lambda _m: None))
            after = store.list_successful_runs()
            self.assertEqual(len(after), 2)
            self.assertEqual(store.count_runs(), before_count)
        finally:
            store.close()

    def test_no_change_backup_does_not_create_extra_run(self) -> None:
        source_file = self.source / "save.txt"
        source_file.write_text("version one", encoding="utf-8")
        self.run_backup()

        store = VersionStore(self.target)
        try:
            before_count = store.count_runs()
            before_successful = len(store.list_successful_runs())
        finally:
            store.close()

        self.run_backup()

        store = VersionStore(self.target)
        try:
            self.assertEqual(store.count_runs(), before_count)
            self.assertEqual(len(store.list_successful_runs()), before_successful)
        finally:
            store.close()

    def test_existing_plain_mirror_gets_one_baseline_version_then_no_extra_runs(self) -> None:
        source_file = self.source / "save.txt"
        source_file.write_text("version one", encoding="utf-8")
        mirror_file = mirror_path_for_source(self.target, source_file)
        mirror_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, mirror_file)

        self.run_backup()

        store = VersionStore(self.target)
        try:
            self.assertEqual(len(store.list_successful_runs()), 1)
            self.assertEqual(store.count_versions(), 1)
            before_count = store.count_runs()
        finally:
            store.close()

        self.run_backup()

        store = VersionStore(self.target)
        try:
            self.assertEqual(store.count_runs(), before_count)
            self.assertEqual(len(store.list_successful_runs()), 1)
            self.assertEqual(store.count_versions(), 1)
        finally:
            store.close()

    def test_overlapping_sources_do_not_record_duplicate_versions_for_same_file(self) -> None:
        nested = self.source / "nested"
        nested.mkdir()
        source_file = nested / "save.txt"
        source_file.write_text("version one", encoding="utf-8")
        self.cfg.sources = [
            PathRule(source=str(self.source)),
            PathRule(source=str(nested)),
        ]

        self.run_backup()

        store = VersionStore(self.target)
        try:
            self.assertEqual(len(store.list_successful_runs()), 1)
            self.assertEqual(store.count_versions(), 1)
            before_count = store.count_runs()
        finally:
            store.close()

        self.run_backup()

        store = VersionStore(self.target)
        try:
            self.assertEqual(store.count_runs(), before_count)
            self.assertEqual(len(store.list_successful_runs()), 1)
            self.assertEqual(store.count_versions(), 1)
        finally:
            store.close()

    def test_target_inside_source_is_not_backed_up_recursively(self) -> None:
        nested_target = self.source / "backup"
        self.cfg.target_dir = str(nested_target)
        source_file = self.source / "save.txt"
        source_file.write_text("version one", encoding="utf-8")

        self.run_backup()
        self.run_backup()

        mirrored_files = [
            p.relative_to(nested_target)
            for p in nested_target.rglob("*")
            if p.is_file() and ".backup_versions" not in p.relative_to(nested_target).parts
        ]
        self.assertEqual(mirrored_files, [mirror_relative_for_source(source_file)])

        store = VersionStore(nested_target)
        try:
            self.assertEqual(len(store.list_successful_runs()), 1)
            self.assertEqual(store.count_versions(), 1)
        finally:
            store.close()

    def test_same_size_content_change_is_backed_up_even_with_same_mtime(self) -> None:
        source_file = self.source / "save.txt"
        source_file.write_text("aaaa", encoding="utf-8")
        self.run_backup()
        original_mtime = source_file.stat().st_mtime

        source_file.write_text("bbbb", encoding="utf-8")
        os.utime(source_file, (source_file.stat().st_atime, original_mtime))
        self.assertTrue(run_backup(
            self.cfg,
            progress_cb=lambda _i, _t: None,
            log_cb=lambda _m: None,
            use_hash=True,
        ))

        mirror_file = mirror_path_for_source(self.target, source_file)
        self.assertEqual(mirror_file.read_text(encoding="utf-8"), "bbbb")
        store = VersionStore(self.target)
        try:
            runs = sorted(store.list_successful_runs(), key=lambda r: r.id)
            self.assertEqual(len(runs), 2)
            old_plan = build_restore_plan(store, runs[0].id, source_file, mode="export", export_root=self.root / "old")
            self.assertEqual(old_plan.export_count, 1)
            self.assertTrue(apply_restore_plan(old_plan, store, log_cb=lambda _m: None))
        finally:
            store.close()
        self.assertEqual(((self.root / "old") / mirror_relative_for_source(source_file)).read_text(encoding="utf-8"),
                         "aaaa")

    def test_same_content_with_shifted_mirror_mtime_does_not_create_extra_run(self) -> None:
        source_file = self.source / "save.txt"
        source_file.write_text("version one", encoding="utf-8")
        self.run_backup()
        mirror_file = mirror_path_for_source(self.target, source_file)
        os.utime(mirror_file, (mirror_file.stat().st_atime, mirror_file.stat().st_mtime + 30))

        store = VersionStore(self.target)
        try:
            before_count = store.count_runs()
            before_successful = len(store.list_successful_runs())
        finally:
            store.close()

        self.run_backup()

        store = VersionStore(self.target)
        try:
            self.assertEqual(store.count_runs(), before_count)
            self.assertEqual(len(store.list_successful_runs()), before_successful)
        finally:
            store.close()

    def test_no_change_backup_uses_index_metadata_without_full_file_hash(self) -> None:
        source_file = self.source / "save.txt"
        source_file.write_text("version one", encoding="utf-8")
        self.run_backup()

        with mock.patch("src.copier.sha1", side_effect=AssertionError("sha1 should not be used")):
            self.run_backup()

    def test_changed_backup_does_not_update_index_for_all_unchanged_files(self) -> None:
        changed = self.source / "changed.txt"
        unchanged = self.source / "unchanged.txt"
        changed.write_text("version one", encoding="utf-8")
        unchanged.write_text("stable", encoding="utf-8")
        self.run_backup()

        changed.write_text("version two with a different size", encoding="utf-8")
        seen_updates: list[Path] = []
        original = VersionStore.record_seen

        def wrapped_record_seen(store, src, *args, **kwargs):
            seen_updates.append(src)
            return original(store, src, *args, **kwargs)

        with mock.patch.object(VersionStore, "record_seen", wrapped_record_seen):
            self.run_backup()

        self.assertEqual(seen_updates, [])

    def test_backup_records_hash_from_source_without_hashing_written_target(self) -> None:
        source_file = self.source / "save.txt"
        source_file.write_text("version one", encoding="utf-8")

        with mock.patch("src.version_store.sha1", side_effect=AssertionError("target hash fallback should not be used")):
            self.run_backup()

    def test_successful_run_materializes_state_after_failed_indexed_run(self) -> None:
        source_file = self.source / "save.txt"
        source_file.write_text("version one", encoding="utf-8")
        self.run_backup()

        source_file.write_text("version two with a different size", encoding="utf-8")
        mirror_file = mirror_path_for_source(self.target, source_file)
        store = VersionStore(self.target)
        try:
            failed_run_id = store.begin_run()
            archived_rel = store.prepare_archive_current(str(source_file.resolve()), mirror_file, failed_run_id)
            shutil.copy2(source_file, mirror_file)
            store.record_copied(
                source_file,
                self.source,
                mirror_relative_for_source(source_file),
                mirror_file,
                failed_run_id,
                archived_rel,
            )
            store.finish_run(failed_run_id, False)
        finally:
            store.close()

        self.run_backup()

        store = VersionStore(self.target)
        try:
            runs = sorted(store.list_successful_runs(), key=lambda r: r.id)
            self.assertEqual(len(runs), 2)
            plan = build_restore_plan(store, runs[-1].id, source_file, mode="export", export_root=self.root / "export")
            self.assertEqual(plan.export_count, 1)
            self.assertTrue(apply_restore_plan(plan, store, log_cb=lambda _m: None))
        finally:
            store.close()

        exported = self.root / "export" / mirror_relative_for_source(source_file)
        self.assertEqual(exported.read_text(encoding="utf-8"), "version two with a different size")

    def test_restore_keeps_safety_copy_of_unbacked_current_file_before_overwrite(self) -> None:
        source_file = self.source / "save.txt"
        source_file.write_text("version one", encoding="utf-8")
        self.run_backup()
        source_file.write_text("version two with a different size", encoding="utf-8")
        self.run_backup()

        source_file.write_text("manual edit that was never backed up", encoding="utf-8")
        store = VersionStore(self.target)
        try:
            runs = sorted(store.list_successful_runs(), key=lambda r: r.id)
            restore_v1 = build_restore_plan(store, runs[0].id, source_file, mode="original")
            self.assertTrue(apply_restore_plan(restore_v1, store, log_cb=lambda _m: None))
            runs_after = sorted(store.list_successful_runs(), key=lambda r: r.id)
            self.assertEqual(len(runs_after), 2)
            self.assertEqual(mirror_path_for_source(self.target, source_file).read_text(encoding="utf-8"),
                             "version two with a different size")
        finally:
            store.close()

        self.assertEqual(source_file.read_text(encoding="utf-8"), "version one")
        safety_files = self.restore_safety_files()
        self.assertEqual(len(safety_files), 1)
        self.assertEqual(safety_files[0].read_text(encoding="utf-8"), "manual edit that was never backed up")

        self.run_backup()
        self.assertEqual(mirror_path_for_source(self.target, source_file).read_text(encoding="utf-8"), "version one")
        store = VersionStore(self.target)
        try:
            runs_after_backup = sorted(store.list_successful_runs(), key=lambda r: r.id)
            self.assertEqual(len(runs_after_backup), 3)
        finally:
            store.close()

    def test_restore_capture_only_handles_selected_actions(self) -> None:
        selected = self.source / "selected.txt"
        untouched = self.source / "untouched.txt"
        selected.write_text("selected v1", encoding="utf-8")
        untouched.write_text("untouched v1", encoding="utf-8")
        self.run_backup()
        selected.write_text("selected v2 with a different size", encoding="utf-8")
        untouched.write_text("untouched v2 with a different size", encoding="utf-8")
        self.run_backup()

        selected.write_text("selected manual edit", encoding="utf-8")
        untouched.write_text("untouched manual edit", encoding="utf-8")
        store = VersionStore(self.target)
        try:
            runs = sorted(store.list_successful_runs(), key=lambda r: r.id)
            folder_plan = build_restore_plan(store, runs[0].id, self.source, mode="original")
            selected_only = RestorePlan(
                run_id=folder_plan.run_id,
                scope_path=folder_plan.scope_path,
                mode=folder_plan.mode,
                actions=[a for a in folder_plan.actions if a.target_path == selected],
            )
            self.assertEqual(len(selected_only.actions), 1)
            self.assertTrue(apply_restore_plan(selected_only, store, log_cb=lambda _m: None))

            runs_after = sorted(store.list_successful_runs(), key=lambda r: r.id)
            self.assertEqual(len(runs_after), 2)
        finally:
            store.close()

        self.assertEqual(selected.read_text(encoding="utf-8"), "selected v1")
        self.assertEqual(untouched.read_text(encoding="utf-8"), "untouched manual edit")
        self.assertEqual(mirror_path_for_source(self.target, selected).read_text(encoding="utf-8"),
                         "selected v2 with a different size")
        self.assertEqual(mirror_path_for_source(self.target, untouched).read_text(encoding="utf-8"),
                         "untouched v2 with a different size")

    def test_archive_folder_names_start_with_datetime(self) -> None:
        source_file = self.source / "save.txt"
        source_file.write_text("version one", encoding="utf-8")
        self.run_backup()
        source_file.write_text("version two with a different size", encoding="utf-8")
        self.run_backup()

        archived = list((self.target / ".backup_versions" / "files").iterdir())
        self.assertEqual(len(archived), 1)
        self.assertRegex(archived[0].name, r"^\d{8}_\d{6}_run_\d+$")

    def test_folder_restore_plan_includes_only_changed_files(self) -> None:
        one = self.source / "one.txt"
        two = self.source / "two.txt"
        three = self.source / "three.txt"
        one.write_text("same one", encoding="utf-8")
        two.write_text("old two", encoding="utf-8")
        three.write_text("same three", encoding="utf-8")
        self.run_backup()

        two.write_text("new two with a different size", encoding="utf-8")
        self.run_backup()

        store = VersionStore(self.target)
        try:
            runs = sorted(store.list_successful_runs(), key=lambda r: r.id)
            plan = build_restore_plan(store, runs[0].id, self.source, mode="original")
        finally:
            store.close()

        self.assertEqual(len(plan.actions), 1)
        self.assertEqual(plan.actions[0].action, "replace")
        self.assertEqual(plan.actions[0].target_path, two)

    def test_fast_restore_preview_does_not_hash_file_contents(self) -> None:
        source_file = self.source / "save.txt"
        source_file.write_text("version one", encoding="utf-8")
        self.run_backup()
        source_file.write_text("version two with a different size", encoding="utf-8")
        self.run_backup()

        store = VersionStore(self.target)
        try:
            runs = sorted(store.list_successful_runs(), key=lambda r: r.id)
            with mock.patch("src.restore.same_file", side_effect=AssertionError("preview should not hash files")):
                plan = build_restore_plan_for_sources(
                    store,
                    runs[0].id,
                    self.cfg.sources,
                    mode="original",
                    compare_contents=False,
                )
        finally:
            store.close()

        self.assertEqual(plan.copy_count, 1)
        self.assertEqual(plan.actions[0].target_path, source_file)

    def test_deleted_source_file_is_archived_and_removed_from_mirror(self) -> None:
        source_file = self.source / "save.txt"
        source_file.write_text("version one", encoding="utf-8")
        self.run_backup()
        source_file.unlink()

        self.run_backup()

        mirror_file = mirror_path_for_source(self.target, source_file)
        self.assertFalse(mirror_file.exists())

        store = VersionStore(self.target)
        try:
            runs = sorted(store.list_successful_runs(), key=lambda r: r.id)
            restore_plan = build_restore_plan(store, runs[0].id, source_file, mode="original")
            self.assertEqual(restore_plan.copy_count, 1)
            self.assertTrue(apply_restore_plan(restore_plan, store, log_cb=lambda _m: None))
        finally:
            store.close()

        self.assertEqual(source_file.read_text(encoding="utf-8"), "version one")

    def test_newly_excluded_file_is_archived_and_removed_from_mirror(self) -> None:
        source_file = self.source / "save.txt"
        source_file.write_text("version one", encoding="utf-8")
        self.run_backup()

        self.cfg.sources[0].excludes = ["save.txt"]
        self.run_backup()

        mirror_file = mirror_path_for_source(self.target, source_file)
        self.assertFalse(mirror_file.exists())
        self.assertTrue(source_file.exists())

        store = VersionStore(self.target)
        try:
            runs = sorted(store.list_successful_runs(), key=lambda r: r.id)
            self.assertEqual(len(runs), 2)
            restore_plan = build_restore_plan(store, runs[0].id, source_file, mode="export", export_root=self.root / "export")
            self.assertEqual(restore_plan.export_count, 1)
            self.assertTrue(apply_restore_plan(restore_plan, store, log_cb=lambda _m: None))
        finally:
            store.close()

        exported = self.root / "export" / mirror_relative_for_source(source_file)
        self.assertEqual(exported.read_text(encoding="utf-8"), "version one")

    def test_export_restore_writes_selected_version_to_folder(self) -> None:
        source_file = self.source / "save.txt"
        export_root = self.root / "export"
        source_file.write_text("version one", encoding="utf-8")
        self.run_backup()
        source_file.write_text("version two with a different size", encoding="utf-8")
        self.run_backup()

        store = VersionStore(self.target)
        try:
            runs = sorted(store.list_successful_runs(), key=lambda r: r.id)
            plan = build_restore_plan(store, runs[0].id, source_file, mode="export", export_root=export_root)
            self.assertEqual(plan.copy_count, 1)
            self.assertTrue(apply_restore_plan(plan, store, log_cb=lambda _m: None))
        finally:
            store.close()

        exported = export_root / mirror_relative_for_source(source_file)
        self.assertEqual(exported.read_text(encoding="utf-8"), "version one")

    def test_export_restore_folder_writes_full_selected_version_with_mirror_structure(self) -> None:
        one = self.source / "one.txt"
        nested = self.source / "nested" / "two.txt"
        nested.parent.mkdir()
        one.write_text("one", encoding="utf-8")
        nested.write_text("two", encoding="utf-8")
        self.run_backup()

        export_root = self.root / "export"
        store = VersionStore(self.target)
        try:
            run = store.list_successful_runs()[0]
            plan = build_restore_plan(store, run.id, self.source, mode="export", export_root=export_root)
            self.assertEqual(plan.export_count, 2)
            self.assertTrue(all(a.action == "export" for a in plan.actions))
            self.assertTrue(apply_restore_plan(plan, store, log_cb=lambda _m: None))
        finally:
            store.close()

        self.assertEqual((export_root / mirror_relative_for_source(one)).read_text(encoding="utf-8"), "one")
        self.assertEqual((export_root / mirror_relative_for_source(nested)).read_text(encoding="utf-8"), "two")
        self.assertFalse((self.target / ".backup_versions" / "restore_safety").exists())

    def test_folder_restore_deletes_files_created_after_selected_run(self) -> None:
        first = self.source / "first.txt"
        later = self.source / "later.txt"
        first.write_text("first", encoding="utf-8")
        self.run_backup()
        later.write_text("later", encoding="utf-8")
        self.run_backup()

        store = VersionStore(self.target)
        try:
            runs = sorted(store.list_successful_runs(), key=lambda r: r.id)
            plan = build_restore_plan(store, runs[0].id, self.source, mode="original")
            delete_targets = {a.target_path for a in plan.actions if a.action == "delete"}
            self.assertIn(later, delete_targets)
            self.assertTrue(apply_restore_plan(plan, store, log_cb=lambda _m: None))
            self.assertEqual(len(store.list_successful_runs()), 2)
        finally:
            store.close()

        self.assertTrue(first.exists())
        self.assertFalse(later.exists())
        self.assertTrue(mirror_path_for_source(self.target, later).exists())

        self.run_backup()
        self.assertFalse(mirror_path_for_source(self.target, later).exists())

    def test_preview_marks_newer_original_file_as_conflict(self) -> None:
        source_file = self.source / "save.txt"
        source_file.write_text("version one", encoding="utf-8")
        self.run_backup()

        store = VersionStore(self.target)
        try:
            run = store.list_successful_runs()[0]
            source_file.write_text("local newer edit", encoding="utf-8")
            os.utime(source_file, (source_file.stat().st_atime + 10, source_file.stat().st_mtime + 10))
            plan = build_restore_plan(store, run.id, source_file, mode="original")
            self.assertEqual(plan.conflict_count, 1)
        finally:
            store.close()

    def test_restore_dialog_builds_preview_for_configured_sources_without_scope_folder(self) -> None:
        one = self.source / "one.txt"
        two = self.source / "two.txt"
        one.write_text("one", encoding="utf-8")
        two.write_text("old two", encoding="utf-8")
        self.run_backup()
        two.write_text("new two with a different size", encoding="utf-8")
        self.run_backup()

        store = VersionStore(self.target)
        try:
            runs = sorted(store.list_successful_runs(), key=lambda r: r.id)
            plan = build_restore_plan_for_sources(
                store,
                runs[0].id,
                self.cfg.sources,
                mode="original",
                export_root=None,
            )
        finally:
            store.close()

        self.assertEqual(len(plan.actions), 1)
        self.assertEqual(plan.actions[0].action, "replace")
        self.assertEqual(plan.actions[0].target_path, two)

    def test_retention_materializes_next_snapshot_before_deleting_oldest_run(self) -> None:
        self.cfg.retention_keep_successful_runs = 2
        one = self.source / "one.txt"
        two = self.source / "two.txt"
        one.write_text("one v1", encoding="utf-8")
        two.write_text("two v1", encoding="utf-8")
        self.run_backup()

        one.write_text("one v2 with a different size", encoding="utf-8")
        self.run_backup()

        two.write_text("two v2 with a different size", encoding="utf-8")
        self.run_backup()

        store = VersionStore(self.target)
        try:
            runs = sorted(store.list_successful_runs(), key=lambda r: r.id)
            self.assertEqual(len(runs), 2)

            export_v2 = self.root / "export_v2"
            plan_v2 = build_restore_plan(store, runs[0].id, self.source, mode="export", export_root=export_v2)
            self.assertEqual(plan_v2.export_count, 2)
            self.assertTrue(apply_restore_plan(plan_v2, store, log_cb=lambda _m: None))

            export_v3 = self.root / "export_v3"
            plan_v3 = build_restore_plan(store, runs[1].id, self.source, mode="export", export_root=export_v3)
            self.assertEqual(plan_v3.export_count, 2)
            self.assertTrue(apply_restore_plan(plan_v3, store, log_cb=lambda _m: None))
        finally:
            store.close()

        self.assertEqual((export_v2 / mirror_relative_for_source(one)).read_text(encoding="utf-8"),
                         "one v2 with a different size")
        self.assertEqual((export_v2 / mirror_relative_for_source(two)).read_text(encoding="utf-8"), "two v1")
        self.assertEqual((export_v3 / mirror_relative_for_source(one)).read_text(encoding="utf-8"),
                         "one v2 with a different size")
        self.assertEqual((export_v3 / mirror_relative_for_source(two)).read_text(encoding="utf-8"),
                         "two v2 with a different size")

    def test_retention_removes_archive_files_that_no_remaining_snapshot_references(self) -> None:
        self.cfg.retention_keep_successful_runs = 1
        source_file = self.source / "save.txt"
        source_file.write_text("version one", encoding="utf-8")
        self.run_backup()
        source_file.write_text("version two with a different size", encoding="utf-8")
        self.run_backup()
        source_file.write_text("version three with a different size again", encoding="utf-8")
        self.run_backup()

        store = VersionStore(self.target)
        try:
            runs = store.list_successful_runs()
            self.assertEqual(len(runs), 1)
            latest_plan = build_restore_plan(store, runs[0].id, source_file, mode="export", export_root=self.root / "latest")
            self.assertEqual(latest_plan.export_count, 1)
            self.assertTrue(apply_restore_plan(latest_plan, store, log_cb=lambda _m: None))
        finally:
            store.close()

        self.assertEqual(
            ((self.root / "latest") / mirror_relative_for_source(source_file)).read_text(encoding="utf-8"),
            "version three with a different size again",
        )
        self.assertEqual(self.archive_files(), [])

    def test_delete_middle_snapshot_materializes_next_snapshot(self) -> None:
        one = self.source / "one.txt"
        two = self.source / "two.txt"
        one.write_text("one v1", encoding="utf-8")
        two.write_text("two v1", encoding="utf-8")
        self.run_backup()

        one.write_text("one v2 with a different size", encoding="utf-8")
        self.run_backup()

        two.write_text("two v2 with a different size", encoding="utf-8")
        self.run_backup()

        store = VersionStore(self.target)
        try:
            runs = sorted(store.list_successful_runs(), key=lambda r: r.id)
            self.assertEqual(len(runs), 3)
            result = store.delete_successful_run(runs[1].id)
            self.assertEqual(result.deleted_run_ids, [runs[1].id])

            remaining = sorted(store.list_successful_runs(), key=lambda r: r.id)
            self.assertEqual([run.id for run in remaining], [runs[0].id, runs[2].id])

            export_old = self.root / "export_old"
            old_plan = build_restore_plan(store, runs[0].id, self.source, mode="export", export_root=export_old)
            self.assertEqual(old_plan.export_count, 2)
            self.assertTrue(apply_restore_plan(old_plan, store, log_cb=lambda _m: None))

            export_latest = self.root / "export_latest"
            latest_plan = build_restore_plan(store, runs[2].id, self.source, mode="export", export_root=export_latest)
            self.assertEqual(latest_plan.export_count, 2)
            self.assertTrue(apply_restore_plan(latest_plan, store, log_cb=lambda _m: None))
        finally:
            store.close()

        self.assertEqual((export_old / mirror_relative_for_source(one)).read_text(encoding="utf-8"), "one v1")
        self.assertEqual((export_old / mirror_relative_for_source(two)).read_text(encoding="utf-8"), "two v1")
        self.assertEqual((export_latest / mirror_relative_for_source(one)).read_text(encoding="utf-8"),
                         "one v2 with a different size")
        self.assertEqual((export_latest / mirror_relative_for_source(two)).read_text(encoding="utf-8"),
                         "two v2 with a different size")


if __name__ == "__main__":
    unittest.main()

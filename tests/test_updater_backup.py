from pathlib import Path
import zipfile

from src.config import PathRule, Settings
from src.copier import run_backup
from src.utils import iter_files
from src.version_store import mirror_path_for_source
from src.zip_snapshot import create_zip_snapshots


def test_backups_skip_only_own_updater_runtime(tmp_path, monkeypatch):
    source = tmp_path / "AppData"
    config = source / "Roaming" / "BackupTool" / "config.json"
    runtime = config.parent / "updates"
    runtime.mkdir(parents=True)
    config.write_text('{"settings": true}', encoding="utf-8")
    for name in ("updater.lock", "BackupTool.exe.download", "BackupToolUpdater.tmp"):
        (runtime / name).write_text("temporary", encoding="utf-8")
    save = source / "game" / "save.sav"
    save.parent.mkdir()
    save.write_bytes(b"game progress")
    other_lock = source / "game" / "updater.lock"
    other_lock.write_text("user data", encoding="utf-8")
    monkeypatch.setattr("src.utils.CONFIG_FILE", config)
    monkeypatch.setattr(Settings, "patch", lambda **kwargs: None)
    cfg = Settings(target_dir=str(tmp_path / "backup"),
                   sources=[PathRule(source=str(source))], wait_on_finish=False)

    assert run_backup(cfg, progress_cb=lambda *_: None, log_cb=lambda *_: None)
    for original in (config, save, other_lock):
        assert mirror_path_for_source(Path(cfg.target_dir), original).read_bytes() == original.read_bytes()
    assert not mirror_path_for_source(Path(cfg.target_dir), runtime).exists()

    snapshot = create_zip_snapshots(cfg, log_cb=lambda *_: None)
    assert snapshot.errors == []
    with zipfile.ZipFile(snapshot.archives[0].archive_path) as archive:
        assert set(archive.namelist()) == {
            config.relative_to(source).as_posix(),
            save.relative_to(source).as_posix(),
            other_lock.relative_to(source).as_posix(),
        }

    # Direct source selections must not re-include the cache or its subfolders.
    nested = runtime / "nested"
    nested.mkdir()
    (nested / "partial.download").write_bytes(b"partial")
    for root in (runtime, nested):
        assert list(iter_files(PathRule(source=str(root)))) == []

import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Iterable, Optional

from src.utils import sha1


VERSION_DIR_NAME = ".backup_versions"


@dataclass(frozen=True)
class RunInfo:
    id: int
    started_at: str
    finished_at: Optional[str]
    success: bool


@dataclass(frozen=True)
class RetentionResult:
    kept_run_ids: list[int]
    deleted_run_ids: list[int]
    deleted_file_count: int
    freed_bytes: int


@dataclass(frozen=True)
class FileRecord:
    id: int
    source_path: str
    source_root: str
    mirror_rel: str
    state: str
    current_size: Optional[int]
    current_mtime: Optional[float]
    current_hash: Optional[str]
    last_seen_run_id: Optional[int]


@dataclass(frozen=True)
class SnapshotItem:
    file_id: int
    source_path: str
    source_root: str
    mirror_rel: str
    state: str
    event: Optional[str]
    content_rel: Optional[str]
    size: Optional[int]
    mtime: Optional[float]
    hash: Optional[str]


def mirror_relative_for_source(src: Path) -> Path:
    resolved = src.expanduser().resolve()
    drive = resolved.drive.rstrip(":")
    if drive:
        return Path(drive) / resolved.relative_to(resolved.anchor)
    anchor = resolved.anchor.strip("\\/").replace(":", "") or "root"
    try:
        return Path(anchor) / resolved.relative_to(resolved.anchor)
    except ValueError:
        return Path(anchor) / resolved.name


def mirror_path_for_source(target_root: Path, src: Path) -> Path:
    return target_root / mirror_relative_for_source(src)


def archive_root(target_root: Path) -> Path:
    return target_root / VERSION_DIR_NAME


class VersionStore:
    def __init__(self, target_root: Path):
        self.target_root = target_root.expanduser().resolve()
        self.root = archive_root(self.target_root)
        self.files_root = self.root / "files"
        self.db_path = self.root / "index.sqlite3"
        self._lock = Lock()
        self.root.mkdir(parents=True, exist_ok=True)
        self.files_root.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.create_function("path_in_scope", 2, _path_in_scope_sql)
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    success INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_path TEXT NOT NULL UNIQUE,
                    source_root TEXT NOT NULL,
                    mirror_rel TEXT NOT NULL,
                    state TEXT NOT NULL,
                    current_size INTEGER,
                    current_mtime REAL,
                    current_hash TEXT,
                    last_seen_run_id INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_files_state ON files(state);
                CREATE INDEX IF NOT EXISTS idx_files_source_root ON files(source_root);
                CREATE TABLE IF NOT EXISTS versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id INTEGER NOT NULL,
                    run_id INTEGER NOT NULL,
                    event TEXT NOT NULL,
                    content_rel TEXT,
                    size INTEGER,
                    mtime REAL,
                    hash TEXT,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY(file_id) REFERENCES files(id),
                    FOREIGN KEY(run_id) REFERENCES runs(id)
                );
                CREATE INDEX IF NOT EXISTS idx_versions_file_run ON versions(file_id, run_id, id);
                """
            )
            self._conn.commit()

    def begin_run(self) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO runs(started_at, success) VALUES(?, 0)",
                (datetime.now().isoformat(timespec="seconds"),),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, success: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET finished_at = ?, success = ? WHERE id = ?",
                (datetime.now().isoformat(timespec="seconds"), int(success), run_id),
            )
            self._conn.commit()

    def list_successful_runs(self) -> list[RunInfo]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, started_at, finished_at, success
                FROM runs
                WHERE success = 1
                ORDER BY id DESC
                """
            ).fetchall()
        return [
            RunInfo(
                id=int(r["id"]),
                started_at=str(r["started_at"]),
                finished_at=r["finished_at"],
                success=bool(r["success"]),
            )
            for r in rows
        ]

    def list_source_roots(self) -> list[str]:
        """Return every source root recorded in this backup workspace."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT source_root, MIN(id) AS first_file_id
                FROM files
                WHERE source_root <> ''
                GROUP BY source_root
                ORDER BY first_file_id
                """
            ).fetchall()
        return [str(row["source_root"]) for row in rows]

    def get_run(self, run_id: int) -> Optional[RunInfo]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, started_at, finished_at, success FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return RunInfo(
            id=int(row["id"]),
            started_at=str(row["started_at"]),
            finished_at=row["finished_at"],
            success=bool(row["success"]),
        )

    def count_runs(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS count FROM runs").fetchone()
        return int(row["count"])

    def get_file(self, source_path: str) -> Optional[FileRecord]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, source_path, source_root, mirror_rel, state,
                       current_size, current_mtime, current_hash, last_seen_run_id
                FROM files
                WHERE source_path = ?
                """,
                (source_path,),
            ).fetchone()
        return self._row_to_file(row) if row else None

    def list_present_files(self) -> list[FileRecord]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, source_path, source_root, mirror_rel, state,
                       current_size, current_mtime, current_hash, last_seen_run_id
                FROM files
                WHERE state = 'present'
                """
            ).fetchall()
        return [self._row_to_file(r) for r in rows]

    def list_files(self) -> list[FileRecord]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, source_path, source_root, mirror_rel, state,
                       current_size, current_mtime, current_hash, last_seen_run_id
                FROM files
                """
            ).fetchall()
        return [self._row_to_file(r) for r in rows]

    def count_versions(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS count FROM versions").fetchone()
        return int(row["count"])

    def successful_run_ids(self) -> set[int]:
        with self._lock:
            rows = self._conn.execute("SELECT id FROM runs WHERE success = 1").fetchall()
        return {int(row["id"]) for row in rows}

    def prune_successful_runs(self, keep_successful: int) -> RetentionResult:
        if keep_successful <= 0:
            return RetentionResult(
                kept_run_ids=[run.id for run in self.list_successful_runs()],
                deleted_run_ids=[],
                deleted_file_count=0,
                freed_bytes=0,
            )

        deleted_run_ids: list[int] = []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id
                FROM runs
                WHERE success = 1
                ORDER BY id DESC
                """
            ).fetchall()
            ordered_desc = [int(row["id"]) for row in rows]
            keep_ids = set(ordered_desc[:keep_successful])
            delete_ids = sorted(run_id for run_id in ordered_desc[keep_successful:] if run_id not in keep_ids)

            if not delete_ids:
                return RetentionResult(
                    kept_run_ids=ordered_desc,
                    deleted_run_ids=[],
                    deleted_file_count=0,
                    freed_bytes=0,
                )

            try:
                self._conn.execute("BEGIN")
                for run_id in delete_ids:
                    self._delete_successful_run_locked(run_id)
                    deleted_run_ids.append(run_id)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

        deleted_file_count, freed_bytes = self._delete_unreferenced_archive_files()
        kept_run_ids = [run.id for run in self.list_successful_runs()]
        return RetentionResult(
            kept_run_ids=kept_run_ids,
            deleted_run_ids=deleted_run_ids,
            deleted_file_count=deleted_file_count,
            freed_bytes=freed_bytes,
        )

    def delete_successful_run(self, run_id: int) -> RetentionResult:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM runs WHERE id = ? AND success = 1",
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Successful backup version {run_id} does not exist")
            try:
                self._conn.execute("BEGIN")
                self._delete_successful_run_locked(run_id)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

        deleted_file_count, freed_bytes = self._delete_unreferenced_archive_files()
        kept_run_ids = [run.id for run in self.list_successful_runs()]
        return RetentionResult(
            kept_run_ids=kept_run_ids,
            deleted_run_ids=[run_id],
            deleted_file_count=deleted_file_count,
            freed_bytes=freed_bytes,
        )

    def record_seen(
            self,
            src: Path,
            source_root: Path,
            mirror_rel: Path,
            mirror_path: Path,
            run_id: int,
            *,
            content_hash: Optional[str] = None,
            force_version: bool = False,
    ) -> None:
        stat = mirror_path.stat()
        content_hash = content_hash or sha1(src)
        source_path = str(src.expanduser().resolve())
        source_root_s = str(source_root.expanduser().resolve())
        mirror_rel_s = mirror_rel.as_posix()
        with self._lock:
            row = self._conn.execute(
                "SELECT id, state FROM files WHERE source_path = ?",
                (source_path,),
            ).fetchone()
            if row is None:
                cur = self._conn.execute(
                    """
                    INSERT INTO files(source_path, source_root, mirror_rel, state,
                                      current_size, current_mtime, current_hash, last_seen_run_id)
                    VALUES(?, ?, ?, 'present', ?, ?, ?, ?)
                    """,
                    (source_path, source_root_s, mirror_rel_s, stat.st_size, stat.st_mtime, content_hash, run_id),
                )
                file_id = int(cur.lastrowid)
                self._insert_version(file_id, run_id, "created", None, stat.st_size, stat.st_mtime, content_hash)
            else:
                self._conn.execute(
                    """
                    UPDATE files
                    SET source_root = ?, mirror_rel = ?, state = 'present',
                        current_size = ?, current_mtime = ?, current_hash = ?, last_seen_run_id = ?
                    WHERE id = ?
                    """,
                    (source_root_s, mirror_rel_s, stat.st_size, stat.st_mtime, content_hash, run_id, int(row["id"])),
                )
                if row["state"] == "deleted":
                    self._insert_version(int(row["id"]), run_id, "created", None, stat.st_size, stat.st_mtime, content_hash)
                elif force_version:
                    self._insert_version(int(row["id"]), run_id, "updated", None, stat.st_size, stat.st_mtime, content_hash)
            self._conn.commit()

    def prepare_archive_current(self, source_path: str, mirror_path: Path, run_id: int) -> Optional[str]:
        if not mirror_path.exists():
            return None
        version_id = None
        with self._lock:
            file_row = self._conn.execute(
                "SELECT id FROM files WHERE source_path = ?",
                (source_path,),
            ).fetchone()
            file_id = int(file_row["id"]) if file_row else None
            if file_id is not None:
                version_row = self._conn.execute(
                    """
                    SELECT id
                    FROM versions
                    WHERE file_id = ? AND event != 'deleted'
                    ORDER BY run_id DESC, id DESC
                    LIMIT 1
                    """,
                    (file_id,),
                ).fetchone()
                version_id = int(version_row["id"]) if version_row else None
        archive_rel = self._archive_relative_path(run_id, source_path, version_id)
        archive_path = self.files_root / archive_rel
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path = self._dedupe_path(archive_path)
        shutil.copy2(mirror_path, archive_path, follow_symlinks=False)
        archive_rel_s = archive_path.relative_to(self.files_root).as_posix()
        if version_id is not None:
            with self._lock:
                row = self._conn.execute(
                    "SELECT content_rel FROM versions WHERE id = ?",
                    (version_id,),
                ).fetchone()
                if row is not None and not row["content_rel"]:
                    self._conn.execute(
                        "UPDATE versions SET content_rel = ? WHERE id = ?",
                        (archive_rel_s, version_id),
                    )
                    self._conn.commit()
        return archive_rel_s

    def record_copied(
            self,
            src: Path,
            source_root: Path,
            mirror_rel: Path,
            mirror_path: Path,
            run_id: int,
            archived_previous_rel: Optional[str],
            content_hash: Optional[str] = None,
    ) -> None:
        stat = mirror_path.stat()
        content_hash = content_hash or sha1(src)
        source_path = str(src.expanduser().resolve())
        source_root_s = str(source_root.expanduser().resolve())
        mirror_rel_s = mirror_rel.as_posix()
        with self._lock:
            row = self._conn.execute(
                "SELECT id, state FROM files WHERE source_path = ?",
                (source_path,),
            ).fetchone()
            if row is None:
                cur = self._conn.execute(
                    """
                    INSERT INTO files(source_path, source_root, mirror_rel, state,
                                      current_size, current_mtime, current_hash, last_seen_run_id)
                    VALUES(?, ?, ?, 'present', ?, ?, ?, ?)
                    """,
                    (source_path, source_root_s, mirror_rel_s, stat.st_size, stat.st_mtime, content_hash, run_id),
                )
                file_id = int(cur.lastrowid)
                event = "created"
            else:
                file_id = int(row["id"])
                if archived_previous_rel:
                    self._attach_archive_to_latest_present(file_id, archived_previous_rel)
                event = "created" if row["state"] == "deleted" else "updated"
                self._conn.execute(
                    """
                    UPDATE files
                    SET source_root = ?, mirror_rel = ?, state = 'present',
                        current_size = ?, current_mtime = ?, current_hash = ?, last_seen_run_id = ?
                    WHERE id = ?
                    """,
                    (source_root_s, mirror_rel_s, stat.st_size, stat.st_mtime, content_hash, run_id, file_id),
                )
            self._insert_version(file_id, run_id, event, None, stat.st_size, stat.st_mtime, content_hash)
            self._conn.commit()

    def record_deleted(
            self,
            record: FileRecord,
            run_id: int,
            archived_previous_rel: Optional[str],
    ) -> None:
        with self._lock:
            if archived_previous_rel:
                self._attach_archive_to_latest_present(record.id, archived_previous_rel)
            self._conn.execute(
                """
                UPDATE files
                SET state = 'deleted', current_size = NULL, current_mtime = NULL, current_hash = NULL,
                    last_seen_run_id = ?
                WHERE id = ?
                """,
                (run_id, record.id),
            )
            self._insert_version(record.id, run_id, "deleted", None, None, None, None)
            self._conn.commit()

    def snapshot_for_scope(self, run_id: int, scope_path: Path) -> list[SnapshotItem]:
        scope_s = str(scope_path.expanduser().resolve())
        with self._lock:
            rows = self._conn.execute(
                """
                WITH scoped_files AS (
                    SELECT id, source_path, source_root, mirror_rel
                    FROM files
                    WHERE path_in_scope(source_path, ?) = 1
                ),
                ranked_versions AS (
                    SELECT
                        v.file_id,
                        v.event,
                        v.content_rel,
                        v.size,
                        v.mtime,
                        v.hash,
                        ROW_NUMBER() OVER (
                            PARTITION BY v.file_id
                            ORDER BY v.run_id DESC, v.id DESC
                        ) AS rn
                    FROM versions v
                    JOIN scoped_files sf ON sf.id = v.file_id
                    JOIN runs r ON r.id = v.run_id
                    WHERE v.run_id <= ? AND r.success = 1
                )
                SELECT
                    f.id,
                    f.source_path,
                    f.source_root,
                    f.mirror_rel,
                    rv.event,
                    rv.content_rel,
                    rv.size,
                    rv.mtime,
                    rv.hash
                FROM scoped_files f
                LEFT JOIN ranked_versions rv ON rv.file_id = f.id AND rv.rn = 1
                """,
                (scope_s, run_id),
            ).fetchall()
        return _rows_to_snapshot_items(rows)

    def snapshot_for_source_root(self, run_id: int, source_root: Path) -> list[SnapshotItem]:
        source_root_s = str(source_root.expanduser().resolve())
        with self._lock:
            rows = self._conn.execute(
                """
                WITH scoped_files AS (
                    SELECT id, source_path, source_root, mirror_rel
                    FROM files
                    WHERE source_root = ?
                ),
                ranked_versions AS (
                    SELECT
                        v.file_id,
                        v.event,
                        v.content_rel,
                        v.size,
                        v.mtime,
                        v.hash,
                        ROW_NUMBER() OVER (
                            PARTITION BY v.file_id
                            ORDER BY v.run_id DESC, v.id DESC
                        ) AS rn
                    FROM versions v
                    JOIN scoped_files sf ON sf.id = v.file_id
                    JOIN runs r ON r.id = v.run_id
                    WHERE v.run_id <= ? AND r.success = 1
                )
                SELECT
                    f.id,
                    f.source_path,
                    f.source_root,
                    f.mirror_rel,
                    rv.event,
                    rv.content_rel,
                    rv.size,
                    rv.mtime,
                    rv.hash
                FROM scoped_files f
                LEFT JOIN ranked_versions rv ON rv.file_id = f.id AND rv.rn = 1
                """,
                (source_root_s, run_id),
            ).fetchall()
        return _rows_to_snapshot_items(rows)

    def content_path_for_snapshot(self, item: SnapshotItem) -> Optional[Path]:
        if item.state != "present":
            return None
        if item.content_rel:
            return self.files_root / item.content_rel
        return self.target_root / Path(item.mirror_rel)

    def archive_restore_target(self, target_path: Path, selected_run_id: int) -> Optional[Path]:
        if not target_path.exists() or not target_path.is_file():
            return None
        label = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        archive_path = (
            self.root
            / "restore_safety"
            / f"{label}_restore_to_run_{selected_run_id}"
            / mirror_relative_for_source(target_path)
        )
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path = self._dedupe_path(archive_path)
        shutil.copy2(target_path, archive_path, follow_symlinks=False)
        return archive_path

    def _attach_archive_to_latest_present(self, file_id: int, archive_rel: str) -> None:
        row = self._conn.execute(
            """
            SELECT id, content_rel
            FROM versions
            WHERE file_id = ? AND event != 'deleted'
            ORDER BY run_id DESC, id DESC
            LIMIT 1
            """,
            (file_id,),
        ).fetchone()
        if row is None or row["content_rel"]:
            return
        self._conn.execute(
            "UPDATE versions SET content_rel = ? WHERE id = ?",
            (archive_rel, int(row["id"])),
        )

    def _insert_version(
            self,
            file_id: int,
            run_id: int,
            event: str,
            content_rel: Optional[str],
            size: Optional[int],
            mtime: Optional[float],
            content_hash: Optional[str],
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO versions(file_id, run_id, event, content_rel, size, mtime, hash, recorded_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (file_id, run_id, event, content_rel, size, mtime, content_hash, datetime.now().isoformat(timespec="seconds")),
        )

    def _next_successful_run_id_locked(self, run_id: int) -> Optional[int]:
        row = self._conn.execute(
            """
            SELECT id
            FROM runs
            WHERE success = 1 AND id > ?
            ORDER BY id ASC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        return int(row["id"]) if row else None

    def _delete_successful_run_locked(self, run_id: int) -> None:
        next_run_id = self._next_successful_run_id_locked(run_id)
        if next_run_id is not None:
            self._materialize_run_for_deleted_previous_locked(run_id, next_run_id)
        self._conn.execute("DELETE FROM versions WHERE run_id = ?", (run_id,))
        self._conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))

    def _materialize_run_for_deleted_previous_locked(self, deleted_run_id: int, next_run_id: int) -> None:
        files = self._conn.execute("SELECT id FROM files").fetchall()
        for file_row in files:
            file_id = int(file_row["id"])
            before = self._effective_successful_version_locked(file_id, deleted_run_id, include_boundary=False)
            after = self._effective_successful_version_locked(file_id, next_run_id, include_boundary=True)
            if _same_effective_version(before, after):
                continue
            event = "deleted"
            content_rel = None
            size = None
            mtime = None
            content_hash = None
            if after is not None:
                event = str(after["event"])
                content_rel = after["content_rel"]
                size = after["size"]
                mtime = after["mtime"]
                content_hash = after["hash"]
            self._insert_version(file_id, next_run_id, event, content_rel, size, mtime, content_hash)

    def _effective_successful_version_locked(
            self,
            file_id: int,
            run_id: int,
            *,
            include_boundary: bool,
    ) -> Optional[sqlite3.Row]:
        op = "<=" if include_boundary else "<"
        return self._conn.execute(
            f"""
            SELECT v.event, v.content_rel, v.size, v.mtime, v.hash
            FROM versions v
            JOIN runs r ON r.id = v.run_id
            WHERE v.file_id = ? AND v.run_id {op} ? AND r.success = 1
            ORDER BY v.run_id DESC, v.id DESC
            LIMIT 1
            """,
            (file_id, run_id),
        ).fetchone()

    def _delete_unreferenced_archive_files(self) -> tuple[int, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT content_rel FROM versions WHERE content_rel IS NOT NULL"
            ).fetchall()
        referenced = {Path(str(row["content_rel"])).as_posix() for row in rows}
        deleted = 0
        freed = 0
        if not self.files_root.exists():
            return deleted, freed

        for path in sorted(self.files_root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_file():
                rel = path.relative_to(self.files_root).as_posix()
                if rel in referenced:
                    continue
                try:
                    size = path.stat().st_size
                    path.unlink()
                    deleted += 1
                    freed += size
                except OSError:
                    continue
            elif path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
        return deleted, freed

    def _archive_relative_path(self, run_id: int, source_path: str, version_id: Optional[int]) -> Path:
        return Path(self._run_archive_folder(run_id)) / mirror_relative_for_source(Path(source_path))

    def _run_archive_folder(self, run_id: int) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT started_at FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        stamp = row["started_at"] if row else datetime.now().isoformat(timespec="seconds")
        try:
            dt = datetime.fromisoformat(str(stamp))
            label = dt.strftime("%Y%m%d_%H%M%S")
        except ValueError:
            label = "".join(ch if ch.isalnum() else "_" for ch in str(stamp))[:32]
        return f"{label}_run_{run_id}"

    @staticmethod
    def _dedupe_path(path: Path) -> Path:
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        idx = 2
        while True:
            candidate = parent / f"{stem}.{idx}{suffix}"
            if not candidate.exists():
                return candidate
            idx += 1

    @staticmethod
    def _row_to_file(row: sqlite3.Row) -> FileRecord:
        return FileRecord(
            id=int(row["id"]),
            source_path=str(row["source_path"]),
            source_root=str(row["source_root"]),
            mirror_rel=str(row["mirror_rel"]),
            state=str(row["state"]),
            current_size=row["current_size"],
            current_mtime=row["current_mtime"],
            current_hash=row["current_hash"],
            last_seen_run_id=row["last_seen_run_id"],
        )


def source_roots_for_rules(sources: Iterable[object]) -> list[Path]:
    roots: list[Path] = []
    for rule in sources:
        source = getattr(rule, "source", None)
        if not source:
            continue
        try:
            root = Path(source).expanduser().resolve()
        except OSError:
            continue
        if root.exists():
            roots.append(root)
    return roots


def _path_in_scope(path: str, scope: str) -> bool:
    path_l = path.rstrip("\\/").casefold()
    scope_l = scope.rstrip("\\/").casefold()
    return path_l == scope_l or path_l.startswith(scope_l + "\\") or path_l.startswith(scope_l + "/")


def _path_in_scope_sql(path: object, scope: object) -> int:
    if path is None or scope is None:
        return 0
    return int(_path_in_scope(str(path), str(scope)))


def _rows_to_snapshot_items(rows: Iterable[sqlite3.Row]) -> list[SnapshotItem]:
    result: list[SnapshotItem] = []
    for row in rows:
        source_path = str(row["source_path"])
        event = row["event"]
        state = "absent" if event is None or event == "deleted" else "present"
        result.append(
            SnapshotItem(
                file_id=int(row["id"]),
                source_path=source_path,
                source_root=str(row["source_root"]),
                mirror_rel=str(row["mirror_rel"]),
                state=state,
                event=str(event) if event is not None else None,
                content_rel=row["content_rel"],
                size=row["size"],
                mtime=row["mtime"],
                hash=row["hash"],
            )
        )
    return result


def _same_effective_version(left: Optional[sqlite3.Row], right: Optional[sqlite3.Row]) -> bool:
    if left is None or right is None:
        return left is right
    return (
        left["event"] == right["event"]
        and left["content_rel"] == right["content_rel"]
        and left["size"] == right["size"]
        and left["mtime"] == right["mtime"]
        and left["hash"] == right["hash"]
    )

import hashlib
import logging
import shutil
from pathlib import Path

LOG_PATH = Path("backup_app.log")
logging.basicConfig(
    filename=LOG_PATH, level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s", encoding="utf-8"
)
log = logging.getLogger("backup_tool")


def sha1(path: Path, buf: int = 1 << 17) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        while chunk := f.read(buf):
            h.update(chunk)
    return h.hexdigest()


def same_file(src: Path, dst: Path, use_hash: bool = False) -> bool:
    if not dst.exists():
        return False
    ss, ds = src.stat(), dst.stat()
    if ss.st_size != ds.st_size or int(ss.st_mtime) != int(ds.st_mtime):
        return False
    return not use_hash or sha1(src) == sha1(dst)


def copy2(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst, follow_symlinks=False)

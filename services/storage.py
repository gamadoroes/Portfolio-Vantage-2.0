import os
import tempfile
import threading
import time
from pathlib import Path

# Per-path locks to prevent concurrent writes to the same file
_locks = {}
_locks_lock = threading.Lock()


def _get_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _locks_lock:
        if key not in _locks:
            _locks[key] = threading.Lock()
        return _locks[key]


def _replace_with_retry(tmp_path: str, target_path: Path, attempts: int = 5, delay_s: float = 0.05):
    last_error = None
    for i in range(attempts):
        try:
            os.replace(tmp_path, str(target_path))
            return
        except (PermissionError, OSError) as e:
            last_error = e
            if i == attempts - 1:
                break
            time.sleep(delay_s * (i + 1))
    raise last_error


def read_text(path: Path, default=""):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return default


def write_text(path: Path, content: str):
    lock = _get_lock(path)
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp", prefix=".write_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            _replace_with_retry(tmp, path)
        except (PermissionError, OSError):
            # Last resort: non-atomic direct write if replace keeps failing.
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            try:
                os.unlink(tmp)
            except OSError:
                pass
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

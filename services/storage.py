import json
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


def read_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _read_json_reliable(path: Path, default, attempts: int = 4, delay_s: float = 0.05):
    """Read JSON with retries for transient I/O errors (OneDrive sync, antivirus).

    Unlike ``read_json``, this retries when the file *exists* but cannot be
    read or parsed, so that a momentary lock doesn't silently return the
    default and cause the caller to overwrite good data with empty data.
    """
    for i in range(attempts):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return default
        except Exception:
            if i == attempts - 1:
                # Final attempt failed — fall back to default only if the file
                # doesn't exist; otherwise re-raise to avoid silent data loss.
                if not path.exists():
                    return default
                raise
            time.sleep(delay_s * (i + 1))


def _write_json_unlocked(path: Path, data):
    """Atomic write to JSON file. Caller must hold the path lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix=".write_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        _replace_with_retry(tmp, path)
    except (PermissionError, OSError):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
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


def write_json(path: Path, data):
    lock = _get_lock(path)
    with lock:
        _write_json_unlocked(path, data)


def update_json(path: Path, updater, default=None):
    """Atomically read-modify-write a JSON file.
    updater(data) receives current data, mutates or returns new data.
    The file lock is held for the entire read-modify-write cycle.
    """
    lock = _get_lock(path)
    with lock:
        data = _read_json_reliable(path, default if default is not None else {})
        result = updater(data)
        if result is None:
            result = data  # allow in-place mutation
        _write_json_unlocked(path, result)
        return result


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

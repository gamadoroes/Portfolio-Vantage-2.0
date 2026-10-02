import os
import threading
from unittest.mock import patch

import pytest

from services.storage import _replace_with_retry, read_text, write_text


def test_write_and_read_text(tmp_path):
    path = tmp_path / "note.txt"
    write_text(path, "hello world")
    assert read_text(path) == "hello world"


def test_read_text_missing_file_returns_default(tmp_path):
    path = tmp_path / "nope.txt"
    assert read_text(path) == ""
    assert read_text(path, "fallback") == "fallback"


def test_write_text_creates_parent_dirs(tmp_path):
    path = tmp_path / "a" / "b" / "c" / "note.txt"
    write_text(path, "nested")
    assert read_text(path) == "nested"


def test_write_text_overwrites_existing(tmp_path):
    path = tmp_path / "note.txt"
    write_text(path, "first")
    write_text(path, "second")
    assert read_text(path) == "second"


def test_concurrent_text_writes_dont_corrupt(tmp_path):
    """Multiple threads writing to the same text file must not produce a
    torn/corrupt file — whatever ends up on disk must be exactly one
    writer's complete content, never an interleaving of two."""
    path = tmp_path / "shared.txt"
    errors = []

    def writer(n):
        try:
            for i in range(20):
                write_text(path, f"thread-{n}-iteration-{i}")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Errors during concurrent writes: {errors}"
    result = read_text(path)
    assert result.startswith("thread-")
    assert "-iteration-" in result


def test_replace_with_retry_succeeds_first_try(tmp_path):
    tmp = tmp_path / "src.tmp"
    tmp.write_text("content", encoding="utf-8")
    target = tmp_path / "target.txt"

    _replace_with_retry(str(tmp), target)

    assert target.read_text(encoding="utf-8") == "content"
    assert not tmp.exists()


def test_replace_with_retry_retries_on_permission_error_then_succeeds(tmp_path):
    """A transient PermissionError (e.g. OneDrive briefly holding the file
    open) must be retried rather than raised immediately, and the retry
    must go on to complete the real replace."""
    tmp = tmp_path / "src.tmp"
    tmp.write_text("content", encoding="utf-8")
    target = tmp_path / "target.txt"

    real_replace = os.replace
    call_count = {"n": 0}

    def flaky_replace(src, dst):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise PermissionError("simulated transient lock")
        return real_replace(src, dst)

    with patch("services.storage.os.replace", side_effect=flaky_replace), \
         patch("services.storage.time.sleep", return_value=None):
        _replace_with_retry(str(tmp), target, attempts=5, delay_s=0.01)

    assert call_count["n"] == 3
    assert target.read_text(encoding="utf-8") == "content"


def test_replace_with_retry_raises_last_error_after_exhausting_attempts(tmp_path):
    """If os.replace keeps failing for the whole attempt budget, the last
    error must be re-raised — not swallowed — so callers can fall back."""
    tmp = tmp_path / "src.tmp"
    tmp.write_text("content", encoding="utf-8")
    target = tmp_path / "target.txt"

    with patch("services.storage.os.replace", side_effect=PermissionError("locked")), \
         patch("services.storage.time.sleep", return_value=None):
        with pytest.raises(PermissionError):
            _replace_with_retry(str(tmp), target, attempts=3, delay_s=0.01)


def test_write_text_falls_back_to_direct_write_when_replace_keeps_failing(tmp_path):
    """write_text must fall back to a non-atomic direct write if the atomic
    rename keeps failing, rather than losing the write entirely."""
    path = tmp_path / "note.txt"

    with patch("services.storage._replace_with_retry", side_effect=PermissionError("locked")):
        write_text(path, "fallback content")

    assert read_text(path) == "fallback content"

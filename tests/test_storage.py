import threading

from services.storage import read_json, read_text, write_json, write_text


def test_write_and_read_json(tmp_path):
    path = tmp_path / "data.json"
    payload = {"key": "value", "nums": [1, 2, 3]}
    write_json(path, payload)
    assert read_json(path, None) == payload


def test_read_json_missing_file_returns_default(tmp_path):
    path = tmp_path / "nope.json"
    assert read_json(path, {"fallback": True}) == {"fallback": True}


def test_read_json_corrupt_file_returns_default(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json at all", encoding="utf-8")
    assert read_json(path, []) == []


def test_write_json_creates_parent_dirs(tmp_path):
    path = tmp_path / "a" / "b" / "c" / "data.json"
    write_json(path, {"nested": True})
    assert read_json(path, None) == {"nested": True}


def test_write_and_read_text(tmp_path):
    path = tmp_path / "note.txt"
    write_text(path, "hello world")
    assert read_text(path) == "hello world"


def test_read_text_missing_file_returns_default(tmp_path):
    path = tmp_path / "nope.txt"
    assert read_text(path) == ""
    assert read_text(path, "fallback") == "fallback"


def test_write_json_overwrites_existing(tmp_path):
    path = tmp_path / "data.json"
    write_json(path, {"v": 1})
    write_json(path, {"v": 2})
    assert read_json(path, None) == {"v": 2}


def test_concurrent_writes_dont_corrupt(tmp_path):
    """Multiple threads writing to the same file should not produce corrupt JSON."""
    path = tmp_path / "shared.json"
    errors = []

    def writer(n):
        try:
            for i in range(20):
                write_json(path, {"thread": n, "iteration": i})
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Errors during concurrent writes: {errors}"
    # File should contain valid JSON after all writes
    result = read_json(path, None)
    assert result is not None
    assert "thread" in result
    assert "iteration" in result

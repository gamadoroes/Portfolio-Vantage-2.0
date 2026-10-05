import sys
from pathlib import Path

from db.connection import default_database_path, get_database_path, set_database_path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _expected_default(home):
    if sys.platform == "win32":
        return home / "PortfolioVantage" / "app.db"
    return home / ".local" / "share" / "PortfolioVantage" / "app.db"


def _as_home(monkeypatch, home):
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    set_database_path(None)


def test_default_database_is_a_per_user_absolute_path(tmp_path, monkeypatch):
    _as_home(monkeypatch, tmp_path)

    assert Path(get_database_path()) == _expected_default(tmp_path)
    assert Path(default_database_path()) == _expected_default(tmp_path)
    assert Path(get_database_path()).is_absolute()


def test_default_database_is_not_under_appdata(tmp_path, monkeypatch):
    # The Microsoft Store build of Python silently redirects writes under %LOCALAPPDATA% into a
    # private per-package folder. A database "at" AppData\Local\... would then be invisible to
    # Explorer, backup tools and every other Python, and a different Python would quietly start
    # an empty one.
    appdata = tmp_path / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    _as_home(monkeypatch, tmp_path)

    assert not Path(get_database_path()).is_relative_to(appdata)
    assert not Path(get_database_path()).is_relative_to(tmp_path / "AppData")


def test_default_database_does_not_depend_on_the_current_directory(tmp_path, monkeypatch):
    # The first default was the relative path "instance/app.db", so launching from another folder
    # silently started a second, empty database.
    _as_home(monkeypatch, tmp_path)
    here = Path(get_database_path())

    elsewhere = tmp_path / "somewhere" / "else"
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)

    assert Path(get_database_path()) == here


def test_database_path_setting_beats_the_default(tmp_path, monkeypatch):
    chosen = tmp_path / "chosen.db"
    monkeypatch.setenv("DATABASE_PATH", str(chosen))
    set_database_path(None)

    assert Path(get_database_path()) == chosen


def test_tests_never_write_snapshots_into_the_real_backups_folder():
    # Snapshots default to a folder inside the repo. Without the override in tests/conftest.py, a
    # test run would write snapshots of throwaway test databases there and could prune real ones.
    from config import Config

    assert not Path(Config.BACKUP_DIR).resolve().is_relative_to(REPO_ROOT)

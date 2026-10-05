import os
from pathlib import Path

from dotenv import load_dotenv

from db.connection import default_database_path

load_dotenv()

APP_ROOT = Path(__file__).resolve().parent


class Config:
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "research_architect_dev_key_2025")

    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
    OPENAI_DEEP_RESEARCH_MODEL = os.environ.get("OPENAI_DEEP_RESEARCH_MODEL", "o4-mini-deep-research")

    DATABASE_PATH = os.environ.get("DATABASE_PATH") or default_database_path()

    # Daily database snapshots. Closed files, so safe to keep in a synced folder (unlike the live database).
    BACKUP_DIR = os.environ.get("BACKUP_DIR") or str(APP_ROOT / "backups")


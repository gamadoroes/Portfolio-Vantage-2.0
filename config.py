import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "research_architect_dev_key_2025")

    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
    OPENAI_DEEP_RESEARCH_MODEL = os.environ.get("OPENAI_DEEP_RESEARCH_MODEL", "o4-mini-deep-research")

    DATABASE_PATH = os.environ.get("DATABASE_PATH", "instance/app.db")


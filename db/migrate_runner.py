import sqlite3
from datetime import datetime
from pathlib import Path

from .connection import get_connection

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _ensure_migrations_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )


def _applied_versions(conn):
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row["version"] for row in rows}


def _migration_files():
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def apply_migrations():
    with get_connection() as conn:
        _ensure_migrations_table(conn)
        applied = _applied_versions(conn)
        for path in _migration_files():
            version = path.stem
            if version in applied:
                continue
            try:
                conn.execute("BEGIN")
                # Parse and execute SQL statements from migration file
                # using sqlite3.complete_statement() for proper SQL handling
                # (correctly accounts for string literals, comments, triggers, etc.)
                sql_content = path.read_text(encoding="utf-8")
                statement_buffer = ""
                for line in sql_content.split("\n"):
                    statement_buffer += line + "\n"
                    if sqlite3.complete_statement(statement_buffer):
                        # Execute the complete statement
                        if statement_buffer.strip():
                            conn.execute(statement_buffer)
                        statement_buffer = ""
                # Record successful migration
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, datetime.now().isoformat()),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

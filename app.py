import os
import threading
import time

from flask import Flask

from config import Config
from db import backup, migrate_runner
from routes.ai import ai_bp
from routes.artifacts import artifacts_bp
from routes.board import board_bp
from routes.chats import chats_bp
from routes.evidence import evidence_bp
from routes.files import files_bp
from routes.main import main_bp
from routes.projects import projects_bp
from routes.prompts import prompts_bp
from routes.research_tasks import research_tasks_bp


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    from db.connection import has_database_path_override, set_database_path
    # Tests pre-pin the DB path via set_database_path() before calling
    # create_app(); don't clobber that override back to the production default.
    if not has_database_path_override():
        set_database_path(app.config["DATABASE_PATH"])

    app.register_blueprint(main_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(chats_bp)
    app.register_blueprint(artifacts_bp)
    app.register_blueprint(prompts_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(research_tasks_bp)
    app.register_blueprint(board_bp)
    app.register_blueprint(evidence_bp)

    # Bring the schema up to date on the first request, not here: `from app import
    # app` runs create_app() at import time, and importing must not modify a database.
    schema_lock = threading.Lock()
    schema_ready = {"done": False}
    snapshot_check = {"next_at": 0.0}  # time.monotonic() of the next "is a daily snapshot due?" check

    @app.before_request
    def ensure_schema_is_current():
        if not schema_ready["done"]:
            with schema_lock:
                if not schema_ready["done"]:
                    migrate_runner.apply_migrations()
                    schema_ready["done"] = True

        # Daily snapshot: checked at most hourly, by one request at a time. A failed snapshot is
        # reported but must never break the request that happened to trigger it.
        if time.monotonic() >= snapshot_check["next_at"]:
            with schema_lock:
                if time.monotonic() >= snapshot_check["next_at"]:
                    snapshot_check["next_at"] = time.monotonic() + 3600
                    try:
                        backup.ensure_daily_snapshot(app.config["BACKUP_DIR"])
                    except Exception as exc:
                        print(f"[backup] could not write the daily snapshot: {exc}")

    return app


app = create_app()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    debug_mode = _env_bool("FLASK_DEBUG", False)
    port = int(os.getenv("PORT", "5000"))
    app.run(
        debug=debug_mode,
        port=port,
        threaded=True,
        # Auto-reloader can interrupt active SSE streams and surface as network errors.
        use_reloader=False,
    )

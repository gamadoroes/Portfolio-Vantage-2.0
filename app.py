from flask import Flask
import os

from config import Config
from routes.ai import ai_bp
from routes.artifacts import artifacts_bp
from routes.chats import chats_bp
from routes.files import files_bp
from routes.main import main_bp
from routes.prompts import prompts_bp
from routes.projects import projects_bp


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    app.register_blueprint(main_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(chats_bp)
    app.register_blueprint(artifacts_bp)
    app.register_blueprint(prompts_bp)
    app.register_blueprint(ai_bp)

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

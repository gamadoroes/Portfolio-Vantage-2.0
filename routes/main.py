from flask import Blueprint, render_template, session, jsonify, current_app

from services.project_service import list_projects

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    projects = list_projects()
    if not projects:
        return render_template("index.html", projects=[], current_project=None)

    current_project = session.get("current_project", projects[0])
    if current_project not in projects:
        current_project = projects[0]

    session["current_project"] = current_project
    return render_template("index.html", projects=projects, current_project=current_project)


@main_bp.route("/api/config/status")
def config_status():
    return jsonify(
        {
            "openai_api_key_set": bool(current_app.config.get("OPENAI_API_KEY")),
            "anthropic_api_key_set": bool(current_app.config.get("ANTHROPIC_API_KEY")),
            "openai_deep_research_model": current_app.config.get(
                "OPENAI_DEEP_RESEARCH_MODEL", ""
            ),
        }
    )

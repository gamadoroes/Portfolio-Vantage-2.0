import json
import os

from flask import Blueprint, Response, current_app, jsonify, request, send_file, session, stream_with_context

from services.chat_service import append_message_to_chat, create_new_chat, load_chat_sessions_locked
from services.deep_research_output import extract_deep_research_output as _extract_deep_research_output
from services.docx_service import generate_insights_report
from services.file_index_service import (
    reconcile_file_index,
    reconcile_selected_file_ids,
    resolve_file_refs_to_names,
)
from services.file_service import load_project_files
from services.llm_service import edit_completion, prompt_completion, stream_chat_completion
from services.openai_service import retrieve_deep_research, start_deep_research
from services.project_service import load_project_prompt, normalize_project_name, project_exists
from services.prompt_drafting_service import strip_prompt_budget_sections as _strip_prompt_budget_sections
from services.research_run_service import (
    cancel_run,
    complete_run,
    create_run,
    fail_run,
    is_duplicate_run,
    load_runs,
    update_run,
)

ai_bp = Blueprint("ai", __name__)
HIDDEN_SOURCE_FILES = {"insights.json", "insights_history.json", "excluded_competitors.json"}
DEFAULT_MAX_FILE_CHARS = int(os.getenv("AI_MAX_FILE_CHARS", "18000"))
DEFAULT_MAX_CONTEXT_CHARS = int(os.getenv("AI_MAX_CONTEXT_CHARS", "90000"))
DEFAULT_CHAT_MAX_TOKENS = int(os.getenv("AI_CHAT_MAX_TOKENS", "12000"))


def _existing_project_name(raw_project):
    project = normalize_project_name(raw_project)
    if not project or not project_exists(project):
        return None
    return project


def _source_files_only(files):
    if not isinstance(files, dict):
        return {}
    return {k: v for k, v in files.items() if k not in HIDDEN_SOURCE_FILES}


def _clip_text(content, max_chars):
    text = content if isinstance(content, str) else str(content or "")
    if len(text) <= max_chars:
        return text

    head = max_chars // 2
    tail = max_chars - head
    return (
        text[:head]
        + "\n\n[...content truncated for relevance and latency...]\n\n"
        + text[-tail:]
    )


def _build_files_context(files, max_file_chars=DEFAULT_MAX_FILE_CHARS, max_total_chars=DEFAULT_MAX_CONTEXT_CHARS):
    blocks = ["# REFERENCE DATA\n\n"]
    used = len(blocks[0])

    for filename, content in files.items():
        clipped = _clip_text(content, max_file_chars)
        block = f"## {filename}\n\n{clipped}\n\n---\n\n"

        if used + len(block) > max_total_chars:
            remaining = max_total_chars - used
            if remaining > 256:
                blocks.append(block[:remaining])
            blocks.append("\n\n[Reference data truncated due to size limits.]\n")
            break

        blocks.append(block)
        used += len(block)

    return "".join(blocks)


def _max_tokens_for_request(stateless, insight_type):
    if not stateless:
        return DEFAULT_CHAT_MAX_TOKENS

    if insight_type == "competitors":
        return 3200
    if isinstance(insight_type, str) and insight_type.startswith("phase-"):
        if insight_type == "phase-7":
            return 6500
        return 5200
    return 6000


def _temperature_for_request(stateless, insight_type):
    if not stateless:
        return 0.35
    if insight_type == "competitors":
        return 0.25
    if isinstance(insight_type, str) and insight_type.startswith("phase-"):
        if insight_type == "phase-7":
            return 0.25
        return 0.15
    return 0.25


@ai_bp.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    project = _existing_project_name(data.get("project") or session.get("current_project"))
    if not project:
        return jsonify({"error": "No project selected"}), 400

    user_message = data.get("message")
    chat_id = data.get("chat_id")
    stateless = bool(data.get("stateless"))
    insight_type = data.get("insight_type")
    insights_context = data.get("insights_context")
    require_sources = bool(data.get("require_sources"))

    system_prompt = load_project_prompt(project, "system_prompt")
    project_prompt = load_project_prompt(project, "project_prompt")
    all_files = _source_files_only(load_project_files(project))
    index_entries = reconcile_file_index(project)
    selection_state = reconcile_selected_file_ids(project, index_entries)
    selected_files = selection_state.get("selected_files", [])

    # Optional override: restrict context to specific source files.
    # Use "is not None" so callers can intentionally pass [] (e.g. Phase 7 consolidation only).
    source_file_ids_override = data.get("source_file_ids", None)
    source_files_override = data.get("source_files", None)
    if source_file_ids_override is not None:
        requested = (
            source_file_ids_override if isinstance(source_file_ids_override, list) else []
        )
        resolved = resolve_file_refs_to_names(project, requested, index_entries)
        files = {k: v for k, v in all_files.items() if k in set(resolved)}
    elif source_files_override is not None:
        requested = source_files_override if isinstance(source_files_override, list) else []
        resolved = resolve_file_refs_to_names(project, requested, index_entries)
        files = {k: v for k, v in all_files.items() if k in set(resolved)}
    elif selected_files:
        files = {k: v for k, v in all_files.items() if k in set(selected_files)}
    else:
        files = all_files

    if require_sources and not files:
        return (
            jsonify(
                {
                    "error": (
                        "No valid source files available for this run. "
                        "Link phase files (or select source files) and retry."
                    )
                }
            ),
            400,
        )

    if stateless:
        messages = [{"role": "user", "content": user_message}]
    else:
        # Use the locked reader so we never see a half-written file from a
        # concurrent append in another thread (e.g. a second browser tab).
        sessions = load_chat_sessions_locked(project)
        if chat_id not in sessions:
            chat_id = create_new_chat(project)
            sessions = load_chat_sessions_locked(project)

        # Build message list for LLM context (read-only copy)
        messages = list(sessions[chat_id]["messages"])
        messages.append({"role": "user", "content": user_message})

        # Atomically persist user message immediately (not after stream completes)
        append_message_to_chat(project, chat_id, {"role": "user", "content": user_message})

    def generate():
        try:
            yield f"data: {json.dumps({'type': 'status', 'message': 'Analysing...'})}\n\n"

            system_blocks = []
            system_blocks.append(
                {
                    "type": "text",
                    "text": """ROLE: Senior Strategy Consultant specialising in Australian Higher Education.
TONE: McKinsey-quality consulting — authoritative, evidence-based, and action-oriented.
LANGUAGE: Australian English throughout (e.g. "analyse", "organised", "recognised", "programme" where contextually appropriate).
OUTPUT: Well-structured Markdown with clear headings (##, ###), bullet points, bold emphasis, and professional narrative. Use code blocks for copyable prompts. Blend analytical prose with structured lists for readability.""",
                    "cache_control": {"type": "ephemeral"},
                }
            )

            if project_prompt:
                system_blocks.append(
                    {
                        "type": "text",
                        "text": f"# OBJECTIVE\n\n{project_prompt}",
                        "cache_control": {"type": "ephemeral"},
                    }
                )

            if files:
                files_context = _build_files_context(files)
                system_blocks.append(
                    {
                        "type": "text",
                        "text": files_context,
                        "cache_control": {"type": "ephemeral"},
                    }
                )

            if system_prompt:
                system_blocks.append(
                    {
                        "type": "text",
                        "text": f"# CUSTOM RULES\n\n{system_prompt}",
                        "cache_control": {"type": "ephemeral"},
                    }
                )

            if insights_context:
                if isinstance(insights_context, (dict, list)):
                    insights_text = json.dumps(insights_context, ensure_ascii=False, indent=2)
                else:
                    insights_text = str(insights_context)
                if insights_text.strip():
                    system_blocks.append(
                        {
                            "type": "text",
                            "text": f"# PRIOR PHASE INSIGHTS\n\n{insights_text}",
                            "cache_control": {"type": "ephemeral"},
                        }
                    )

            full_response = ""
            max_tokens = _max_tokens_for_request(stateless, insight_type)
            temperature = _temperature_for_request(stateless, insight_type)
            for text in stream_chat_completion(
                system_blocks,
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
            ):
                full_response += text
                yield f"data: {json.dumps({'type': 'content', 'text': text})}\n\n"

            if not stateless:
                # Atomically persist assistant message (user message already saved)
                append_message_to_chat(project, chat_id, {"role": "assistant", "content": full_response})

            done_payload = {"type": "done"}
            if not stateless:
                done_payload["chat_id"] = chat_id
            yield f"data: {json.dumps(done_payload)}\n\n"

        except Exception as e:
            print(f"Error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@ai_bp.route("/api/edit", methods=["POST"])
def edit_text():
    try:
        data = request.get_json(silent=True) or {}
        project = _existing_project_name(data.get("project") or session.get("current_project"))

        selected_text = data.get("selected_text", "")
        edit_instruction = data.get("instruction")
        full_chapter = data.get("full_chapter", "")

        system_prompt = load_project_prompt(project, "system_prompt")

        has_selection = bool(selected_text and selected_text.strip())

        if has_selection:
            edit_system = """You are a senior Strategy Consultant specialising in Australian Higher Education.
Write in a McKinsey-quality consulting tone — authoritative, evidence-based, and action-oriented.
Use Australian English throughout (e.g. "analyse", "organised", "recognised").

TASK: Rewrite the user's selected text based on their instruction.
Output ONLY the rewritten text. Maintain professional register and analytical depth."""
        else:
            edit_system = """You are a senior Strategy Consultant specialising in Australian Higher Education.
Write in a McKinsey-quality consulting tone — authoritative, evidence-based, and action-oriented.
Use Australian English throughout (e.g. "analyse", "organised", "recognised").

TASK: Respond to the user's instruction based on the document context provided.
Output well-structured content in Markdown format with ## headings, ### sub-headings, bullet points, and bold emphasis where appropriate. Blend narrative prose with structured lists for readability."""

        if system_prompt:
            edit_system += f"\n\nMethodology Guidelines:\n{system_prompt}"

        # Include linked source files as reference data when provided
        source_file_ids_override = data.get("source_file_ids")
        source_files_override = data.get("source_files")
        if (source_file_ids_override or source_files_override) and project:
            all_files = _source_files_only(load_project_files(project))
            index_entries = reconcile_file_index(project)
            requested = (
                source_file_ids_override
                if isinstance(source_file_ids_override, list) and source_file_ids_override
                else source_files_override
            )
            resolved_files = resolve_file_refs_to_names(project, requested, index_entries)
            source_context = {k: v for k, v in all_files.items() if k in set(resolved_files)}
            if source_context:
                ref = "\n\n" + _build_files_context(source_context, max_file_chars=12000, max_total_chars=50000)
                edit_system += ref

        if full_chapter:
            edit_system += f"\n\nFULL DOCUMENT CONTEXT:\n{full_chapter}"

        if has_selection:
            user_content = f"""Rewrite this text according to my instructions.

ORIGINAL TEXT:
{selected_text}

INSTRUCTIONS:
{edit_instruction}

OUTPUT (rewritten text only):"""
        else:
            user_content = f"""{edit_instruction}"""

        messages = [
            {
                "role": "user",
                "content": user_content,
            }
        ]

        response = edit_completion(edit_system, messages)

        return jsonify(
            {
                "response": response.content[0].text,
                "tokens": {
                    "input": response.usage.input_tokens,
                    "output": response.usage.output_tokens,
                },
            }
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@ai_bp.route("/api/prompt-dev", methods=["POST"])
def prompt_dev():
    try:
        data = request.json or {}
        framework = data.get("framework", "general")
        inputs = data.get("inputs", {})
        system_prompt = data.get(
            "system_prompt",
            "ROLE: Prompt Engineer. TASK: Produce a high-quality, copy-ready prompt.",
        )

        user_lines = [f"Framework: {framework}"]
        for key, value in inputs.items():
            user_lines.append(f"{key}: {value}")
        user_message = (
            "Create a prompt based on the following inputs:\n"
            + "\n".join(user_lines)
            + "\n\nOUTPUT RULES:\n"
            + "- Do not include token budgets, budget allocation, token counts, runtime limits, or timeframe sections.\n"
            + "- Do not include headings like 'Token Budget Allocation' or 'Budget & Timeline'.\n"
            + "- Return only the final prompt text."
        )

        prompt_text = prompt_completion(system_prompt, user_message)
        prompt_text = _strip_prompt_budget_sections(prompt_text)
        return jsonify({"success": True, "prompt": prompt_text})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@ai_bp.route("/api/deep-research/start", methods=["POST"])
def deep_research_start():
    try:
        data = request.get_json(silent=True) or {}
        prompt = data.get("prompt", "").strip()
        project = _existing_project_name(data.get("project") or session.get("current_project"))
        chat_id = data.get("chat_id")

        if not prompt:
            return jsonify({"success": False, "error": "Prompt is required."}), 400
        if not project:
            return jsonify({"success": False, "error": "No project selected."}), 400

        if not current_app.config.get("OPENAI_API_KEY"):
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "OpenAI deep research not configured.",
                    }
                ),
                400,
            )

        if is_duplicate_run(project, prompt):
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "This research prompt was already submitted moments ago. Please wait for it to complete.",
                    }
                ),
                409,
            )

        print("[deep-research] start (background)")
        response = start_deep_research(prompt)
        run_id = create_run(project, response.id, chat_id, prompt)
        return jsonify({
            "success": True,
            "response_id": response.id,
            "run_id": run_id,
            "status": response.status,
        })
    except Exception as e:
        print(f"[deep-research] error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@ai_bp.route("/api/deep-research/status/<response_id>", methods=["GET"])
def deep_research_status(response_id):
    try:
        response = retrieve_deep_research(response_id)
        status = getattr(response, "status", None)
        output_text, output_markdown, citations = _extract_deep_research_output(response)

        # Extract error details from the response (OpenAI includes these on failure)
        error_info = None
        for attr in ("error", "last_error"):
            err_obj = getattr(response, attr, None)
            if err_obj:
                if isinstance(err_obj, dict):
                    error_info = err_obj.get("message") or str(err_obj)
                elif isinstance(err_obj, str):
                    error_info = err_obj
                else:
                    error_info = getattr(err_obj, "message", None) or str(err_obj)
                break

        if status == "completed" and not output_text:
            print(f"[deep-research] WARNING: completed but no output for {response_id}")
            print(f"[deep-research] raw output attr: {getattr(response, 'output', None)}")

        if status in ("failed", "incomplete"):
            print(f"[deep-research] FAILED {response_id}: {error_info}")
            print(f"[deep-research] response attrs: {[a for a in dir(response) if not a.startswith('_')]}")

        # Normalize OpenAI status to our internal values
        STATUS_MAP = {
            "queued": "running",
            "in_progress": "running",
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
            "incomplete": "failed",
        }
        normalized = STATUS_MAP.get(status, status or "running")

        # Persist run status if run_id and project are provided
        run_id = request.args.get("run_id")
        project = _existing_project_name(request.args.get("project") or session.get("current_project"))
        if run_id and project:
            update_run(project, run_id, status=normalized)

        print(f"[deep-research] status {response_id}: {status} -> {normalized}")
        return jsonify(
            {
                "success": True,
                "status": normalized,
                "output": output_text,
                "output_markdown": output_markdown,
                "citations": citations,
                "error": error_info,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@ai_bp.route("/api/research-runs", methods=["GET"])
def list_research_runs():
    project = _existing_project_name(request.args.get("project") or session.get("current_project"))
    if not project:
        return jsonify({"success": False, "error": "No project"}), 400
    runs = load_runs(project)
    return jsonify({"success": True, "runs": runs})


@ai_bp.route("/api/research-runs/<run_id>/cancel", methods=["POST"])
def cancel_research_run(run_id):
    data = request.get_json(silent=True) or {}
    project = _existing_project_name(data.get("project") or session.get("current_project"))
    if not project:
        return jsonify({"success": False}), 400
    cancel_run(project, run_id)
    return jsonify({"success": True})


@ai_bp.route("/api/research-runs/<run_id>/complete", methods=["POST"])
def complete_research_run(run_id):
    data = request.get_json(silent=True) or {}
    project = _existing_project_name(data.get("project") or session.get("current_project"))
    if not project:
        return jsonify({"success": False}), 400
    if data.get("error"):
        fail_run(project, run_id, data["error"])
    else:
        complete_run(project, run_id, artifact_id=data.get("artifact_id"))
    return jsonify({"success": True})


@ai_bp.route("/api/insights/populate-competitor", methods=["POST"])
def populate_competitor():
    try:
        data = request.get_json(silent=True) or {}
        competitor_name = data.get("competitor_name", "").strip()
        project = _existing_project_name(data.get("project") or session.get("current_project"))

        if not competitor_name:
            return jsonify({"success": False, "error": "Competitor name is required."}), 400
        if not project:
            return jsonify({"success": False, "error": "No project selected."}), 400

        all_files = _source_files_only(load_project_files(project))
        index_entries = reconcile_file_index(project)
        selection_state = reconcile_selected_file_ids(project, index_entries)
        valid_selected = [f for f in selection_state.get("selected_files", []) if f in all_files]

        files = (
            {k: v for k, v in all_files.items() if k in valid_selected}
            if valid_selected
            else all_files
        )

        if not files:
            return jsonify({"success": False, "error": "No source data files available."}), 400

        files_context = _build_files_context(files, max_file_chars=12000, max_total_chars=70000)

        system_prompt = (
            "You are a senior Strategy Consultant and Strict Data Auditor specialising in Australian Higher Education. "
            "Use Australian English throughout. You may ONLY use the provided Source Data.\n"
            "CRITICAL: If information is NOT explicitly stated, the value MUST be exactly \"MISSING\".\n"
            "DO NOT hallucinate. DO NOT use general knowledge. DO NOT guess.\n"
            "For the 'details' field, produce well-structured Markdown with ## headings, bullet points, and **bold** emphasis. "
            "Write in a McKinsey consulting tone — analytical, authoritative, and evidence-based.\n"
            "Return ONLY valid JSON with no additional text.\n\n"
            f"# SOURCE DATA\n\n{files_context}"
        )

        user_message = (
            f'Extract all available information about the competitor "{competitor_name}" '
            f"from the source data above.\n\n"
            f"Return JSON in this exact format:\n"
            f'{{\n'
            f'  "price": "Price or MISSING",\n'
            f'  "duration": "Duration or MISSING",\n'
            f'  "usp": "USP or MISSING",\n'
            f'  "details": "A rich markdown summary of everything known about this competitor from the source data, or empty string if nothing found."\n'
            f'}}'
        )

        raw = prompt_completion(system_prompt, user_message, max_tokens=3200)

        # Parse JSON from response
        import re as _re
        json_str = ""
        code_match = _re.search(r"```json\s*([\s\S]*?)\s*```", raw)
        if code_match:
            json_str = code_match.group(1).strip()
        if not json_str:
            first = raw.find("{")
            last = raw.rfind("}")
            if first != -1 and last != -1:
                json_str = raw[first : last + 1]

        if not json_str:
            return jsonify({"success": False, "error": "AI did not return valid JSON."}), 500

        parsed = json.loads(json_str)
        return jsonify({
            "success": True,
            "price": parsed.get("price", "MISSING"),
            "duration": parsed.get("duration", "MISSING"),
            "usp": parsed.get("usp", "MISSING"),
            "details": parsed.get("details", ""),
        })

    except Exception as e:
        print(f"[populate-competitor] error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@ai_bp.route("/api/insights/report", methods=["POST"])
def insights_report():
    try:
        data = request.json or {}
        insights = data.get("insights")
        project_name = data.get("project_name", "Research Project")

        if not insights:
            return jsonify({"success": False, "error": "No insights data provided."}), 400

        buf = generate_insights_report(insights, project_name=project_name)

        return send_file(
            buf,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            as_attachment=True,
            download_name=f"Strategic_Insights_{project_name.replace(' ', '_')}.docx",
        )
    except Exception as e:
        print(f"[report] error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

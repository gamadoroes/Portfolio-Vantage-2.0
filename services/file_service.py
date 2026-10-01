from .file_index_service import toggle_selected_file
from .project_service import get_project_file_path, get_project_files_dir, normalize_filename
from .storage import read_text, write_text


def _is_internal_temp_file(filename):
    return isinstance(filename, str) and filename.startswith(".write_") and filename.endswith(".tmp")


def load_project_files(project_name):
    files_dir = get_project_files_dir(project_name)
    if files_dir is None:
        return {}
    files_content = {}
    if files_dir.exists():
        for file_path in files_dir.iterdir():
            if file_path.is_file():
                if _is_internal_temp_file(file_path.name):
                    continue
                try:
                    files_content[file_path.name] = read_text(file_path, "")
                except Exception:
                    pass
    return files_content


def save_project_file(project_name, filename, content):
    file_path = get_project_file_path(project_name, filename)
    if file_path is None:
        raise ValueError("Invalid file name.")
    write_text(file_path, content if isinstance(content, str) else str(content or ""))
    return normalize_filename(filename)


def delete_project_file(project_name, filename):
    file_path = get_project_file_path(project_name, filename)
    if file_path is None:
        return False
    if file_path.exists():
        file_path.unlink()
        return True
    return False


def rename_project_file(project_name, old_name, new_name):
    old_path = get_project_file_path(project_name, old_name)
    new_path = get_project_file_path(project_name, new_name)
    if old_path is None or new_path is None:
        return False
    if old_path.exists():
        # Use replace() instead of rename() — on Windows, rename() raises
        # FileExistsError if the target exists (e.g. a zero-byte placeholder
        # from _unique_md_filename), while replace() works cross-platform.
        old_path.replace(new_path)
        return True
    return False


def toggle_file_selection(project_name, filename):
    return toggle_selected_file(project_name, filename=filename)

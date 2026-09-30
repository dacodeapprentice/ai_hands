"""
tools.py — The "arms" of the software.

These are the actual actions the AI is allowed to perform on your project
folder. Each function here corresponds to one "tool" the AI can call.
If you ever want to add a new ability for the AI (e.g. "search inside files"),
this is the file where you'd add it.
"""

import os
import subprocess
import platform

# Safety: every tool call is restricted to happen inside this folder.
# The AI can never read/write/run anything outside of PROJECT_DIR.
PROJECT_DIR = None


class UnsafeProjectDirError(Exception):
    """Raised when the chosen project folder is a system-critical location."""
    pass


def _is_dangerous_project_dir(path: str) -> bool:
    """
    Refuses to let the AI operate directly on OS-critical folders, or on
    a drive/filesystem root. Confirmation popups protect individual
    actions, but picking a system folder as the project itself would
    put every file in it one approved instruction away from being
    changed or deleted - this blocks that scenario outright, before
    any instruction is even possible.
    """
    normalized = os.path.normcase(os.path.abspath(path))
    drive, tail = os.path.splitdrive(normalized)

    # Refuse any drive/filesystem root (C:\, D:\, /) - too broad to be a
    # sensible "project", and likely to contain OS-critical folders.
    if tail in ("", os.sep, "\\", "/"):
        return True

    system = platform.system()
    if system == "Windows":
        dangerous = [
            os.environ.get("WINDIR", "C:\\Windows"),
            os.environ.get("PROGRAMFILES", "C:\\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"),
            os.environ.get("PROGRAMDATA", "C:\\ProgramData"),
        ]
    else:
        dangerous = [
            "/", "/bin", "/sbin", "/usr", "/etc", "/lib", "/lib64",
            "/System", "/Library", "/private", "/boot", "/dev", "/proc",
            "/sys", "/var",
        ]

    for d in dangerous:
        d_norm = os.path.normcase(os.path.abspath(d))
        if normalized == d_norm or normalized.startswith(d_norm + os.sep):
            return True
    return False

# Set by the UI at startup. When a tool is about to do something
# risky (delete a file, run a destructive shell command), it calls
# this function with a plain-language description and waits for a
# True/False answer before proceeding. If nothing sets this (e.g.
# running tools.py standalone in a test), risky actions are allowed
# by default so tests don't hang waiting for a UI that isn't there.
CONFIRM_CALLBACK = None


def set_confirm_callback(fn):
    global CONFIRM_CALLBACK
    CONFIRM_CALLBACK = fn


def _confirm(description: str) -> bool:
    if CONFIRM_CALLBACK is None:
        return True
    return CONFIRM_CALLBACK(description)


# Substrings that mark a shell command as destructive enough to ask
# before running. Simple and readable on purpose - it doesn't need to
# be exhaustive, just catch the common, high-damage cases.
_DANGEROUS_COMMAND_MARKERS = [
    "rm ", "rm-", "rmdir", "del ", "erase ", "format ",
    "git reset --hard", "git clean -f", "git push --force",
    "drop table", "drop database", "truncate ",
    "> /dev/", "shutdown", "mkfs",
]


def _is_dangerous_command(command: str) -> bool:
    lowered = command.lower()
    return any(marker in lowered for marker in _DANGEROUS_COMMAND_MARKERS)


def set_project_dir(path: str):
    """Called once when the app starts, to lock the AI to one folder."""
    global PROJECT_DIR
    if _is_dangerous_project_dir(path):
        raise UnsafeProjectDirError(
            f"'{path}' looks like a system folder (or a whole drive), not "
            f"a project folder. For safety, this app won't let the AI "
            f"operate there. Please choose (or create) a regular folder "
            f"instead, e.g. one inside your Documents or Desktop."
        )
    PROJECT_DIR = os.path.abspath(path)
    os.makedirs(PROJECT_DIR, exist_ok=True)


def _safe_path(relative_path: str) -> str:
    """
    Turns a relative path (e.g. 'main.py') into a full path inside
    PROJECT_DIR, and blocks any attempt to escape the folder
    (e.g. '../../etc/passwd').
    """
    if PROJECT_DIR is None:
        raise RuntimeError("Project directory not set yet.")

    full_path = os.path.abspath(os.path.join(PROJECT_DIR, relative_path))
    if not full_path.startswith(PROJECT_DIR):
        raise PermissionError(f"Blocked: '{relative_path}' is outside the project folder.")
    return full_path


def read_file(path: str) -> str:
    """Read and return the full contents of a file."""
    full_path = _safe_path(path)
    if not os.path.exists(full_path):
        return f"ERROR: file '{path}' does not exist."
    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def write_file(path: str, content: str) -> str:
    """Create a new file, or completely overwrite an existing one. Requires user confirmation first."""
    full_path = _safe_path(path)
    action = "Overwrite" if os.path.exists(full_path) else "Create"
    preview = content if len(content) <= 300 else content[:300] + "\n... (truncated)"
    if not _confirm(f"{action} the file '{path}' with this content:\n\n{preview}"):
        return f"Cancelled: the user did not approve writing to '{path}'."

    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Wrote {len(content)} characters to '{path}'."


def edit_file(path: str, old_text: str, new_text: str) -> str:
    """
    Targeted edit: find 'old_text' inside the file and replace it with
    'new_text'. This is the preferred way to make changes — it only
    touches the part that needs to change, not the whole file.
    Fails loudly if old_text isn't found, or is found more than once,
    so the AI doesn't guess wrong and corrupt the file.
    Requires user confirmation before the change is applied.
    """
    full_path = _safe_path(path)
    if not os.path.exists(full_path):
        return f"ERROR: file '{path}' does not exist."

    with open(full_path, "r", encoding="utf-8") as f:
        content = f.read()

    count = content.count(old_text)
    if count == 0:
        return f"ERROR: could not find the given text in '{path}'. No changes made."
    if count > 1:
        return (
            f"ERROR: the given text appears {count} times in '{path}'. "
            f"Please provide more surrounding context to make it unique."
        )

    def _preview(text, limit=200):
        return text if len(text) <= limit else text[:limit] + "... (truncated)"

    description = (
        f"Edit '{path}':\n\n"
        f"Replace:\n{_preview(old_text)}\n\n"
        f"With:\n{_preview(new_text)}"
    )
    if not _confirm(description):
        return f"Cancelled: the user did not approve editing '{path}'."

    new_content = content.replace(old_text, new_text, 1)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    return f"Edited '{path}' successfully."


def delete_file(path: str) -> str:
    """Delete a file from the project. Requires user confirmation first."""
    full_path = _safe_path(path)
    if not os.path.exists(full_path):
        return f"ERROR: file '{path}' does not exist."

    if not _confirm(f"Delete the file: {path}"):
        return f"Cancelled: the user did not approve deleting '{path}'."

    os.remove(full_path)
    return f"Deleted '{path}'."


def list_directory(path: str = ".") -> str:
    """List files and folders inside the project (or a subfolder of it)."""
    full_path = _safe_path(path)
    if not os.path.exists(full_path):
        return f"ERROR: '{path}' does not exist."

    entries = []
    for root, dirs, files in os.walk(full_path):
        # skip hidden/internal folders that aren't useful to show
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules")]
        rel_root = os.path.relpath(root, PROJECT_DIR)
        for d in dirs:
            entries.append(os.path.join(rel_root, d) + "/")
        for f in files:
            entries.append(os.path.join(rel_root, f))
    return "\n".join(sorted(entries)) if entries else "(empty folder)"


def run_shell_command(command: str) -> str:
    """
    Run a shell command inside the project folder (e.g. to run a script,
    install a package, or run tests). Returns combined stdout/stderr.
    Has a timeout so a stuck command can't freeze the app forever.
    Every command requires user confirmation before running, since this
    can install software or change things outside of the project files.
    """
    if PROJECT_DIR is None:
        raise RuntimeError("Project directory not set yet.")

    warning = " (this looks like it could delete or overwrite data)" if _is_dangerous_command(command) else ""
    if not _confirm(f"Run this command{warning}:\n\n{command}"):
        return "Cancelled: the user did not approve running this command."

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = result.stdout + result.stderr
        return output.strip() or "(command produced no output)"
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 60 seconds."


# ---- Tool schema: describes these functions to the AI so it knows how
# ---- to call them. This is the format the API expects.
TOOL_SCHEMA = [
    {
        "name": "read_file",
        "description": "Read the full contents of a file in the project folder.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Relative file path"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Create a new file or completely overwrite an existing file with new content. Requires user confirmation before it happens.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path"},
                "content": {"type": "string", "description": "Full contents to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Make a targeted change to a file by replacing an exact chunk of existing text with new text. Preferred over write_file for small changes. Requires user confirmation before it happens.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string", "description": "Exact existing text to find (must be unique in the file)"},
                "new_text": {"type": "string", "description": "Text to replace it with"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
    {
        "name": "delete_file",
        "description": "Delete a file from the project. Requires user confirmation before it happens.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Relative file path to delete"}},
            "required": ["path"],
        },
    },
    {
        "name": "list_directory",
        "description": "List all files and folders in the project (or a subfolder).",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Relative folder path, default '.'"}},
            "required": [],
        },
    },
    {
        "name": "run_shell_command",
        "description": "Run a shell command inside the project folder, e.g. to run a script or install a dependency. Requires user confirmation before it happens.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
]

# Maps tool name -> actual Python function, so the agent loop can call it.
TOOL_FUNCTIONS = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "delete_file": delete_file,
    "list_directory": list_directory,
    "run_shell_command": run_shell_command,
}

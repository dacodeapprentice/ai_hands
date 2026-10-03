"""
checkpoints.py — The "memory" of the project.

Every time the AI finishes a task, we save a checkpoint (a git commit).
This lets you say "go back to before" and instantly restore the project
to exactly how it was, with no need for the AI to remember anything —
the files themselves carry the history.

Uses git under the hood, but you never need to type a git command
yourself — this file drives it for you.
"""

import subprocess
import os


class GitNotFoundError(RuntimeError):
    """Raised when the 'git' command isn't installed or isn't on PATH."""
    pass


def _run_git(project_dir: str, *args) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise GitNotFoundError(
            "Git isn't installed (or isn't on your system PATH), so the "
            "checkpoint/undo feature can't work. Install it from "
            "https://git-scm.com/downloads, then restart this app. "
            "On Windows, make sure to leave the \"Git from the command line\" "
            "option checked during install."
        )
    return (result.stdout + result.stderr).strip()


def init_checkpoints(project_dir: str):
    """Set up git in the project folder if it isn't already."""
    git_dir = os.path.join(project_dir, ".git")
    if not os.path.exists(git_dir):
        _run_git(project_dir, "init")
        _run_git(project_dir, "config", "user.email", "ai@local")
        _run_git(project_dir, "config", "user.name", "AI Hands")
        # Make an empty starting checkpoint so there's always something to revert to
        _run_git(project_dir, "commit", "--allow-empty", "-m", "Initial checkpoint")


def save_checkpoint(project_dir: str, message: str) -> str:
    """Save the current state of the project as a new checkpoint."""
    _run_git(project_dir, "add", "-A")
    output = _run_git(project_dir, "commit", "-m", message)
    return output


def list_checkpoints(project_dir: str, limit: int = 20):
    """Return a list of (hash, message) for recent checkpoints, newest first."""
    output = _run_git(project_dir, "log", f"-{limit}", "--pretty=format:%h|%s")
    if not output:
        return []
    checkpoints = []
    for line in output.split("\n"):
        if "|" in line:
            commit_hash, message = line.split("|", 1)
            checkpoints.append((commit_hash, message))
    return checkpoints


def revert_to_checkpoint(project_dir: str, commit_hash: str) -> str:
    """
    Restore the project to exactly how it looked at a previous checkpoint.
    Anything after that point is discarded from the working files
    (but stays in git history, so nothing is ever truly lost).
    """
    output = _run_git(project_dir, "reset", "--hard", commit_hash)
    return output


def revert_to_previous(project_dir: str) -> str:
    """Shortcut: revert to the checkpoint right before the current one."""
    checkpoints = list_checkpoints(project_dir, limit=2)
    if len(checkpoints) < 2:
        return "No earlier checkpoint to revert to."
    previous_hash = checkpoints[1][0]
    return revert_to_checkpoint(project_dir, previous_hash)

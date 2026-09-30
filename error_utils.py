"""
error_utils.py — Makes errors understandable, not just visible.

Whenever the app catches an error to show it to you, it goes through
here first. Every error you see gets two parts:
  1. A short, plain-language guess at what actually happened
  2. The full technical stack trace underneath, ready to copy and
     paste to Claude (or anyone else) for help — you never need to
     screenshot a error and hope it's readable.

This doesn't replace the specific, already-friendly messages built
elsewhere in the app (like "Git isn't installed..." or "this API key
is invalid") — those already explain themselves. This is for the
errors that don't: unexpected ones, bugs, anything the app wasn't
specifically built to recognize.
"""

import json
import traceback


def explain_exception(exc: Exception) -> str:
    """
    Best-effort plain-language guess at what kind of problem this is,
    based on the exception's type. Falls back to an honest "not sure"
    rather than guessing wrong.
    """
    if isinstance(exc, PermissionError):
        return (
            "The app doesn't have permission to do this. A file or folder "
            "involved might be read-only, in a protected location, or open "
            "in another program right now."
        )
    if isinstance(exc, FileNotFoundError):
        return (
            "A file or program the app needed wasn't found on your "
            "computer. This could be a missing dependency, or a file that "
            "was moved, renamed, or deleted."
        )
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return (
            "A network connection failed or took too long to respond. "
            "Check your internet connection and try again."
        )
    if isinstance(exc, json.JSONDecodeError):
        return (
            "The AI sent back a response the app couldn't understand. "
            "This is usually a one-off glitch — try sending your message "
            "again."
        )
    if isinstance(exc, (KeyError, TypeError, AttributeError, IndexError)):
        return (
            "This looks like a bug in the app itself, not something you "
            "did wrong. Share the technical details below and it can be "
            "fixed."
        )
    if isinstance(exc, RuntimeError):
        return (
            "Something the app needed wasn't ready or set up correctly "
            "yet. The details below say exactly what."
        )
    return (
        "An unexpected error happened. It's not clear from the error type "
        "alone what caused it — the technical details below will help "
        "figure it out."
    )


def format_error_for_user(exc: Exception, friendly_message: str = None) -> str:
    """
    Builds the full error text shown in the chat window: a short
    explanation up top, then a clear divider, then the full stack
    trace underneath. Text in the chat window is already
    copy-pasteable, so this is all someone needs to paste here (or
    anywhere) to get help.

    friendly_message: pass this when the code that caught the error
    already has a specific, well-understood explanation (e.g. "Git
    isn't installed..."). Otherwise, a generic explanation is guessed
    from the exception type.
    """
    explanation = friendly_message or explain_exception(exc)
    trace = traceback.format_exc()
    return (
        f"{explanation}\n\n"
        f"── Technical details (copy this if you ask for help) ──\n"
        f"{trace}"
    )

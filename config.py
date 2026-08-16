"""
config.py — Handles API keys without needing the terminal.

Keys are saved to a small file in your home folder
(~/.ai_hands/config.json) so you only ever have to type them once.
This also provides the setup window shown on first launch, and again
any time every configured provider has run out of usage for the day.
"""

import os
import json
import webbrowser
import tkinter as tk
from tkinter import messagebox

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".ai_hands")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

# Every provider the setup window offers a field for, in the order
# they're tried. (Cerebras is left out here — its free tier turned out
# to require billing, so it's not offered as a "free" option.)
PROVIDER_FIELDS = [
    ("GROQ_API_KEY", "Groq", "https://console.groq.com"),
    ("GEMINI_API_KEY", "Google Gemini", "https://aistudio.google.com/apikey"),
    ("MISTRAL_API_KEY", "Mistral", "https://console.mistral.ai"),
    ("OPENROUTER_API_KEY", "OpenRouter", "https://openrouter.ai/keys"),
]


def load_keys() -> dict:
    """
    Returns a dict of env-var-name -> key value. Checks the saved
    config file first, then falls back to actual environment
    variables for anyone who prefers setting them that way.
    """
    keys = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                keys = json.load(f)
        except (json.JSONDecodeError, OSError):
            keys = {}

    for env_var, _, _ in PROVIDER_FIELDS:
        if not keys.get(env_var) and os.environ.get(env_var):
            keys[env_var] = os.environ[env_var]

    return keys


def save_keys(keys: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    # never save empty strings - treat them as "not set"
    cleaned = {k: v for k, v in keys.items() if v}
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2)


def apply_keys_to_environment(keys: dict):
    """Makes the saved keys visible to the provider classes, which read os.environ."""
    for env_var, value in keys.items():
        if value:
            os.environ[env_var] = value


class ApiKeySetupDialog(tk.Toplevel):
    """
    A simple modal window with one text field per provider. Leave any
    field blank to skip that provider - only set the ones you've
    signed up for. Submitting saves them for next time.
    """

    def __init__(self, parent, message=None):
        super().__init__(parent)
        self.title("Set up AI providers")
        self.geometry("480x360")
        self.resizable(False, False)
        self.result_saved = False

        # Keep this dialog modal and on top so it can't be missed/lost
        # behind the main window - relevant to the "can't type" issue too.
        self.transient(parent)
        self.grab_set()

        intro = message or (
            "Enter a free API key for at least one provider below.\n"
            "Click \"get key\" to open that provider's signup page in your browser — "
            "sign up there, copy the key it gives you, then paste it in the box.\n"
            "Leave any provider you don't have blank."
        )
        tk.Label(self, text=intro, wraplength=450, justify="left").pack(padx=12, pady=(12, 8), anchor="w")

        existing = load_keys()
        self.entries = {}

        for env_var, label, url in PROVIDER_FIELDS:
            row = tk.Frame(self)
            row.pack(fill="x", padx=12, pady=4)
            tk.Label(row, text=f"{label}:", width=14, anchor="w").pack(side="left")
            entry = tk.Entry(row, show="*", width=32)
            entry.insert(0, existing.get(env_var, ""))
            entry.pack(side="left", fill="x", expand=True)
            self.entries[env_var] = entry
            get_key_label = tk.Label(row, text="get key", fg="blue", cursor="hand2")
            get_key_label.pack(side="left", padx=(4, 0))
            get_key_label.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))
            # Note: this opens the URL in your default browser - it doesn't
            # create a key for you automatically, since providers require
            # signing up first. Sign up on the page that opens, copy the
            # key it gives you, then paste it into the field to the left.
            tk.Label(self, text=url, font=("TkDefaultFont", 8), fg="gray").pack(anchor="w", padx=12)

        button_row = tk.Frame(self)
        button_row.pack(pady=12)
        tk.Button(button_row, text="Save", width=12, command=self._on_save).pack(side="left", padx=4)
        tk.Button(button_row, text="Cancel", width=12, command=self.destroy).pack(side="left", padx=4)

        self.entries[PROVIDER_FIELDS[0][0]].focus_set()

    def _on_save(self):
        keys = {env_var: entry.get().strip() for env_var, entry in self.entries.items()}
        if not any(keys.values()):
            messagebox.showwarning(
                "No keys entered",
                "Enter at least one API key, or Cancel and set them up later."
            )
            return
        save_keys(keys)
        apply_keys_to_environment(keys)
        self.result_saved = True
        self.destroy()


def ensure_keys_configured(parent) -> bool:
    """
    Call this at startup. If no keys are saved/set yet, shows the setup
    dialog. Returns True once at least one key is available (whether it
    was already set, or the user just entered one), False if the user
    cancelled without entering anything.
    """
    keys = load_keys()
    apply_keys_to_environment(keys)
    if any(keys.values()):
        return True

    dialog = ApiKeySetupDialog(parent)
    parent.wait_window(dialog)
    return dialog.result_saved

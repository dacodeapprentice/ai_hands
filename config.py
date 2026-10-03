"""
config.py — Handles API keys without needing the terminal.

Keys are saved to a small file in your home folder
(~/.ai_hands/config.json) so you only ever have to type them once.
This also provides the setup window shown on first launch, and again
any time every configured provider has run out of usage for the day.

It also reads a ".env" file if one exists in the folder you run the
app from (e.g. C:\\Users\\sofia\\Downloads\\ai_hands) - so instead of
typing `$env:MISTRAL_API_KEY="..."` in PowerShell every time you open
a new terminal, you can put that same value in a plain ".env" file
sitting next to main.py once, and it's picked up automatically on
every startup from here on.
"""

import os
import json
import threading
import webbrowser
import tkinter as tk
from tkinter import messagebox

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".ai_hands")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

# Every provider the setup window offers a field for, in the order
# they're tried.
PROVIDER_FIELDS = [
    ("GROQ_API_KEY", "Groq", "https://console.groq.com"),
]


def _dotenv_path() -> str:
    """
    The ".env" file this app looks for lives in the current working
    directory - i.e. whatever folder you were in (via `cd`) when you
    ran `python main.py`. Computed fresh each time rather than once at
    import time, since it should reflect wherever the app is actually
    being run from.
    """
    return os.path.join(os.getcwd(), ".env")


def _parse_dotenv_file(path: str) -> dict:
    """
    Reads simple KEY=VALUE lines from a .env-style file. Supports
    blank lines, '#' comments, and optional quotes around the value
    (both are fine: MISTRAL_API_KEY=abc123 or MISTRAL_API_KEY="abc123").
    Missing file just means no values to add - not an error.
    """
    values = {}
    if not os.path.exists(path):
        return values
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                if key:
                    values[key] = value
    except OSError:
        pass
    return values


def load_dotenv_from_cwd() -> dict:
    """Returns whatever key/value pairs are in the .env file, if any."""
    return _parse_dotenv_file(_dotenv_path())


def load_keys() -> dict:
    """
    Returns a dict of env-var-name -> key value. Checks, in order:
    1. The saved config file (from a previous "Save" in the API Keys window)
    2. A ".env" file in the current directory, if present
    3. An actual environment variable already set in this session
    (e.g. via `$env:` in PowerShell)
    """
    keys = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                keys = json.load(f)
        except (json.JSONDecodeError, OSError):
            keys = {}

    dotenv_values = load_dotenv_from_cwd()
    for env_var, _, _ in PROVIDER_FIELDS:
        if not keys.get(env_var) and dotenv_values.get(env_var):
            keys[env_var] = dotenv_values[env_var]

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
    _write_dotenv_to_cwd(cleaned)


def _write_dotenv_to_cwd(cleaned: dict):
    """
    Also writes keys saved through the "API Keys" window into a .env
    file in the current directory, so they're available as a plain,
    editable file too (not just the hidden config.json) - whichever
    way you set a key (typing $env:, editing .env by hand, or using
    this window), everything stays in sync. Preserves any lines this
    app doesn't recognize rather than wiping the whole file.
    """
    path = _dotenv_path()
    known_keys = {env_var for env_var, _, _ in PROVIDER_FIELDS}
    kept_lines = []

    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.rstrip("\n")
                    check = stripped.strip()
                    if check and not check.startswith("#") and "=" in check:
                        existing_key = check.split("=", 1)[0].strip()
                        if existing_key in known_keys:
                            continue  # will be rewritten below with the current value
                    kept_lines.append(stripped)
        except OSError:
            return  # if we can't read it, don't risk clobbering it

    try:
        with open(path, "w", encoding="utf-8") as f:
            for line in kept_lines:
                f.write(line + "\n")
            for env_var, value in cleaned.items():
                if env_var in known_keys:
                    f.write(f'{env_var}="{value}"\n')
    except OSError:
        pass  # not critical - config.json is still the source of truth


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
        button_row.pack(pady=(4, 8))
        tk.Button(button_row, text="Test Keys", width=12, command=self._on_test).pack(side="left", padx=4)
        tk.Button(button_row, text="Save", width=12, command=self._on_save).pack(side="left", padx=4)
        tk.Button(button_row, text="Cancel", width=12, command=self.destroy).pack(side="left", padx=4)

        self.results_label = tk.Label(self, text="", wraplength=450, justify="left", fg="#333333")
        self.results_label.pack(padx=12, pady=(0, 8), anchor="w")

        self.entries[PROVIDER_FIELDS[0][0]].focus_set()

    def _on_test(self):
        # Import here (not at module top) to avoid config.py depending
        # on providers.py before it's needed - this dialog is the only
        # place that tests connectivity directly.
        from providers import GroqProvider, RateLimitError, ProviderError
        factories = {
            "GROQ_API_KEY": GroqProvider,
        }

        keys_to_test = {
            env_var: entry.get().strip()
            for env_var, entry in self.entries.items()
            if entry.get().strip()
        }
        if not keys_to_test:
            self.results_label.config(text="Enter at least one key first, then click Test Keys.", fg="#b00020")
            return

        self.results_label.config(text="Testing... this can take a few seconds per key.", fg="#555555")

        def run_tests():
            lines = []
            for env_var, key in keys_to_test.items():
                label = next(l for e, l, _ in PROVIDER_FIELDS if e == env_var)
                provider = factories[env_var](api_key=key)
                try:
                    provider.chat([{"role": "user", "content": "Say OK"}], [])
                    lines.append(f"✓ {label}: working")
                except RateLimitError:
                    lines.append(f"✓ {label}: key is valid, but rate-limited right now")
                except ProviderError as e:
                    lines.append(f"✗ {label}: {e}")
                except Exception as e:
                    lines.append(f"✗ {label}: unexpected error — {e}")
            self.after(0, lambda: self.results_label.config(
                text="\n".join(lines),
                fg="#1b4332" if all("✓" in l for l in lines) else "#b00020",
            ))

        threading.Thread(target=run_tests, daemon=True).start()

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

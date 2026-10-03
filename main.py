"""
main.py — The window you actually see and use.

Run this file to open the app. It's a simple window:
- A chat-style output area — your messages on the right, the AI's on
  the left, in different colors so they're easy to tell apart
- A text box at the bottom where you type instructions
- A Send button
- An "Undo Last Change" button, for the revert-to-checkpoint flow

No coding needed to use it — just type and click. The first time you
run it, a small setup window asks for at least one free API key.
"""

import threading
import queue
import tkinter as tk
from tkinter import scrolledtext, filedialog, messagebox

from providers import GroqProvider, ModelRouter, ProviderError
import config
import error_utils
import tools
from agent import Agent
import checkpoints

# Simple color scheme, chosen for contrast so AI/user/status/error
# messages are easy to tell apart at a glance.
COLORS = {
    "ai_bg": "#e8f4ea", "ai_fg": "#1b4332",
    "user_bg": "#e6eefc", "user_fg": "#1d3557",
    "status_fg": "#7a7a7a",
    "error_fg": "#b00020",
    "system_fg": "#555555",
}


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("AI Hands")
        self.root.geometry("800x600")

        self.agent = None
        self.project_dir = None
        self.update_queue = queue.Queue()
        self.confirm_result = {}
        self._retry_instruction = None

        self._build_ui()
        self._poll_queue()
        self._log("Welcome to AI Hands. Setting things up...", "system")

        # Setup happens after the window is actually showing, so any
        # dialogs (key setup, folder picker) sit on top of a real
        # window instead of racing the initial draw.
        self.root.after(50, self._startup_sequence)

    # ---------- UI layout ----------

    def _build_ui(self):
        top_frame = tk.Frame(self.root)
        top_frame.pack(fill="x", padx=8, pady=4)

        self.folder_label = tk.Label(top_frame, text="No folder selected", anchor="w")
        self.folder_label.pack(side="left", fill="x", expand=True)

        change_folder_btn = tk.Button(top_frame, text="Change Folder", command=self._prompt_for_project_folder)
        change_folder_btn.pack(side="right", padx=(4, 0))

        change_keys_btn = tk.Button(top_frame, text="API Keys", command=self._open_key_setup)
        change_keys_btn.pack(side="right")

        self.output_box = scrolledtext.ScrolledText(self.root, wrap="word")
        self.output_box.pack(fill="both", expand=True, padx=8, pady=4)
        # Left in "normal" state on purpose so text can be selected and
        # copied (Ctrl+C) normally - a "disabled" Text widget blocks
        # selection too, not just editing. Typed edits are blocked
        # separately below, while still allowing copy/select/navigate.
        self.output_box.bind("<Key>", self._block_output_editing)

        # Message styling: AI on the left, user on the right, each with
        # its own color, plus separate styles for status/system/error lines.
        self.output_box.tag_configure("ai", justify="left", foreground=COLORS["ai_fg"],
                                       background=COLORS["ai_bg"], lmargin1=4, lmargin2=4,
                                       rmargin=80, spacing1=4, spacing3=8)
        self.output_box.tag_configure("user", justify="right", foreground=COLORS["user_fg"],
                                       background=COLORS["user_bg"], rmargin=4,
                                       lmargin1=80, lmargin2=80, spacing1=4, spacing3=8)
        self.output_box.tag_configure("status", justify="left", foreground=COLORS["status_fg"],
                                       font=("TkDefaultFont", 9, "italic"))
        self.output_box.tag_configure("system", justify="center", foreground=COLORS["system_fg"],
                                       font=("TkDefaultFont", 9))
        self.output_box.tag_configure("error", justify="left", foreground=COLORS["error_fg"],
                                       font=("TkDefaultFont", 10, "bold"))

        bottom_frame = tk.Frame(self.root)
        bottom_frame.pack(fill="x", padx=8, pady=8)

        self.input_box = tk.Text(bottom_frame, height=3)
        self.input_box.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.input_box.bind("<Return>", self._on_enter_pressed)

        button_frame = tk.Frame(bottom_frame)
        button_frame.pack(side="right")

        self.send_btn = tk.Button(button_frame, text="Send", width=16, command=self._on_send)
        self.send_btn.pack(pady=(0, 4), fill="x")

        self.undo_btn = tk.Button(button_frame, text="Undo Last Change", width=16, command=self._on_undo)
        self.undo_btn.pack(fill="x")

    # ---------- Startup / setup ----------

    def _startup_sequence(self):
        if not config.ensure_keys_configured(self.root):
            self._log("No API key configured yet. Click \"API Keys\" above whenever you're ready.\n", "system")
        self._prompt_for_project_folder()

    def _open_key_setup(self, reason=None):
        dialog = config.ApiKeySetupDialog(
            self.root,
            message=reason or "Add or update your free API keys below."
        )
        self.root.wait_window(dialog)
        if not dialog.result_saved:
            return
        if self.project_dir:
            # Keys changed - rebuild the provider chain so it takes effect immediately.
            self._setup_agent(self.project_dir)

        if self._retry_instruction:
            instruction = self._retry_instruction
            self._retry_instruction = None
            self._log(f"Retrying with the new provider: {instruction}", "status")
            self.send_btn.config(state="disabled")
            thread = threading.Thread(target=self._run_task_thread, args=(instruction,), daemon=True)
            thread.start()

    def _prompt_for_project_folder(self):
        folder = filedialog.askdirectory(title="Choose (or create) a project folder")
        if not folder:
            if self.project_dir is None:
                messagebox.showwarning("Folder required", "You need to choose a project folder to continue.")
                self.root.after(100, self._prompt_for_project_folder)
            return

        self.project_dir = folder
        self.folder_label.config(text=f"Project folder: {folder}")
        self._setup_agent(folder)

        # Fixes the "can't type after picking a folder" issue: on some
        # systems the window loses input focus after a native OS dialog
        # closes. Force it back to the main window and the text box.
        self.root.after(50, self._restore_focus)

    def _restore_focus(self):
        self.root.lift()
        self.root.focus_force()
        self.input_box.focus_set()

    def _setup_agent(self, folder):
        try:
            candidate_providers = [GroqProvider()]

            router = ModelRouter(candidate_providers, on_switch=self._on_provider_switch)

            tools.set_confirm_callback(self._confirm_dangerous_action)
            self.agent = Agent(
                router, project_dir=folder,
                on_step=self._queue_status, on_error=self._queue_tool_error,
            )
            self._log(f"Ready. Working in: {folder}", "system")
            self._log(f"Active AI providers (in fallback order):\n{router.status()}", "system")
        except tools.UnsafeProjectDirError as e:
            self.agent = None
            self.project_dir = None
            self.folder_label.config(text="No folder selected")
            self._log(error_utils.format_error_for_user(e, friendly_message=str(e)), "error")
            self.root.after(50, self._prompt_for_project_folder)
        except checkpoints.GitNotFoundError as e:
            self.agent = None
            self._log(error_utils.format_error_for_user(e, friendly_message=str(e)), "error")
        except ProviderError as e:
            self.agent = None
            self._log(
                error_utils.format_error_for_user(
                    e,
                    friendly_message=f"{e}\nClick \"API Keys\" above to add one, then try sending your message again.",
                ),
                "error",
            )
        except Exception as e:
            # Catch-all: something unexpected went wrong. Always show
            # it instead of failing silently -
            # a blank window with no explanation is worse than an ugly
            # error message.
            self.agent = None
            self._log(error_utils.format_error_for_user(e), "error")

    def _on_provider_switch(self, old_name, new_name):
        self._queue_status(f"{old_name} is out of free usage for now — switching to {new_name}.")

    # ---------- Sending instructions ----------

    def _on_enter_pressed(self, event):
        if event.state & 0x0001:  # shift held = newline instead of send
            return
        self._on_send()
        return "break"

    def _on_send(self):
        instruction = self.input_box.get("1.0", "end").strip()
        if not instruction:
            return
        if self.agent is None:
            self._log(
                "No AI provider is set up yet. Click \"API Keys\" above, "
                "add at least one key, and click Save — then try again.",
                "error",
            )
            return
        self.input_box.delete("1.0", "end")
        self._log(instruction, "user")
        self.send_btn.config(state="disabled")

        thread = threading.Thread(target=self._run_task_thread, args=(instruction,), daemon=True)
        thread.start()

    def _run_task_thread(self, instruction):
        try:
            result = self.agent.run_task(instruction)
            self.update_queue.put(("result", result))
        except ProviderError as e:
            if "hit their limits" in str(e):
                # Routine, expected situation (not a bug) - no stack trace needed.
                self.update_queue.put(("providers_exhausted", (str(e), instruction)))
            else:
                formatted = error_utils.format_error_for_user(e, friendly_message=str(e))
                self.update_queue.put(("error", (formatted, instruction)))
        except Exception as e:
            formatted = error_utils.format_error_for_user(e)
            self.update_queue.put(("error", (formatted, instruction)))

    def _on_undo(self):
        if self.agent is None:
            self._log(
                "No AI provider is set up yet, so there's nothing to undo.",
                "error",
            )
            return
        if not self.agent.checkpoints_enabled:
            self._log(
                "Undo isn't available because Git isn't installed. "
                "Install it from https://git-scm.com/downloads and restart the app.",
                "error",
            )
            return
        output = checkpoints.revert_to_previous(self.project_dir)
        self._log(f"Reverted to previous checkpoint.\n{output}", "system")

    # ---------- Dangerous-action confirmation ----------
    # Runs on the agent's background thread; blocks it until the main
    # thread shows a real dialog and the user answers.

    def _confirm_dangerous_action(self, description: str) -> bool:
        event = threading.Event()
        holder = {}

        def show_dialog():
            holder["ok"] = messagebox.askyesno(
                "Confirm before continuing",
                f"The AI wants to do the following:\n\n{description}\n\nAllow it?",
            )
            event.set()

        self.root.after(0, show_dialog)
        event.wait()
        return holder.get("ok", False)

    # ---------- Thread-safe UI updates ----------

    def _queue_status(self, message):
        self.update_queue.put(("status", message))

    def _queue_tool_error(self, message):
        self.update_queue.put(("tool_error", message))

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.update_queue.get_nowait()
                if kind == "status":
                    self._log(payload, "status")
                elif kind == "tool_error":
                    # A tool hit an unexpected exception mid-task. The
                    # task itself keeps going (the AI got a short summary
                    # and may retry or adjust) - this just makes sure the
                    # full explanation + trace is visible, not buried.
                    self._log(payload, "error")
                elif kind == "result":
                    self._log(payload, "ai")
                    self.send_btn.config(state="normal")
                elif kind == "error":
                    error_text, failed_instruction = payload
                    self._log(
                        f"{error_text}\nYour message has been put back in the box below — just click Send to try again.",
                        "error",
                    )
                    self.input_box.delete("1.0", "end")
                    self.input_box.insert("1.0", failed_instruction)
                    self.send_btn.config(state="normal")
                elif kind == "providers_exhausted":
                    error_text, failed_instruction = payload
                    self._retry_instruction = failed_instruction
                    self._log("All configured AI providers are out of free usage for today.", "error")
                    self.send_btn.config(state="normal")
                    self._open_key_setup(
                        reason=(
                            "All your AI providers are out of free usage for today.\n"
                            "Paste a key for a different provider below to keep going — "
                            "your last instruction will pick up right where it left off."
                        )
                    )
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _log(self, text, tag):
        self.output_box.insert("end", text + "\n\n", tag)
        self.output_box.see("end")

    def _block_output_editing(self, event):
        """
        Lets the output box stay selectable/copyable (state stays
        "normal") while still preventing the user from actually typing
        into it. Allows copy (Ctrl+C), select-all (Ctrl+A), and normal
        navigation/scrolling keys through; blocks everything else that
        would modify the text.
        """
        ctrl_held = bool(event.state & 0x0004)
        if ctrl_held and event.keysym.lower() in ("c", "a", "insert"):
            return None  # allow copy / select-all
        if event.keysym in (
            "Up", "Down", "Left", "Right", "Prior", "Next", "Home", "End", "Tab"
        ):
            return None  # allow navigation/scrolling
        return "break"  # block any actual typing/editing


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()

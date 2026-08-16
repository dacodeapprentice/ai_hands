"""
agent.py — The agent loop. This is the heart of the software.

Given one instruction from you, this keeps calling the AI, letting it
use tools (read/write/edit files, run commands), feeding back the
results, and repeating — until the AI decides it's done and just
replies with plain text instead of another tool call.

When it's done, a checkpoint is automatically saved.
"""

import json
import tools
import checkpoints

# How many recent messages (not counting the system prompt) to keep
# between tasks. Older ones are dropped to keep token usage down.
# This only trims BETWEEN completed tasks, never mid-task — trimming
# while the AI is partway through a tool-call sequence would risk
# cutting a tool call away from its result, which breaks the API.
HISTORY_LIMIT = 20

SYSTEM_PROMPT = """You are an AI with direct control over a project folder \
through the tools available to you. You are not giving advice — you are \
doing the work yourself, directly, using the tools.

Rules:
- Prefer edit_file for small/targeted changes over write_file, which \
overwrites an entire file.
- Work autonomously: don't ask the user questions mid-task, make \
reasonable decisions yourself and keep going.
- When the task is fully done, reply with plain text (no tool call) \
summarizing what you did. That signals you're finished.
- Keep replies concise — the user will see everything you did listed \
out already through your tool calls.
"""


class Agent:
    def __init__(self, router, project_dir, on_step=None):
        """
        router: a ModelRouter instance (the "brain" connection)
        project_dir: folder the AI is allowed to work in
        on_step: optional callback(str) called with progress updates,
                 so the UI can show live status ("Reading main.py...")
        """
        self.router = router
        self.project_dir = project_dir
        self.on_step = on_step or (lambda msg: None)
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        tools.set_project_dir(project_dir)

        self.checkpoints_enabled = True
        try:
            checkpoints.init_checkpoints(project_dir)
        except checkpoints.GitNotFoundError as e:
            self.checkpoints_enabled = False
            self.on_step(
                f"Note: {e} The AI will still work, but 'Undo Last Change' "
                f"won't be available until Git is installed."
            )

    def run_task(self, instruction: str, max_steps: int = 25) -> str:
        """
        Runs one full instruction through the agent loop until the AI
        says it's done (or max_steps safety limit is hit).
        Returns the AI's final summary text.
        """
        self._trim_history()
        self.messages.append({"role": "user", "content": instruction})

        for step in range(max_steps):
            response = self.router.chat(self.messages, tools.TOOL_SCHEMA)
            choice = response["choices"][0]["message"]

            tool_calls = choice.get("tool_calls")

            if not tool_calls:
                # No tool call = the AI is done with this task.
                final_text = choice.get("content", "").strip()
                self.messages.append({"role": "assistant", "content": final_text})
                if self.checkpoints_enabled:
                    commit_msg = self._summarize_for_commit(instruction)
                    checkpoints.save_checkpoint(self.project_dir, commit_msg)
                    self.on_step(f"Checkpoint saved: {commit_msg}")
                return final_text

            # The AI wants to use one or more tools — run them and feed results back.
            self.messages.append(choice)
            for call in tool_calls:
                func_name = call["function"]["name"]
                try:
                    args = json.loads(call["function"]["arguments"])
                except json.JSONDecodeError:
                    args = {}

                self.on_step(f"Running {func_name}({args})...")

                func = tools.TOOL_FUNCTIONS.get(func_name)
                if func is None:
                    result = f"ERROR: unknown tool '{func_name}'"
                else:
                    try:
                        result = func(**args)
                    except Exception as e:
                        result = f"ERROR: {e}"

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": str(result),
                })

        return "Stopped: reached the maximum number of steps for this task without finishing."

    def _trim_history(self, keep_last: int = HISTORY_LIMIT):
        """
        Keeps the system prompt plus the most recent `keep_last` messages,
        dropping everything older to cap token usage. Only called between
        tasks (see run_task), when every tool call already has its
        matching result — so it's always safe to cut a boundary there.

        The one thing to guard against: the cut point could still land
        in the middle of an older tool_call/result pair from *within*
        the kept window. If the very first kept message is a 'tool'
        result whose matching tool_call got trimmed away, some providers
        reject that as an invalid conversation. So if trimming would
        start on a stray 'tool' message, we drop it (and any consecutive
        ones) rather than send a broken pair.
        """
        system = self.messages[0]
        recent = self.messages[1:][-keep_last:]

        while recent and recent[0]["role"] == "tool":
            recent.pop(0)

        self.messages = [system] + recent

    def _summarize_for_commit(self, instruction: str) -> str:
        """Short commit message derived from the instruction (kept simple for v1)."""
        text = instruction.strip().replace("\n", " ")
        return text[:72] + ("..." if len(text) > 72 else "")

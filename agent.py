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
import error_utils

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
- Every file change and every shell command will pause and ask the user \
for approval before it happens — this is handled automatically by the \
tools themselves, so just call the tool as normal; don't ask the user \
about it yourself in your reply, and don't avoid calling a tool out of \
caution. If the user declines, the tool result will tell you so and you \
should adjust your approach.
- Make your own reasonable decisions about how to accomplish the task; \
you don't need to ask the user clarifying questions about their intent, \
only actual changes get confirmed.
- When the task is fully done, reply with plain text (no tool call) \
summarizing what you did. That signals you're finished.
- Keep replies concise — the user will see everything you did listed \
out already through your tool calls.
"""


class Agent:
    def __init__(self, router, project_dir, on_step=None, on_error=None):
        """
        router: a ModelRouter instance (the "brain" connection)
        project_dir: folder the AI is allowed to work in
        on_step: optional callback(str) called with progress updates,
                 so the UI can show live status ("Reading main.py...")
        on_error: optional callback(str) called when a tool hits a
                  genuinely unexpected exception (not a normal,
                  already-clear "file not found" type message) - lets
                  the UI show these distinctly (e.g. in red) with a
                  full stack trace, rather than mixed in with routine
                  status updates. Falls back to on_step if not given.
        """
        self.router = router
        self.project_dir = project_dir
        self.on_step = on_step or (lambda msg: None)
        self.on_error = on_error or self.on_step
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
            # Store a normalized copy, not the raw response, so any extra
            # provider-specific fields (e.g. a "reasoning" field some
            # reasoning-capable Groq models include) don't get carried
            # forward and rejected by a *different* provider if a
            # rate-limit fallback switches mid-conversation.
            self.messages.append(self._normalize_assistant_message(choice))
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
                        # The AI gets a short summary so it can adjust its
                        # approach; the person watching gets the full,
                        # readable explanation + stack trace, since this
                        # is exactly the kind of error they can't diagnose
                        # on their own otherwise.
                        result = f"ERROR: {e}"
                        self.on_error(error_utils.format_error_for_user(e))

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": str(result),
                })

        return "Stopped: reached the maximum number of steps for this task without finishing."

    def _normalize_assistant_message(self, choice: dict) -> dict:
        """
        Rebuilds an assistant message using only the fields every
        provider is expected to send/accept: role, content, and (when
        present) tool_calls with just id/type/function. Different
        providers attach different extras to their raw response (e.g.
        a "reasoning" field on some Groq reasoning models) - those
        extras are fine to see once, but must not get stored into the
        shared conversation history, since a rate-limit fallback can
        hand that same history to a completely different provider on
        the very next call, and some providers (Mistral included)
        strictly reject unrecognized fields.
        """
        normalized = {"role": "assistant", "content": choice.get("content")}
        raw_tool_calls = choice.get("tool_calls")
        if raw_tool_calls:
            normalized["tool_calls"] = [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["function"]["name"],
                        "arguments": call["function"]["arguments"],
                    },
                }
                for call in raw_tool_calls
            ]
        return normalized

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

# AI Hands — Setup Guide

This is a small desktop app. You give it an instruction, and it directly
edits files in a project folder on your computer — you never touch code
yourself.

Cloud AI providers are the main option (more capable than local
models), with your own computer (via Ollama) kept as a last-resort
fallback that never runs out. **You no longer need the terminal to set
API keys** — the app has a built-in setup window for that now.

## One-time setup

### 1. Install Python (if you don't already have it)
Download from https://python.org/downloads — during install, make sure
you check the box that says **"Add Python to PATH"**.

### 2. Install the dependencies this app needs
Open a terminal (Windows: search "cmd" or "PowerShell"; Mac: search
"Terminal") and type:

```
pip install requests psutil
```

### 3. Get at least one free API key
You only need one to get started — more just means more free daily
usage before the app needs to fall back to another. Recommended:

- **Groq** — https://console.groq.com (no credit card)
- **Mistral** — https://console.mistral.ai (no credit card)
- **OpenRouter** — https://openrouter.ai/keys (no credit card)

(Cerebras was tried earlier but its free tier turned out to require
billing, so it's not offered here.)

### 4. (Optional) Install Ollama as a backstop
For when every cloud provider is tapped out for the day. Install from
https://ollama.com, then run the app once — it checks your computer's
specs and tells you which local model tag fits it. Pull that model
with:

```
ollama pull <model tag the app recommends>
```

If you skip this, the app still works fine on cloud providers alone —
there's just no fallback left once every configured cloud key is out
of free usage for the day.

## Running the app

In a terminal, navigate to this folder and run:

```
python main.py
```

**First time only:** a small setup window opens asking for your API
key(s) — paste in whatever you got in step 3, click Save. It's
remembered for next time, so you won't see this window again unless
you click "API Keys" in the top bar to change something.

After that, pick (or create) a project folder — this is the folder the
AI will be allowed to work in (its "block of clay").

## How to use it

- Type an instruction in the box at the bottom, press Enter (or click Send).
- Your messages appear on the right, the AI's on the left, in
  different colors, so a growing conversation stays easy to follow.
- **Every file change and every command pauses for your approval
  first** — creating a file, editing one, deleting one, or running any
  command (installing something, running a script, etc.). A popup
  shows exactly what's about to happen; nothing happens until you
  click "Yes."
- All text in the conversation can be selected and copied normally
  (Ctrl+C) — it just can't be typed into directly.
- If a message fails to send (e.g. a connection hiccup), it's put
  back in the input box automatically — just click Send again, no
  retyping needed.
- When a task finishes, it reports back and automatically saves a
  checkpoint (skipped if Git isn't installed — see below).
- Click **"Undo Last Change"** to revert to the checkpoint before the
  most recent one, if you want to try a different direction.
- If every configured provider runs out of free usage for the day, the
  app tells you and reopens the key setup window so you can add
  another one on the spot — your last instruction picks up right where
  it left off once you save a new key.

## Notes for this draft

- The app refuses to work directly on system folders (like `C:\Windows`,
  `C:\Program Files`, or a whole drive root) — pick a regular project
  folder, e.g. inside Documents or Desktop.
- In the "API Keys" window, click **"Test Keys"** to check each key
  right away — it tells you plainly if a key is invalid, needs billing,
  or if the problem is your internet connection, instead of you having
  to guess from a vague error later.

- Provider order: Groq, Mistral, OpenRouter (whichever you've set keys
  for), then your own computer via Ollama as the last resort. Switches
  automatically mid-task if one hits its limit — you'll see a status
  line when that happens. (Google Gemini was tried but removed — its
  responses weren't reliable enough for this workflow.)
- Conversation history is capped to the most recent 20 messages
  between tasks, to keep token usage down on long sessions. Safe
  because the project's real "memory" is the files and checkpoints,
  not the chat log.
- If Git isn't installed, the AI still works fully — only the
  checkpoint/undo feature is unavailable until you install it.
- API keys are saved to `~/.ai_hands/config.json` on your computer —
  not sent anywhere except directly to the AI provider you got the key
  from.

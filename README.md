# AI Hands — Setup Guide

This is a small desktop app. You give it an instruction, and it directly
edits files in a project folder on your computer — you never touch code
yourself.

**Groq is the only AI provider this app uses.** You no longer need the
terminal to set its API key — the app has a built-in setup window for
that, and can also read it from a `.env` file (see below).

## One-time setup

### 1. Install Python (if you don't already have it)
Download from https://python.org/downloads — during install, make sure
you check the box that says **"Add Python to PATH"**.

### 2. Install the dependency this app needs
Open a terminal (Windows: search "cmd" or "PowerShell"; Mac: search
"Terminal") and type:

```
pip install requests
```

### 3. Get a free Groq API key
Go to https://console.groq.com (no credit card needed), sign up, and
create an API key.

## Running the app

In a terminal, navigate to this folder and run:

```
python main.py
```

**First time only:** a small setup window opens asking for your Groq
API key — paste it in and click Save. It's remembered for next time,
so you won't see this window again unless you click "API Keys" in the
top bar to change something.

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
- If Groq runs out of free usage for the day, the app tells you and
  reopens the key setup window so you can update the key or wait for
  it to reset — your last instruction picks up right where it left off
  once you're ready to continue.

## Notes for this draft

- **No more retyping `$env:` every session:** if a `.env` file exists
  in the folder you run `python main.py` from, your key loads
  automatically at startup. Create it once (a plain text file named
  exactly `.env`, no other extension) with a line like:
  ```
  GROQ_API_KEY=your_key_here
  ```
  Saving a key through the "API Keys" window also writes/updates this
  same file automatically, so however you set it, everything stays in
  sync.
- Every red error message includes a short plain-language guess at
  what went wrong, followed by the full technical stack trace — all of
  it is selectable/copyable, so if something breaks and you're not
  sure what it means, just copy the whole error and paste it here.
- Conversation history is capped to the most recent 20 messages
  between tasks, to keep token usage down on long sessions. Safe
  because the project's real "memory" is the files and checkpoints,
  not the chat log.
- The app refuses to work directly on system folders (like
  `C:\Windows`, `C:\Program Files`, or a whole drive root) — pick a
  regular project folder, e.g. inside Documents or Desktop.
- In the "API Keys" window, click **"Test Keys"** to check your key
  right away — it tells you plainly if it's invalid, needs billing, or
  if the problem is your internet connection.
- If Git isn't installed, the AI still works fully — only the
  checkpoint/undo feature is unavailable until you install it.
- Your API key is saved to `~/.ai_hands/config.json` and to a `.env`
  file in the app's folder — not sent anywhere except directly to Groq.

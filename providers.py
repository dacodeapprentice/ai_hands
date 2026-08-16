"""
providers.py — The connection to the "brain", now supporting several
different AI providers so the software can automatically swap between
them when one runs out of free usage for the day.

Every provider class exposes the same method: chat(messages, tools),
and always returns the same shape of response (OpenAI's format), no
matter which provider actually answered. That's what lets agent.py
stay completely unaware of which AI is currently driving — it only
ever talks to ModelRouter.

Supported out of the box (all have real free tiers):
  - Groq
  - Google Gemini
  - OpenRouter
  - Mistral
  - Cerebras

Each provider only activates if you've set its API key as an
environment variable. You don't need all five — set as many as you
want, and the router will use whichever ones are available, in the
order you list them.
"""

import os
import json
import requests


class RateLimitError(Exception):
    """Raised when a provider says 'slow down' (HTTP 429)."""
    pass


class ProviderError(Exception):
    """Raised for any other provider failure."""
    pass


# ---------------------------------------------------------------------
# Most providers (Groq, OpenRouter, Mistral, Cerebras) all speak the
# same "OpenAI-compatible" chat completions format, so one class can
# handle all of them — only the URL, default model, and env var differ.
# ---------------------------------------------------------------------

class OpenAICompatibleProvider:
    def __init__(self, name, url, default_model, env_var, api_key=None, extra_headers=None):
        self.name = name
        self.url = url
        self.model = default_model
        self.api_key = api_key or os.environ.get(env_var)
        self.extra_headers = extra_headers or {}

    def available(self) -> bool:
        return bool(self.api_key)

    def chat(self, messages, tools):
        if not self.api_key:
            raise ProviderError(f"{self.name}: no API key set.")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }

        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]

        payload = {
            "model": self.model,
            "messages": messages,
            "tools": openai_tools,
            "tool_choice": "auto",
        }

        response = requests.post(self.url, headers=headers, json=payload, timeout=90)

        if response.status_code == 429:
            raise RateLimitError(f"{self.name} rate limit hit.")
        if response.status_code != 200:
            raise ProviderError(f"{self.name} error {response.status_code}: {response.text}")

        return response.json()


def GroqProvider(model="llama-3.3-70b-versatile", api_key=None):
    return OpenAICompatibleProvider(
        name="Groq",
        url="https://api.groq.com/openai/v1/chat/completions",
        default_model=model,
        env_var="GROQ_API_KEY",
        api_key=api_key,
    )


def OpenRouterProvider(model="meta-llama/llama-3.3-70b-instruct:free", api_key=None):
    return OpenAICompatibleProvider(
        name="OpenRouter",
        url="https://openrouter.ai/api/v1/chat/completions",
        default_model=model,
        env_var="OPENROUTER_API_KEY",
        api_key=api_key,
    )


def MistralProvider(model="mistral-large-latest", api_key=None):
    return OpenAICompatibleProvider(
        name="Mistral",
        url="https://api.mistral.ai/v1/chat/completions",
        default_model=model,
        env_var="MISTRAL_API_KEY",
        api_key=api_key,
    )


def CerebrasProvider(model="llama-3.3-70b", api_key=None):
    return OpenAICompatibleProvider(
        name="Cerebras",
        url="https://api.cerebras.ai/v1/chat/completions",
        default_model=model,
        env_var="CEREBRAS_API_KEY",
        api_key=api_key,
    )


# ---------------------------------------------------------------------
# Gemini uses a genuinely different request/response shape, so it gets
# its own class that translates to/from our common internal format.
# ---------------------------------------------------------------------

class GeminiProvider:
    name = "Gemini"

    def __init__(self, model="gemini-2.0-flash", api_key=None):
        self.model = model
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")

    def available(self) -> bool:
        return bool(self.api_key)

    def chat(self, messages, tools):
        if not self.api_key:
            raise ProviderError("Gemini: no API key set.")

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )

        system_instruction, contents, call_id_to_name = self._build_request(messages)

        gemini_tools = [{
            "functionDeclarations": [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                }
                for t in tools
            ]
        }]

        payload = {"contents": contents, "tools": gemini_tools}
        if system_instruction:
            payload["system_instruction"] = {"parts": [{"text": system_instruction}]}

        response = requests.post(url, json=payload, timeout=90)

        if response.status_code == 429:
            raise RateLimitError("Gemini rate limit hit.")
        if response.status_code != 200:
            raise ProviderError(f"Gemini error {response.status_code}: {response.text}")

        return self._parse_response(response.json(), call_id_to_name)

    # -- translation: our internal (OpenAI-style) messages -> Gemini's format --
    def _build_request(self, messages):
        system_instruction = ""
        contents = []
        call_id_to_name = {}

        for msg in messages:
            role = msg["role"]

            if role == "system":
                system_instruction = msg["content"]

            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": msg["content"]}]})

            elif role == "assistant":
                parts = []
                if msg.get("content"):
                    parts.append({"text": msg["content"]})
                for call in msg.get("tool_calls") or []:
                    name = call["function"]["name"]
                    args = json.loads(call["function"]["arguments"])
                    call_id_to_name[call["id"]] = name
                    parts.append({"functionCall": {"name": name, "args": args}})
                contents.append({"role": "model", "parts": parts})

            elif role == "tool":
                name = call_id_to_name.get(msg["tool_call_id"], "unknown_function")
                contents.append({
                    "role": "user",
                    "parts": [{
                        "functionResponse": {
                            "name": name,
                            "response": {"result": msg["content"]},
                        }
                    }],
                })

        return system_instruction, contents, call_id_to_name

    # -- translation: Gemini's response -> our internal (OpenAI-style) format --
    def _parse_response(self, data, call_id_to_name):
        candidate = data["candidates"][0]
        parts = candidate.get("content", {}).get("parts", [])

        text_parts = []
        tool_calls = []
        for part in parts:
            if "text" in part:
                text_parts.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                call_id = f"gemini_call_{len(tool_calls)}"
                tool_calls.append({
                    "id": call_id,
                    "function": {
                        "name": fc["name"],
                        "arguments": json.dumps(fc.get("args", {})),
                    },
                })

        message = {
            "role": "assistant",
            "content": " ".join(text_parts) if text_parts else "",
            "tool_calls": tool_calls if tool_calls else None,
        }
        return {"choices": [{"message": message}]}


class OllamaProvider:
    """
    Talks to a local Ollama installation running on your own machine
    (default: http://localhost:11434). No API key, no internet
    required once the model is downloaded, and no rate limit — the
    only ceiling is your own hardware.
    """
    name = "Ollama (local)"

    def __init__(self, model, host="http://localhost:11434"):
        self.model = model
        self.host = host.rstrip("/")

    def available(self) -> bool:
        # Local provider is always "configured" — whether it actually
        # works is checked separately (see hardware.py / main.py setup),
        # since that requires Ollama to be running, not just present.
        return True

    def chat(self, messages, tools):
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]

        payload = {
            "model": self.model,
            "messages": messages,
            "tools": openai_tools,
            "stream": False,
        }

        try:
            response = requests.post(f"{self.host}/api/chat", json=payload, timeout=300)
        except requests.exceptions.ConnectionError:
            raise ProviderError(
                "Could not reach Ollama. Make sure the Ollama app/service is "
                "running on this computer (it usually starts automatically "
                "after install)."
            )

        if response.status_code != 200:
            raise ProviderError(f"Ollama error {response.status_code}: {response.text}")

        return self._parse_response(response.json())

    def _parse_response(self, data):
        # Ollama's response shape is already close to OpenAI's, with two
        # differences we need to normalize: tool call arguments come as
        # a dict (not a JSON string), and there's no call "id" field.
        message = data.get("message", {})
        raw_tool_calls = message.get("tool_calls") or []

        tool_calls = []
        for i, call in enumerate(raw_tool_calls):
            func = call.get("function", {})
            tool_calls.append({
                "id": f"ollama_call_{i}",
                "function": {
                    "name": func.get("name", ""),
                    "arguments": json.dumps(func.get("arguments", {})),
                },
            })

        normalized = {
            "role": "assistant",
            "content": message.get("content", ""),
            "tool_calls": tool_calls if tool_calls else None,
        }
        return {"choices": [{"message": normalized}]}


# ---------------------------------------------------------------------
# The router: tries providers in order, moves on when one is rate-limited
# or unavailable (no key set).
# ---------------------------------------------------------------------

class ModelRouter:
    def __init__(self, providers, on_switch=None):
        # only keep providers that actually have a key configured
        self.providers = [p for p in providers if self._is_available(p)]
        self.current_index = 0
        self.on_switch = on_switch or (lambda old_name, new_name: None)
        if not self.providers:
            raise ProviderError(
                "No AI providers are configured. Set at least one API key "
                "(GROQ_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY, "
                "MISTRAL_API_KEY, or CEREBRAS_API_KEY) as an environment variable."
            )

    @staticmethod
    def _is_available(provider):
        return provider.available() if hasattr(provider, "available") else True

    @property
    def current(self):
        return self.providers[self.current_index]

    def chat(self, messages, tools):
        attempts = 0
        while attempts < len(self.providers):
            provider = self.current
            try:
                return provider.chat(messages, tools)
            except RateLimitError:
                old_name = provider.name
                attempts += 1
                self.current_index = (self.current_index + 1) % len(self.providers)
                self.on_switch(old_name, self.current.name)
        raise ProviderError("All configured providers have hit their limits. Try again later.")

    def reset(self):
        """Go back to the first (preferred) provider, e.g. once a day."""
        self.current_index = 0

    def status(self):
        """Returns a readable list of which providers are active and which is current."""
        lines = []
        for i, p in enumerate(self.providers):
            marker = " <- current" if i == self.current_index else ""
            lines.append(f"{p.name}{marker}")
        return "\n".join(lines)

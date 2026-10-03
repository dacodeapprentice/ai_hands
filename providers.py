"""
providers.py — The connection to the "brain".

Every provider class exposes the same method: chat(messages, tools),
and always returns the same shape of response (OpenAI's format), no
matter which provider actually answered. That's what lets agent.py
stay completely unaware of which AI is currently driving — it only
ever talks to ModelRouter.

Groq is the active provider used by this app. (OpenRouter, Mistral,
and Ollama support were removed - Groq is the only one in use now.)
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


def _classify_http_error(name: str, status_code: int, body: str) -> str:
    """
    Turns a raw HTTP status code into a plain-language explanation,
    so errors say what's actually wrong instead of just a number.
    """
    if status_code == 401:
        return (
            f"{name} says this API key is invalid (401 Unauthorized). "
            f"Double-check you copied the whole key with no extra spaces, "
            f"or generate a new one from {name}'s website and paste that in."
        )
    if status_code == 402:
        return (
            f"{name} is asking for payment (402 Payment Required). "
            f"This usually means the free tier needs a billing method on "
            f"file, or your free credits ran out. Try a different provider, "
            f"or add billing on {name}'s site if you want to keep using it."
        )
    if status_code == 403:
        return (
            f"{name} refused this request (403 Forbidden). Your key may "
            f"not have permission for this, or your account may need "
            f"extra verification on {name}'s site."
        )
    if status_code == 404:
        return f"{name} error: the model name this app is using no longer exists (404). This is a bug in the app, not something you did — let me know."
    if status_code >= 500:
        return f"{name}'s own servers are having trouble right now (error {status_code}). This isn't your key or your computer - try again shortly."
    return f"{name} error {status_code}: {body[:200]}"


def _classify_network_error(name: str, exc: Exception) -> str:
    """Turns a raw network exception into a plain-language explanation."""
    if isinstance(exc, requests.exceptions.Timeout):
        return f"{name} didn't respond in time. This is usually a slow or unstable internet connection - try again."
    if isinstance(exc, requests.exceptions.ConnectionError):
        return (
            f"Couldn't reach {name} at all. This usually means no internet "
            f"connection right now, or a firewall/antivirus blocking this "
            f"app's network access. Check your connection and try again."
        )
    return f"Unexpected network error reaching {name}: {exc}"


# ---------------------------------------------------------------------
# Groq (and a few other providers not currently wired into the app)
# all speak the same "OpenAI-compatible" chat completions format, so
# one class can handle all of them — only the URL, model list, and
# env var differ.
# ---------------------------------------------------------------------

class OpenAICompatibleProvider:
    def __init__(self, name, url, models, env_var, api_key=None, extra_headers=None):
        """
        models: a list of model name candidates, tried in order. Free
        model names on these providers get renamed, deprecated, or
        delisted with little notice - if the first one 404s (model
        not found), this automatically tries the next one instead of
        just failing, so a single upstream rename doesn't break the
        whole provider. The model that works is remembered so later
        calls go straight to it instead of re-testing each time.
        """
        self.name = name
        self.url = url
        self.models = models if isinstance(models, list) else [models]
        self.api_key = api_key or os.environ.get(env_var)
        self.extra_headers = extra_headers or {}
        self._working_model_index = 0

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

        last_error = None
        # Start from the model that last worked, so a provider that's
        # already found a good model doesn't retry older ones every call.
        ordered_indices = list(range(self._working_model_index, len(self.models))) + \
                           list(range(0, self._working_model_index))

        for idx in ordered_indices:
            model_name = self.models[idx]
            payload = {
                "model": model_name,
                "messages": messages,
                "tools": openai_tools,
                "tool_choice": "auto",
            }

            try:
                response = requests.post(self.url, headers=headers, json=payload, timeout=90)
            except requests.exceptions.RequestException as exc:
                raise ProviderError(_classify_network_error(self.name, exc))

            if response.status_code == 429:
                raise RateLimitError(f"{self.name} rate limit hit.")

            if response.status_code == 404:
                # This specific model is gone - remember the error and
                # try the next candidate instead of giving up outright.
                last_error = ProviderError(_classify_http_error(self.name, 404, response.text))
                continue

            if response.status_code != 200:
                raise ProviderError(_classify_http_error(self.name, response.status_code, response.text))

            self._working_model_index = idx
            return response.json()

        raise last_error or ProviderError(f"{self.name}: no working model found.")


def GroqProvider(models=None, api_key=None):
    return OpenAICompatibleProvider(
        name="Groq",
        url="https://api.groq.com/openai/v1/chat/completions",
        models=models or ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3-32b"],
        env_var="GROQ_API_KEY",
        api_key=api_key,
    )


def CerebrasProvider(models=None, api_key=None):
    return OpenAICompatibleProvider(
        name="Cerebras",
        url="https://api.cerebras.ai/v1/chat/completions",
        models=models or ["llama-3.3-70b"],
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
                "No AI providers are configured. Set your GROQ_API_KEY "
                "as an environment variable, in a .env file, or via the "
                "\"API Keys\" window."
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

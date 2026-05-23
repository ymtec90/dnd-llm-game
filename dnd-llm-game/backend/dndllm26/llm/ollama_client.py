from collections.abc import AsyncIterator
import json
import re
from typing import Any

from dndllm26.core.settings import get_settings


def _read_attr(value: Any, *names: str) -> Any:
    current = value
    for name in names:
        if isinstance(current, dict):
            current = current.get(name)
        else:
            current = getattr(current, name, None)
        if current is None:
            return None
    return current


class OllamaService:
    def __init__(self) -> None:
        settings = get_settings()
        self.chat_model = settings.ollama_chat_model
        self.utility_model = settings.ollama_utility_model or settings.ollama_chat_model
        self.embed_model = settings.ollama_embed_model
        self.host = settings.ollama_host
        self.client = None

    def _client(self):
        if self.client is None:
            import ollama

            self.client = ollama.AsyncClient(host=self.host, timeout=120)
        return self.client

    @staticmethod
    def error_message(exc: Exception) -> str:
        error = getattr(exc, "error", None)
        status = getattr(exc, "status_code", None)
        if error and status:
            return f"{error} (status code: {status})"
        if error:
            return str(error)
        if str(exc):
            return str(exc)
        return exc.__class__.__name__

    async def health(self) -> dict:
        return await self._client().list()

    async def list_models(self) -> list[str]:
        response = await self._client().list()
        models = _read_attr(response, "models") or []
        names: list[str] = []
        for model in models:
            name = _read_attr(model, "model") or _read_attr(model, "name")
            if name:
                names.append(str(name))
        return names

    async def pull_model(self, model: str) -> None:
        async for _ in await self._client().pull(model=model, stream=True):
            pass

    async def embed(self, text: str) -> list[float]:
        response = await self._client().embed(model=self.embed_model, input=text)
        embeddings = _read_attr(response, "embeddings") or []
        return embeddings[0] if embeddings else []

    async def stream_dm(self, prompt: str) -> AsyncIterator[str]:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a rigorous, cinematic Dungeon Master for a local D&D web game. "
                    "Keep scenes playable, concise, and reactive. Respect player agency. "
                    "Do not invent dice totals; the app handles dice. Avoid markdown headings "
                    "and long decorative formatting. Keep the total response under 1000 "
                    "characters and 200 words. End every response with exactly one "
                    "Choices: section containing 2-4 numbered actionable options."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        stream = await self._client().chat(
            model=self.chat_model,
            messages=messages,
            stream=True,
            options={
                "temperature": 0.85,
                "num_predict": 260,
                "top_p": 0.92,
            },
            keep_alive="10m",
        )
        async for chunk in stream:
            content = _read_attr(chunk, "message", "content") or ""
            if content:
                yield content

    async def chat_text(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        temperature: float = 0.2,
        num_predict: int = 500,
        json_format: bool = False,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": model or self.utility_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {"temperature": temperature, "num_predict": num_predict},
            "keep_alive": "10m",
        }
        if json_format:
            kwargs["format"] = "json"
        response = await self._client().chat(**kwargs)
        return str(_read_attr(response, "message", "content") or "").strip()

    async def chat_json(
        self,
        system: str,
        user: str,
        fallback: dict[str, Any],
        *,
        model: str | None = None,
    ) -> dict[str, Any]:
        try:
            raw = await self.chat_text(system, user, model=model, json_format=True)
            return json.loads(raw)
        except Exception:
            try:
                raw = await self.chat_text(system, user, model=model)
                match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
                if match:
                    return json.loads(match.group(0))
            except Exception:
                pass
        return fallback


ollama_service = OllamaService()

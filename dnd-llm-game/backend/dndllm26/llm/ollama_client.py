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
        import httpx
        if isinstance(exc, httpx.TimeoutException):
            return "A requisição para a API do Ollama expirou (timeout). O modelo pode estar carregando na memória ou o servidor está sobrecarregado."
        if isinstance(exc, httpx.ConnectError):
            return "Não foi possível conectar ao Ollama. Certifique-se de que o Ollama está em execução (ex: 'ollama serve')."
        
        error = getattr(exc, "error", None)
        status = getattr(exc, "status_code", None)
        if error and status:
            if "not found" in str(error).lower() or "not loaded" in str(error).lower():
                return f"O modelo do Ollama não foi encontrado ou não pôde ser carregado: {error} (código de status: {status})"
            return f"{error} (código de status: {status})"
        if error:
            return str(error)
        
        exc_str = str(exc)
        if "connect" in exc_str.lower():
            return "Não foi possível conectar ao Ollama. Certifique-se de que o Ollama está em execução."
        if "timeout" in exc_str.lower():
            return "A requisição para a API do Ollama expirou. O modelo pode estar sendo carregado."
            
        if exc_str:
            return exc_str
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
                    "Você é um Mestre (Dungeon Master) rigoroso e cinematográfico para um jogo de D&D. "
                    "Narrem e gerenciem o jogo exclusivamente em português do Brasil. "
                    "Mantenha as cenas jogáveis, concisas e reativas. Respeite a agência e as ações do jogador. "
                    "Não invente resultados de dados; o aplicativo lida com isso. Evite cabeçalhos markdown "
                    "e formatações decorativas longas. Mantenha a resposta total abaixo de 1000 caracteres "
                    "e 200 palavras. Termine cada resposta com exatamente uma seção Escolhas: contendo de "
                    "2 a 4 opções numeradas de ações jogáveis."
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

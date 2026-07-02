from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "DNDLLM26"
    database_url: str = "sqlite:///data/dndllm26.db"
    data_dir: Path = Path("data")
    upload_dir: Path = Path("data/uploads")
    lancedb_dir: Path = Path("data/lancedb")
    ollama_host: str = "http://localhost:11434"
    ollama_chat_model: str = "llama3.2"
    ollama_utility_model: str = ""
    ollama_embed_model: str = "nomic-embed-text"
    api_host: str = "127.0.0.1"
    api_port: int = 8765
    frontend_port: int = 5173

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.lancedb_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings

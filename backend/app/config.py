from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    database_url: str = "postgresql://codedoc:codedoc@localhost:5433/codedoc"
    redis_url: str = "redis://localhost:6379"
    data_dir: Path = Path("./data")

    # Claude. Without a key here the SDK falls back to the environment or an `ant auth login` profile.
    anthropic_api_key: str | None = None
    agent_model: str = "claude-opus-5-5"
    agent_effort: str = "medium"
    agent_max_steps: int = 12
    agent_server_fallback: bool = True

    # Voyage. Without a key the app uses an offline hashing embedder and skips reranking.
    voyage_api_key: str | None = None
    embed_model: str = "voyage-code-3"
    embed_dim: int = 1024
    rerank_model: str = "rerank-2.5"

    # Indexing limits
    max_file_bytes: int = 1_000_000
    chunk_max_chars: int = 1500
    embed_batch_size: int = 64

    cors_origins: list[str] = ["http://localhost:3000"]
    # Indexing a server-side folder is handy locally but must be off on a shared deployment.
    allow_local_paths: bool = True
    # Public demo mode: no adding, re-indexing or deleting repositories.
    read_only: bool = False

    @property
    def offline_embeddings(self) -> bool:
        return not self.voyage_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()

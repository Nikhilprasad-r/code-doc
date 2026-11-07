from functools import lru_cache
from pathlib import Path
from typing import Literal

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

    # Code graph (graphify). Built alongside the embeddings; a failure leaves the rest of the index usable.
    graph_enabled: bool = True
    graph_timeout_s: float = 600

    cors_origins: list[str] = ["http://localhost:3000"]
    # Indexing a server-side folder is handy locally but must be off on a shared deployment.
    allow_local_paths: bool = True
    # Public demo mode: no adding, re-indexing or deleting repositories.
    read_only: bool = False

    # Observability. Logs go to stderr; tracing turns on when OTEL_EXPORTER_OTLP_ENDPOINT is set (app/observability).
    log_format: Literal["json", "text"] = "json"
    log_level: str = "INFO"
    # Store each question and answer in answer_runs (metrics are recorded either way).
    record_answer_text: bool = True

    # MCP over HTTP (/mcp): requests must carry one of these Host / Origin values (DNS-rebinding protection).
    mcp_allowed_hosts: list[str] = ["localhost:*", "127.0.0.1:*", "[::1]:*"]
    mcp_allowed_origins: list[str] = ["http://localhost:*", "http://127.0.0.1:*", "http://[::1]:*"]

    @property
    def offline_embeddings(self) -> bool:
        return not self.voyage_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()

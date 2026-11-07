"""Types shared by the agent's tool modules (`tools`, `graph_tools`)."""

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict

from app.retrieval.embeddings import Embedder, Reranker

MAX_OUTPUT_CHARS = 12_000


@dataclass
class ToolContext:
    repo: dict
    embedder: Embedder
    reranker: Reranker

    @property
    def repo_id(self) -> int:
        return self.repo["id"]


@dataclass
class ToolOutput:
    text: str
    sources: list[dict] = field(default_factory=list)
    is_error: bool = False

    def clipped(self) -> str:
        if len(self.text) <= MAX_OUTPUT_CHARS:
            return self.text
        return self.text[:MAX_OUTPUT_CHARS] + "\n... [output truncated; narrow the query]"


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")

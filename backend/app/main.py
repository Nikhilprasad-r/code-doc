"""HTTP API: repositories, indexing jobs, structured data, search, and streaming chat."""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import anthropic
from arq import ArqRedis, create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.answer import answer_question
from app.config import get_settings
from app.db import close_db, connection, init_db
from app.ingest.pipeline import UPLOAD_PREFIX, index_repo
from app.ingest.source import is_git_url, repo_name
from app.retrieval.embeddings import get_embedder, get_reranker
from app.retrieval.search import hybrid_search

log = logging.getLogger("codedoc")
settings = get_settings()
_background: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    redis = RedisSettings.from_dsn(settings.redis_url)
    redis.conn_retries = 1
    try:
        app.state.arq = await create_pool(redis)
    except Exception:  # no Redis: index inside this process (fine for local development)
        log.warning("Redis unavailable at %s; indexing will run in-process", settings.redis_url)
        app.state.arq = None
    yield
    if app.state.arq is not None:
        await app.state.arq.close()
    await close_db()


app = FastAPI(title="Code Documentation Assistant", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])


# --- repositories ------------------------------------------------------------------------------

class RepoCreate(BaseModel):
    source: str = Field(description="https git URL or, when allowed, a local folder path")
    ref: str | None = Field(None, description="Branch or tag to clone")


def _require_writable() -> None:
    if settings.read_only:
        raise HTTPException(403, "This deployment is read-only")


async def _enqueue(repo_id: int) -> None:
    pool: ArqRedis | None = app.state.arq
    if pool is not None:
        await pool.enqueue_job("index_job", repo_id)
        return
    task = asyncio.create_task(index_repo(repo_id))
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _get_repo(repo_id: int) -> dict:
    async with connection() as conn:
        repo = await (await conn.execute("SELECT * FROM repos WHERE id = %s", [repo_id])).fetchone()
    if repo is None:
        raise HTTPException(404, "Repository not found")
    return repo


async def _insert_repo(name: str, source: str, ref: str | None) -> dict:
    async with connection() as conn:
        return await (await conn.execute(
            "INSERT INTO repos (name, source, ref) VALUES (%s, %s, %s) RETURNING *", [name, source, ref],
        )).fetchone()


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "offline_embeddings": settings.offline_embeddings, "agent_model": settings.agent_model,
            "read_only": settings.read_only}


@app.get("/repos")
async def list_repos() -> list[dict]:
    async with connection() as conn:
        return await (await conn.execute(
            "SELECT id, name, source, ref, commit_sha, status, progress, stats, error, created_at, updated_at "
            "FROM repos ORDER BY created_at DESC")).fetchall()


@app.post("/repos", status_code=202)
async def create_repo(body: RepoCreate) -> dict:
    _require_writable()
    source = body.source.strip()
    if not is_git_url(source):
        if not settings.allow_local_paths:
            raise HTTPException(400, "Only https git URLs are accepted here")
        if not Path(source).expanduser().is_dir():
            raise HTTPException(400, "Source must be an https git URL or an existing folder")
    repo = await _insert_repo(repo_name(source), source, body.ref)
    await _enqueue(repo["id"])
    return repo


@app.post("/repos/upload", status_code=202)
async def upload_repo(file: UploadFile = File(...)) -> dict:
    _require_writable()
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, "Upload a .zip archive")
    repo = await _insert_repo(repo_name(file.filename or "upload.zip"), UPLOAD_PREFIX + (file.filename or ""), None)
    target = settings.data_dir / "uploads" / f"{repo['id']}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as out:
        while chunk := await file.read(1 << 20):
            out.write(chunk)
    await _enqueue(repo["id"])
    return repo


@app.get("/repos/{repo_id}")
async def get_repo(repo_id: int) -> dict:
    return await _get_repo(repo_id)


@app.post("/repos/{repo_id}/reindex", status_code=202)
async def reindex(repo_id: int) -> dict:
    _require_writable()
    repo = await _get_repo(repo_id)
    async with connection() as conn:
        await conn.execute("UPDATE repos SET status = 'queued', progress = '{}' WHERE id = %s", [repo_id])
    await _enqueue(repo_id)
    return {**repo, "status": "queued"}


@app.delete("/repos/{repo_id}", status_code=204)
async def delete_repo(repo_id: int) -> None:
    _require_writable()
    async with connection() as conn:
        await conn.execute("DELETE FROM repos WHERE id = %s", [repo_id])


@app.get("/repos/{repo_id}/files")
async def list_files(repo_id: int) -> list[dict]:
    async with connection() as conn:
        return await (await conn.execute(
            "SELECT path, language, line_count FROM files WHERE repo_id = %s ORDER BY path", [repo_id])).fetchall()


@app.get("/repos/{repo_id}/file")
async def get_file(repo_id: int, path: str) -> dict:
    async with connection() as conn:
        row = await (await conn.execute(
            "SELECT path, language, content, line_count FROM files WHERE repo_id = %s AND path = %s",
            [repo_id, path])).fetchone()
    if row is None:
        raise HTTPException(404, "File not found")
    return row


@app.get("/repos/{repo_id}/endpoints")
async def get_endpoints(repo_id: int) -> list[dict]:
    async with connection() as conn:
        return await (await conn.execute(
            "SELECT framework, method, route, handler, path, line FROM endpoints WHERE repo_id = %s "
            "ORDER BY path, line", [repo_id])).fetchall()


@app.get("/repos/{repo_id}/dependencies")
async def get_dependencies(repo_id: int) -> list[dict]:
    async with connection() as conn:
        return await (await conn.execute(
            "SELECT ecosystem, name, version_spec, scope, manifest_path FROM dependencies WHERE repo_id = %s "
            "ORDER BY manifest_path, name", [repo_id])).fetchall()


class SearchRequest(BaseModel):
    query: str
    k: int = Field(8, ge=1, le=30)
    path_prefix: str | None = None


@app.post("/repos/{repo_id}/search")
async def search(repo_id: int, body: SearchRequest) -> list[dict]:
    repo = await _get_repo(repo_id)
    hits = await hybrid_search(repo_id, body.query, get_embedder(settings), get_reranker(settings),
                               repo_embed_model=repo["stats"].get("embed_model"), k=body.k,
                               path_prefix=body.path_prefix)
    return [hit.__dict__ for hit in hits]


# --- chat --------------------------------------------------------------------------------------

class ChatRequest(BaseModel):
    repo_id: int
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: uuid.UUID | None = None
    # "agent" (default): tool-using agent. "rag": one retrieval + one model call, no follow-up turns.
    mode: Literal["agent", "rag"] = "agent"


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


async def _load_history(conversation_id: uuid.UUID, repo_id: int) -> list[dict]:
    async with connection() as conn:
        row = await (await conn.execute(
            "SELECT messages FROM conversations WHERE id = %s AND repo_id = %s", [conversation_id, repo_id],
        )).fetchone()
    return row["messages"] if row else []


async def _save_history(conversation_id: uuid.UUID, repo_id: int, messages: list[dict]) -> None:
    async with connection() as conn:
        await conn.execute(
            "INSERT INTO conversations (id, repo_id, messages) VALUES (%s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET messages = EXCLUDED.messages, updated_at = now()",
            [conversation_id, repo_id, json.dumps(messages)],
        )


async def _chat_events(body: ChatRequest, repo: dict) -> AsyncIterator[str]:
    conversation_id = body.conversation_id or uuid.uuid4()
    yield _sse({"type": "conversation", "id": str(conversation_id)})
    agent = body.mode == "agent"
    history = await _load_history(conversation_id, repo["id"]) if agent else []
    try:
        async for event in answer_question(body.message, repo, mode=body.mode, history=history, settings=settings):
            if event["type"] == "_messages" and agent:
                await _save_history(conversation_id, repo["id"], event["messages"])
            if event["type"].startswith("_"):
                continue
            yield _sse(event)
    except anthropic.AuthenticationError:
        yield _sse({"type": "error", "message": "Anthropic credentials are missing or invalid. "
                                                "Set ANTHROPIC_API_KEY and restart the API."})
    except anthropic.RateLimitError:
        yield _sse({"type": "error", "message": "Rate limited by the Anthropic API. Try again shortly."})
    except anthropic.APIStatusError as exc:
        log.exception("Anthropic API error")
        yield _sse({"type": "error", "message": f"Anthropic API error {exc.status_code}: {exc.message}"})
    except anthropic.APIConnectionError:
        yield _sse({"type": "error", "message": "Could not reach the Anthropic API."})
    except anthropic.AnthropicError as exc:
        yield _sse({"type": "error", "message": str(exc)})
    except TypeError as exc:
        if "authentication" not in str(exc).lower():
            raise
        # The SDK raises TypeError when no API key, token or `ant auth login` profile is configured.
        yield _sse({"type": "error", "message": "No Anthropic credentials configured. "
                                                "Set ANTHROPIC_API_KEY in .env and restart the API."})


@app.post("/chat")
async def chat(body: ChatRequest) -> StreamingResponse:
    repo = await _get_repo(body.repo_id)
    if repo["status"] != "ready":
        raise HTTPException(409, f"Repository is {repo['status']}, not ready")
    return StreamingResponse(_chat_events(body, repo), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

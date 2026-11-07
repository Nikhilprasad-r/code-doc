"""Repository bookkeeping shared by the HTTP API and the MCP server."""

from pathlib import Path

from app.config import Settings
from app.db import connection
from app.ingest.source import is_git_url, repo_name


class RepoError(Exception):
    """A repository request that cannot be served (unknown repo, bad source, read-only deployment)."""


def validate_source(source: str, settings: Settings) -> str:
    source = source.strip()
    if settings.read_only:
        raise RepoError("This deployment is read-only")
    if is_git_url(source):
        return source
    if not settings.allow_local_paths:
        raise RepoError("Only https git URLs are accepted here")
    if not Path(source).expanduser().is_dir():
        raise RepoError("Source must be an https git URL or an existing folder")
    return source


async def insert_repo(name: str, source: str, ref: str | None) -> dict:
    async with connection() as conn:
        return await (await conn.execute(
            "INSERT INTO repos (name, source, ref) VALUES (%s, %s, %s) RETURNING *", [name, source, ref],
        )).fetchone()


async def create_repo(source: str, ref: str | None, settings: Settings) -> dict:
    source = validate_source(source, settings)
    return await insert_repo(repo_name(source), source, ref)


async def get_repo(repo_id: int) -> dict | None:
    async with connection() as conn:
        return await (await conn.execute("SELECT * FROM repos WHERE id = %s", [repo_id])).fetchone()


async def list_repos(include_eval: bool = False) -> list[dict]:
    """Newest first. Evaluation-only copies (line-chunked indexes) are hidden unless asked for."""
    where = "" if include_eval else "WHERE chunking = 'ast'"
    async with connection() as conn:
        return await (await conn.execute(
            f"SELECT * FROM repos {where} ORDER BY updated_at DESC")).fetchall()


async def resolve_repo(identifier: str | int | None, default: str | None = None) -> dict:
    """Find a ready repository by id, exact name, or unique name suffix ("spring-petclinic").
    With no identifier, use `default`, or the only ready repository if there is exactly one."""
    identifier = identifier if identifier not in (None, "") else default
    ready = [r for r in await list_repos() if r["status"] == "ready"]
    if identifier in (None, ""):
        if len(ready) == 1:
            return ready[0]
        names = ", ".join(f"{r['name']} (id {r['id']})" for r in ready) or "none"
        raise RepoError(f"Say which repository to use. Indexed repositories: {names}")
    text = str(identifier).strip()
    if text.isdigit():
        repo = await get_repo(int(text))
        if repo is None:
            raise RepoError(f"No repository with id {text}")
        if repo["status"] != "ready":
            raise RepoError(f"Repository {repo['name']} is {repo['status']}, not ready")
        return repo
    lowered = text.lower().removesuffix(".git").rstrip("/")
    exact = [r for r in ready if r["name"].lower() == lowered or r["source"].lower().rstrip("/") == lowered]
    matches = exact or [r for r in ready if r["name"].lower().endswith("/" + lowered) or lowered in r["name"].lower()]
    if len(matches) == 1 or exact:
        return matches[0]
    if not matches:
        names = ", ".join(r["name"] for r in ready) or "none"
        raise RepoError(f"No ready repository matches '{text}'. Indexed repositories: {names}")
    raise RepoError(f"'{text}' matches several repositories: {', '.join(r['name'] for r in matches)}")

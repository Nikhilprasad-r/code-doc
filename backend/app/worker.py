"""Background indexing worker (arq). Run with: arq app.worker.WorkerSettings"""

from arq.connections import RedisSettings

from app.config import get_settings
from app.db import close_db, init_db
from app.ingest.pipeline import index_repo


async def index_job(ctx: dict, repo_id: int) -> dict:
    return await index_repo(repo_id)


async def startup(ctx: dict) -> None:
    await init_db()


async def shutdown(ctx: dict) -> None:
    await close_db()


class WorkerSettings:
    functions = [index_job]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = startup
    on_shutdown = shutdown
    job_timeout = 3600
    max_jobs = 2

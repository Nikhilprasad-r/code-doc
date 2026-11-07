"""Background indexing worker (arq). Run with: arq app.worker.WorkerSettings"""

from arq.connections import RedisSettings

from app.config import get_settings
from app.db import close_db, init_db
from app.ingest.pipeline import index_repo
from app.observability.logs import configure_logging
from app.observability.tracing import setup_tracing, shutdown_tracing


async def index_job(ctx: dict, repo_id: int) -> dict:
    return await index_repo(repo_id)


async def startup(ctx: dict) -> None:
    configure_logging(get_settings())  # here, not at import: arq installs its own logging config in between
    setup_tracing("codedoc-worker")  # no-op unless OTEL_EXPORTER_OTLP_ENDPOINT is set
    await init_db()


async def shutdown(ctx: dict) -> None:
    await close_db()
    shutdown_tracing()


class WorkerSettings:
    functions = [index_job]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = startup
    on_shutdown = shutdown
    job_timeout = 3600
    max_jobs = 2

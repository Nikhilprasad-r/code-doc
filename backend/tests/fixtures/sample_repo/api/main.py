from fastapi import FastAPI
from flask import Blueprint

from api.routes import items
from api.core.config import settings

app = FastAPI(title="Sample")
app.include_router(items.router, prefix=settings.API_V1_STR)

legacy = Blueprint("legacy", __name__, url_prefix="/legacy")


@legacy.route("/health", methods=["GET", "HEAD"])
def health():
    return {"ok": True}

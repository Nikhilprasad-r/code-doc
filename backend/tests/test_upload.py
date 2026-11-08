"""Zip uploads: safe extraction (unit), and an uploaded archive indexed end to end through the API
(integration)."""

import asyncio
import io
import shutil
import zipfile
from pathlib import Path

import httpx
import pytest

from app import main
from app.config import get_settings
from app.ingest.pipeline import index_repo
from app.ingest.source import SourceError, extract_zip
from app.retrieval.embeddings import HashEmbedder, NoopReranker
from tests.conftest import FIXTURE_REPO


def _zip(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, text in entries.items():
            zf.writestr(name, text)
    return buffer.getvalue()


def test_extract_zip_rejects_paths_outside_the_folder(tmp_path: Path):
    archive = tmp_path / "evil.zip"
    archive.write_bytes(_zip({"ok.py": "x = 1", "../escaped.py": "y = 2"}))
    with pytest.raises(SourceError, match="Unsafe path"):
        extract_zip(archive, tmp_path / "out")
    assert not (tmp_path / "escaped.py").exists()


def test_extract_zip_unwraps_a_single_top_level_folder(tmp_path: Path):
    archive = tmp_path / "project.zip"
    archive.write_bytes(_zip({"project/app.py": "x = 1", "__MACOSX/project/._app.py": ""}))
    root = extract_zip(archive, tmp_path / "out")
    assert root == tmp_path / "out" / "project"
    assert (root / "app.py").read_text() == "x = 1"


@pytest.mark.integration
async def test_an_uploaded_zip_is_indexed_like_the_folder_it_came_from(indexed_repo, monkeypatch):
    """The fixture repository uploaded as a zip, through the API's in-process indexing, gives the same
    index as the same files read from disk (the `indexed_repo` fixture)."""
    _, folder_stats, _ = indexed_repo
    archive = _zip({f"sample_repo/{p.relative_to(FIXTURE_REPO)}": p.read_text()
                    for p in sorted(FIXTURE_REPO.rglob("*")) if p.is_file()})
    monkeypatch.setattr(main.app.state, "arq", None, raising=False)  # no Redis: index in this process
    # Offline embeddings and no reranking, so the test never calls Voyage, even when a key is configured.
    embedder = HashEmbedder(get_settings().embed_dim)
    monkeypatch.setattr(main, "index_repo", lambda repo_id: index_repo(repo_id, embedder=embedder))
    monkeypatch.setattr(main, "get_embedder", lambda _settings: embedder)
    monkeypatch.setattr(main, "get_reranker", lambda _settings: NoopReranker())

    data_dir = get_settings().data_dir
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        rejected = await client.post("/repos/upload", files={"file": ("notes.txt", b"hello", "text/plain")})
        assert rejected.status_code == 400

        created = await client.post("/repos/upload", files={"file": ("sample_repo.zip", archive, "application/zip")})
        assert created.status_code == 202
        repo_id = created.json()["id"]
        try:
            assert created.json()["name"] == "sample_repo"
            await asyncio.gather(*main._background)

            repo = (await client.get(f"/repos/{repo_id}")).json()
            assert repo["status"] == "ready", repo["error"]
            keys = ("files", "chunks", "symbols", "endpoints", "dependencies")
            assert {k: repo["stats"][k] for k in keys} == {k: folder_stats[k] for k in keys}

            search = await client.post(f"/repos/{repo_id}/search", json={"query": "ItemService archive", "k": 3})
            assert "api/routes/items.py" in [hit["path"] for hit in search.json()]
            readme = await client.get(f"/repos/{repo_id}/file", params={"path": "README.md"})
            assert readme.status_code == 200
        finally:
            await client.delete(f"/repos/{repo_id}")
            (data_dir / "uploads" / f"{repo_id}.zip").unlink(missing_ok=True)
            for folder in ("repos", "graphs"):
                shutil.rmtree(data_dir / folder / str(repo_id), ignore_errors=True)

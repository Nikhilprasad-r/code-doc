"""The MCP server, driven by the SDK's in-memory client against the indexed fixture repository."""

import pytest
from mcp.client import Client

from app.config import get_settings
from app.db import connection
from app.mcp_server import server
from tests.conftest import FIXTURE_REPO

pytestmark = pytest.mark.integration

EXPECTED_TOOLS = {"list_repositories", "index_repository", "ask_codebase", "repo_overview", "search_code",
                  "read_file", "grep", "find_symbol", "list_endpoints", "list_dependencies", "find_imports",
                  "architecture", "related", "impact"}


async def call(name: str, args: dict) -> tuple[bool, str]:
    async with Client(server) as client:
        result = await client.call_tool(name, args)
    return result.is_error, result.content[0].text


async def test_tools_are_listed_with_schemas_and_annotations():
    async with Client(server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == EXPECTED_TOOLS
    assert tools["search_code"].input_schema["required"] == ["query"]
    assert "repo" in tools["search_code"].input_schema["properties"]
    assert tools["grep"].annotations.read_only_hint is True
    assert tools["index_repository"].annotations.read_only_hint is False


async def test_code_tools_answer_from_the_index(indexed_repo):
    repo_id, _, _ = indexed_repo
    error, text = await call("list_repositories", {})
    assert not error and f"(id {repo_id}, ready" in text

    error, text = await call("find_symbol", {"repo": repo_id, "name": "ItemService.archive"})
    assert not error and "api/routes/items.py:42-45" in text

    error, text = await call("list_endpoints", {"repo": str(repo_id), "route_contains": "owners"})
    assert not error and "/owners/{ownerId}" in text and text.startswith("[sample-test @")

    error, text = await call("read_file", {"repo": repo_id, "path": "api/routes/items.py", "start_line": 28,
                                           "end_line": 30})
    assert not error and "28  @router.delete" in text


async def test_errors_are_tool_errors_not_crashes(indexed_repo):
    error, text = await call("read_file", {"repo": "no-such-repo", "path": "x.py"})
    assert error and "No ready repository matches 'no-such-repo'" in text
    repo_id, _, _ = indexed_repo
    error, text = await call("read_file", {"repo": repo_id, "path": "missing.py"})
    assert error and "File not found" in text


async def test_ask_codebase_reports_missing_credentials_cleanly(indexed_repo):
    if get_settings().anthropic_api_key:
        pytest.skip("an Anthropic key is configured; this test checks the no-key message")
    repo_id, _, _ = indexed_repo
    error, text = await call("ask_codebase", {"repo": repo_id, "question": "How are items archived?"})
    assert error and "ANTHROPIC_API_KEY" in text


async def test_index_repository_reuses_an_existing_source(indexed_repo):
    repo_id, _, _ = indexed_repo
    error, text = await call("index_repository", {"source": str(FIXTURE_REPO.resolve())})
    assert not error and f"(id {repo_id})" in text and "endpoints" in text
    async with connection() as conn:
        count = (await (await conn.execute(
            "SELECT count(*) AS n FROM repos WHERE source = %s", [str(FIXTURE_REPO.resolve())])).fetchone())["n"]
    assert count == 1

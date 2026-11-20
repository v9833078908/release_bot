import httpx
import respx

from app.github import GitHub, RawCommit

API = "https://api.github.com"


@respx.mock
async def test_commits_in_range():
    respx.get(f"{API}/repos/o/r/compare/base...head").mock(
        return_value=httpx.Response(200, json={"commits": [
            {"sha": "s1", "commit": {"message": "feat(x): a"}, "parents": [{"sha": "base"}]},
            {"sha": "s2", "commit": {"message": "fix(y): b"}, "parents": [{"sha": "s1"}]},
        ]}))
    gh = GitHub("tok", "o/r")
    assert await gh.commits_in_range("base", "head") == [("s1", "feat(x): a"), ("s2", "fix(y): b")]


@respx.mock
async def test_commits_in_range_missing_base_returns_none():
    respx.get(f"{API}/repos/o/r/compare/base...head").mock(return_value=httpx.Response(404))
    gh = GitHub("tok", "o/r")
    assert await gh.commits_in_range("base", "head") is None


@respx.mock
async def test_compare_commits_captures_parents():
    respx.get(f"{API}/repos/o/r/compare/base...head").mock(
        return_value=httpx.Response(200, json={"commits": [
            {"sha": "s1", "commit": {"message": "m1"}, "parents": [{"sha": "p1"}, {"sha": "p2"}]},
        ]}))
    gh = GitHub("tok", "o/r")
    assert await gh.compare_commits("base", "head") == [RawCommit("s1", "m1", ("p1", "p2"))]


@respx.mock
async def test_enrich_commit_files_and_file_text():
    respx.get(f"{API}/repos/o/r/commits/s1").mock(
        return_value=httpx.Response(200, json={"files": [{"filename": "frontend/src/App.js"}]})
    )
    respx.get(f"{API}/repos/o/r/commits/s2").mock(return_value=httpx.Response(404))
    respx.get(f"{API}/repos/o/r/contents/infra/rollout-flags.env", params={"ref": "head"}).mock(
        return_value=httpx.Response(200, text="MCP_ENABLED=false\n")
    )
    gh = GitHub("tok", "o/r")
    files = await gh.enrich_commit_files(["s1", "s2"])
    assert files == {"s1": ("frontend/src/App.js",), "s2": ()}
    assert await gh.file_text("infra/rollout-flags.env", "head") == "MCP_ENABLED=false\n"

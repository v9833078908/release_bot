import httpx
import respx

from app.github import GitHub

API = "https://api.github.com"


@respx.mock
async def test_commits_in_range():
    respx.get(f"{API}/repos/o/r/compare/base...head").mock(
        return_value=httpx.Response(200, json={"commits": [
            {"sha": "s1", "commit": {"message": "feat(x): a"}},
            {"sha": "s2", "commit": {"message": "fix(y): b"}},
        ]}))
    gh = GitHub("tok", "o/r")
    assert await gh.commits_in_range("base", "head") == [("s1", "feat(x): a"), ("s2", "fix(y): b")]


@respx.mock
async def test_commits_in_range_missing_base_returns_none():
    respx.get(f"{API}/repos/o/r/compare/base...head").mock(return_value=httpx.Response(404))
    gh = GitHub("tok", "o/r")
    assert await gh.commits_in_range("base", "head") is None

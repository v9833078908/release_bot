import httpx
import respx

from app.filter import Commit
from app.llm import build_user_message, draft_release_notes, load_prompt
from app.models import Post


def test_load_prompt_nonempty():
    assert "Game Pulse" in load_prompt()


def test_build_user_message_lists_commits_and_hint():
    msg = build_user_message([Commit("s", "feat", "topics", "add card", False)], "короче")
    assert "feat" in msg and "add card" in msg and "короче" in msg


@respx.mock
async def test_draft_release_notes_parses_json_to_post():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content":
            '{"themes":[{"title":"T","body":"B"}],"fixes":["f"]}'}}]}))
    out = await draft_release_notes("key", "model", [Commit("s", "feat", "x", "y", False)])
    assert isinstance(out, Post)
    assert out.themes[0].title == "T" and out.themes[0].body == "B"
    assert out.fixes == ["f"]


@respx.mock
async def test_draft_release_notes_strips_code_fence():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content":
            '```json\n{"themes":[],"fixes":["x"]}\n```'}}]}))
    out = await draft_release_notes("key", "model", [])
    assert out.fixes == ["x"]


@respx.mock
async def test_draft_release_notes_retries_once_on_bad_json():
    good = '{"themes":[{"title":"ok","body":"b"}],"fixes":[]}'
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": '{"themes":[{"tit'}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": good}}]}),
        ])
    out = await draft_release_notes("key", "model", [])
    assert out.themes[0].title == "ok"


@respx.mock
async def test_draft_release_notes_retries_on_null_content():
    good = '{"themes":[{"title":"ok","body":"b"}],"fixes":[]}'
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": good}}]}),
        ])
    out = await draft_release_notes("key", "model", [])
    assert out.themes[0].title == "ok"


@respx.mock
async def test_draft_release_notes_drops_blank_themes():
    # a whitespace-only theme must not survive as a non-empty list, or generate's
    # is_empty guard is bypassed and a header-only shell renders.
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content":
            '{"themes":[{"title":" ","body":" "}],"fixes":[" "]}'}}]}))
    out = await draft_release_notes("key", "model", [])
    assert out.themes == []
    assert out.fixes == []
    assert out.is_empty

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
            '{"intro":"i","features":["f"],"improvements":[],"fixes_summary":null}'}}]}))
    out = await draft_release_notes("key", "model", [Commit("s", "feat", "x", "y", False)])
    assert isinstance(out, Post)
    assert out.intro == "i" and out.features == ["f"] and out.fixes_summary is None


@respx.mock
async def test_draft_release_notes_strips_code_fence():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content":
            '```json\n{"intro":"i","features":[],"improvements":[],"fixes_summary":"x"}\n```'}}]}))
    out = await draft_release_notes("key", "model", [])
    assert out.fixes_summary == "x"

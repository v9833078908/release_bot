import httpx
import respx

from app.filter import Commit
from app.llm import _parse_post, build_user_message, draft_release_notes, load_prompt
from app.models import Post


def test_load_prompt_nonempty():
    prompt = load_prompt()
    assert "Game Pulse" in prompt
    assert "source_shas" in prompt
    # Regression guard: no hardcoded July release boilerplate to parrot
    assert "Отчёты о расходах и токенах" not in prompt
    assert "веб-скрапера" not in prompt


def test_build_user_message_includes_sha_surfaces_details_context_and_hint():
    c = Commit(
        "abcd1234ef", "feat", "store-ratings", "add CSV export",
        body="Export all reviews from page",
        surfaces=("/store-ratings (Рейтинги сторов)",),
    )
    msg = build_user_message([c], "короче", context="MCP_ENABLED: OFF (false)")
    assert "[abcd1234]" in msg
    assert "add CSV export" in msg
    assert "surfaces: /store-ratings" in msg
    assert "details: Export all reviews" in msg
    assert "MCP_ENABLED: OFF (false)" in msg
    assert "короче" in msg


def test_parse_post_drops_ungrounded_items_when_source_shas_invalid():
    raw = (
        '{"themes":['
        '{"title":"Реальная фича","body":"Описание","source_shas":["abcd1234"]},'
        '{"title":"Галлюцинация","body":"Выдумано","source_shas":["deadbeef"]},'
        '{"title":"Без коммита","body":"Пусто","source_shas":[]}'
        '],"fixes":['
        '{"text":"Реальный фикс","source_shas":["abcd1234"]},'
        '{"text":"Выдуманный фикс","source_shas":["00000000"]}'
        ']}'
    )
    post = _parse_post(raw, known_shas={"abcd1234567890"})
    assert [t.title for t in post.themes] == ["Реальная фича"]
    assert post.fixes == ["Реальный фикс"]


@respx.mock
async def test_draft_release_notes_parses_json_to_post():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content":
            '{"themes":[{"title":"T.","body":"B","source_shas":["s1234567"]}],"fixes":[{"text":"f","source_shas":["s1234567"]}]}'}}]}))
    out = await draft_release_notes("key", "model", [Commit("s123456789", "feat", "x", "y", False)])
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
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content":
            '{"themes":[{"title":" ","body":" "}],"fixes":[" "]}'}}]}))
    out = await draft_release_notes("key", "model", [])
    assert out.themes == []
    assert out.fixes == []
    assert out.is_empty

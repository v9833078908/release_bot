import json
from dataclasses import asdict

from app.filter import Commit, filter_commits
from app.formatter import render_html


def is_publishable(draft_to_sha: str, prod_sha: str | None) -> bool:
    """A draft may be published only when its target is the commit live on prod."""
    return prod_sha is not None and draft_to_sha == prod_sha


def publish_block_reason(draft_to_sha: str, prod_sha: str | None, trigger: str = "deploy") -> str | None:
    """None if the draft is publishable; otherwise a human message saying why not.

    `trigger` distinguishes direction: a "preview" draft targets main HEAD, which is
    AHEAD of prod (not deployed yet); any other trigger going stale means prod has
    since moved PAST the draft's target. SHA inequality alone can't tell those apart.
    """
    if prod_sha is None:
        return "Не могу получить текущий прод-SHA, попробуй позже."
    if is_publishable(draft_to_sha, prod_sha):
        return None
    if trigger == "preview":
        return ("Пока нельзя опубликовать: изменения ещё не на проде (превью). "
                "Опубликуй после прод-деплоя.")
    return (f"Нельзя опубликовать: цель черновика {draft_to_sha[:8]} != текущий прод "
            f"{prod_sha[:8]}. Прод ушёл вперёд - отмени черновик, бот соберёт новый "
            f"по полному диапазону.")


async def generate_draft(*, trigger, store, github, get_prod_sha, settings, llm, hint=None,
                         to_sha=None) -> dict:
    from_sha = store.get_marker()
    if to_sha is None:
        to_sha = await get_prod_sha()
        if to_sha is None:
            return {"result": "no_prod_sha"}
    if to_sha == from_sha:
        return {"result": "no_changes", "commit_count": 0}

    raw = await github.commits_in_range(from_sha, to_sha)
    if raw is None:
        raw = await github.commits_since(to_sha, store.get_last_published_at())

    commits = filter_commits(raw, tuple(settings.feature_prefix_list))
    features = [c for c in commits if c.type == "feat"]
    n, fcount = len(commits), len(features)
    raw_dump = [asdict(c) for c in commits]

    if trigger == "scheduled" and fcount < settings.min_features_to_publish:
        store.create_draft(status="skipped", trigger=trigger, from_sha=from_sha, to_sha=to_sha,
                           commit_count=n, feature_count=fcount, raw_commits=raw_dump, draft_text="")
        return {"result": "skipped", "commit_count": n, "feature_count": fcount}

    if n == 0:
        return {"result": "no_changes", "commit_count": 0}

    post = await llm(settings.openrouter_api_key, settings.llm_model, commits, hint)
    text = render_html(post)
    draft_id = store.create_draft(status="pending", trigger=trigger, from_sha=from_sha, to_sha=to_sha,
                                  commit_count=n, feature_count=fcount, raw_commits=raw_dump, draft_text=text)
    return {"result": "drafted", "draft_id": draft_id, "commit_count": n,
            "feature_count": fcount, "text": text}


async def regenerate_draft(*, store, draft_id, settings, llm, hint=None) -> str:
    d = store.get_draft(draft_id)
    commits = [Commit(**c) for c in json.loads(d["raw_commits"])]
    post = await llm(settings.openrouter_api_key, settings.llm_model, commits, hint)
    text = render_html(post)
    store.set_draft_text(draft_id, text)
    return text

import json
from dataclasses import asdict

from app.filter import Commit, filter_commits
from app.formatter import render_html


async def generate_draft(*, trigger, store, github, get_prod_sha, settings, llm, hint=None) -> dict:
    from_sha = store.get_marker()
    to_sha = await get_prod_sha()
    if to_sha is None:
        return {"result": "no_prod_sha"}
    if to_sha == from_sha:
        return {"result": "no_changes", "commit_count": 0}

    raw = await github.commits_in_range(from_sha, to_sha)
    if raw is None:
        raw = await github.commits_since(to_sha, store.get_last_published_at())

    commits = filter_commits(raw)
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

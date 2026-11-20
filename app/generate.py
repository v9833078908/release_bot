import inspect
import json
from dataclasses import asdict

from app.filter import (
    Commit,
    collapse_commits,
    filter_commits,
    is_feature_commit,
    prioritize_for_enrichment,
)
from app.formatter import render_html
from app.rollout import build_rollout_context


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


def _call_llm(llm, api_key: str, model: str, commits: list[Commit], hint: str | None, context: str | None):
    sig = inspect.signature(llm)
    if "context" in sig.parameters or any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    ):
        return llm(api_key, model, commits, hint, context=context)
    return llm(api_key, model, commits, hint)


async def prepare_release_inputs(
    github,
    from_sha: str,
    to_sha: str,
    *,
    feature_prefixes: tuple[str, ...],
    since_iso: str | None = None,
) -> tuple[list[tuple[str, str]], list[Commit], str]:
    """Fetch, file-enrich, filter, collapse commits and build rollout-flag context."""
    raw = await github.commits_in_range(from_sha, to_sha)
    if raw is None and since_iso is not None:
        raw = await github.commits_since(to_sha, since_iso)
    raw = raw or []

    candidates = filter_commits(raw, feature_prefixes)
    if candidates and hasattr(github, "enrich_commit_files"):
        to_enrich = prioritize_for_enrichment(candidates, limit=90)
        files_by_sha = await github.enrich_commit_files([c.sha for c in to_enrich])
        if files_by_sha:
            kept_shas = {c.sha for c in candidates}
            filtered_raw = [(sha, msg) for sha, msg in raw if sha in kept_shas]
            candidates = filter_commits(filtered_raw, feature_prefixes, files_by_sha=files_by_sha)

    commits = collapse_commits(candidates)

    rollout_context = ""
    if commits and hasattr(github, "file_text"):
        base_flags = await github.file_text("infra/rollout-flags.env", from_sha)
        head_flags = await github.file_text("infra/rollout-flags.env", to_sha)
        rollout_context = build_rollout_context(base_flags, head_flags)

    return raw, commits, rollout_context


async def generate_draft(*, trigger, store, github, get_prod_sha, settings, llm, hint=None,
                         to_sha=None) -> dict:
    from_sha = store.get_marker()
    if to_sha is None:
        to_sha = await get_prod_sha()
        if to_sha is None:
            return {"result": "no_prod_sha"}
    if to_sha == from_sha:
        return {"result": "no_changes", "commit_count": 0}

    raw, commits, rollout_context = await prepare_release_inputs(
        github,
        from_sha,
        to_sha,
        feature_prefixes=tuple(settings.feature_prefix_list),
        since_iso=store.get_last_published_at(),
    )

    features = [c for c in commits if is_feature_commit(c)]
    n, fcount = len(commits), len(features)
    raw_dump = [asdict(c) for c in commits]

    if trigger == "scheduled" and fcount < settings.min_features_to_publish:
        store.create_draft(status="skipped", trigger=trigger, from_sha=from_sha, to_sha=to_sha,
                           commit_count=n, feature_count=fcount, raw_commits=raw_dump,
                           rollout_context=rollout_context, draft_text="")
        return {"result": "skipped", "commit_count": n, "feature_count": fcount}

    if n == 0:
        if raw:
            dropped = [((m.splitlines() or [""])[0]).strip()[:100] for _, m in raw][:15]
            return {"result": "no_release_worthy", "commit_count": 0, "raw_count": len(raw),
                    "dropped": dropped, "from_sha": from_sha, "to_sha": to_sha}
        return {"result": "no_changes", "commit_count": 0}

    post = await _call_llm(llm, settings.openrouter_api_key, settings.llm_model, commits, hint, rollout_context)
    if post.is_empty:
        subjects = [f"{c.type}({c.scope or '-'}): {c.subject}" for c in commits][:15]
        return {"result": "no_user_facing", "commit_count": n, "feature_count": fcount,
                "from_sha": from_sha, "to_sha": to_sha, "dropped": subjects}
    text = render_html(post)
    draft_id = store.create_draft(status="pending", trigger=trigger, from_sha=from_sha, to_sha=to_sha,
                                  commit_count=n, feature_count=fcount, raw_commits=raw_dump,
                                  rollout_context=rollout_context, draft_text=text)
    return {"result": "drafted", "draft_id": draft_id, "commit_count": n,
            "feature_count": fcount, "text": text}


async def regenerate_draft(*, store, draft_id, settings, llm, hint=None) -> str | None:
    d = store.get_draft(draft_id)
    commits = [Commit(**c) for c in json.loads(d["raw_commits"])]
    rollout_context = d.get("rollout_context") or ""
    post = await _call_llm(llm, settings.openrouter_api_key, settings.llm_model, commits, hint, rollout_context)
    if post.is_empty:
        return None   # keep the existing text; caller reports "no user-facing change"
    text = render_html(post)
    store.set_draft_text(draft_id, text)
    return text

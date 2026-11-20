import argparse
import asyncio
import json

from app.config import get_settings
from app.filter import is_feature_commit
from app.formatter import finalize_publish, render_html
from app.generate import prepare_release_inputs
from app.github import GitHub
from app.llm import build_user_message, draft_release_notes
from app.prod import fetch_prod_sha
from app.store import Store


async def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run release notes generation.")
    parser.add_argument("--from-sha", help="Base commit SHA (defaults to store marker)")
    parser.add_argument("--to-sha", help="Head commit SHA (defaults to prod /version)")
    parser.add_argument("--model", help="Override OpenRouter model slug")
    parser.add_argument("--hint", help="Optional editor hint")
    parser.add_argument("--show-inputs", action="store_true", help="Print filtered commits and flags context")
    args = parser.parse_args()

    s = get_settings()
    gh = GitHub(s.github_token, s.github_repo)
    model = args.model or s.llm_model
    since_iso = None

    if args.from_sha and args.to_sha:
        from_sha, to_sha = args.from_sha, args.to_sha
    else:
        store = Store("data/dry_run.db", s.initial_marker_sha)
        from_sha = args.from_sha or store.get_marker()
        to_sha = args.to_sha or await fetch_prod_sha(s.prod_version_url)
        if not to_sha:
            raise SystemExit("Could not fetch prod SHA")
        since_iso = store.get_last_published_at()

    raw, commits, context = await prepare_release_inputs(
        gh,
        from_sha,
        to_sha,
        feature_prefixes=tuple(s.feature_prefix_list),
        since_iso=since_iso,
    )

    print(
        f"Range: {from_sha[:8]}..{to_sha[:8]} | raw={len(raw)} | sent={len(commits)} "
        f"| feats={sum(1 for c in commits if is_feature_commit(c))} | model={model}"
    )
    if args.show_inputs:
        print("=== USER MESSAGE ===")
        print(build_user_message(commits, args.hint, context))
        print("=== END USER MESSAGE ===")

    post = await draft_release_notes(s.openrouter_api_key, model, commits, args.hint, context=context)
    if post.is_empty:
        print("RESULT: no_user_facing")
        print(json.dumps({"raw_count": len(raw), "sent": len(commits)}))
        return
    html = render_html(post)
    print(finalize_publish(html, 0, to_sha, "20.11.2026"))


if __name__ == "__main__":
    asyncio.run(main())

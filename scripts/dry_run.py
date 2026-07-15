import asyncio

from app.config import get_settings
from app.generate import generate_draft
from app.github import GitHub
from app.llm import draft_release_notes
from app.prod import fetch_prod_sha
from app.store import Store


async def main() -> None:
    s = get_settings()
    gh = GitHub(s.github_token, s.github_repo)
    store = Store("data/dry_run.db", s.initial_marker_sha)
    res = await generate_draft(trigger="manual", store=store, github=gh,
                               get_prod_sha=lambda: fetch_prod_sha(s.prod_version_url),
                               settings=s, llm=draft_release_notes)
    print(res.get("result"))
    print(res.get("text", ""))


if __name__ == "__main__":
    asyncio.run(main())

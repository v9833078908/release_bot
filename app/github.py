import httpx

_BASE = "https://api.github.com"


class GitHub:
    def __init__(self, token: str, repo: str):
        self.repo = repo
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def commits_in_range(self, base: str, head: str) -> list[tuple[str, str]] | None:
        out: list[tuple[str, str]] = []
        page = 1
        async with httpx.AsyncClient(timeout=30) as c:
            while True:
                r = await c.get(f"{_BASE}/repos/{self.repo}/compare/{base}...{head}",
                                headers=self._headers, params={"per_page": 100, "page": page})
                if r.status_code == 404:
                    return None
                r.raise_for_status()
                commits = r.json().get("commits", [])
                out.extend((cm["sha"], cm["commit"]["message"]) for cm in commits)
                if len(commits) < 100:
                    break
                page += 1
        return out

    async def commits_since(self, head: str, since_iso: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        page = 1
        async with httpx.AsyncClient(timeout=30) as c:
            while True:
                r = await c.get(f"{_BASE}/repos/{self.repo}/commits", headers=self._headers,
                                params={"sha": head, "since": since_iso, "per_page": 100, "page": page})
                r.raise_for_status()
                commits = r.json()
                out.extend((cm["sha"], cm["commit"]["message"]) for cm in commits)
                if len(commits) < 100:
                    break
                page += 1
        return out

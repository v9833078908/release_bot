import asyncio
from dataclasses import dataclass

import httpx

_BASE = "https://api.github.com"


@dataclass
class RawCommit:
    sha: str
    message: str
    parents: tuple[str, ...] = ()


class GitHub:
    def __init__(self, token: str, repo: str):
        self.repo = repo
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def compare_commits(self, base: str, head: str) -> list[RawCommit] | None:
        out: list[RawCommit] = []
        page = 1
        async with httpx.AsyncClient(timeout=30) as c:
            while True:
                r = await c.get(
                    f"{_BASE}/repos/{self.repo}/compare/{base}...{head}",
                    headers=self._headers,
                    params={"per_page": 100, "page": page},
                )
                if r.status_code == 404:
                    return None
                r.raise_for_status()
                commits = r.json().get("commits", [])
                for cm in commits:
                    parents = tuple(p["sha"] for p in (cm.get("parents") or ()) if p.get("sha"))
                    out.append(RawCommit(cm["sha"], cm["commit"]["message"], parents))
                if len(commits) < 100:
                    break
                page += 1
        return out

    async def commits_since_rich(self, head: str, since_iso: str) -> list[RawCommit]:
        out: list[RawCommit] = []
        page = 1
        async with httpx.AsyncClient(timeout=30) as c:
            while True:
                r = await c.get(
                    f"{_BASE}/repos/{self.repo}/commits",
                    headers=self._headers,
                    params={"sha": head, "since": since_iso, "per_page": 100, "page": page},
                )
                r.raise_for_status()
                commits = r.json()
                for cm in commits:
                    parents = tuple(p["sha"] for p in (cm.get("parents") or ()) if p.get("sha"))
                    out.append(RawCommit(cm["sha"], cm["commit"]["message"], parents))
                if len(commits) < 100:
                    break
                page += 1
        out.reverse()  # match compare order (oldest first)
        return out

    async def commits_in_range(self, base: str, head: str) -> list[tuple[str, str]] | None:
        commits = await self.compare_commits(base, head)
        return None if commits is None else [(c.sha, c.message) for c in commits]

    async def commits_since(self, head: str, since_iso: str) -> list[tuple[str, str]]:
        commits = await self.commits_since_rich(head, since_iso)
        return [(c.sha, c.message) for c in reversed(commits)]

    async def enrich_commit_files(self, shas: list[str], *, concurrency: int = 8) -> dict[str, tuple[str, ...]]:
        if not shas:
            return {}
        sem = asyncio.Semaphore(concurrency)
        out: dict[str, tuple[str, ...]] = {}
        async with httpx.AsyncClient(timeout=30) as c:
            async def _fetch(sha: str) -> None:
                async with sem:
                    try:
                        r = await c.get(
                            f"{_BASE}/repos/{self.repo}/commits/{sha}",
                            headers=self._headers,
                        )
                        if r.status_code != 200:
                            out[sha] = ()
                            return
                        files = tuple(f["filename"] for f in (r.json().get("files") or ()) if f.get("filename"))
                        out[sha] = files
                    except httpx.HTTPError:
                        out[sha] = ()

            await asyncio.gather(*(_fetch(s) for s in shas))
        return out

    async def file_text(self, path: str, ref: str) -> str | None:
        headers = {**self._headers, "Accept": "application/vnd.github.raw+json"}
        async with httpx.AsyncClient(timeout=30) as c:
            try:
                r = await c.get(
                    f"{_BASE}/repos/{self.repo}/contents/{path}",
                    headers=headers,
                    params={"ref": ref},
                )
            except httpx.HTTPError:
                return None
            if r.status_code != 200:
                return None
            return r.text

    async def head_sha(self, branch: str = "main") -> str | None:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(
                f"{_BASE}/repos/{self.repo}/commits/{branch}",
                headers=self._headers,
                params={"per_page": 1},
            )
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()["sha"]

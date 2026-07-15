import httpx


async def fetch_prod_sha(url: str) -> str | None:
    async with httpx.AsyncClient(timeout=30) as c:
        try:
            r = await c.get(url)
        except httpx.HTTPError:
            return None
    if r.status_code != 200:
        return None
    return r.json().get("sha")

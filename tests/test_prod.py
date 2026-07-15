import httpx
import respx

from app.prod import fetch_prod_sha

URL = "https://tools.herocraft.com/api/v1/version"


@respx.mock
async def test_fetch_prod_sha_ok():
    respx.get(URL).mock(return_value=httpx.Response(200, json={"sha": "abc"}))
    assert await fetch_prod_sha(URL) == "abc"


@respx.mock
async def test_fetch_prod_sha_error_returns_none():
    respx.get(URL).mock(return_value=httpx.Response(502))
    assert await fetch_prod_sha(URL) is None


@respx.mock
async def test_fetch_prod_sha_network_error_returns_none():
    respx.get(URL).mock(side_effect=httpx.ConnectError("boom"))
    assert await fetch_prod_sha(URL) is None

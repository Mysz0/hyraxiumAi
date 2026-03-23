import pytest
import httpx
import respx
import os


@pytest.fixture
def web_config(test_config):
    os.environ["SEARXNG_HOST"] = "http://searx.test"
    from importlib import reload
    import hyrax.config as m
    reload(m)
    cfg = m.Settings()
    yield cfg
    os.environ.pop("SEARXNG_HOST", None)


@pytest.fixture
def web(web_config):
    from hyrax.web import Web
    return Web(web_config)


async def test_search_returns_results(web):
    mock_response = {
        "results": [
            {"title": "Result 1", "url": "http://example.com/1", "content": "snippet 1"},
            {"title": "Result 2", "url": "http://example.com/2", "content": "snippet 2"},
        ]
    }
    with respx.mock:
        respx.get("http://searx.test/search").mock(return_value=httpx.Response(200, json=mock_response))
        results = await web.search("python asyncio")
    assert len(results) == 2
    assert results[0].title == "Result 1"
    assert results[0].url == "http://example.com/1"
    assert results[0].snippet == "snippet 1"


async def test_search_returns_empty_on_timeout(web):
    with respx.mock:
        respx.get("http://searx.test/search").mock(side_effect=httpx.TimeoutException("slow"))
        assert await web.search("anything") == []


async def test_search_returns_empty_on_http_error(web):
    with respx.mock:
        respx.get("http://searx.test/search").mock(return_value=httpx.Response(500))
        assert await web.search("anything") == []


async def test_fetch_page_returns_stripped_text(web):
    html = "<html><body><p>Hello world</p><script>bad js</script></body></html>"
    with respx.mock:
        respx.get("http://example.com/page").mock(return_value=httpx.Response(200, text=html))
        text = await web.fetch_page("http://example.com/page")
    assert "Hello world" in text
    assert "bad js" not in text


async def test_fetch_page_truncates_at_3000_chars(web):
    html = f"<html><body><p>{'a' * 5000}</p></body></html>"
    with respx.mock:
        respx.get("http://example.com/page").mock(return_value=httpx.Response(200, text=html))
        text = await web.fetch_page("http://example.com/page")
    assert len(text) <= 3000


async def test_fetch_page_returns_empty_on_timeout(web):
    with respx.mock:
        respx.get("http://example.com/page").mock(side_effect=httpx.TimeoutException("slow"))
        assert await web.fetch_page("http://example.com/page") == ""


async def test_search_raises_when_disabled(test_config):
    from hyrax.web import Web, WebSearchDisabledError
    web = Web(test_config)
    with pytest.raises(WebSearchDisabledError):
        await web.search("anything")

# src/hyrax/web.py
"""
Web capabilities for Hyrax.

Searches via SearXNG and fetches/scrapes pages with BeautifulSoup.
Both operations are disabled and guarded when SEARXNG_HOST is not configured.
"""
from dataclasses import dataclass

import httpx
import structlog
from bs4 import BeautifulSoup

log = structlog.get_logger()

_PAGE_CHAR_LIMIT = 3000
_RESEARCH_PAGE_CHAR_LIMIT = 8000
_SEARCH_TIMEOUT = 10.0
_FETCH_TIMEOUT = 15.0


class WebSearchDisabledError(Exception):
    """Raised if web search is called when SEARXNG_HOST is not configured."""


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class Web:
    def __init__(self, config) -> None:
        self._config = config
        self._client = httpx.AsyncClient(timeout=_SEARCH_TIMEOUT)

    def _require_enabled(self) -> None:
        if not self._config.web_search_enabled:
            raise WebSearchDisabledError(
                "Web search is disabled — set SEARXNG_HOST to enable it."
            )

    async def search(self, query: str) -> list[SearchResult]:
        """Search SearXNG and return top 5 results."""
        self._require_enabled()
        try:
            response = await self._client.get(
                f"{self._config.searxng_host}/search",
                params={"q": query, "format": "json"},
                timeout=_SEARCH_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            return [
                SearchResult(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("content", r.get("snippet", "")),
                )
                for r in data.get("results", [])[:5]
            ]
        except httpx.TimeoutException:
            log.warning("web.search timeout", query=query)
            return []
        except httpx.HTTPError as exc:
            log.warning("web.search http_error", error=str(exc))
            return []
        except Exception as exc:
            log.warning("web.search failed", error=str(exc))
            return []

    async def fetch_page(self, url: str, char_limit: int = _PAGE_CHAR_LIMIT) -> str:
        """Fetch a URL, strip HTML, return up to char_limit chars of body text."""
        try:
            response = await self._client.get(url, timeout=_FETCH_TIMEOUT)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            for tag in soup(["script", "style", "nav", "footer"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)
            return text[:char_limit]
        except httpx.TimeoutException:
            log.warning("web.fetch_page timeout", url=url)
            return ""
        except Exception as exc:
            log.warning("web.fetch_page failed", url=url, error=str(exc))
            return ""

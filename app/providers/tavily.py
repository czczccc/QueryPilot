"""Tavily 搜索适配器：认证、请求、字段转换与错误映射。"""

import logging

import httpx

from app.models import RawSearchResult
from app.providers.base import ProviderError

logger = logging.getLogger(__name__)

TAVILY_URL = "https://api.tavily.com/search"
USER_AGENT = "QueryPilot/0.1 (https://github.com/querypilot)"


def _relevance(item: dict) -> float | None:
    try:
        return float(item["score"])
    except (KeyError, TypeError, ValueError):
        return None


class TavilyProvider:
    """将 Tavily 搜索结果转换为统一的 `RawSearchResult`。"""

    name = "tavily"

    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = 8.0,
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._timeout = timeout

    async def search(self, query: str, limit: int = 8) -> list[RawSearchResult]:
        if not self._api_key:
            raise ProviderError(self.name, "missing_key", "TAVILY_API_KEY 未配置")
        payload = {
            "query": query,
            "max_results": max(1, min(limit, 10)),
            "search_depth": "basic",
            "include_answer": False,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": USER_AGENT,
        }
        try:
            if self._client is not None:
                resp = await self._client.post(TAVILY_URL, json=payload, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(TAVILY_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException as exc:
            raise ProviderError(self.name, "timeout") from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderError(self.name, f"http_{exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, "network") from exc
        except ValueError as exc:  # 非 JSON 响应
            raise ProviderError(self.name, "bad_response") from exc

        results = []
        for item in data.get("results", []):
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            results.append(
                RawSearchResult(
                    title=str(item.get("title") or "").strip() or "(无标题)",
                    url=url,
                    snippet=str(item.get("content") or "").strip(),
                    provider=self.name,
                    relevance=_relevance(item),
                )
            )
        return results

"""搜索提供方统一接口与类型化错误。

第三方供应商的字段只允许出现在各自适配器内部，核心层只认识
`app.models.RawSearchResult`。
"""

from typing import Protocol

from app.models import RawSearchResult


class ProviderError(Exception):
    """搜索提供方错误（HTTP/限流/超时/解析），不含密钥或内部堆栈。"""

    def __init__(self, provider: str, error_type: str, message: str = "") -> None:
        super().__init__(message or f"{provider} provider error: {error_type}")
        self.provider = provider
        self.error_type = error_type


class SearchProvider(Protocol):
    """统一搜索提供方接口。"""

    name: str

    async def search(self, query: str, limit: int) -> list[RawSearchResult]:
        """返回统一原始结果，或抛出 `ProviderError`。"""
        ...

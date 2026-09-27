"""评测跑法：真实联网录制（--live）或离线回放录制（--replay）。

录制在 httpx 最底层（`AsyncClient.send`）做：所有外部请求（搜索源、夸克验证、TMDB/豆瓣、LLM）
的响应按「方法 + 地址 + 请求体」存进 evals/recordings/<id>.json，回放时原样返回，
所以回放不联网、不要任何 key，结果稳定，能放进 CI 当回归检查。

录制文件里**不存请求头**（cookie、Authorization 都在头里），请求体里的 api_key 字段也会去掉。
"""

import asyncio
import contextlib
import hashlib
import json
import socket
from collections.abc import Iterator
from pathlib import Path

import httpx

from app.models import QuarkLink, SearchRequest
from app.services.agent import SearchAgent
from app.services.intent import DeepSeekParser
from app.services.memory import LinkStore
from app.services.metadata import MetadataLookup
from app.services.search import QuarkSearchService
from evals.metrics import Case

ROOT = Path(__file__).resolve().parent
RECORDINGS = ROOT / "recordings"
SECRET_KEYS = {"api_key", "apikey", "key", "token", "cookie", "passcode"}


def load_cases(path: Path = ROOT / "titles.json") -> list[Case]:
    return [Case(**c) for c in json.loads(path.read_text(encoding="utf-8"))]


def load_labels(path: Path = ROOT / "labels.json") -> dict[str, dict[str, bool]]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _clean_body(raw: bytes) -> str:
    """请求体做键：JSON 去掉密钥字段并排序；其它原样（按 utf-8 解码）。"""
    text = raw.decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except ValueError:
        return text
    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if k.lower() not in SECRET_KEYS}
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def _clean_url(url: httpx.URL) -> str:
    params = [(k, v) for k, v in url.params.multi_items() if k.lower() not in SECRET_KEYS]
    return str(url.copy_with(query=None)) + ("?" + str(httpx.QueryParams(params))
                                            if params else "")


def request_key(request: httpx.Request) -> str:
    body = _clean_body(request.content) if request.content else ""
    digest = hashlib.sha1(body.encode()).hexdigest()[:12] if body else "-"
    return f"{request.method} {_clean_url(request.url)} {digest}"


class Tape:
    """一个片名的录制：请求键 → 依次出现过的响应（同一请求多次时按顺序返回，用完重复最后一个）。"""

    def __init__(self, data: dict | None = None):
        self.data: dict = data or {"meta": {}, "exchanges": {}}
        self._served: dict[str, int] = {}
        self.misses: list[str] = []

    def add(self, key: str, response: httpx.Response, content: bytes) -> None:
        self.data["exchanges"].setdefault(key, []).append({
            "status": response.status_code,
            "content_type": response.headers.get("content-type", ""),
            "body": content.decode("utf-8", "replace"),
        })

    def serve(self, request: httpx.Request) -> httpx.Response:
        key = request_key(request)
        items = self.data["exchanges"].get(key)
        if not items:
            self.misses.append(key)
            raise httpx.ConnectError("回放里没有这个请求", request=request)
        i = self._served.get(key, 0)
        self._served[key] = i + 1
        item = items[min(i, len(items) - 1)]
        return httpx.Response(
            item["status"], content=item["body"].encode("utf-8"),
            headers={"content-type": item["content_type"]} if item["content_type"] else {},
            request=request,
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Tape":
        return cls(json.loads(path.read_text(encoding="utf-8")))


@contextlib.contextmanager
def recording(tape: Tape) -> Iterator[None]:
    """真实联网，同时把每个响应录进 tape。"""
    original = httpx.AsyncClient.send

    async def send(self, request, **kwargs):
        response = await original(self, request, **kwargs)
        content = await response.aread()
        tape.add(request_key(request), response, content)
        return httpx.Response(response.status_code, headers=response.headers,
                              content=content, request=request)

    httpx.AsyncClient.send = send
    try:
        yield
    finally:
        httpx.AsyncClient.send = original


@contextlib.contextmanager
def replaying(tape: Tape) -> Iterator[None]:
    """完全离线：请求从 tape 里取；DNS 检查也不联网。"""
    original, original_dns = httpx.AsyncClient.send, socket.getaddrinfo

    async def send(self, request, **kwargs):
        return tape.serve(request)

    httpx.AsyncClient.send = send
    socket.getaddrinfo = lambda host, port, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))]
    try:
        yield
    finally:
        httpx.AsyncClient.send = original
        socket.getaddrinfo = original_dns


def build_agent(meta: dict) -> SearchAgent:
    """和线上 build_default_service 一样的组装，开关由 meta 决定（录制时记下，回放时照搬）。

    回放时 key 用占位值：请求照样发（被 tape 接住），真实 key 从不进录制文件。
    """
    from app.config import load_settings

    settings = load_settings()
    replay = meta.get("replay", False)
    llm_key = ("replay" if replay else settings.deepseek_api_key) if meta.get("llm") else ""
    tavily_key = ("replay" if replay else settings.tavily_api_key) if meta.get("tavily") else ""
    tmdb_key = ("replay" if replay else settings.tmdb_api_key) if meta.get("tmdb") else ""
    from app.providers.tavily import TavilyProvider

    service = QuarkSearchService(
        parser=DeepSeekParser(api_key=llm_key, timeout=settings.request_timeout_seconds),
        tavily=TavilyProvider(api_key=tavily_key, timeout=settings.request_timeout_seconds),
        use_qkyunso=True, use_bing=True, timeout=settings.request_timeout_seconds,
        store=LinkStore(":memory:"),
        extra_sites=tuple(meta.get("extra_sites") or ()),
    )
    lookup = MetadataLookup(tmdb_key=tmdb_key, tmdb_base=settings.tmdb_api_base,
                            douban=bool(meta.get("douban", True)))
    return SearchAgent(service, api_key=llm_key, cache_minutes=0, lookup=lookup)


async def search(agent: SearchAgent, case: Case) -> list[QuarkLink]:
    resp = await agent.run(SearchRequest(query=case.query, refresh=True, client_id="evaluation"),
                           fresh_hours=0)
    return resp.links


def live_meta() -> dict:
    from app.config import load_settings

    s = load_settings()
    return {"llm": bool(s.deepseek_api_key), "tavily": bool(s.tavily_api_key),
            "tmdb": bool(s.tmdb_api_key), "douban": s.douban_lookup,
            "extra_sites": list(s.extra_sites)}


async def run_live(case: Case, record: bool = True) -> list[QuarkLink]:
    meta = live_meta()
    tape = Tape({"meta": {**meta, "query": case.query}, "exchanges": {}})
    with recording(tape):
        links = await search(build_agent(meta), case)
    if record:
        tape.save(RECORDINGS / f"{case.id}.json")
    return links


async def run_replay(case: Case, path: Path | None = None) -> tuple[list[QuarkLink], Tape]:
    tape = Tape.load(path or RECORDINGS / f"{case.id}.json")
    with replaying(tape):
        links = await search(build_agent({**tape.data["meta"], "replay": True}), case)
    return links, tape


def run(coro):
    return asyncio.run(coro)

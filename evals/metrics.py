"""评测打分：拿标准答案（片名、别名、年份、季）当裁判，给搜索结果算指标。

裁判规则刻意和线上的相关性判定分开写、而且更简单：只看夸克分享自己的标题和文件名，
包含标准片名或别名、年份不冲突（±1 年）、季数不冲突，就算「真相关」。
裁判判错的个别链接可以在 labels.json 里人工改判（{case_id: {share: true/false}}）。
"""

import re
from dataclasses import dataclass, field

from app.models import QuarkLink
from app.services.memory import resource_key
from app.services.relevance import contains_name, seasons_in, years_in

_BRACKET = re.compile(r"[《》]")


@dataclass
class Case:
    id: str
    query: str
    title: str
    aliases: list[str] = field(default_factory=list)
    year: str | None = None
    media: str | None = None
    season: int | None = None
    note: str = ""

    @property
    def names(self) -> list[str]:
        keys = [resource_key(_BRACKET.sub("", n)) for n in [self.title, *self.aliases]]
        return [k for k in dict.fromkeys(keys) if len(k) >= 2]


def gold(link: QuarkLink, case: Case, labels: dict[str, bool] | None = None) -> bool | None:
    """这条链接是不是真的要找的那部；拿不到分享内容时返回 None（不计入指标）。"""
    if labels and link.share in labels:
        return labels[link.share]
    texts = [t for t in [link.share_title, *link.files_preview] if t]
    if not texts:
        return None
    joined = " ".join(texts)
    if not any(contains_name(n, resource_key(joined)) for n in case.names):
        return False
    if case.year:
        years = years_in(joined)
        if years and all(abs(y - int(case.year)) > 1 for y in years):
            return False
    if case.season:
        seasons = seasons_in(joined)
        if seasons and case.season not in seasons:
            return False
    return True


@dataclass
class CaseResult:
    id: str
    query: str
    links: int = 0
    verified: int = 0  # 验证出结果的（有效 + 失效）
    valid: int = 0
    predicted: int = 0  # 系统判为相关（有效且 match）
    true_positive: int = 0
    relevant: int = 0  # 裁判认为相关的有效链接
    top1: bool | None = None  # 排第一的相关结果对不对（系统一个相关的都没给出时为 None）
    found: bool = False  # 有没有给出至少一个真相关的有效链接
    wrong: list[str] = field(default_factory=list)  # 判为相关但其实不是的分享标题（前几个）
    missed: list[str] = field(default_factory=list)  # 真相关但没判为相关的（前几个）
    error: str | None = None
    pansou_valid: int = 0  # 只有 PanSou 找到的有效链接（同一分享码其他来源也有时标注保留其他来源）
    pansou_relevant: int = 0  # 其中真相关的


def score_case(case: Case, links: list[QuarkLink], labels: dict | None = None) -> CaseResult:
    r = CaseResult(case.id, case.query, links=len(links))
    first_pred = None
    for lk in links:
        if lk.state in ("valid", "invalid"):
            r.verified += 1
        if lk.state != "valid":
            continue
        r.valid += 1
        g = gold(lk, case, labels)
        if (lk.source or "").startswith("PanSou"):
            r.pansou_valid += 1
            r.pansou_relevant += bool(g)
        pred = lk.relevance == "match"
        if pred:
            r.predicted += 1
            if first_pred is None:
                first_pred = g
        if g:
            r.relevant += 1
            if pred:
                r.true_positive += 1
                r.found = True
            elif len(r.missed) < 3:
                r.missed.append(lk.share_title or lk.name)
        elif pred and g is False and len(r.wrong) < 3:
            r.wrong.append(lk.share_title or lk.name)
    r.top1 = first_pred if first_pred is not None or r.predicted == 0 else None
    if r.predicted == 0:
        r.top1 = None
    return r


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 3) if b else None


def summarize(results: list[CaseResult]) -> dict:
    """总体指标：

    - precision 相关率：系统判为相关的有效链接里，真相关的比例（越高越不会给错片）
    - recall 召回率：真相关的有效链接里，被系统判为相关的比例
    - validity 有效率：验证出结果的链接里，有效的比例
    - top1 准确率：给出了相关结果的搜索里，排第一的那个是对的比例
    - found 命中率：至少给出一个真相关有效链接的搜索占比
    """
    ok = [r for r in results if r.error is None]
    tp = sum(r.true_positive for r in ok)
    judged_top1 = [r for r in ok if r.top1 is not None]
    return {
        "cases": len(results),
        "errors": len(results) - len(ok),
        "precision": _ratio(tp, sum(r.predicted for r in ok)),
        "recall": _ratio(tp, sum(r.relevant for r in ok)),
        "validity": _ratio(sum(r.valid for r in ok), sum(r.verified for r in ok)),
        "top1": _ratio(sum(1 for r in judged_top1 if r.top1), len(judged_top1)),
        "found": _ratio(sum(1 for r in ok if r.found), len(ok)),
        "pansou_valid": sum(r.pansou_valid for r in ok),
        "pansou_relevant": sum(r.pansou_relevant for r in ok),
    }


def report_markdown(summary: dict, results: list[CaseResult], title: str) -> str:
    def pct(v: float | None) -> str:
        return "—" if v is None else f"{v * 100:.1f}%"

    lines = [
        f"# {title}", "",
        f"共 {summary['cases']} 个片名，出错 {summary['errors']} 个。", "",
        "| 指标 | 数值 | 含义 |", "|---|---|---|",
        f"| 相关率 precision | {pct(summary['precision'])} | 判为相关的有效链接里真相关的比例 |",
        f"| 召回率 recall | {pct(summary['recall'])} | 真相关的有效链接里被判为相关的比例 |",
        f"| 有效率 validity | {pct(summary['validity'])} | 验证出结果的链接里有效的比例 |",
        f"| 首条准确率 top1 | {pct(summary['top1'])} | 排第一的相关结果是对的比例 |",
        f"| 命中率 found | {pct(summary['found'])} | 至少找到一个真相关有效链接的比例 |",
        "",
        (f"PanSou 新增（只有 PanSou 找到的）有效链接 {summary.get('pansou_valid', 0)} 条，"
         f"其中真相关 {summary.get('pansou_relevant', 0)} 条。"),
        "", "| 片名 | 有效/验证 | 判相关 | 真相关 | 命中 | 判错的 | 漏掉的 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.error:
            lines.append(f"| {r.query} | 出错：{r.error} | | | | | |")
            continue
        lines.append(
            f"| {r.query} | {r.valid}/{r.verified} | {r.predicted} | {r.true_positive}/"
            f"{r.relevant} | {'✓' if r.found else '✗'} | {'；'.join(r.wrong)} | "
            f"{'；'.join(r.missed)} |"
        )
    return "\n".join(lines) + "\n"

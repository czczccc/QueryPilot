"""评测入口。

    python -m evals.run --live            # 连真实服务跑全部片名，并录制到 evals/recordings/
    python -m evals.run --live --only guichuideng,jingyinglvshi
    python -m evals.run --replay          # 离线回放已有录制（不联网、不要 key）
    python -m evals.run --synthetic       # 手工构造的易错样例（CI 跑这个）

报告写到 evals/reports/<模式>-latest.md（和 .json）。
"""

import argparse
import asyncio
import json
import logging
import socket
import sys
from dataclasses import asdict
from pathlib import Path

from evals import harness, synthetic
from evals.metrics import CaseResult, report_markdown, score_case, summarize

REPORTS = Path(__file__).resolve().parent / "reports"
logger = logging.getLogger(__name__)
TITLES = {"live": "真实环境评测", "replay": "回放评测", "synthetic": "易错样例评测"}


async def evaluate(mode: str, only: set[str] | None = None, record: bool = True,
                   limit: int | None = None) -> tuple[dict, list[CaseResult]]:
    labels = harness.load_labels()
    results: list[CaseResult] = []
    if mode == "synthetic":
        todo = [(c, r) for c, r in synthetic.load() if not only or c.id in only]
    else:
        todo = [(c, None) for c in harness.load_cases() if not only or c.id in only]
        if mode == "replay":
            todo = [(c, r) for c, r in todo if (harness.RECORDINGS / f"{c.id}.json").exists()]
    for case, spec in todo[:limit]:
        try:
            if mode == "synthetic":
                original = socket.getaddrinfo
                socket.getaddrinfo = lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))]
                try:
                    links = await synthetic.run_synthetic(case, spec)
                finally:
                    socket.getaddrinfo = original
            elif mode == "replay":
                links, _ = await harness.run_replay(case)
            else:
                links = await harness.run_live(case, record=record)
            results.append(score_case(case, links, labels.get(case.id)))
        except Exception as e:  # 单个片名出错不影响其余
            logger.exception("评测出错：%s", case.query)
            results.append(CaseResult(case.id, case.query, error=type(e).__name__))
        if mode == "live":
            print(f"  {case.query}: 完成", file=sys.stderr)
    return summarize(results), results


def write_report(mode: str, summary: dict, results: list[CaseResult]) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    md = REPORTS / f"{mode}-latest.md"
    md.write_text(report_markdown(summary, results, TITLES[mode]), encoding="utf-8")
    (REPORTS / f"{mode}-latest.json").write_text(json.dumps(
        {"summary": summary, "cases": [asdict(r) for r in results]}, ensure_ascii=False,
        indent=1), encoding="utf-8")
    return md


def main() -> None:
    p = argparse.ArgumentParser(description="QueryPilot 搜索评测")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--live", action="store_true", help="连真实服务跑（需要 .env 里的 key）")
    g.add_argument("--replay", action="store_true", help="离线回放 evals/recordings")
    g.add_argument("--synthetic", action="store_true", help="手工构造的易错样例")
    p.add_argument("--only", help="只跑这些片名 id（逗号分隔，见 titles.json）")
    p.add_argument("--limit", type=int, help="最多跑几个")
    p.add_argument("--no-record", action="store_true", help="--live 时不保存录制")
    args = p.parse_args()
    mode = "live" if args.live else "replay" if args.replay else "synthetic"
    logging.basicConfig(level=logging.WARNING)
    only = set(args.only.split(",")) if args.only else None
    summary, results = asyncio.run(evaluate(mode, only, not args.no_record, args.limit))
    path = write_report(mode, summary, results)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"报告：{path}")


if __name__ == "__main__":
    main()

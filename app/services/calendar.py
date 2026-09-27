"""追剧日历：订阅的剧集按 TMDB 播出日期排开，每集标出状态。

状态：
- saved 网盘里已经有了（自动转存清点出来的）
- available 已经有资源（搜到的有效相关分享集数覆盖到这一集），还没存
- no_resource 已经播出，但还没搜到这一集的资源
- upcoming 还没播出
"""

from datetime import date, datetime, timedelta, timezone

from app.models import AirEpisode, Subscription

# 播出日期按北京时间算（国产剧为主；Windows 上没装 tzdata 也能用）
CN_TZ = timezone(timedelta(hours=8))


def today() -> date:
    return datetime.now(CN_TZ).date()


def episode_status(sub: Subscription, ep: AirEpisode, now: date) -> str:
    if ep.episode in sub.saved_episodes:
        return "saved"
    if ep.episode <= sub.best_episodes:
        return "available"
    aired = ep.air_date is not None and date.fromisoformat(ep.air_date) <= now
    return "no_resource" if aired else "upcoming"


def build_calendar(
    subs: list[Subscription], start: date, end: date, now: date,
) -> list[dict]:
    """[start, end] 之间播出的集（按日期、剧名排序）。"""
    out = []
    for sub in subs:
        if sub.media != "tv":
            continue
        for ep in sub.schedule:
            if ep.air_date is None or not start <= date.fromisoformat(ep.air_date) <= end:
                continue
            if ep.episode < sub.start_episode:
                continue
            out.append({
                "subscription_id": sub.id, "resource": sub.resource, "poster": sub.poster,
                "season": sub.season or 1, "episode": ep.episode, "air_date": ep.air_date,
                "name": ep.name, "status": episode_status(sub, ep, now),
            })
    out.sort(key=lambda e: (e["air_date"], e["resource"], e["episode"]))
    return out

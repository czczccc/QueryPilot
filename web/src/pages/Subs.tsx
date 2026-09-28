import { Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { Button, cn, Skeleton, Tabs, TabsList, TabsTrigger, Tooltip } from "@cz/design-system";
import { store } from "../lib/api";
import { navigate } from "../lib/app-state";
import { Notifications } from "../features/subs/Notifications";
import { HistoryItem, SubsList } from "../features/subs/SubCard";
import { SubscribeDialog, type SubscribeTarget } from "../features/subs/SubscribeDialog";
import { HIDDEN_TABS, savedTab, subMedia, useSubsData, validTab, type Sub, type Tab } from "../features/subs/store";

const EMPTY: Record<string, string> = {
  tv: "还没有订阅剧集。",
  movie: "还没有订阅电影。",
  history: "还没有完成的订阅。集齐或手动完成的订阅会出现在这里，可以一键重新订阅。",
};

const NEW_TARGET: SubscribeTarget = { query: "", resource: "", empty: true };

// 订阅概况：只用已拉到的订阅数据统计，方便一眼看出哪些要处理
function Summary({ subs }: { subs: Sub[] }) {
  if (!subs.length) return null;
  const tv = subs.filter((s) => subMedia(s) === "tv");
  const failing = subs.filter((s) => s.last_error && s.state !== "paused").length;
  const paused = subs.filter((s) => s.state === "paused").length;
  const missing = tv.reduce((n, s) => n + (Array.isArray(s.lack_episodes) ? s.lack_episodes.length : 0), 0);
  const cells: [string, number, string, string][] = [
    ["追更中", subs.length - paused, "", "剧集和电影里正在定期检查的订阅"],
    ["待补集数", missing, missing ? "text-warning-fg" : "", "所有剧集订阅里还没存进网盘的集数合计（按每季订阅的追踪范围算）"],
    ["检查失败", failing, failing ? "text-danger-fg" : "", "上次检查失败的订阅，服务器会自动重试"],
    ["已暂停", paused, "", "暂停期间不检查"],
  ];
  return (
    <div aria-label="订阅概况" className="flex flex-col gap-2">
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-card border border-line-subtle bg-line-subtle sm:grid-cols-4">
        {cells.map(([label, n, tone, tip]) => (
          <Tooltip key={label} content={<span className="max-w-64">{tip}</span>}>
            <div tabIndex={0} className="flex flex-col gap-0.5 bg-surface px-4 py-3.5 outline-none focus-visible:shadow-ring">
              <b className={cn("text-heading font-medium tabular-nums", tone || "text-fg")}>{n}</b>
              <span className="text-caption text-fg-muted">{label}</span>
            </div>
          </Tooltip>
        ))}
      </div>
      <p className="text-caption text-fg-subtle">待补集数：所有剧集订阅里还没存进网盘的集数合计，电影不计入。</p>
    </div>
  );
}

export function SubsPage({ tab: routeTab }: { tab?: string }) {
  const data = useSubsData();
  const [dialog, setDialog] = useState(false);

  // 旧链接 #/subs/calendar 跳到剧集；地址栏里的标签页记到本机
  useEffect(() => {
    if (routeTab && HIDDEN_TABS.has(routeTab)) navigate("#/subs/tv", true);
    else if (validTab(routeTab)) store.set("qp_subs_tab", routeTab);
  }, [routeTab]);

  const tab: Tab = validTab(routeTab) ? routeTab : savedTab();
  const counts: Record<string, number> = {
    tv: data.subs.filter((s) => subMedia(s) === "tv").length,
    movie: data.subs.filter((s) => subMedia(s) === "movie").length,
    history: data.history.length,
  };
  const setTab = (t: string) => {
    if (t === tab) return;
    store.set("qp_subs_tab", t);
    navigate("#/subs/" + t, true); // 地址栏跟着标签页走，刷新后还在这一页
  };

  const list = tab === "history" ? data.history : data.subs.filter((s) => subMedia(s) === tab);

  return (
    <div className="flex flex-col gap-8">
      <header className="flex items-end justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-display-sm text-fg">我的订阅</h1>
          <p className="text-body text-fg-muted">追更、补集、洗版，都在后台自动完成。</p>
        </div>
        {data.enabled && (
          <Button onClick={() => setDialog(true)}>
            <Plus />
            新订阅
          </Button>
        )}
      </header>

      {!data.enabled ? (
        data.loaded ? (
          <p className="rounded-card border border-dashed border-line py-16 text-center text-body text-fg-muted">{data.offText}</p>
        ) : (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-20 w-full rounded-card" />
            <Skeleton className="h-32 w-full rounded-card" />
            <Skeleton className="h-32 w-full rounded-card" />
          </div>
        )
      ) : (
        <>
          <Summary subs={data.subs} />
          <Notifications notes={data.notes} subs={data.subs} />
          <section className="flex flex-col gap-4">
            <Tabs value={tab} onValueChange={setTab}>
              <TabsList aria-label="订阅分类">
                {(
                  [
                    ["tv", "剧集"],
                    ["movie", "电影"],
                    ["history", "历史"],
                  ] as const
                ).map(([k, label]) => (
                  <TabsTrigger key={k} value={k}>
                    {label}
                    {counts[k] > 0 && <span className="text-caption text-fg-subtle tabular-nums">{counts[k]}</span>}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
            <ul className="flex flex-col gap-3">
              {!list.length && <li className="rounded-card border border-dashed border-line px-6 py-14 text-center text-body text-fg-muted">{EMPTY[tab]}</li>}
              {tab === "history" ? data.history.map((h) => <HistoryItem key={h.id} h={h} />) : <SubsList subs={list as Sub[]} collections={data.collections} />}
            </ul>
          </section>
        </>
      )}

      <SubscribeDialog open={dialog} target={NEW_TARGET} autoSave={false} onDone={() => setDialog(false)} />
    </div>
  );
}

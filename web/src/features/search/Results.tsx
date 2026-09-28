import { ArrowRight, Bell, ChevronDown, Copy, HardDrive, SearchX } from "lucide-react";
import { useRef, useState, type ReactNode } from "react";
import { Badge, Button, Input, Switch, cn } from "@cz/design-system";
import { copyText } from "../../lib/api";
import { useAppState } from "../../lib/app-state";
import { RES_LABEL, shareUrl } from "../../lib/format";
import { useMe } from "../../lib/me";
import { useToast } from "../../lib/toast";
import { SubscribeDialog } from "../subs/SubscribeDialog";
import { driveEpisodesText, maybeLinks, subscribeTarget, visibleLinks, type SearchResult } from "./agent";
import { LinkCard, useTokenPrompt } from "./LinkCard";

function Chips({ label, items }: { label: string; items: string[] }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="mr-1 text-caption text-fg-subtle">{label}</span>
      {items.map((it, i) => (
        <Badge key={i}>{it}</Badge>
      ))}
    </div>
  );
}

function Parsed({ data }: { data: SearchResult }) {
  const p = data.parsed;
  const sub: string[] = [];
  if (p.english_name) sub.push(p.english_name);
  const d = data.douban;
  if (d) sub.push("豆瓣：" + d.title + (d.year ? " (" + d.year + ")" : "") + (d.kind ? " · " + d.kind : ""));
  const tags: string[] = [];
  if (p.quality) tags.push("清晰度 " + p.quality);
  if (p.aliases && p.aliases.length) tags.push(...p.aliases.map((a) => "别名 " + a));
  return (
    <div className="flex flex-col gap-2">
      <p className="text-caption font-medium tracking-wide text-fg-subtle">资源识别</p>
      <h2 className="text-title font-semibold text-fg">{p.resource}</h2>
      {sub.length > 0 && <p className="text-footnote text-fg-muted">{sub.join("　·　")}</p>}
      {tags.length > 0 && <Chips label="识别" items={tags} />}
      {p.search_suggestions && p.search_suggestions.length > 0 && <Chips label="搜索词" items={p.search_suggestions} />}
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: ReactNode; tone?: "ok" | "bad" }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-footnote text-fg-subtle">
      {label}
      <b className={cn("font-medium tabular-nums", tone === "ok" ? "text-success-fg" : tone === "bad" ? "text-danger-fg" : "text-fg-muted")}>{value}</b>
    </span>
  );
}

function Metrics({ data, onRefresh }: { data: SearchResult; onRefresh: () => void }) {
  const m = data.metrics;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-x-4 gap-y-1.5">
        <Stat label="耗时" value={(m.duration_ms / 1000).toFixed(1) + "s"} />
        <Stat label="原始" value={m.raw_result_count} />
        <Stat label="去重后" value={m.deduplicated_result_count} />
        {!!m.memory_hits && <Stat label="记忆复用" value={m.memory_hits} tone="ok" />}
        {!!m.skipped_invalid && <Stat label="跳过已知失效" value={m.skipped_invalid} />}
        {(data.providers || []).map((p, i) => (
          <Stat
            key={i}
            label={p.name}
            value={({ ok: "正常", error: "出错", skipped: "跳过" } as Record<string, string>)[p.status] || p.status}
            tone={p.status === "ok" ? "ok" : p.status === "error" ? "bad" : undefined}
          />
        ))}
        {m.fallback_used && <Badge tone="warning">已使用基础查询（AI 解析暂不可用）</Badge>}
      </div>
      {m.served_from_memory && !data.followup && (
        <div className="flex flex-wrap items-center gap-3 rounded-md bg-accent-soft px-3 py-2 text-footnote text-accent-fg">
          <span className="flex-1">这些结果来自之前搜索并验证过的记忆，已跳过全网搜索。</span>
          <Button variant="secondary" size="sm" onClick={onRefresh}>
            重新全网搜索
          </Button>
        </div>
      )}
    </div>
  );
}

function Followup({ data, busy, onAsk }: { data: SearchResult; busy: boolean; onAsk: (q: string) => void }) {
  const [text, setText] = useState("");
  const f = data.filters || {};
  const conds: string[] = [];
  if (f.season) conds.push("第" + f.season + "季");
  if (f.resolution) conds.push((RES_LABEL[f.resolution] || f.resolution) + " 以上");
  if (f.subtitle) conds.push("要字幕");
  if (f.hdr) conds.push("要 HDR");
  return (
    <form
      noValidate
      className="flex flex-col gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        const q = text.trim();
        if (q.length < 2) return;
        setText("");
        onAsk(q);
      }}
    >
      <p className="text-footnote text-fg-subtle">
        对话：<b className="font-medium text-fg-muted">{(data.history || []).join(" › ")}</b>
        {conds.length > 0 && "　·　当前条件：" + conds.join("、")}
      </p>
      <div className="flex gap-2">
        <Input
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={200}
          autoComplete="off"
          enterKeyHint="send"
          aria-label="继续追问"
          placeholder="继续追问：要第二季 / 只要中字的 / 4K 的呢 / 再找找"
          className="min-w-0 flex-1"
        />
        <Button type="submit" disabled={busy}>
          <ArrowRight />
          追问
        </Button>
      </div>
    </form>
  );
}

function SubscribeBox({ data, boxRef, onSubscribed }: { data: SearchResult; boxRef: React.RefObject<HTMLDivElement | null>; onSubscribed: () => void }) {
  const { me } = useMe();
  const target = subscribeTarget(data);
  const [auto, setAuto] = useState(false);
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState(false);
  return (
    <div
      ref={boxRef}
      className={cn(
        "flex flex-col gap-4 rounded-card border p-4 sm:flex-row sm:items-center sm:p-5",
        target.empty ? "border-accent-line bg-accent-soft" : "border-line-subtle bg-surface",
      )}
    >
      <span className="grid size-9 shrink-0 place-items-center rounded-full bg-sunken text-accent-fg" aria-hidden>
        <Bell className="size-4" />
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <b className="text-body font-medium text-fg">{target.empty ? "暂时没有资源，要不要先订阅？" : "追更《" + target.resource + "》"}</b>
        <span className="text-footnote text-fg-muted">
          {target.empty
            ? "服务器会定期替你重搜「" + target.query + "」，有资源了第一时间提醒你"
            : "按季追踪缺的集，有新集或更高清的版本时提醒你；可以自动补齐网盘"}
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        {me.login && (
          <label className="flex items-center gap-2 text-footnote text-fg-muted" title={me.logged_in ? undefined : "需要先扫码登录夸克"}>
            <Switch checked={auto} disabled={done} onCheckedChange={setAuto} />
            {target.empty ? "有资源时自动转存" : "自动转存"}
          </label>
        )}
        <Button variant={done ? "soft" : target.empty ? "primary" : "secondary"} size={target.empty ? "sm" : "md"} disabled={open || done} onClick={() => setOpen(true)}>
          {done ? "已订阅 ✓" : target.empty ? "有资源时通知我" : "订阅"}
        </Button>
      </div>
      <SubscribeDialog
        open={open}
        target={target}
        autoSave={auto}
        onDone={(sub) => {
          setOpen(false);
          if (sub) {
            setDone(true);
            onSubscribed();
          }
        }}
      />
    </div>
  );
}

export function EmptyState({ title, text }: { title: string; text: string }) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-12 text-center">
      <span className="mb-2 grid size-12 place-items-center rounded-full bg-sunken text-fg-subtle">
        <SearchX className="size-5" />
      </span>
      <h3 className="text-body-lg font-medium text-fg">{title}</h3>
      <p className="max-w-md text-footnote text-fg-muted">{text}</p>
    </div>
  );
}

export function Results({
  data,
  query,
  busy,
  minRes,
  setMinRes,
  onRefresh,
  onFollowup,
}: {
  data: SearchResult;
  query: string;
  busy: boolean;
  minRes: string;
  setMinRes: (v: string) => void;
  onRefresh: () => void;
  onFollowup: (q: string) => void;
}) {
  const toast = useToast();
  const { subsEnabled } = useAppState();
  const { ask, dialog } = useTokenPrompt();
  const [hideDead, setHideDead] = useState(true);
  const [copiedAll, setCopiedAll] = useState(false);
  const [subscribed, setSubscribed] = useState(false);
  const [maybeOpen, setMaybeOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  const links = data.links || [];
  const visible = visibleLinks(links, hideDead, minRes);
  const main = visible.filter((l) => l.relevance !== "uncertain");
  const maybe = maybeLinks(links, hideDead, minRes);
  const validCount = links.filter((l) => l.state === "valid" && l.relevance === "match").length;

  const driveStep = (data.steps || []).slice().reverse().find((x) => x.tool === "check_my_drive");
  const driveText = driveStep && driveStep.observation && !driveStep.observation.error ? driveEpisodesText(driveStep.observation.episodes) : "";

  const copyAll = () => {
    if (!visible.length) {
      toast("当前没有可复制的链接", "error");
      return;
    }
    const lines = visible.map((l) => [l.name, shareUrl(l.share, l.pwd), l.pwd || "-", l.time, l.conf, l.state === "valid" ? "有效" : "失效"].join("\t"));
    copyText(lines.join("\n"))
      .then(() => {
        setCopiedAll(true);
        setTimeout(() => setCopiedAll(false), 1600);
        toast("已复制 " + lines.length + " 条链接（可直接粘贴到表格）");
      })
      .catch(() => toast("复制失败", "error"));
  };

  const card = (l: (typeof links)[number], i: number) => <LinkCard key={l.share + ":" + i} l={l} index={i} query={query} askToken={ask} />;

  return (
    <section aria-label="搜索结果" className="flex flex-col gap-8">
      {driveText && (
        <p className="flex items-center gap-2 rounded-md border border-info-line bg-info-soft px-3 py-2 text-footnote text-info-fg">
          <HardDrive className="size-4 shrink-0" />
          你的网盘里已经有：{driveText}
        </p>
      )}

      <div className="flex flex-col gap-5">
        <Parsed data={data} />
        <Metrics data={data} onRefresh={onRefresh} />
      </div>

      {data.session_id && <Followup key={data.session_id + (data.history || []).join()} data={data} busy={busy} onAsk={onFollowup} />}

      {subsEnabled && <SubscribeBox key={data.session_id || data.query} data={data} boxRef={boxRef} onSubscribed={() => setSubscribed(true)} />}

      {links.length === 0 ? (
        <EmptyState title="没有找到夸克网盘链接" text="建议换一种写法（别名、英文名、加 4K / 全集 等）后重试，或者在上方追问「再找找」。" />
      ) : (
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-3 border-b border-line-subtle pb-4 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-footnote text-fg-muted">
              显示 <b className="font-medium text-fg">{visible.length}</b> / {links.length} 条，其中有效且相关 {validCount} 条
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <label className="flex items-center gap-2 text-footnote text-fg-muted">
                <Switch checked={hideDead} onCheckedChange={setHideDead} />
                只看有效且相关
              </label>
              <label className="flex items-center gap-2 text-footnote text-fg-muted">
                最低清晰度
                <select
                  value={minRes}
                  onChange={(e) => setMinRes(e.target.value)}
                  className="h-control-sm rounded-sm border border-line bg-surface px-2 text-footnote text-fg outline-none focus-visible:shadow-ring"
                >
                  <option value="">不限</option>
                  <option value="720p">720p</option>
                  <option value="1080p">1080p</option>
                  <option value="2160p">4K</option>
                </select>
              </label>
              <Button variant="secondary" size="sm" onClick={copyAll}>
                <Copy />
                {copiedAll ? "已复制 ✓" : "复制全部链接"}
              </Button>
              {subsEnabled && (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={subscribed}
                  onClick={() => boxRef.current?.scrollIntoView({ behavior: "smooth", block: "center" })}
                >
                  <Bell />
                  {subscribed ? "已订阅 ✓" : "订阅更新"}
                </Button>
              )}
            </div>
          </div>

          {main.length === 0 ? (
            maybe.length ? (
              <EmptyState title="没有确认相关的链接" text={"下面有 " + maybe.length + " 条「可能相关」的资源，没能确认是不是这部，请展开自己核对。"} />
            ) : (
              <EmptyState title="当前筛选下没有链接" text={"共有 " + links.length + " 条结果被筛掉了。可以关掉「只看有效且相关」或调低清晰度要求再看看。"} />
            )
          ) : (
            <ol className="flex flex-col gap-3">{main.map(card)}</ol>
          )}

          {maybe.length > 0 && (
            <div className="rounded-card border border-dashed border-line">
              <button
                type="button"
                aria-expanded={maybeOpen}
                onClick={() => setMaybeOpen((o) => !o)}
                className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-left outline-none focus-visible:shadow-ring"
              >
                <span className="text-body font-medium text-fg">可能相关（{maybe.length}）</span>
                <span className="flex-1 text-footnote text-fg-subtle">没能确认是不是这部，请自己核对</span>
                <ChevronDown className={cn("size-4 text-fg-subtle transition duration-fast ease-standard", maybeOpen && "rotate-180")} />
              </button>
              {maybeOpen && <ol className="flex flex-col gap-3 px-3 pb-3">{maybe.map(card)}</ol>}
            </div>
          )}
        </div>
      )}
      {dialog}
    </section>
  );
}

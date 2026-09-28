import { useCallback, useEffect, useState } from "react";
import { Button, Card, Input, Label, Skeleton } from "@cz/design-system";
import { copyText } from "../../lib/api";
import { useToast } from "../../lib/toast";
import { adminApi, DataTable, Kpi, num, Pill, subjectLabel, todayStr, tokens, ts, useAsk } from "./shared";

export type OnError = (e: unknown) => void;

function useLoader<T>(load: () => Promise<T>, onError: OnError, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const reload = useCallback(() => {
    load().then(setData, onError);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => reload(), [reload]);
  return [data, reload] as const;
}

const Section = ({ title, hint, children }: { title?: string; hint?: string; children: React.ReactNode }) => (
  <Card className="flex min-w-0 flex-col gap-4 p-5">
    {title && (
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-title-sm text-fg">{title}</h2>
        {hint && <span className="text-caption text-fg-subtle">{hint}</span>}
      </div>
    )}
    {children}
  </Card>
);

// ---- 概览 ----
const LIMIT_LABEL: Record<string, [string, string]> = {
  anon_daily_ai: ["未登录每天 AI 搜索", "次"],
  anon_daily_searches: ["未登录每天搜索", "次"],
  user_daily_ai: ["登录用户每天 AI 搜索", "次"],
  ip_daily_searches: ["每个 IP 每天搜索上限", "次"],
  site_daily_tokens: ["全站每天 token 预算", ""],
};

type Trend = { day: string; searches?: number; llm_searches?: number; tokens?: number };

export function Overview({ onError, version }: { onError: OnError; version: number }) {
  const [d] = useLoader(() => adminApi("/overview"), onError, [version]);
  if (!d) return <Skeleton className="h-64 w-full" />;
  const t = d.today || {};
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi label="今日搜索" value={num(t.searches)} sub={d.day} />
        <Kpi label="今日 AI 搜索" value={num(t.llm_searches)} sub={"LLM 调用 " + num(t.llm_calls) + " 次"} />
        <Kpi label="今日 tokens" value={tokens(t.tokens)} sub="拿不到接口用量时为估算" />
        <Kpi label="账号总数" value={num(d.users)} sub="扫码登录过的夸克账号" />
      </div>
      <Section title="最近 14 天" hint="宽柱：搜索次数　细条：AI 搜索">
        <TrendChart trend={d.trend || []} />
      </Section>
      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="今天用量最多">
          <DataTable
            rows={d.top || []}
            empty="今天还没有人搜索"
            cols={[
              { label: "身份", render: (r: any) => <span>{subjectLabel(r)}{r.banned && <Pill tone="danger">已停用</Pill>}</span> },
              { label: "搜索", key: "searches", num: true },
              { label: "AI", key: "llm_searches", num: true },
              { label: "tokens", render: (r: any) => tokens(r.tokens), num: true },
            ]}
          />
        </Section>
        <Section title="当前额度设置">
          {!d.limits ? (
            <p className="text-footnote text-fg-subtle">额度功能未开启</p>
          ) : (
            <dl className="flex flex-col gap-2 text-footnote">
              {Object.entries(d.limits as Record<string, number>).map(([key, v]) => {
                const [label, unit] = LIMIT_LABEL[key] || [key, ""];
                return (
                  <div key={key} className="flex justify-between gap-4">
                    <dt className="text-fg-muted">{label}</dt>
                    <dd className="text-right text-fg tabular-nums">{v === 0 ? "不限" : key.includes("tokens") ? num(v) : num(v) + " " + unit}</dd>
                  </div>
                );
              })}
            </dl>
          )}
          <p className="text-caption text-fg-subtle">这些值来自服务器 .env，改完需要重启服务。0 表示不限。</p>
        </Section>
      </div>
    </div>
  );
}

function TrendChart({ trend }: { trend: Trend[] }) {
  if (!trend.length) return <p className="text-footnote text-fg-subtle">还没有数据</p>;
  const max = Math.max(1, ...trend.map((x) => x.searches || 0));
  return (
    <div className="overflow-x-auto">
      <div className="flex h-48 min-w-lg items-end gap-1.5">
        {trend.map((x) => {
          const h = Math.round(((x.searches || 0) / max) * 100);
          const ai = Math.round(((x.llm_searches || 0) / max) * 100);
          return (
            <div
              key={x.day}
              className="flex h-full flex-1 flex-col items-center gap-1"
              title={x.day + "：搜索 " + num(x.searches) + " 次，AI 搜索 " + num(x.llm_searches) + " 次，" + tokens(x.tokens) + " tokens"}
            >
              <span className="h-4 text-caption text-fg-subtle tabular-nums">{x.searches ? num(x.searches) : ""}</span>
              <div className="relative flex w-full flex-1 items-end justify-center">
                {/* 高度随数据变化，只能用内联样式 */}
                <div className="w-full max-w-8 rounded-sm bg-accent-soft" style={{ height: Math.max(h, x.searches ? 3 : 0) + "%" }} />
                <div className="absolute bottom-0 w-1.5 rounded-sm bg-accent" style={{ height: ai + "%" }} />
              </div>
              <span className="text-caption text-fg-subtle tabular-nums">{(x.day || "").slice(5)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---- 账号 ----
const PAGE = 50;

export function Users({ onError, version }: { onError: OnError; version: number }) {
  const toast = useToast();
  const ask = useAsk();
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [rows, reload] = useLoader<any[]>(
    () => adminApi("/users?limit=" + PAGE + "&offset=" + offset + (query ? "&q=" + encodeURIComponent(query) : "")),
    onError,
    [offset, query, version],
  );
  const who = (u: any) => u.nickname || u.user_id;
  const safe = (fn: () => Promise<void>) => () => fn().catch(onError);

  const ban = (u: any) =>
    safe(async () => {
      const reason = await ask({ title: "停用「" + who(u) + "」", description: "原因会显示给对方，可留空", input: { value: "" }, danger: true, ok: "停用" });
      if (reason === null) return;
      await adminApi("/users/" + encodeURIComponent(u.user_id) + "/ban", { method: "POST", body: { reason } });
      toast("已停用 " + who(u));
      reload();
    });
  const unban = (u: any) =>
    safe(async () => {
      await adminApi("/users/" + encodeURIComponent(u.user_id) + "/unban", { method: "POST" });
      toast("已恢复 " + who(u));
      reload();
    });
  const limit = (u: any) =>
    safe(async () => {
      const v = await ask({
        title: "给「" + who(u) + "」单独设每日 AI 搜索次数",
        description: "留空恢复默认，0 表示不限",
        input: { value: u.ai_limit == null ? "" : String(u.ai_limit) },
      });
      if (v === null) return;
      const t = v.trim();
      if (t !== "" && !/^\d+$/.test(t)) {
        toast("请输入非负整数", "error");
        return;
      }
      await adminApi("/users/" + encodeURIComponent(u.user_id) + "/limit", { method: "PUT", body: { ai_limit: t === "" ? null : Number(t) } });
      toast("已更新额度");
      reload();
    });

  return (
    <Section>
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setOffset(0);
          setQuery(q.trim());
          if (offset === 0 && q.trim() === query) reload();
        }}
      >
        <Input type="search" aria-label="搜索账号" placeholder="按昵称或账号 id 搜索" value={q} onChange={(e) => setQ(e.target.value)} />
        <Button type="submit" variant="secondary" className="shrink-0">
          搜索
        </Button>
      </form>
      {!rows ? (
        <Skeleton className="h-40 w-full" />
      ) : (
        <DataTable
          rows={rows}
          empty={query ? "没有匹配的账号" : "还没有账号"}
          cols={[
            {
              label: "账号",
              render: (u: any) => (
                <span className="flex flex-col">
                  <b className="font-medium">{u.nickname || "（无昵称）"}</b>
                  <small className="text-caption text-fg-subtle">{u.user_id}</small>
                </span>
              ),
            },
            {
              label: "状态",
              render: (u: any) =>
                u.banned ? <Pill tone="danger">{"已停用" + (u.ban_reason ? "：" + u.ban_reason : "")}</Pill> : <Pill tone="success">正常</Pill>,
            },
            { label: "最近活跃", render: (u: any) => <span className="whitespace-nowrap">{ts(u.last_seen)}</span> },
            { label: "今日搜索", key: "today_searches", num: true },
            { label: "累计搜索", key: "total_searches", num: true },
            { label: "累计 tokens", render: (u: any) => tokens(u.total_tokens), num: true },
            { label: "AI 额度", render: (u: any) => (u.ai_limit == null ? "默认" : u.ai_limit === 0 ? "不限" : u.ai_limit + " 次/天") },
            { label: "邀请码", render: (u: any) => u.invite_code || "-" },
            {
              label: "操作",
              render: (u: any) => (
                <span className="flex gap-1">
                  <Button size="sm" variant="secondary" onClick={limit(u)}>
                    改额度
                  </Button>
                  {u.banned ? (
                    <Button size="sm" variant="secondary" onClick={unban(u)}>
                      恢复
                    </Button>
                  ) : (
                    <Button size="sm" variant="secondary" className="text-danger-fg" onClick={ban(u)}>
                      停用
                    </Button>
                  )}
                </span>
              ),
            },
          ]}
        />
      )}
      <div className="flex items-center justify-center gap-3">
        <Button size="sm" variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
          上一页
        </Button>
        <span className="text-footnote text-fg-muted">第 {offset / PAGE + 1} 页</span>
        <Button size="sm" variant="secondary" disabled={!rows || rows.length < PAGE} onClick={() => setOffset(offset + PAGE)}>
          下一页
        </Button>
      </div>
    </Section>
  );
}

// ---- 每日用量 ----
export function Usage({ onError, version }: { onError: OnError; version: number }) {
  const toast = useToast();
  const ask = useAsk();
  const [day, setDay] = useState(todayStr());
  const [shown, setShown] = useState(day);
  const [d, reload] = useLoader(() => adminApi("/usage?limit=200" + (shown ? "&day=" + shown : "")), onError, [shown, version]);
  const s = d?.site || {};
  return (
    <Section>
      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (day === shown) reload();
          else setShown(day);
        }}
      >
        <Label htmlFor="usage-day" className="shrink-0 font-normal text-fg-muted">
          日期
        </Label>
        <Input id="usage-day" type="date" className="w-44" value={day} onChange={(e) => setDay(e.target.value)} />
        <Button type="submit" variant="secondary" className="shrink-0">
          查看
        </Button>
      </form>
      {!d ? (
        <Skeleton className="h-40 w-full" />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Kpi label="搜索" value={num(s.searches)} sub={d.day} />
            <Kpi label="AI 搜索" value={num(s.llm_searches)} />
            <Kpi label="LLM 调用" value={num(s.llm_calls)} />
            <Kpi label="tokens" value={tokens(s.tokens)} />
          </div>
          <DataTable
            rows={d.rows || []}
            empty="这一天没有用量记录"
            cols={[
              { label: "身份", render: (r: any) => <span>{subjectLabel(r)}{r.banned && <Pill tone="danger">已停用</Pill>}</span> },
              { label: "类型", render: (r: any) => ((r.subject || "").startsWith("ip:") ? "未登录" : r.subject === "system" ? "系统" : "账号") },
              { label: "搜索", key: "searches", num: true },
              { label: "AI 搜索", key: "llm_searches", num: true },
              { label: "LLM 调用", key: "llm_calls", num: true },
              { label: "tokens", render: (r: any) => tokens(r.tokens), num: true },
              {
                label: "操作",
                render: (r: any) =>
                  (r.subject || "").startsWith("ip:") ? (
                    <Button
                      size="sm"
                      variant="secondary"
                      className="text-danger-fg"
                      onClick={() =>
                        (async () => {
                          const ip = r.subject.slice(3);
                          const reason = await ask({ title: "封禁 " + ip, description: "原因可留空", input: { value: "" }, danger: true, ok: "封禁" });
                          if (reason === null) return;
                          await adminApi("/bans", { method: "POST", body: { ip, reason } });
                          toast("已封禁 " + ip);
                        })().catch(onError)
                      }
                    >
                      封此 IP
                    </Button>
                  ) : (
                    ""
                  ),
              },
            ]}
          />
        </>
      )}
    </Section>
  );
}

// ---- 邀请码 ----
function useCopy() {
  const toast = useToast();
  return (text: string) => copyText(text).then(() => toast("已复制：" + text), () => toast("复制失败", "error"));
}

export function Invites({ onError, version }: { onError: OnError; version: number }) {
  const toast = useToast();
  const ask = useAsk();
  const copy = useCopy();
  const [count, setCount] = useState("5");
  const [uses, setUses] = useState("1");
  const [note, setNote] = useState("");
  const [rows, reload] = useLoader<any[]>(() => adminApi("/invites"), onError, [version]);

  const create = (e: React.FormEvent) => {
    e.preventDefault();
    const body = {
      count: Math.min(100, Math.max(1, Number(count) || 1)),
      max_uses: Math.max(0, Number(uses) || 0),
      note: note.trim(),
    };
    adminApi<{ code: string }[]>("/invites", { method: "POST", body })
      .then((codes) => {
        toast("已生成 " + codes.length + " 个邀请码");
        if (codes.length) copy(codes.map((c) => c.code).join("\n"));
        reload();
      })
      .catch(onError);
  };

  return (
    <Section>
      <form className="flex flex-wrap items-end gap-3" onSubmit={create}>
        <FieldBox label="数量" id="inv-count">
          <Input id="inv-count" type="number" min={1} max={100} className="w-24" value={count} onChange={(e) => setCount(e.target.value)} />
        </FieldBox>
        <FieldBox label="每个可用次数" id="inv-uses">
          <Input id="inv-uses" type="number" min={0} max={10000} className="w-28" value={uses} onChange={(e) => setUses(e.target.value)} />
        </FieldBox>
        <FieldBox label="备注" id="inv-note" grow>
          <Input id="inv-note" maxLength={60} placeholder="比如：朋友" value={note} onChange={(e) => setNote(e.target.value)} />
        </FieldBox>
        <Button type="submit">生成邀请码</Button>
      </form>
      <p className="text-caption text-fg-subtle">可用次数填 0 表示不限次数。只有 .env 开启邀请制（新用户需要邀请码）时才会用到。</p>
      {!rows ? (
        <Skeleton className="h-40 w-full" />
      ) : (
        <DataTable
          rows={rows}
          empty="还没有邀请码"
          cols={[
            { label: "邀请码", render: (r: any) => <code className="rounded-sm bg-sunken px-1.5 py-0.5 font-mono">{r.code}</code> },
            { label: "备注", key: "note" },
            { label: "已用 / 可用", render: (r: any) => <span className="whitespace-nowrap">{num(r.uses || 0) + " / " + (r.max_uses === 0 ? "不限" : num(r.max_uses))}</span> },
            { label: "状态", render: (r: any) => (r.max_uses && r.uses >= r.max_uses ? <Pill>已用完</Pill> : <Pill tone="success">可用</Pill>) },
            { label: "创建", render: (r: any) => <span className="whitespace-nowrap">{ts(r.created)}</span> },
            {
              label: "操作",
              render: (r: any) => (
                <span className="flex gap-1">
                  <Button size="sm" variant="secondary" onClick={() => copy(r.code)}>
                    复制
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    className="text-danger-fg"
                    onClick={() =>
                      (async () => {
                        if ((await ask({ title: "删除邀请码 " + r.code + "？", danger: true, ok: "删除" })) === null) return;
                        await adminApi("/invites/" + encodeURIComponent(r.code), { method: "DELETE" });
                        toast("已删除");
                        reload();
                      })().catch(onError)
                    }
                  >
                    删除
                  </Button>
                </span>
              ),
            },
          ]}
        />
      )}
    </Section>
  );
}

function FieldBox({ label, id, grow, children }: { label: string; id: string; grow?: boolean; children: React.ReactNode }) {
  return (
    <div className={grow ? "flex min-w-40 flex-1 flex-col gap-1.5" : "flex flex-col gap-1.5"}>
      <Label htmlFor={id} className="font-normal text-fg-muted">
        {label}
      </Label>
      {children}
    </div>
  );
}

// ---- 封禁 IP ----
export function Bans({ onError, version }: { onError: OnError; version: number }) {
  const toast = useToast();
  const [ip, setIp] = useState("");
  const [reason, setReason] = useState("");
  const [rows, reload] = useLoader<any[]>(() => adminApi("/bans"), onError, [version]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const v = ip.trim();
    if (!v) return;
    adminApi("/bans", { method: "POST", body: { ip: v, reason: reason.trim() } })
      .then(() => {
        toast("已封禁 " + v);
        setIp("");
        setReason("");
        reload();
      })
      .catch(onError);
  };

  return (
    <Section>
      <form className="flex flex-wrap items-end gap-3" onSubmit={submit}>
        <FieldBox label="IP 地址" id="ban-ip">
          <Input id="ban-ip" maxLength={64} placeholder="1.2.3.4" required className="w-44" value={ip} onChange={(e) => setIp(e.target.value)} />
        </FieldBox>
        <FieldBox label="原因" id="ban-reason" grow>
          <Input id="ban-reason" maxLength={100} placeholder="选填，会显示给对方" value={reason} onChange={(e) => setReason(e.target.value)} />
        </FieldBox>
        <Button type="submit" variant="danger">
          封禁
        </Button>
      </form>
      {!rows ? (
        <Skeleton className="h-40 w-full" />
      ) : (
        <DataTable
          rows={rows}
          empty="没有被封的 IP"
          cols={[
            { label: "IP", render: (r: any) => <code className="rounded-sm bg-sunken px-1.5 py-0.5 font-mono">{r.ip}</code> },
            { label: "原因", key: "reason" },
            { label: "时间", render: (r: any) => <span className="whitespace-nowrap">{ts(r.created)}</span> },
            {
              label: "操作",
              render: (r: any) => (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() =>
                    adminApi("/bans/" + encodeURIComponent(r.ip), { method: "DELETE" })
                      .then(() => {
                        toast("已解封 " + r.ip);
                        reload();
                      })
                      .catch(onError)
                  }
                >
                  解封
                </Button>
              ),
            },
          ]}
        />
      )}
    </Section>
  );
}

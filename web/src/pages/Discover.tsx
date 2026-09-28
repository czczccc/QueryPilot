import { AnimatePresence, motion } from "framer-motion";
import { CircleAlert, Info } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Skeleton, cn, rise } from "@cz/design-system";
import { clientId, rateLimitText, readSSE } from "../lib/api";
import { useMe, type Quota } from "../lib/me";
import { AgentPanel, type AgentView } from "../features/search/AgentPanel";
import { Results } from "../features/search/Results";
import { SearchHero } from "../features/search/SearchHero";
import type { SearchResult, Step } from "../features/search/agent";

type Status = { text: string; kind: "info" | "loading" | "error" } | null;

const IDLE_VIEW: AgentView = { steps: [], pending: null, title: "", running: false, failed: false, collapsed: false, collapsible: false };

function QuotaNote({ q }: { q: Quota }) {
  const { me, login } = useMe();
  return (
    <div role="status" className="flex flex-wrap items-center gap-3 rounded-card border border-warning-line bg-warning-soft px-4 py-3 text-footnote text-warning-fg">
      <CircleAlert className="size-4 shrink-0" />
      <span className="min-w-0 flex-1">{q.message}</span>
      {q.reason === "user_quota" && !(q.logged_in || me.logged_in) && me.login && (
        <Button variant="secondary" size="sm" onClick={() => login()}>
          扫码登录
        </Button>
      )}
    </div>
  );
}

export function DiscoverPage() {
  const { me, reload, setQuota, setBanned, login } = useMe();
  const [input, setInput] = useState("");
  const [searched, setSearched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<AgentView>(IDLE_VIEW);
  const [status, setStatus] = useState<Status>(null);
  const [quotaNote, setQuotaNote] = useState<Quota | null>(null);
  const [result, setResult] = useState<SearchResult | null>(null);
  const [lastQuery, setLastQuery] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const [minRes, setMinRes] = useState("");
  const current = useRef<AbortController | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const meRef = useRef(me);
  meRef.current = me;

  // 最低清晰度默认值来自偏好设置
  useEffect(() => {
    if (!clientId) return;
    fetch("/api/prefs?client_id=" + encodeURIComponent(clientId))
      .then((r) => (r.ok ? r.json() : null))
      .then((p) => p && setMinRes(p.min_resolution || ""))
      .catch(() => {});
  }, []);

  // 计时
  useEffect(() => {
    if (!busy) return;
    const t0 = Date.now();
    setElapsed(0);
    const h = setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(h);
  }, [busy]);

  useEffect(() => () => current.current?.abort(), []);

  const applyQuota = useCallback(
    (q: Quota | null | undefined) => {
      if (!q) return;
      setQuota(q);
      if (q.message) setQuotaNote(q);
    },
    [setQuota],
  );

  const doSearch = useCallback(
    async (query: string, refresh = false, followupOf: string | null = null): Promise<void> => {
      if (meRef.current.banned) return;
      setLastQuery(query);
      setQuotaNote(null);
      setResult(null);
      current.current?.abort();
      setSearched(true);
      setView({ steps: [], pending: "正在理解你要找的资源", title: "agent 正在搜索", running: true, failed: false, collapsed: false, collapsible: false });
      setStatus({ text: "agent 正在搜索并验证链接，通常需要 20~90 秒，过程会实时显示在下方", kind: "loading" });
      setBusy(true);
      requestAnimationFrame(() => panelRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }));

      const url =
        "/api/agent/stream?query=" + encodeURIComponent(query) +
        (refresh ? "&refresh=true" : "") +
        (clientId ? "&client_id=" + encodeURIComponent(clientId) : "") +
        (followupOf ? "&session_id=" + encodeURIComponent(followupOf) : "");
      const controller = new AbortController();
      current.current = controller;
      let finished = false;
      const done = () => {
        finished = true;
        clearTimeout(timer);
        if (current.current === controller) setBusy(false);
      };
      const failed = (title: string, detail: string) => {
        setView((v) => ({ ...v, pending: null, title, running: false, failed: true }));
        setStatus({ text: detail, kind: "error" });
      };
      const timer = setTimeout(() => {
        if (finished) return;
        controller.abort();
        done();
        failed("搜索超时", "搜索超时（超过 150 秒），请稍后重试或换一个更精确的资源名。");
      }, 150000);
      controller.signal.addEventListener("abort", () => clearTimeout(timer));

      let resp: Response;
      try {
        resp = await fetch(url, { signal: controller.signal, headers: { Accept: "text/event-stream" } });
      } catch {
        if (finished || controller.signal.aborted) return;
        done();
        failed("搜索中断", "网络连接失败，请检查网络后重试。");
        return;
      }

      if (!resp.ok) {
        done();
        const body = await resp.json().catch(() => ({}));
        const detail = resp.status === 429 ? rateLimitText(resp, body) : typeof body.detail === "string" ? body.detail : "搜索失败，请稍后重试。";
        if (resp.status === 401) {
          // 未登录免费次数用完：直接引导扫码登录，登录成功后自动重新搜索
          failed("需要登录", detail);
          if (meRef.current.login && (await login(detail))) doSearch(query, refresh, followupOf);
        } else if (resp.status === 403) {
          failed("无法搜索", detail);
          setBanned(detail);
        } else if (resp.status === 429) {
          failed("请求太频繁", detail);
        } else {
          failed("搜索中断", detail);
        }
        reload();
        return;
      }

      try {
        await readSSE(resp, (event, data) => {
          if (finished) return;
          if (event === "step") {
            const step: Step = JSON.parse(data);
            setView((v) => ({ ...v, steps: [...v.steps, step], pending: step.tool !== "finish" ? "正在决定下一步" : null }));
          } else if (event === "quota") {
            applyQuota(JSON.parse(data));
          } else if (event === "result") {
            done();
            setStatus(null);
            const r: SearchResult = JSON.parse(data);
            setView((v) => ({
              ...v,
              pending: null,
              running: false,
              failed: false,
              title: (r.planner === "llm" ? "AI 规划" : "规则规划") + " · " + r.steps.length + " 步 · " + r.stop_reason,
              // 结果出来后默认收起过程，把视线交给结果；想看可以展开
              collapsed: r.links.length > 0,
              collapsible: true,
            }));
            setResult(r);
            applyQuota(r.quota);
          } else if (event === "error") {
            done();
            let detail = "搜索失败，请稍后重试。";
            try {
              detail = JSON.parse(data).detail || detail;
            } catch {
              /* 保持默认提示 */
            }
            failed("搜索中断", detail);
          }
        });
      } catch {
        if (controller.signal.aborted) return;
      }
      if (!finished) {
        done();
        failed("搜索中断", "连接意外断开，请稍后重试。");
      }
    },
    [applyQuota, login, reload, setBanned],
  );

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 pb-16">
      <SearchHero searched={searched} busy={busy} onSearch={(q) => doSearch(q)} inputValue={input} setInputValue={setInput} />

      {searched && (
        <div ref={panelRef} className="flex flex-col gap-4">
          <AgentPanel view={view} elapsed={elapsed} onToggle={() => setView((v) => ({ ...v, collapsed: !v.collapsed }))} />

          <AnimatePresence initial={false}>
            {status && (
              <motion.p
                key={status.kind + status.text}
                variants={rise}
                initial="hidden"
                animate="visible"
                role={status.kind === "error" ? "alert" : "status"}
                className={cn(
                  "flex items-start gap-2 px-1 text-footnote",
                  status.kind === "error" ? "text-danger-fg" : "text-fg-muted",
                )}
              >
                {status.kind === "error" ? <CircleAlert className="mt-0.5 size-4 shrink-0" /> : <Info className="mt-0.5 size-4 shrink-0" />}
                {status.text}
              </motion.p>
            )}
          </AnimatePresence>

          {busy && (
            <div aria-hidden className="flex flex-col gap-3">
              {[0, 1, 2].map((i) => (
                <div key={i} className="flex flex-col gap-2.5 rounded-card border border-line-subtle bg-surface p-5">
                  <Skeleton className="h-4 w-2/3" />
                  <Skeleton className="h-3 w-full" />
                  <Skeleton className="h-3 w-1/2" />
                </div>
              ))}
            </div>
          )}

          {quotaNote && <QuotaNote q={quotaNote} />}
        </div>
      )}

      {result && (
        <Results
          key={result.session_id || result.query + lastQuery}
          data={result}
          query={lastQuery}
          busy={busy}
          minRes={minRes}
          setMinRes={setMinRes}
          onRefresh={() => doSearch(lastQuery, true)}
          onFollowup={(q) => doSearch(q, false, result.session_id || null)}
        />
      )}
    </div>
  );
}

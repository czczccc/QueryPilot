import { AnimatePresence, motion } from "framer-motion";
import { Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button, Kbd, cn, transition } from "@cz/design-system";
import { api, store } from "../../lib/api";
import { useMe, type Quota } from "../../lib/me";

type Trend = { title: string; year?: string | number; media?: string; poster?: string };
const DEFAULT_EXAMPLES = [
  { title: "漫长的季节", query: "漫长的季节 4K" },
  { title: "绝命律师", query: "绝命律师 全集" },
  { title: "沙丘2", query: "沙丘2 4K HDR" },
  { title: "繁花", query: "繁花 全集 1080p" },
];
const DEFAULT_PLACEHOLDER = "例如：漫长的季节 4K / 绝命律师 全集 / 沙丘2";
const TRENDING_KEY = "qp_trending";
const TRENDING_TTL = 6 * 3600 * 1000;
const FLOW = ["多源搜索", "逐条验证", "识别清晰度", "转存到网盘", "更新自动追"];

function trendingItems(data: any): Trend[] {
  const list: any[] = Array.isArray(data) ? data : (data && (data.items || data.trending)) || [];
  const seen = new Set<string>();
  return list.filter((x) => x && x.title && !seen.has(x.title) && seen.add(x.title)).slice(0, 6);
}

function useTrending(): Trend[] {
  const [items, setItems] = useState<Trend[]>([]);
  useEffect(() => {
    const cached = store.get<{ at: number; items: Trend[] } | null>(TRENDING_KEY, null);
    if (cached && Date.now() - cached.at < TRENDING_TTL && Array.isArray(cached.items)) {
      setItems(cached.items);
      return;
    }
    (async () => {
      try {
        const resp = await fetch("/api/trending");
        if (!resp.ok) return;
        const data = await resp.json();
        const its = trendingItems(data);
        setItems(its);
        // 内置兜底列表（source=default）不缓存，下次打开再试真的热门
        if (its.length && data.source !== "default") store.set(TRENDING_KEY, { at: Date.now(), items: its });
      } catch {
        /* 网络问题：保留写死的示例 */
      }
    })();
  }, []);
  return items;
}

// 占位文字在几部热门片名之间轮换；输入框有焦点或有内容时不动
function usePlaceholder(titles: string[], inputRef: React.RefObject<HTMLInputElement | null>) {
  const [ph, setPh] = useState(DEFAULT_PLACEHOLDER);
  const [swap, setSwap] = useState(false);
  const key = titles.join("|");
  useEffect(() => {
    if (!titles.length) return;
    let i = 0;
    const narrow = window.matchMedia("(max-width: 600px)");
    const show = () => {
      const two = titles.length > 1 && !narrow.matches; // 手机上只放一个，免得被截断
      setPh("例如：" + titles[i % titles.length] + (two ? " / " + titles[(i + 1) % titles.length] : ""));
    };
    show();
    let t: ReturnType<typeof setTimeout>;
    const h = setInterval(() => {
      const el = inputRef.current;
      if ((el && (document.activeElement === el || el.value)) || document.hidden) return;
      i += 1;
      setSwap(true);
      t = setTimeout(() => {
        show();
        setSwap(false);
      }, 180);
    }, 4000);
    return () => {
      clearInterval(h);
      clearTimeout(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return { ph, swap };
}

function QuotaLine({ q }: { q: Quota | null }) {
  const { me, login } = useMe();
  if (!q) return null;
  const ai = q.limit === 0 || q.remaining == null ? "不限" : String(q.remaining);
  const loggedIn = q.logged_in || me.logged_in;
  return (
    <div aria-live="polite" className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-footnote text-fg-muted">
      <span className="inline-flex items-center gap-2">
        <span aria-hidden className={cn("size-1.5 rounded-full", q.ai ? "bg-success" : "bg-warning")} />
        {!loggedIn && q.searches_remaining != null ? (
          <span>
            今日还可免费搜索 <b className="font-medium text-fg">{q.searches_remaining}</b> 次（其中 AI 搜索 <b className="font-medium text-fg">{ai}</b> 次）
          </span>
        ) : (
          <span>
            今日 AI 搜索剩余 <b className="font-medium text-fg">{ai}</b> 次
          </span>
        )}
        {!q.ai && <span className="rounded-pill bg-warning-soft px-2 text-caption text-warning-fg">当前为基础模式</span>}
      </span>
      {!loggedIn && me.login && (
        <Button variant="link" size="sm" onClick={() => login()}>
          扫码登录获得更多
        </Button>
      )}
    </div>
  );
}

function MemoryStats() {
  const [d, setD] = useState<{ valid: number; searches?: number } | null>(null);
  useEffect(() => {
    api("/api/memory/stats").then((r) => {
      if (r.ok && r.body.enabled && r.body.valid) setD(r.body);
    });
  }, []);
  if (!d) return null;
  return (
    <p className="text-center text-footnote text-fg-subtle">
      已为大家验证并记住 <b className="font-medium text-fg-muted">{d.valid}</b> 条有效链接 · 累计搜索 <b className="font-medium text-fg-muted">{d.searches || 0}</b> 次
    </p>
  );
}

export function SearchHero({
  searched,
  busy,
  onSearch,
  inputValue,
  setInputValue,
}: {
  searched: boolean;
  busy: boolean;
  onSearch: (q: string) => void;
  inputValue: string;
  setInputValue: (v: string) => void;
}) {
  const { me } = useMe();
  const inputRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState("");
  const [shake, setShake] = useState(0);
  const trending = useTrending();
  const { ph, swap } = usePlaceholder(
    trending.map((x) => x.title),
    inputRef,
  );
  const banned = me.banned;

  // 「/」聚焦搜索框
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
      const tag = (document.activeElement && document.activeElement.tagName) || "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      e.preventDefault();
      inputRef.current?.focus();
      inputRef.current?.select();
    };
    document.addEventListener("keydown", on);
    return () => document.removeEventListener("keydown", on);
  }, []);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const q = inputValue.trim();
    if (q.length < 2 || q.length > 200) {
      setError("请输入 2–200 个字符的搜索内容。");
      setShake((n) => n + 1);
      inputRef.current?.focus();
      return;
    }
    setError("");
    inputRef.current?.blur(); // 手机上收起键盘
    onSearch(q);
  };

  const examples = trending.length
    ? trending.map((x) => ({ title: x.title, query: x.title, poster: x.poster, media: x.media, tip: [x.title, x.year, x.media === "movie" ? "电影" : x.media === "tv" ? "剧集" : ""].filter(Boolean).join(" · ") }))
    : DEFAULT_EXAMPLES.map((x) => ({ ...x, poster: undefined, media: undefined, tip: x.query }));

  return (
    <section aria-label="搜索输入" className={cn("flex flex-col items-center gap-6 transition-all duration-slow ease-standard", searched ? "pt-2" : "pt-10 sm:pt-20")}>
      <AnimatePresence initial={false}>
        {!searched && (
          <motion.div
            key="hero"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto", transition: transition.smooth }}
            exit={{ opacity: 0, height: 0, transition: transition.exit }}
            className="flex flex-col items-center gap-4 overflow-hidden text-center"
          >
            <p className="inline-flex items-center gap-2 rounded-pill border border-line-subtle bg-surface px-3 py-1 text-caption text-fg-muted">
              <span aria-hidden className="size-1.5 rounded-full bg-accent" />
              AI Agent · 夸克网盘资源搜索
            </p>
            <h1 className="text-display-sm font-semibold tracking-tight text-fg sm:text-display">
              想看什么，<span className="text-accent-fg">交给 agent 去找</span>
            </h1>
            <p className="max-w-xl text-body-lg text-fg-muted">说出想看的片名，agent 会自己搜索、验证、挑出最好的版本；订阅之后，更新会自动存进你的网盘。</p>
          </motion.div>
        )}
      </AnimatePresence>

      {banned && (
        <div role="alert" className="flex w-full max-w-2xl flex-col gap-1 rounded-card border border-danger-line bg-danger-soft px-4 py-3 text-footnote text-danger-fg">
          <strong className="text-body font-medium">无法使用搜索</strong>
          <span>{banned}</span>
        </div>
      )}

      <form noValidate onSubmit={submit} className="flex w-full max-w-2xl flex-col gap-3">
        <motion.div
          key={shake}
          animate={shake ? { x: [0, -6, 6, -4, 4, 0] } : undefined}
          transition={{ duration: 0.32 }}
          className={cn(
            "flex items-center gap-2 rounded-2xl border bg-surface p-1.5 pl-4 shadow-sm transition duration-fast ease-standard focus-within:border-line-strong focus-within:shadow-md",
            error ? "border-danger-line" : "border-line",
          )}
        >
          <Search className="size-5 shrink-0 text-fg-subtle" aria-hidden />
          <label htmlFor="query" className="sr-only">
            搜索内容
          </label>
          <input
            ref={inputRef}
            id="query"
            type="search"
            autoComplete="off"
            enterKeyHint="search"
            maxLength={200}
            disabled={!!banned}
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            placeholder={ph}
            aria-invalid={!!error || undefined}
            className={cn(
              "h-11 min-w-0 flex-1 bg-transparent text-body-lg text-fg outline-none placeholder:text-fg-subtle placeholder:transition-opacity placeholder:duration-fast",
              swap && "placeholder:opacity-0",
            )}
          />
          <Kbd className="hidden sm:inline-flex" aria-hidden>
            /
          </Kbd>
          <Button type="submit" size="lg" loading={busy} disabled={!!banned} className="rounded-xl">
            {busy ? "搜索中" : "搜索"}
          </Button>
        </motion.div>
        {error && (
          <p role="alert" className="px-2 text-footnote text-danger-fg">
            {error}
          </p>
        )}
        <QuotaLine q={me.quota} />
        {!searched && (
          <div aria-label="示例查询" className="flex items-center gap-2 overflow-x-auto pb-1 sm:flex-wrap sm:justify-center">
            <span className="shrink-0 text-caption text-fg-subtle">{trending.length ? "正在热播" : "试试"}</span>
            {examples.map((x) => (
              <button
                key={x.query}
                type="button"
                title={x.tip}
                disabled={!!banned}
                onClick={() => {
                  setInputValue(x.query);
                  onSearch(x.query);
                }}
                className="inline-flex shrink-0 items-center gap-1.5 rounded-pill border border-line-subtle bg-surface py-1 pr-3 pl-1.5 text-footnote text-fg-muted transition duration-fast ease-standard hover:border-line hover:text-fg disabled:opacity-45"
              >
                {x.poster && (
                  <img
                    src={x.poster}
                    alt=""
                    loading="lazy"
                    referrerPolicy="no-referrer"
                    onError={(e) => e.currentTarget.remove()}
                    className="size-5 rounded-full object-cover"
                  />
                )}
                {!x.poster && <span className="w-1" />}
                <span>{x.title}</span>
                {x.media && <span className="text-caption text-fg-subtle">{x.media === "movie" ? "电影" : "剧集"}</span>}
              </button>
            ))}
          </div>
        )}
      </form>

      <AnimatePresence initial={false}>
        {!searched && (
          <motion.div
            key="flow"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1, transition: transition.smooth }}
            exit={{ opacity: 0, height: 0, transition: transition.exit }}
            className="flex w-full max-w-3xl flex-col items-center gap-6 overflow-hidden pt-6"
          >
            <ol aria-label="agent 的工作流程" className="grid w-full grid-cols-2 gap-2 sm:grid-cols-5">
              {FLOW.map((t, i) => (
                <li key={t} className={cn("flex items-center gap-2.5 rounded-card border border-line-subtle bg-surface px-3 py-3", i === 4 && "col-span-2 sm:col-span-1")}>
                  <span className="grid size-6 shrink-0 place-items-center rounded-full bg-accent-soft font-mono text-caption text-accent-fg">{i + 1}</span>
                  <span className="text-footnote text-fg">{t}</span>
                </li>
              ))}
            </ol>
            <MemoryStats />
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}

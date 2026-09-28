// 订阅弹窗：先搜 TMDB/豆瓣让用户选条目，选不到就按关键词订阅。
// 约定的接口，搜索页的「订阅」按钮会用它：
//   <SubscribeDialog open target={...} autoSave onDone={(sub) => ...} />
// onDone(sub)：订阅成功传回新订阅对象；取消或失败传 null。
import { ChevronRight, Hash, Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button, cn, Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, Input, Skeleton } from "@cz/design-system";
import { clientId, rateLimitText, subApi } from "../../lib/api";
import { useMe } from "../../lib/me";
import { useToast } from "../../lib/toast";
import { collectionName, loadSubsTwice, subMedia, switchTab, tidyTitle } from "./store";
import { initialRules, Poster, readRules, RuleFields, Select, SUB_RULES, SwitchRow, useLoginIfNeeded } from "./ui";

export type SubscribeTarget = { query: string; resource: string; empty: boolean };

type Part = { id?: number | string; index: number; title: string; year?: string | number; poster?: string; released?: boolean };
type Cand = Record<string, any> & {
  id?: string | number;
  title: string;
  media?: string;
  source?: string;
  kind?: string;
  episodes?: Record<string, number>;
  collection?: { id?: string | number; name?: string; poster?: string; parts?: Part[] };
};

const DIALOG_RULES = SUB_RULES.filter((r) => r.key !== "total_episodes" && !r.editOnly);
const DIALOG_RULES_MOVIE = DIALOG_RULES.filter((r) => !r.tvOnly);
const PASS_KEYS = ["resolution", "include", "exclude", "upgrade", "upgrade_to"];

function collectionParts(c: Cand): Part[] {
  return ((c.collection && c.collection.parts) || []).slice().sort((a, b) => a.index - b.index);
}
const isColl = (c: Cand) => c.kind === "collection" && collectionParts(c).length > 0;

function seasonsOf(c: Cand): number[] {
  const s = Object.keys(c.episodes || {})
    .map(Number)
    .filter((n) => n > 0)
    .sort((a, b) => a - b);
  return s.length ? s : [1];
}

/** 每个候选的默认下拉值：剧集默认最新一季，系列默认 default_part */
function defaultPick(c: Cand): string {
  if (isColl(c)) {
    const parts = collectionParts(c);
    return String(parts.some((p) => p.index === c.default_part) ? c.default_part : parts[0].index);
  }
  if (c.media === "tv") {
    const s = seasonsOf(c);
    return String(s[s.length - 1]);
  }
  return "";
}

function CandRow({
  c,
  selected,
  pick,
  onSelect,
  onPick,
}: {
  c: Cand;
  selected: boolean;
  pick: string;
  onSelect: () => void;
  onPick: (v: string) => void;
}) {
  const coll = isColl(c);
  const parts = coll ? collectionParts(c) : [];
  const title = coll ? collectionName(c.collection!.name || c.title) || c.title : tidyTitle(c.title) || c.title;
  const poster = coll ? c.collection!.poster || c.poster || parts[0]?.poster : c.poster;
  const meta = coll
    ? ["系列 · 共 " + parts.length + " 部", c.source === "douban" ? "豆瓣" : "TMDB"].join(" · ")
    : [c.year || "", c.media === "movie" ? "电影" : "剧集", c.media === "tv" && c.seasons ? "共 " + c.seasons + " 季" : "", c.source === "tmdb" ? "TMDB" : "豆瓣"]
        .filter(Boolean)
        .join(" · ");
  const seasons = c.media === "tv" && !coll ? seasonsOf(c) : [];
  return (
    <label
      className={cn(
        "flex cursor-pointer flex-wrap items-center gap-3 rounded-card border p-2.5 transition duration-fast ease-standard sm:flex-nowrap",
        selected ? "border-accent-line bg-accent-soft" : "border-line-subtle hover:border-line hover:bg-hover",
      )}
    >
      <input type="radio" name="sd-cand" className="sr-only" checked={selected} onChange={onSelect} />
      <Poster src={poster} title={title} size="sm" />
      <span className="flex min-w-0 flex-1 flex-col">
        <b className="truncate text-body font-medium text-fg">{title}</b>
        {!coll && c.original_title && c.original_title !== c.title && <span className="truncate text-caption text-fg-subtle">{c.original_title}</span>}
        <span className="text-caption text-fg-muted">{meta}</span>
      </span>
      {coll && (
        <Select
          aria-label="选择第几部"
          value={pick}
          className="h-control-sm w-full text-footnote sm:w-44"
          onClick={onSelect}
          onChange={(e) => {
            onSelect();
            onPick(e.target.value);
          }}
        >
          {parts.map((p) => (
            <option key={p.index} value={String(p.index)}>
              {["第 " + p.index + " 部", tidyTitle(p.title), p.year || ""].filter(Boolean).join(" · ") + (p.released === false ? "（未上映）" : "")}
            </option>
          ))}
          {parts.length > 1 && <option value="all">整个系列（{parts.length} 部）</option>}
        </Select>
      )}
      {seasons.length > 0 && (
        <Select
          aria-label="选择季"
          value={pick}
          className="h-control-sm w-full text-footnote sm:w-36"
          onClick={onSelect}
          onChange={(e) => {
            onSelect();
            onPick(e.target.value);
          }}
        >
          {seasons.map((s) => {
            const n = c.episodes && c.episodes[s];
            return (
              <option key={s} value={String(s)}>
                第 {s} 季{n ? " · " + n + " 集" : ""}
              </option>
            );
          })}
          {seasons.length > 1 && c.source !== "douban" && c.id && <option value="all">全部季（{seasons.length} 季）</option>}
        </Select>
      )}
    </label>
  );
}

export function SubscribeDialog({
  open,
  target,
  autoSave,
  onDone,
}: {
  open: boolean;
  target: SubscribeTarget;
  autoSave: boolean;
  onDone: (sub: any | null) => void;
}) {
  const { me } = useMe();
  const toast = useToast();
  const loginIfNeeded = useLoginIfNeeded();
  const [q, setQ] = useState("");
  const [searched, setSearched] = useState(""); // 最近一次查找用的名字（关键词选项里显示）
  const [cands, setCands] = useState<Cand[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [sel, setSel] = useState<number | "kw">("kw");
  const [picks, setPicks] = useState<Record<number, string>>({});
  const [auto, setAuto] = useState(false);
  const [join, setJoin] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [rules, setRules] = useState(() => initialRules(DIALOG_RULES, null));
  const [busy, setBusy] = useState(false);
  const [hidden, setHidden] = useState(false); // 扫码弹窗叠在上面时先收起
  const closed = useRef(true);
  const inputRef = useRef<HTMLInputElement>(null);
  const lookupSeq = useRef(0);

  async function lookup(nameArg?: string) {
    const name = (nameArg ?? q).trim();
    setCands(null);
    if (!name) {
      inputRef.current?.focus();
      return;
    }
    const seq = ++lookupSeq.current;
    setLoading(true);
    let list: Cand[] = [];
    try {
      const resp = await fetch("/api/media/search?q=" + encodeURIComponent(name));
      if (resp.status === 429) toast(rateLimitText(resp, await resp.json().catch(() => ({}))), "error", 4000);
      else list = resp.ok ? await resp.json() : [];
    } catch {
      list = [];
    }
    if (seq !== lookupSeq.current) return;
    list = Array.isArray(list) ? list.slice(0, 10) : [];
    setLoading(false);
    setSearched(name);
    setCands(list);
    setPicks(Object.fromEntries(list.map((c, i) => [i, defaultPick(c)])));
    setSel(list.length ? 0 : "kw");
  }

  useEffect(() => {
    if (!open) return;
    closed.current = false;
    setQ(target.resource || "");
    setCands(null);
    setSel("kw");
    setPicks({});
    setAuto(!!autoSave);
    setJoin(false);
    setMoreOpen(false);
    setRules(initialRules(DIALOG_RULES, null));
    setBusy(false);
    setHidden(false);
    if (target.resource) lookup(target.resource);
    else setTimeout(() => inputRef.current?.focus(), 50);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const finish = (sub: any | null) => {
    if (closed.current) return;
    closed.current = true;
    lookupSeq.current++;
    onDone(sub);
  };

  const c = typeof sel === "number" && cands ? cands[sel] : null;
  const pick = typeof sel === "number" ? picks[sel] || "" : "";
  const all = !!c && pick === "all";
  const allTv = all && !isColl(c!) && c!.media === "tv";
  const isMovie = !!c && (c.media === "movie" || isColl(c));

  // 发起订阅；需要登录时引导扫码，成功后重试一次。返回订阅对象或 null
  async function subscribe(payload: Record<string, any>): Promise<any | null> {
    const res = await subApi("", "POST", { client_id: clientId, ...payload });
    if (res.ok) {
      const sub = res.body;
      toast(
        "已订阅《" + sub.resource + "》" + (payload.auto_save ? "，正在后台检查，有资源会自动转存到你的网盘" : "，有资源或更新时会在「我的订阅」提醒"),
        "ok",
        3500,
      );
      switchTab(subMedia(sub));
      loadSubsTwice();
      return sub;
    }
    if (await loginIfNeeded(res, payload.auto_save ? "自动转存需要先扫码登录夸克" : "订阅追剧需要先扫码登录夸克")) return subscribe(payload);
    return null;
  }

  // 整个系列 / 一部剧的全部季：后端为每一部（季）建一个订阅，只占 1 个名额
  async function subscribeGroup(payload: Record<string, any>, name: string, tv: boolean): Promise<any | null> {
    const res = await subApi("/collection", "POST", { client_id: clientId, ...payload });
    if (res.ok) {
      const list = Array.isArray(res.body) ? res.body : [];
      toast(
        tv
          ? list.length
            ? "已订阅《" + name + "》的 " + list.length + " 季（只占 1 个订阅名额）" + (payload.auto_join ? "，以后出新季会自动加入" : "")
            : "《" + name + "》的每一季都已经订阅过或已完成"
          : list.length
            ? "已订阅《" + name + "》系列的 " + list.length + " 部电影（只占 1 个订阅名额）" + (payload.auto_join ? "，以后出新作会自动加入" : "")
            : "《" + name + "》系列的电影都已经订阅过或已完成",
        "ok",
        4000,
      );
      switchTab(tv ? "tv" : "movie");
      loadSubsTwice();
      return list[0] || { resource: name };
    }
    if (await loginIfNeeded(res, payload.auto_save ? "自动转存需要先扫码登录夸克" : "订阅追剧需要先扫码登录夸克")) return subscribeGroup(payload, name, tv);
    return null;
  }

  async function run(task: () => Promise<any | null>) {
    setBusy(true);
    setHidden(true);
    const sub = await task();
    if (sub || closed.current) {
      finish(sub);
      return;
    }
    setHidden(false);
    setBusy(false);
  }

  const submit = () => {
    const name = q.trim();
    if (name.length < 2 && !c) {
      toast("片名至少 2 个字", "error");
      inputRef.current?.focus();
      return;
    }
    const activeRules = isMovie ? DIALOG_RULES_MOVIE : DIALOG_RULES;
    const r = readRules(activeRules, rules, null);
    const autoOn = me.login && auto;

    if (c && all) {
      const body: Record<string, any> = {
        collection_id: allTv ? "tv:" + c.id : String(c.collection!.id),
        auto_join: join,
        auto_save: !!autoOn,
      };
      PASS_KEYS.forEach((k) => {
        if (r[k] !== undefined && r[k] !== "") body[k] = r[k];
      });
      const gname = allTv ? tidyTitle(c.title) || c.title : collectionName(c.collection!.name) || c.title;
      run(() => subscribeGroup(body, gname, allTv));
      return;
    }

    const payload: Record<string, any> = {};
    if (c && isColl(c)) {
      // 选了系列里的某一部：按普通电影订阅
      const parts = collectionParts(c);
      const p = parts.find((x) => String(x.index) === pick) || parts[0];
      const t = tidyTitle(p.title) || p.title;
      payload.media = "movie";
      payload.tmdb_id = p.id ? String(p.id) : undefined;
      payload.year = p.year || undefined;
      payload.poster = p.poster || c.collection!.poster || undefined;
      payload.resource = t;
      payload.query = t.length >= 2 ? t : name;
      payload.collection_id = c.collection!.id ? String(c.collection!.id) : undefined;
      payload.collection_name = collectionName(c.collection!.name) || undefined;
      payload.collection_index = p.index;
    } else if (c) {
      payload.media = c.media;
      payload.year = c.year || undefined;
      payload.poster = c.poster || undefined;
      payload[c.source === "douban" ? "douban_id" : "tmdb_id"] = c.id || undefined;
      const t = tidyTitle(c.title) || c.title;
      payload.resource = t;
      payload.query = t.length >= 2 ? t : target.query || name;
      if (c.media === "tv") {
        payload.season = pick ? Number(pick) : 1;
        if (payload.season > 1) payload.query = t + " 第" + payload.season + "季";
      }
    } else {
      payload.resource = name;
      payload.query = name === target.resource && target.query ? target.query : name;
    }
    Object.keys(r).forEach((k) => {
      if (r[k] !== "") payload[k] = r[k];
    });
    if (c && c.media === "movie") delete payload.start_episode;
    if (autoOn) payload.auto_save = true;
    Object.keys(payload).forEach((k) => payload[k] === undefined && delete payload[k]);
    run(() => subscribe(payload));
  };

  const okText = !all ? "订阅" : allTv ? "订阅全部季" : "订阅整个系列";

  return (
    <Dialog open={open && !hidden} onOpenChange={(o) => !o && !busy && finish(null)}>
      <DialogContent className="max-h-full max-w-xl overflow-hidden">
        <DialogHeader>
          <DialogTitle>{target.resource ? "订阅《" + target.resource + "》" : "新订阅"}</DialogTitle>
          <DialogDescription className="text-footnote">选中对应的影视条目，订阅会按季追踪缺的集；找不到也可以直接按关键词订阅。</DialogDescription>
        </DialogHeader>

        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            lookup();
          }}
        >
          <div className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-fg-subtle" />
            <Input ref={inputRef} type="search" placeholder="片名，如 繁花、沙丘" maxLength={100} aria-label="片名" value={q} onChange={(e) => setQ(e.target.value)} className="pl-9" />
          </div>
          <Button type="submit" variant="secondary">
            查找
          </Button>
        </form>

        <div className="-mx-6 flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-6 pb-1">
          {(loading || cands) && (
            <div role="radiogroup" className="flex flex-col gap-2">
              {loading && [0, 1, 2].map((i) => <Skeleton key={i} className="h-19 w-full rounded-card" />)}
              {!loading &&
                cands &&
                cands.map((cand, i) => (
                  <CandRow
                    key={i}
                    c={cand}
                    selected={sel === i}
                    pick={picks[i] || ""}
                    onSelect={() => setSel(i)}
                    onPick={(v) => setPicks({ ...picks, [i]: v })}
                  />
                ))}
              {!loading && cands && (
                <label
                  className={cn(
                    "flex cursor-pointer items-center gap-3 rounded-card border p-2.5 transition duration-fast ease-standard",
                    sel === "kw" ? "border-accent-line bg-accent-soft" : "border-line-subtle hover:border-line hover:bg-hover",
                  )}
                >
                  <input type="radio" name="sd-cand" className="sr-only" checked={sel === "kw"} onChange={() => setSel("kw")} />
                  <span aria-hidden className="grid h-14 w-10 shrink-0 place-items-center rounded-sm bg-sunken text-fg-subtle">
                    <Hash className="size-4" />
                  </span>
                  <span className="flex min-w-0 flex-col">
                    <b className="truncate text-body font-medium text-fg">按关键词「{searched}」订阅</b>
                    <span className="text-caption text-fg-muted">{cands.length ? "上面都不对时选这个" : "没找到对应的影视条目，照常定期搜索和提醒"}</span>
                  </span>
                </label>
              )}
            </div>
          )}

          <div className="flex flex-col gap-3">
            {me.login && (
              <SwitchRow checked={auto} onChange={setAuto} tip={me.logged_in ? "剧集会立即补齐网盘里缺的集；电影有合适资源时存一次" : "需要先扫码登录夸克"}>
                自动转存到我的网盘
              </SwitchRow>
            )}
            {all && (
              <SwitchRow
                checked={join}
                onChange={setJoin}
                tip={allTv ? "每天查一次这部剧有没有新的一季，有就自动订阅并通知你" : "每天查一次这个系列有没有新片，有就自动订阅并通知你"}
              >
                {allTv ? "以后出新季自动加入" : "以后出新作自动加入"}
              </SwitchRow>
            )}
            <button
              type="button"
              aria-expanded={moreOpen}
              onClick={() => setMoreOpen(!moreOpen)}
              className="flex w-fit items-center gap-1.5 text-footnote text-fg-muted hover:text-fg"
            >
              <ChevronRight className={cn("size-3.5 transition-transform duration-fast", moreOpen && "rotate-90")} />
              更多规则（清晰度、关键词、起始集）
            </button>
            {moreOpen && (
              <div className="rounded-card bg-sunken p-4">
                <RuleFields rules={isMovie ? DIALOG_RULES_MOVIE : DIALOG_RULES} values={rules} onChange={setRules} sub={null} idPrefix="sd" />
              </div>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="secondary" disabled={busy} onClick={() => finish(null)}>
            取消
          </Button>
          <Button loading={busy} onClick={submit}>
            {okText}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

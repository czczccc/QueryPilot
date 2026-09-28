// 一条订阅：海报 + 标题与状态 + 进度 + 规则 + 自动转存 + 设置面板
import { AnimatePresence, motion } from "framer-motion";
import { Check, ChevronRight, CircleAlert, FolderOpen, MoreHorizontal, RefreshCw, TriangleAlert } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Badge, Button, cn, Tooltip, transition } from "@cz/design-system";
import { cidParam, store, subApi } from "../../lib/api";
import { episodeRanges, formatTime, RES_LABEL, RES_RANK } from "../../lib/format";
import { useMe } from "../../lib/me";
import { useToast } from "../../lib/toast";
import { OrganizeDialog } from "./OrganizeDialog";
import { collectionName, loadSubs, loadSubsTwice, switchTab, subMedia, tidyTitle, type CollectionInfo, type HistoryEntry, type Sub } from "./store";
import { FAIL, initialRules, Poster, readRules, RuleFields, SUB_RULES, SwitchRow, useLoginIfNeeded } from "./ui";

type Tone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

function Tip({ tip, children }: { tip?: string; children: ReactNode }) {
  if (!tip) return <>{children}</>;
  return (
    <Tooltip content={<span className="max-w-72 whitespace-pre-line">{tip}</span>}>
      <span tabIndex={0} className="inline-flex outline-none focus-visible:shadow-ring">
        {children}
      </span>
    </Tooltip>
  );
}

const STATE_BADGE: Record<string, [string, Tone, string]> = {
  new: ["首次搜索中", "info", "刚订阅，服务器正在第一次搜索"],
  active: ["订阅中", "success", "定期重搜，有资源、新集或更高清时提醒"],
  pending: ["待定", "warning", "没识别出条目或不知道总集数：照常搜索和提醒，但不会自动完成"],
  paused: ["已暂停", "neutral", "暂停期间不检查，恢复后立即检查一次"],
};

// 状态徽章：检查失败时不再显示「首次搜索中」；没上映的电影显示「未上映」而不是「待定」
function StateBadge({ sub }: { sub: Sub }) {
  let b: [string, Tone, string];
  const today = new Date().toISOString().slice(0, 10);
  if (sub.last_error && (sub.state === "new" || sub.state === "active")) b = ["检查失败", "warning", "上次检查失败，服务器稍后会自动重试"];
  else if (sub.state === "pending" && sub.media === "movie" && (sub.collection_id || sub.release_date) && (!sub.release_date || sub.release_date > today))
    b = ["未上映", "neutral", sub.release_date ? "上映日期 " + sub.release_date + "，上映后自动开始搜" : "还没定档，上映后自动开始搜"];
  else if (sub.state === "pending" && sub.media === "tv" && String(sub.collection_id || "").startsWith("tv:"))
    b = ["未开播", "neutral", "这一季还没开播，开播后自动开始搜"];
  else b = STATE_BADGE[sub.state || ""] || STATE_BADGE.active;
  return (
    <Tip tip={b[2]}>
      <Badge tone={b[1]} dot>
        {b[0]}
      </Badge>
    </Tip>
  );
}

// 最近一次检查的摘要（frontend-api.md §27）：优先用后端给的一句话 reason
function checkSummary(c: any): string {
  if (!c || typeof c !== "object") return "";
  if (c.reason) return c.reason;
  const parts: string[] = [];
  if (c.found != null) parts.push("搜到 " + c.found + " 条");
  if (c.matched != null) parts.push("确认是这部 " + c.matched + " 条");
  if (c.saved) parts.push("存了 " + c.saved + " 集");
  return parts.join("，");
}
function checkReasons(c: any): string {
  const r = c && c.reasons;
  if (!r || typeof r !== "object") return "";
  return Object.entries(r)
    .map(([k, n]) => k + " " + n + " 条")
    .join("\n");
}

function Progress({ sub }: { sub: Sub }) {
  if (sub.media === "movie") {
    const got = sub.saved_episodes && sub.saved_episodes.length;
    const savedRes = (sub.versions || {})["0"];
    const res = RES_LABEL[got && savedRes ? savedRes : sub.best_resolution];
    const goal = sub.upgrade_to || "2160p";
    return (
      <p className="text-footnote text-fg-muted">
        {got ? "已存进网盘" + (res ? "（" + res + "）" : "") : res ? "已有资源，最高 " + res : "等待资源"}
        {got && sub.upgrade && savedRes && RES_RANK[savedRes] < RES_RANK[goal] && <span className="text-warning-fg">　等 {RES_LABEL[goal]} 版本</span>}
      </p>
    );
  }
  const total = sub.total_episodes;
  if (!total) {
    return <p className="text-footnote text-fg-muted">{(sub.best_episodes ? "已见 " + sub.best_episodes + " 集" : "还没有资源") + " · 总集数未知"}</p>;
  }
  const lack: number[] = sub.lack_episodes || [];
  const range = total - (sub.start_episode || 1) + 1;
  const done = Math.max(0, range - lack.length);
  const pct = range ? Math.round((done / range) * 100) : 0;
  return (
    <div className="flex flex-col gap-1.5">
      <div role="progressbar" aria-valuemin={0} aria-valuemax={range} aria-valuenow={done} className="h-1 w-full overflow-hidden rounded-full bg-sunken">
        <motion.span
          className={cn("block h-full rounded-full", done >= range ? "bg-success" : "bg-accent")}
          initial={{ width: 0 }}
          animate={{ width: pct + "%" }}
          transition={transition.emphasized}
        />
      </div>
      <p className="text-footnote text-fg-muted">
        <b className="font-medium text-fg tabular-nums">
          已存 {done} / {range}
        </b>{" "}
        集{lack.length > 0 && lack.length < range && <span className="text-warning-fg">　缺 {episodeRanges(lack)}</span>}
      </p>
    </div>
  );
}

// 每集已存的清晰度：一集一个小格子；开了洗版时没达到目标的标黄
function VersionStrip({ sub }: { sub: Sub }) {
  const versions: Record<string, string> = sub.versions || {};
  const goal = RES_RANK[sub.upgrade_to || "2160p"];
  const below = (res?: string) => sub.upgrade && res && RES_RANK[res] < goal;
  if (sub.media === "movie") return null;
  if (!Object.keys(versions).length && !sub.upgrade) return null;
  const total = sub.total_episodes;
  const start = sub.start_episode || 1;
  const saved = new Set<number>(sub.saved_episodes || []);
  if (!total || !saved.size || total - start + 1 > 60) return null;
  const chips = [];
  for (let e = start; e <= total; e++) {
    const res = versions[String(e)];
    const has = saved.has(e);
    const title = "第 " + e + " 集：" + (!has ? "还没存" : res ? "已存 " + (RES_LABEL[res] || res) : "已存，认不出清晰度") + (below(res) ? "，等更高清的版本" : "");
    chips.push(
      <span
        key={e}
        title={title}
        className={cn(
          "flex min-w-9 flex-col items-center rounded-xs border px-1 py-0.5 leading-none",
          !has ? "border-dashed border-line text-fg-subtle" : below(res) ? "border-warning-line bg-warning-soft text-warning-fg" : "border-success-line bg-success-soft text-success-fg",
        )}
      >
        <b className="text-caption font-medium tabular-nums">{e}</b>
        {has && <span className="text-caption opacity-80">{res ? RES_LABEL[res] || res : "?"}</span>}
      </span>,
    );
  }
  return (
    <div aria-label="每集已存的清晰度" className="flex flex-wrap gap-1">
      {chips}
    </div>
  );
}

// 规则摘要：≥1080p · 含「内嵌」· 排除「枪版」· 从第 3 集
function rulesSummary(sub: Sub) {
  return [
    sub.resolution ? "≥" + (RES_LABEL[sub.resolution] || sub.resolution) : "",
    sub.include ? "含「" + sub.include + "」" : "",
    sub.exclude ? "排除「" + sub.exclude + "」" : "",
    sub.media === "tv" && sub.start_episode > 1 ? "从第 " + sub.start_episode + " 集" : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

/** 两次点击确认的危险按钮：第一次只是确认，3 秒后复原 */
function ConfirmButton({ label, confirmLabel, onConfirm, title }: { label: string; confirmLabel: string; onConfirm: () => Promise<void>; title?: string }) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <Button
      variant={armed ? "danger" : "ghost"}
      size="sm"
      title={title}
      disabled={busy}
      className={cn(!armed && "text-danger-fg hover:text-danger-fg")}
      onClick={async () => {
        if (!armed) {
          setArmed(true);
          setTimeout(() => setArmed(false), 3000);
          return;
        }
        setBusy(true);
        await onConfirm();
        setBusy(false);
      }}
    >
      {armed ? confirmLabel : label}
    </Button>
  );
}

// 立即检查：同步重搜，可能要几十秒；同一订阅 2 分钟冷却
function CheckNowButton({ sub }: { sub: Sub }) {
  const toast = useToast();
  const loginIfNeeded = useLoginIfNeeded();
  const [busy, setBusy] = useState(false);
  if (sub.state === "paused") return null;
  return (
    <Button
      variant="secondary"
      size="sm"
      loading={busy}
      title={"马上重搜一次" + (sub.auto_save ? "，并补齐网盘里缺的集" : "")}
      onClick={async () => {
        setBusy(true);
        try {
          const res = await subApi("/" + sub.id + "/check", "POST");
          if (res.ok) {
            const notes = res.body.notifications || [];
            if (!notes.length) toast("《" + sub.resource + "》暂时没有变化");
            else notes.slice(0, 3).forEach((n: any) => toast(n.message, n.kind === "auto_save_failed" ? "error" : "ok", 5000));
            await loadSubs();
            setBusy(false);
            return;
          }
          if (await loginIfNeeded(res, "检查订阅需要先扫码登录夸克")) loadSubs();
        } catch {
          toast("检查失败，请稍后重试", "error");
        }
        setBusy(false);
      }}
    >
      {!busy && <RefreshCw />}
      {busy ? "检查中…" : "立即检查"}
    </Button>
  );
}

const EDIT_RULES_TV = SUB_RULES;
const EDIT_RULES_MOVIE = SUB_RULES.filter((r) => !r.tvOnly);

function SubEditor({ sub }: { sub: Sub }) {
  const { me } = useMe();
  const toast = useToast();
  const rules = sub.media === "movie" ? EDIT_RULES_MOVIE : EDIT_RULES_TV;
  const [values, setValues] = useState(() => initialRules(rules, sub));
  const [busy, setBusy] = useState<string | null>(null);
  const [organize, setOrganize] = useState(false);
  const paused = sub.state === "paused";

  const saveRules = async () => {
    const patch = readRules(rules, values, sub);
    if (!Object.keys(patch).length) {
      toast("规则没有变化");
      return;
    }
    setBusy("save");
    const res = await subApi("/" + sub.id, "PATCH", patch).catch(() => FAIL);
    setBusy(null);
    if (res.ok) {
      toast(patch.upgrade ? "已开启《" + sub.resource + "》洗版，正在后台检查一次更高清的版本" : "已保存《" + sub.resource + "》的规则", "ok", patch.upgrade ? 4000 : 2200);
      if (patch.upgrade) loadSubsTwice();
      else loadSubs();
    } else toast(res.body.detail || "保存失败", "error");
  };

  const togglePause = async () => {
    setBusy("pause");
    const res = await subApi("/" + sub.id, "PATCH", { paused: !paused }).catch(() => FAIL);
    if (res.ok) {
      toast(paused ? "已恢复《" + sub.resource + "》，正在后台检查一次" : "已暂停《" + sub.resource + "》", "ok");
      if (paused) loadSubsTwice();
      else loadSubs();
    } else {
      setBusy(null);
      toast(res.body.detail || "操作失败", "error");
    }
  };

  const complete = async () => {
    setBusy("complete");
    const res = await subApi("/" + sub.id + "/complete", "POST").catch(() => FAIL);
    if (res.ok) {
      toast("《" + sub.resource + "》已完成，移入订阅历史", "ok");
      loadSubs();
    } else {
      setBusy(null);
      toast(res.body.detail || "操作失败", "error");
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: "auto", transition: transition.base }}
      exit={{ opacity: 0, height: 0, transition: transition.exit }}
      className="overflow-hidden"
    >
      <div className="mt-4 flex flex-col gap-4 border-t border-line-subtle pt-4">
        <RuleFields rules={rules} values={values} onChange={setValues} sub={sub} idPrefix={"sub" + sub.id} />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-1.5">
            {me.login && (
              <Button variant="secondary" size="sm" title="把这个订阅存过的文件移到同一个目录、按标准命名；重复版本可以勾选删除" onClick={() => setOrganize(true)}>
                <FolderOpen />
                整理网盘目录
              </Button>
            )}
            <Button variant="secondary" size="sm" loading={busy === "pause"} disabled={!!busy} onClick={togglePause}>
              {paused ? "恢复订阅" : "暂停"}
            </Button>
            <Button variant="secondary" size="sm" loading={busy === "complete"} disabled={!!busy} title="不再追这个订阅，移入订阅历史，之后可以一键重新订阅" onClick={complete}>
              标记完成
            </Button>
            <ConfirmButton
              label="取消订阅"
              confirmLabel="确定取消？"
              onConfirm={async () => {
                await subApi("/" + sub.id, "DELETE").catch(() => FAIL);
                toast("已取消订阅《" + sub.resource + "》");
                loadSubs();
              }}
            />
          </div>
          <Button size="sm" loading={busy === "save"} disabled={!!busy} onClick={saveRules}>
            保存规则
          </Button>
        </div>
      </div>
      {me.login && <OrganizeDialog sub={sub} open={organize} onClose={() => setOrganize(false)} />}
    </motion.div>
  );
}

function AutoSaveSwitch({ sub, grouped }: { sub: Sub; grouped?: boolean }) {
  const toast = useToast();
  const loginIfNeeded = useLoginIfNeeded();
  const [checked, setChecked] = useState(!!sub.auto_save);
  const [busy, setBusy] = useState(false);
  const scope = grouped ? (sub.media === "movie" ? "这部：" : "这一季：") : "";
  const tip =
    (sub.media === "movie" ? "出现满足清晰度要求的资源时存一次；之后更高清只提醒" : "打开后立即把网盘里缺的集补齐，之后出新集也自动转存") +
    (grouped ? "。只影响这一项，不影响整组设置" : "");
  const change = async (want: boolean): Promise<void> => {
    setChecked(want);
    setBusy(true);
    try {
      const res = await subApi("/" + sub.id, "PATCH", { auto_save: want });
      if (res.ok) {
        toast(
          want ? "已开启《" + sub.resource + "》自动转存，正在后台补齐网盘缺的集，稍后在转存记录里查看" : "已关闭《" + sub.resource + "》自动转存",
          "ok",
          want ? 4000 : 2200,
        );
        if (want) loadSubsTwice();
        else loadSubs();
      } else {
        setChecked(!want);
        if (await loginIfNeeded(res, "自动转存需要先扫码登录夸克")) {
          setBusy(false);
          return change(want);
        }
      }
    } catch {
      setChecked(!want);
      toast("设置失败，请稍后重试", "error");
    }
    setBusy(false);
  };
  return (
    <SwitchRow checked={checked} disabled={busy} onChange={change} tip={tip}>
      {scope + (sub.media === "movie" ? "有资源自动转存" : "自动转存补齐缺集")}
    </SwitchRow>
  );
}

type SaveRow = { ok: boolean; ts: number; file_count?: number; folder?: string; message?: string };

function SaveLog({ sub }: { sub: Sub }) {
  const [rows, setRows] = useState<SaveRow[] | "loading" | "error" | null>(null);
  const toggle = async () => {
    if (rows) {
      setRows(null);
      return;
    }
    setRows("loading");
    try {
      const resp = await fetch("/api/subscriptions/" + sub.id + "/saves?" + cidParam());
      setRows(resp.ok ? await resp.json() : []);
    } catch {
      setRows("error");
    }
  };
  return (
    <>
      <Button variant="link" size="sm" aria-expanded={!!rows} onClick={toggle}>
        转存记录
      </Button>
      {rows && (
        <ul className="order-last flex w-full flex-col gap-1.5 rounded-md bg-sunken p-3 text-footnote">
          {rows === "loading" && <li className="text-fg-subtle">加载中…</li>}
          {rows === "error" && <li className="text-danger-fg">记录加载失败</li>}
          {Array.isArray(rows) && !rows.length && <li className="text-fg-subtle">还没有自动转存过；发现缺的集时会自动存进你的网盘。</li>}
          {Array.isArray(rows) &&
            rows.slice(0, 20).map((r, i) => (
              <li key={i} className="flex min-w-0 items-start gap-2">
                {r.ok ? <Check className="mt-0.5 size-3.5 shrink-0 text-success" /> : <CircleAlert className="mt-0.5 size-3.5 shrink-0 text-danger" />}
                <span className="shrink-0 text-fg-subtle tabular-nums">{formatTime(r.ts)}</span>
                <span className={cn("min-w-0 break-all", r.ok ? "text-fg-muted" : "text-danger-fg")}>
                  {r.ok ? (r.file_count ? r.file_count + " 个文件 → " : "") + (r.folder || "网盘默认目录") : r.message || "转存失败"}
                </span>
              </li>
            ))}
        </ul>
      )}
    </>
  );
}

export function SubItem({ sub, grouped }: { sub: Sub; grouped?: boolean }) {
  const { login } = useMe();
  const [editing, setEditing] = useState(false);
  const paused = sub.state === "paused";
  const tags = [
    sub.season_year || (sub.state === "pending" && sub.season ? "" : sub.year) || "",
    String(sub.collection_id || "").startsWith("tv:")
      ? (grouped ? "" : "剧集 · ") + "第 " + (sub.season || sub.collection_index || 1) + " 季"
      : sub.collection_name
        ? (grouped ? "" : collectionName(sub.collection_name) + " 系列 · ") + (sub.collection_index ? "第 " + sub.collection_index + " 部" : "电影")
        : sub.media === "movie"
          ? "电影"
          : sub.media === "tv"
            ? "剧集"
            : "按关键词",
    sub.last_error ? "" : sub.last_checked ? "检查于 " + formatTime(sub.last_checked) : "尚未检查",
  ]
    .filter(Boolean)
    .join(" · ");
  const summary = checkSummary(sub.last_check);
  const why = checkReasons(sub.last_check);
  const rules = rulesSummary(sub);
  const goal = RES_LABEL[sub.upgrade_to || "2160p"];

  return (
    <li
      data-sub-id={sub.id}
      className={cn(
        "rounded-card border border-line-subtle bg-surface p-4 transition duration-base ease-standard hover:border-line sm:p-5",
        paused && "bg-canvas",
        grouped && "border-0 bg-transparent px-0 hover:border-0 sm:px-0",
      )}
    >
      <div className="flex gap-4">
        <div className={cn(paused && "opacity-60 grayscale")}>
          <Poster src={sub.poster} title={sub.resource} />
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <div className="flex items-start justify-between gap-3">
            <div className="flex min-w-0 flex-col gap-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <b className="min-w-0 text-title-sm text-fg">《{tidyTitle(sub.resource)}》</b>
                <StateBadge sub={sub} />
                {sub.upgrade &&
                  (sub.upgrade_done ? (
                    <Tip tip={"已存的都达到 " + goal}>
                      <Badge tone="success">已洗版</Badge>
                    </Tip>
                  ) : (
                    <Tip tip="出现更高清的分享时自动再存一份，旧版本在「整理网盘目录」里确认删除">
                      <Badge tone="accent">洗版中 → {goal}</Badge>
                    </Tip>
                  ))}
              </div>
              <span className="text-footnote text-fg-subtle">{tags}</span>
              {summary && (
                <span className="text-footnote text-fg-muted">
                  {why ? (
                    <Tip tip={"筛掉的原因：\n" + why}>
                      <span className="cursor-help underline decoration-line-strong decoration-dotted underline-offset-4">上次检查：{summary}</span>
                    </Tip>
                  ) : (
                    "上次检查：" + summary
                  )}
                </span>
              )}
              {sub.last_error && (
                <span className="flex items-start gap-1.5 text-footnote text-danger-fg">
                  <CircleAlert className="mt-0.5 size-3.5 shrink-0" />
                  <span className="min-w-0 break-all">
                    上次检查失败：{sub.last_error}
                    {sub.last_checked ? " · " + formatTime(sub.last_checked) : ""}
                  </span>
                </span>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <span className="max-sm:hidden">
                <CheckNowButton sub={sub} />
              </span>
              <Button variant="ghost" size="icon-sm" title="订阅设置" aria-label="订阅设置" aria-expanded={editing} onClick={() => setEditing(!editing)}>
                <MoreHorizontal />
              </Button>
            </div>
          </div>

          <Progress sub={sub} />
          <VersionStrip sub={sub} />
          {(rules || sub.folder) && (
            <div className="flex flex-col gap-0.5 text-caption text-fg-subtle">
              {rules && <span>{rules}</span>}
              {sub.folder && (
                <span className="truncate" title={sub.folder}>
                  存到：{sub.folder}
                </span>
              )}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <AutoSaveSwitch key={String(!!sub.auto_save)} sub={sub} grouped={grouped} />
            {sub.auto_save && <SaveLog sub={sub} />}
            <span className="sm:hidden">
              <CheckNowButton sub={sub} />
            </span>
          </div>
        </div>
      </div>

      {sub.auto_save && sub.auto_save_status === "login_expired" && (
        <button
          type="button"
          onClick={async () => {
            if (await login("重新扫码后，《" + sub.resource + "》的自动转存会自动恢复")) loadSubs();
          }}
          className="mt-4 flex w-full items-center gap-2 rounded-md border border-warning-line bg-warning-soft px-3 py-2 text-left text-footnote text-warning-fg hover:bg-warning-soft/70"
        >
          <TriangleAlert className="size-4 shrink-0" />
          夸克登录已失效，自动转存已暂停 · 点此重新扫码
        </button>
      )}
      <AnimatePresence initial={false}>{editing && <SubEditor key="edit" sub={sub} />}</AnimatePresence>
    </li>
  );
}

// ---- 系列 / 全部季分组 ----
function groupSummary(subs: Sub[], isTv: boolean) {
  if (!isTv) {
    const saved = subs.filter((s) => s.saved_episodes && s.saved_episodes.length).length;
    return "已存 " + saved + "/" + subs.length + " 部";
  }
  const full = subs.filter((s) => s.total_episodes && !(s.lack_episodes || []).length).length;
  const lack = subs.reduce((n, s) => n + (s.lack_episodes || []).length, 0);
  return "存齐 " + full + "/" + subs.length + " 季" + (lack ? "，缺 " + lack + " 集" : "");
}

const GROUP_OPEN_KEY = "qp_group_open";
function openGroups(): Set<string> {
  const v = store.get<string[]>(GROUP_OPEN_KEY, []);
  return new Set(Array.isArray(v) ? v : []);
}
function setGroupOpen(cid: string, open: boolean) {
  const set = openGroups();
  if (open) set.add(cid);
  else set.delete(cid);
  store.set(GROUP_OPEN_KEY, JSON.stringify([...set].slice(-200)));
}

function SeriesGroup({ subs: raw, collections }: { subs: Sub[]; collections: CollectionInfo[] }) {
  const toast = useToast();
  const subs = raw.slice().sort((a, b) => (a.collection_index || 0) - (b.collection_index || 0));
  const cid = String(subs[0].collection_id);
  const info = collections.find((c) => String(c.collection_id) === cid);
  const isTv = cid.startsWith("tv:");
  const name = (isTv ? tidyTitle(info?.name || subs[0].collection_name) : collectionName(info?.name || subs[0].collection_name)) || (isTv ? "剧集" : "系列");
  const unit = isTv ? "季" : "部";
  const label = isTv ? "《" + name + "》" : "《" + name + "》系列";
  const [open, setOpen] = useState(() => openGroups().has(cid));
  const [autoJoin, setAutoJoin] = useState(!!info?.auto_join);
  const [joinBusy, setJoinBusy] = useState(false);

  const toggleJoin = async (want: boolean) => {
    setAutoJoin(want);
    setJoinBusy(true);
    const res = await subApi("/collection/" + encodeURIComponent(cid), "PATCH", { auto_join: want }).catch(() => FAIL);
    setJoinBusy(false);
    if (res.ok) {
      if (info) info.auto_join = want;
      toast(want ? label + "以后出新" + (isTv ? "季" : "作") + "会自动订阅" : "已关闭" + label + "的新" + (isTv ? "季" : "作") + "自动加入", "ok");
    } else if (res.status === 404) loadSubs();
    else {
      setAutoJoin(!want);
      toast(res.body.detail || "操作失败", "error");
    }
  };

  return (
    <li className="rounded-card border border-line-subtle bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-3 p-4 sm:px-5">
        <div className="flex min-w-0 flex-col gap-0.5">
          <button
            type="button"
            aria-expanded={open}
            title={open ? "收起" : "展开"}
            onClick={() => {
              setOpen(!open);
              setGroupOpen(cid, !open);
            }}
            className="flex min-w-0 items-center gap-1.5 text-left text-title-sm text-fg hover:text-accent-fg"
          >
            <ChevronRight className={cn("size-4 shrink-0 text-fg-subtle transition-transform duration-fast", open && "rotate-90")} />
            <b className="min-w-0 truncate font-medium">{isTv ? name : name + " 系列"}</b>
          </button>
          <span className="pl-5.5 text-footnote text-fg-subtle">
            订阅中 {subs.length} {unit} · {groupSummary(subs, isTv)}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {info && info.collection_id !== undefined && (
            <SwitchRow
              checked={autoJoin}
              disabled={joinBusy}
              onChange={toggleJoin}
              tip={
                (isTv ? "整部剧的设置：每天查一次有没有新的一季，有就自动订阅并通知你" : "整个系列的设置：每天查一次有没有新片，有就自动订阅并通知你") +
                "。不影响下面每一项自己的自动转存开关"
              }
            >
              {isTv ? "整部剧：新季自动加入" : "整个系列：新作自动加入"}
            </SwitchRow>
          )}
          <ConfirmButton
            label={isTv ? "退订全部季" : "退订整个系列"}
            confirmLabel={"确定退订 " + subs.length + " " + unit + "？"}
            title={isTv ? "这部剧还在订阅中的季一起取消；已完成的历史不受影响" : "系列里还在订阅中的部一起取消；单独订阅的部和已完成的历史不受影响"}
            onConfirm={async () => {
              const res = await subApi("/collection/" + encodeURIComponent(cid), "DELETE").catch(() => FAIL);
              if (res.ok) toast("已退订" + label);
              else toast(res.body.detail || "操作失败", "error");
              loadSubs();
            }}
          />
        </div>
      </div>
      <AnimatePresence initial={false}>
        {open && (
          <motion.ul
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto", transition: transition.base }}
            exit={{ opacity: 0, height: 0, transition: transition.exit }}
            className="divide-y divide-line-subtle overflow-hidden border-t border-line-subtle px-4 sm:px-5"
          >
            {subs.map((s) => (
              <SubItem key={s.id} sub={s} grouped />
            ))}
          </motion.ul>
        )}
      </AnimatePresence>
    </li>
  );
}

/** 「订阅整个系列 / 全部季」建的订阅按系列折叠成一组，放在该系列第一部出现的位置 */
export function SubsList({ subs, collections }: { subs: Sub[]; collections: CollectionInfo[] }) {
  const out: (Sub | Sub[])[] = [];
  const groups: Record<string, Sub[]> = {};
  subs.forEach((sub) => {
    const key = sub.collection_id || (sub.show_id ? "tv:" + sub.show_id : "");
    if (!(sub.series || sub.show_id) || !key) {
      out.push(sub);
      return;
    }
    const s = sub.collection_id ? sub : { ...sub, collection_id: key };
    let g = groups[key];
    if (!g) {
      g = groups[key] = [];
      out.push(g);
    }
    g.push(s);
  });
  return (
    <>
      {out.map((x) =>
        Array.isArray(x) ? <SeriesGroup key={"g" + x[0].collection_id} subs={x} collections={collections} /> : <SubItem key={x.id} sub={x} />,
      )}
    </>
  );
}

// ---- 订阅历史的一条：可一键重新订阅或删除 ----
export function HistoryItem({ h }: { h: HistoryEntry }) {
  const toast = useToast();
  const loginIfNeeded = useLoginIfNeeded();
  const [busy, setBusy] = useState(false);
  const resubscribe = async (): Promise<void> => {
    setBusy(true);
    const res = await subApi("/history/" + h.id + "/resubscribe", "POST").catch(() => FAIL);
    if (res.ok) {
      toast("已重新订阅《" + res.body.resource + "》", "ok");
      switchTab(subMedia(res.body));
      loadSubs();
      return;
    }
    setBusy(false);
    if (await loginIfNeeded(res, "订阅追剧需要先扫码登录夸克")) return resubscribe();
  };
  return (
    <li className="flex gap-4 rounded-card border border-line-subtle bg-surface p-4 sm:p-5">
      <Poster src={h.poster} title={h.resource} />
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 flex-col gap-1">
            <div className="flex flex-wrap items-center gap-2">
              <b className="text-title-sm text-fg">《{tidyTitle(h.resource)}》</b>
              <Badge tone="success">已完成</Badge>
            </div>
            <span className="text-footnote text-fg-subtle">
              {[h.year || "", h.media === "movie" ? "电影" : h.media === "tv" ? "剧集" : "", h.reason, formatTime(h.completed) + " 完成"].filter(Boolean).join(" · ")}
            </span>
          </div>
        </div>
        {h.media === "tv" && h.total_episodes && (
          <span className="text-caption text-fg-subtle">
            共 {h.total_episodes} 集，存了 {h.saved_count} 集
          </span>
        )}
        <div className="mt-1 flex gap-1.5">
          <Button variant="secondary" size="sm" loading={busy} onClick={resubscribe}>
            重新订阅
          </Button>
          <Button
            variant="ghost"
            size="sm"
            title="删除这条历史记录"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              await subApi("/history/" + h.id, "DELETE").catch(() => FAIL);
              await loadSubs();
              setBusy(false);
            }}
          >
            删除
          </Button>
        </div>
      </div>
    </li>
  );
}

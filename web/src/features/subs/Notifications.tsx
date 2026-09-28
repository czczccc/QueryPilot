// 订阅提醒：卡片样式，按订阅分组；未读的一条条「已读」，已读的折叠起来
import { AnimatePresence, motion } from "framer-motion";
import { ChevronRight, ExternalLink, MoreHorizontal } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Badge, Button, cn, rise, transition } from "@cz/design-system";
import { cidParam, store } from "../../lib/api";
import { formatTime, relTime } from "../../lib/format";
import { useMe } from "../../lib/me";
import { useToast } from "../../lib/toast";
import { setNotes, tidyTitle, type Note, type Sub } from "./store";
import { Poster } from "./ui";

type Tone = "accent" | "info" | "success" | "danger" | "warning" | "neutral";

// 每种提醒的标签和一句话说明；后端给了结构化字段就用字段，没给就从 message 里取
const NOTE_KIND: Record<string, [string, Tone, string]> = {
  episodes: ["新集", "accent", ""],
  new_episodes: ["新集", "accent", ""],
  better_quality: ["更高清", "info", "出现了更高清的版本"],
  saved: ["已转存", "success", ""],
  save_failed: ["转存失败", "danger", ""],
  save_paused: ["转存暂停", "warning", ""],
  maybe: ["可能相关", "warning", "找到一个可能相关的资源，请自己核对是不是这部"],
  found: ["有资源了", "accent", "找到了新资源"],
  quality: ["更高清", "info", "出现了更高清的版本"],
  upgraded: ["已洗版", "accent", "已转存更高清的版本"],
  auto_saved: ["已转存", "success", ""],
  auto_save_failed: ["转存失败", "danger", ""],
  auto_save_paused: ["转存暂停", "warning", ""],
  completed: ["已完成", "success", ""],
  series_new: ["系列新作", "accent", ""],
  season_new: ["新的一季", "accent", ""],
  check_failed: ["检查失败", "warning", ""],
};

// 结构化字段优先（frontend-api.md），旧通知从文字里拆：「资源标题」和分享链接
function noteParts(n: Note) {
  const msg = String(n.message || "");
  const shareOk = n.share && /^[0-9a-zA-Z]{6,}$/.test(n.share);
  const url: string = n.url || (msg.match(/https?:\/\/pan\.quark\.cn\/s\/[0-9a-zA-Z]+/) || [])[0] || (shareOk ? "https://pan.quark.cn/s/" + n.share : "");
  const share: string = shareOk ? n.share : (url.match(/\/s\/([0-9a-zA-Z]+)/) || [])[1] || "";
  const titleM = msg.match(/资源[「『]([^」』]+)[」』]/);
  const type = n.type || n.kind;
  let kind = NOTE_KIND[type];
  if (type === "series_new" && /第\s*\d+\s*季/.test(msg)) kind = NOTE_KIND.season_new;
  let summary: string = n.summary || (kind && kind[2]) || "";
  let path = "";
  const savedM = msg.match(/转存.*?(\d+)\s*个新?文件到[「『]([^」』]+)[」』]/);
  if (savedM) {
    path = savedM[2];
    if (!n.resource_title) summary = `转存了 ${savedM[1]} 个新文件`;
  }
  if (!summary) {
    summary =
      msg
        .replace(/https?:\/\/\S+/g, "")
        .replace(/^《[^》]+》/, "")
        .replace(/[：:，,]\s*$/, "")
        .trim() || msg;
  }
  return {
    label: kind ? kind[0] : "提醒",
    tone: (kind ? kind[1] : "neutral") as Tone,
    summary,
    title: (n.resource_title as string) || (titleM ? titleM[1] : "") || (path ? "保存到 " + path : ""),
    url,
    share,
    pwd: (n.pwd as string) || null,
  };
}

type Group = { key: string; name: string; notes: Note[] };
function noteGroups(list: Note[]): Group[] {
  const groups: Group[] = [];
  const byKey: Record<string, Group> = {};
  list.forEach((n) => {
    const key = n.subscription_id != null ? "s" + n.subscription_id : "r" + (n.resource || "");
    if (!byKey[key]) {
      byKey[key] = { key, name: n.subscription_name || n.resource || "", notes: [] };
      groups.push(byKey[key]);
    }
    byKey[key].notes.push(n);
  });
  return groups;
}

// 先在本地标记（界面立即收起），再告诉服务器；失败时下次刷新会恢复
async function markNotesRead(all: Note[], list: Note[]) {
  const ids = new Set(list);
  const next = all.map((n) => (ids.has(n) ? { ...n, read: true } : n));
  setNotes(next);
  if (!next.some((n) => !n.read)) {
    await fetch("/api/notifications/read?" + cidParam(), { method: "POST" }).catch(() => {});
    return;
  }
  await Promise.all(
    list.filter((n) => n.id !== undefined).map((n) => fetch(`/api/notifications/${n.id}/read?` + cidParam(), { method: "POST" }).catch(() => {})),
  );
}

// 删除一条：以后同一个分享也不会再提醒
async function deleteNote(all: Note[], n: Note) {
  setNotes(all.filter((x) => x !== n));
  await fetch(`/api/notifications/${n.id}?` + cidParam(), { method: "DELETE" }).catch(() => {});
}

/** 提醒里的「转存」：和搜索结果的转存按钮同一个接口 */
function SaveButton({ share, pwd }: { share: string; pwd: string | null }) {
  const { save, login, reload } = useMe();
  const toast = useToast();
  const [state, setState] = useState<"idle" | "busy" | "done" | "limit" | "fail">("idle");
  const askToken = (force: boolean) => {
    let token = force ? null : store.getRaw("qp_save_token");
    if (!token) {
      token = window.prompt("请输入转存口令（服务器 .env 里的 SAVE_TOKEN，只保存在本浏览器）");
      if (token) store.set("qp_save_token", token);
    }
    return token;
  };
  const post = (token: string | null) =>
    fetch("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(token ? { "X-Save-Token": token } : {}) },
      body: JSON.stringify({ share, pwd: pwd || null }),
    });
  const run = async () => {
    let token: string | null = null;
    if (!save.logged_in) {
      if (save.login) {
        if (!(await login())) return;
      } else {
        token = askToken(false);
        if (!token) return;
      }
    }
    setState("busy");
    try {
      let resp = await post(token);
      if (resp.status === 401 && token) {
        token = askToken(true);
        resp = token ? await post(token) : resp;
      }
      const body = await resp.json().catch(() => ({}));
      const ok = resp.ok && body.ok;
      if (!ok && /重新扫码|先扫码/.test(body.message || body.detail || "")) reload();
      setState(ok ? "done" : resp.status === 429 ? "limit" : "fail");
      const msg = body.message || body.detail || (ok ? "已转存" : "转存失败");
      toast(ok ? "转存成功" + (body.folder ? "，已放进「" + body.folder + "」" : "") : msg, ok ? "ok" : "error");
    } catch {
      setState("fail");
      toast("网络出错，转存没有完成，请稍后重试", "error");
    }
  };
  const text = { idle: "转存", busy: "转存中…", done: "已转存 ✓", limit: "今天已达上限", fail: "转存失败" }[state];
  return (
    <Button variant="ghost" size="sm" loading={state === "busy"} disabled={state === "done"} onClick={run}>
      {text}
    </Button>
  );
}

function NoteMore({ onDelete }: { onDelete: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  return (
    <div ref={ref} className="relative">
      <Button variant="ghost" size="icon-sm" aria-label="更多操作" aria-expanded={open} onClick={() => setOpen(!open)}>
        <MoreHorizontal />
      </Button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0, transition: transition.snappy }}
            exit={{ opacity: 0, transition: transition.exit }}
            className="absolute right-0 z-20 mt-1 flex w-52 flex-col gap-1 rounded-popover border border-line bg-raised p-1.5 shadow-lg"
          >
            <button
              type="button"
              title="删除后，同一个资源以后不再提醒"
              onClick={() => {
                setOpen(false);
                onDelete();
              }}
              className="rounded-sm px-2 py-1.5 text-left text-footnote text-danger-fg hover:bg-danger-soft"
            >
              删除这条提醒
            </button>
            <span className="px-2 pb-1 text-caption text-fg-subtle">同一个资源以后不再提醒</span>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function NoteItem({ n, all }: { n: Note; all: Note[] }) {
  const { save } = useMe();
  const p = noteParts(n);
  return (
    <motion.li
      layout="position"
      variants={rise}
      initial="hidden"
      animate="visible"
      exit={{ opacity: 0, x: 12, transition: transition.exit }}
      className={cn("flex flex-col gap-2 rounded-card border border-line-subtle bg-surface px-4 py-3", n.read && "bg-canvas")}
    >
      <div className="flex min-w-0 items-start gap-2">
        <Badge tone={n.read ? "neutral" : p.tone}>{p.label}</Badge>
        <span className={cn("min-w-0 flex-1 text-body", n.read ? "text-fg-muted" : "text-fg")}>{p.summary}</span>
        <time className="shrink-0 text-caption text-fg-subtle tabular-nums" title={formatTime(n.ts)}>
          {relTime(n.ts)}
        </time>
      </div>
      {p.title && (
        <p className="truncate text-footnote text-fg-muted" title={p.title}>
          {p.title}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-1.5">
        {p.url && (
          <Button asChild variant="secondary" size="sm">
            <a href={p.url} target="_blank" rel="noopener noreferrer">
              <ExternalLink />
              打开链接
            </a>
          </Button>
        )}
        {p.share && save.enabled && (n.type || n.kind) !== "auto_saved" && <SaveButton share={p.share} pwd={p.pwd} />}
        {!n.read && (
          <Button variant="secondary" size="sm" aria-label="标为已读" onClick={() => markNotesRead(all, [n])}>
            标为已读
          </Button>
        )}
        {n.id !== undefined && (
          <div className="ml-auto">
            <NoteMore onDelete={() => deleteNote(all, n)} />
          </div>
        )}
      </div>
    </motion.li>
  );
}

function NoteGroup({ g, subs, all }: { g: Group; subs: Sub[]; all: Note[] }) {
  const sub = subs.find((x) => "s" + x.id === g.key);
  const unread = g.notes.filter((n) => !n.read);
  return (
    <section className="flex flex-col gap-2">
      <div className="flex min-w-0 items-center gap-2">
        <Poster src={sub?.poster} title={g.name} size="tiny" />
        <b className="min-w-0 truncate text-body font-medium text-fg">《{tidyTitle(g.name)}》</b>
        {g.notes.length > 1 && <span className="shrink-0 text-caption text-fg-subtle">{g.notes.length} 条</span>}
        {unread.length > 1 && (
          <Button variant="link" size="sm" className="ml-auto" onClick={() => markNotesRead(all, unread)}>
            这组已读
          </Button>
        )}
      </div>
      <ul className="flex flex-col gap-2">
        <AnimatePresence initial={false}>
          {g.notes.map((n, i) => (
            <NoteItem key={n.id ?? "i" + i + n.ts} n={n} all={all} />
          ))}
        </AnimatePresence>
      </ul>
    </section>
  );
}

let readOpenMemo = false; // 已读折叠的展开状态，在本次会话里保留

export function Notifications({ notes, subs }: { notes: Note[]; subs: Sub[] }) {
  const unread = notes.filter((n) => !n.read);
  const read = notes.filter((n) => n.read);
  const [readOpen, setReadOpen] = useState(readOpenMemo);
  if (!unread.length && !read.length) return null;
  return (
    <section aria-label="订阅提醒" className="flex flex-col gap-5">
      {unread.length > 0 && (
        <>
          <div className="flex items-center justify-between">
            <span className="text-title-sm text-fg">
              新提醒 <span className="text-accent-fg tabular-nums">{unread.length}</span> 条
            </span>
            <Button variant="ghost" size="sm" onClick={() => markNotesRead(notes, unread)}>
              全部已读
            </Button>
          </div>
          {noteGroups(unread.slice(0, 30)).map((g) => (
            <NoteGroup key={g.key} g={g} subs={subs} all={notes} />
          ))}
        </>
      )}
      {read.length > 0 && (
        <div className="flex flex-col gap-4">
          <button
            type="button"
            aria-expanded={readOpen}
            onClick={() => {
              readOpenMemo = !readOpen;
              setReadOpen(!readOpen);
            }}
            className="flex w-fit items-center gap-1.5 text-footnote text-fg-muted hover:text-fg"
          >
            <ChevronRight className={cn("size-3.5 transition-transform duration-fast", readOpen && "rotate-90")} />
            已读的提醒（{read.length}）
          </button>
          {readOpen && noteGroups(read.slice(0, 30)).map((g) => <NoteGroup key={g.key} g={g} subs={subs} all={notes} />)}
        </div>
      )}
    </section>
  );
}

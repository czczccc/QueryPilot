import { Check, CircleAlert, Copy, ExternalLink, FolderDown } from "lucide-react";
import { motion } from "framer-motion";
import { useRef, useState, type ReactNode } from "react";
import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Tooltip,
  cn,
  rise,
} from "@cz/design-system";
import { cidParam, copyText, store } from "../../lib/api";
import { RES_LABEL, formatSize, shareUrl } from "../../lib/format";
import { useMe } from "../../lib/me";
import { useToast } from "../../lib/toast";
import type { Link, Quality } from "./agent";

type Tone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

function Tip({ tip, children }: { tip?: string | null; children: ReactNode }) {
  if (!tip) return <>{children}</>;
  return (
    <Tooltip content={tip}>
      <span className="inline-flex">{children}</span>
    </Tooltip>
  );
}

function qualityBadges(q?: Quality | null): { text: string; tone: Tone; tip?: string }[] {
  const out: { text: string; tone: Tone; tip?: string }[] = [];
  if (!q) return out;
  if (q.resolution) {
    out.push({
      text: (RES_LABEL[q.resolution] || q.resolution) + (q.resolution_guessed ? "?" : ""),
      tone: "info",
      tip: q.resolution_guessed ? "由文件体积推断" : undefined,
    });
  }
  if (q.hdr) out.push({ text: "HDR", tone: "info" });
  if (q.source) out.push({ text: q.source, tone: "neutral" });
  if (q.low_quality) out.push({ text: "疑似枪版", tone: "danger" });
  if (q.has_subtitle) out.push({ text: "字幕", tone: "neutral" });
  if ((q.video_count || 0) > 1) out.push({ text: q.video_count + " 个视频", tone: "neutral" });
  const size = formatSize(q.size_bytes);
  if (size) out.push({ text: size, tone: "neutral" });
  return out;
}

const STATE_MAP: Record<string, [string, Tone]> = {
  valid: ["有效", "success"],
  invalid: ["已失效", "danger"],
  unknown: ["待确认", "warning"],
};

/** 转存口令输入（替代 window.prompt）：ask() 返回口令或 null */
export function useTokenPrompt() {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const resolver = useRef<((v: string | null) => void) | null>(null);
  const finish = (v: string | null) => {
    resolver.current?.(v);
    resolver.current = null;
    setOpen(false);
  };
  const ask = (forceAsk: boolean): Promise<string | null> => {
    const saved = forceAsk ? null : store.getRaw("qp_save_token");
    if (saved) return Promise.resolve(saved);
    resolver.current?.(null);
    setValue("");
    setOpen(true);
    return new Promise((resolve) => {
      resolver.current = (v) => {
        if (v) store.set("qp_save_token", v);
        resolve(v);
      };
    });
  };
  const dialog = (
    <Dialog open={open} onOpenChange={(o) => !o && finish(null)}>
      <DialogContent>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            finish(value.trim() || null);
          }}
          className="flex flex-col gap-4"
        >
          <DialogHeader>
            <DialogTitle>输入转存口令</DialogTitle>
            <DialogDescription>请输入转存口令（服务器 .env 里的 SAVE_TOKEN，只保存在本浏览器）</DialogDescription>
          </DialogHeader>
          <Input autoFocus type="password" value={value} onChange={(e) => setValue(e.target.value)} aria-label="转存口令" />
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => finish(null)}>
              取消
            </Button>
            <Button type="submit" disabled={!value.trim()}>
              确定
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
  return { ask, dialog };
}

type SaveOutcome = { ok: boolean; body: any; msg: string };

function SaveResult({ r }: { r: SaveOutcome }) {
  if (!r.ok) {
    return (
      <div role="status" className="flex items-start gap-2 rounded-md border border-danger-line bg-danger-soft px-3 py-2 text-footnote text-danger-fg">
        <CircleAlert className="mt-0.5 size-4 shrink-0" />
        <span>{r.msg}</span>
      </div>
    );
  }
  const basis = (r.msg.match(/依据：([^）)]+)/) || [])[1];
  const pending = /后台处理/.test(r.msg);
  const b = r.body;
  return (
    <div role="status" className="flex flex-wrap items-center gap-2 rounded-md border border-success-line bg-success-soft px-3 py-2 text-footnote text-success-fg">
      <Check className="size-4 shrink-0" />
      <span>
        {pending ? "已提交转存，夸克正在后台处理 · 目录 " : "已存入 "}
        <b className="font-semibold">{b.folder || "你的夸克网盘默认目录"}</b>
      </span>
      <span className="flex flex-wrap gap-1">
        {b.category && <Badge tone="accent">识别为 {b.category}</Badge>}
        {basis && <Badge>依据 {basis}</Badge>}
        {!b.category && (
          <Tip tip="没识别出类别，存到了默认目录">
            <Badge>未自动分类</Badge>
          </Tip>
        )}
        {b.file_count ? <Badge>{b.file_count} 个文件</Badge> : null}
      </span>
    </div>
  );
}

function postSave(l: Link, token: string | null) {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["X-Save-Token"] = token;
  return fetch("/api/save", { method: "POST", headers, body: JSON.stringify({ share: l.share, pwd: l.pwd || null }) });
}

function SaveButton({ l, askToken, onResult }: { l: Link; askToken: (force: boolean) => Promise<string | null>; onResult: (r: SaveOutcome) => void }) {
  const { save, login, reload } = useMe();
  const toast = useToast();
  const [state, setState] = useState<"idle" | "busy" | "done" | "fail" | "limit">("idle");
  const label = { idle: "转存到网盘", busy: "转存中…", done: "已转存 ✓", fail: "转存失败", limit: "今天已达上限" }[state];
  const click = async () => {
    let token: string | null = null;
    if (!save.logged_in) {
      if (save.login) {
        if (!(await login())) return;
      } else {
        token = await askToken(false);
        if (!token) return;
      }
    }
    setState("busy");
    try {
      let resp = await postSave(l, token);
      if (resp.status === 401 && token) {
        // 口令不对：清掉重新问一次
        token = await askToken(true);
        resp = token ? await postSave(l, token) : resp;
      }
      const body = await resp.json().catch(() => ({}));
      const ok = resp.ok && body.ok;
      if (!ok && /重新扫码|先扫码/.test(body.message || body.detail || "")) reload();
      setState(ok ? "done" : resp.status === 429 ? "limit" : "fail");
      const msg = body.message || body.detail || (ok ? "已转存" : "转存失败");
      toast(ok ? "转存成功" + (body.folder ? "，已放进「" + body.folder + "」" : "") : msg, ok ? "ok" : "error");
      onResult({ ok, body, msg: typeof msg === "string" ? msg : "转存失败" });
    } catch {
      setState("fail");
      onResult({ ok: false, body: {}, msg: "网络出错，转存没有完成，请稍后重试" });
    }
  };
  return (
    <Button
      variant={state === "done" ? "soft" : "secondary"}
      size="sm"
      disabled={state === "busy" || state === "done"}
      loading={state === "busy"}
      onClick={click}
    >
      {state === "idle" && <FolderDown />}
      {label}
    </Button>
  );
}

// 结果反馈：「不对」「失效」。报过的记在本机，卡片上显示「已反馈」
const REPORT_URL = "/api/feedback/report";
const REPORTED_KEY = "qp_reported";

function ReportButtons({ l, query, onDone }: { l: Link; query: string; onDone: () => void }) {
  const toast = useToast();
  const [done, setDone] = useState<string | null>(() => store.get<Record<string, string>>(REPORTED_KEY, {})[l.share] || null);
  const [busy, setBusy] = useState(false);
  if (done) {
    return <span className="text-footnote text-fg-subtle">{done === "dead" ? "已反馈：失效" : "已反馈：不是这部"}</span>;
  }
  const report = async (reason: string) => {
    setBusy(true);
    const resp = await fetch(REPORT_URL + "?" + cidParam(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ share: l.share, reason, query: query || "" }),
    }).catch(() => null);
    setBusy(false);
    if (!resp || !resp.ok) {
      if (resp && resp.status === 429) toast("今天反馈太多了，明天再来吧", "error");
      else if (resp && resp.status === 422) toast("这个链接格式不对，没法反馈", "error");
      else toast("反馈没发出去，请稍后再试", "error");
      return;
    }
    const all = store.get<Record<string, string>>(REPORTED_KEY, {});
    all[l.share] = reason;
    store.set(REPORTED_KEY, all);
    setDone(reason);
    onDone();
    toast("已收到反馈，谢谢", "ok");
  };
  return (
    <span className="flex items-center gap-1">
      {(
        [
          ["wrong", "不对", "不是要找的这部"],
          ["dead", "失效", "打不开或文件已被删除"],
        ] as const
      ).map(([reason, label, tip]) => (
        <Button key={reason} variant="ghost" size="sm" title={tip} disabled={busy} onClick={() => report(reason)}>
          {label}
        </Button>
      ))}
    </span>
  );
}

export function LinkCard({ l, index, query, askToken }: { l: Link; index: number; query: string; askToken: (force: boolean) => Promise<string | null> }) {
  const toast = useToast();
  const { save } = useMe();
  const [copied, setCopied] = useState(false);
  const [saveResult, setSaveResult] = useState<SaveOutcome | null>(null);
  const [reported, setReported] = useState(() => !!store.get<Record<string, string>>(REPORTED_KEY, {})[l.share]);
  const [stateText, stateTone] = STATE_MAP[l.state || "unknown"] || STATE_MAP.unknown;
  const url = "https://pan.quark.cn/s/" + l.share;
  const dead = l.state === "invalid";

  const copy = () => {
    const link = shareUrl(l.share, l.pwd);
    copyText(link)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1600);
        toast("已复制" + (l.pwd ? "（含提取码）" : "") + "：" + link);
      })
      .catch(() => toast("复制失败，请手动选中链接复制", "error"));
    // 反馈：被复制过的链接下次排序更靠前（失败不影响使用）
    fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ share: l.share }),
    }).catch(() => {});
  };

  const previewParts: string[] = [];
  if (l.share_title) previewParts.push("分享标题：" + l.share_title);
  if (l.files_preview && l.files_preview.length) previewParts.push("内容：" + l.files_preview.join("、"));

  return (
    <motion.li
      variants={rise}
      initial="hidden"
      animate="visible"
      transition={{ delay: Math.min(index, 12) * 0.03 }}
      className={cn(
        "flex flex-col gap-3 rounded-card border border-line-subtle bg-surface p-4 transition duration-fast ease-standard hover:border-line sm:p-5",
        (dead || reported) && "opacity-60",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <h3 className="min-w-0 text-body-lg font-medium break-words text-fg">{l.name}</h3>
        <Badge tone={stateTone} dot className="shrink-0">
          {stateText}
        </Badge>
      </div>

      <div className="flex flex-wrap gap-1">
        <Badge tone={l.conf === "高" ? "success" : l.conf === "低" ? "warning" : "neutral"}>{l.conf}置信</Badge>
        {qualityBadges(l.quality).map((b, i) => (
          <Tip key={i} tip={b.tip}>
            <Badge tone={b.tone}>{b.text}</Badge>
          </Tip>
        ))}
        {l.state === "valid" && l.relevance === "mismatch" && (
          <Tip tip={l.relevance_note}>
            <Badge tone="danger">片名不符</Badge>
          </Tip>
        )}
        {l.state === "valid" && l.relevance === "uncertain" && (
          <Tip tip={l.relevance_note || "没能确认是不是这部作品"}>
            <Badge tone="warning">待核对</Badge>
          </Tip>
        )}
        {l.copy_count ? <Badge tone="accent">{l.copy_count} 次复制</Badge> : null}
        {l.from_memory && (
          <Tip tip={l.last_checked ? "上次验证：" + new Date(l.last_checked * 1000).toLocaleString() : ""}>
            <Badge>记忆</Badge>
          </Tip>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md bg-sunken px-3 py-2 font-mono text-footnote">
        <a href={url} target="_blank" rel="noopener noreferrer" className="min-w-0 break-all text-accent-fg hover:underline">
          {url}
        </a>
        <span className="text-fg-muted">{l.pwd ? "提取码 " + l.pwd : "无提取码"}</span>
      </div>

      <div className="flex flex-wrap gap-x-3 gap-y-1 text-footnote text-fg-subtle">
        {l.time && <span>{l.time}</span>}
        <span>来源：{l.source}</span>
      </div>

      {previewParts.length > 0 && (
        <p className="line-clamp-2 text-footnote text-fg-muted" title={previewParts.join("\n")}>
          {previewParts.join("　|　")}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Button variant={copied ? "soft" : "primary"} size="sm" onClick={copy}>
          {copied ? <Check /> : <Copy />}
          {copied ? "已复制 ✓" : "复制链接"}
        </Button>
        <Button variant="secondary" size="sm" asChild>
          <a href={url} target="_blank" rel="noopener noreferrer">
            <ExternalLink />
            打开
          </a>
        </Button>
        {save.enabled && l.state === "valid" && <SaveButton l={l} askToken={askToken} onResult={setSaveResult} />}
        <span className="ml-auto">
          <ReportButtons l={l} query={query} onDone={() => setReported(true)} />
        </span>
      </div>

      {saveResult && <SaveResult r={saveResult} />}
    </motion.li>
  );
}

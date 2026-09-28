import { useEffect, useState } from "react";
import { Button, Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, Input, Label, Skeleton } from "@cz/design-system";
import { cidParam } from "../../lib/api";
import { useToast } from "../../lib/toast";

export type PrivacyView = "privacy" | "delete" | null;

// 文字以后端 GET /api/privacy 为准；接口不可用时用这份简版兜底
const PRIVACY_FALLBACK = [
  { title: "夸克登录凭证", text: "扫码登录后，夸克登录凭证加密保存在服务器上，只用来转存到你的网盘、检查和整理订阅。退出登录会删除这台设备的凭证。" },
  { title: "订阅和用量", text: "保存你的订阅、订阅历史、通知、转存记录、偏好设置，以及每天的搜索和 AI 用量（用于额度限制）。" },
  { title: "删除我的数据", text: "可以随时删除上面这些数据并退出登录。已经存到你网盘里的文件不受影响。" },
];

const DATA_LABELS: [string, string][] = [
  ["subscriptions", "个订阅"],
  ["history", "条订阅历史"],
  ["collections", "个系列订阅"],
  ["notifications", "条提醒"],
  ["auto_saves", "条转存记录"],
  ["prefs", "份偏好设置"],
  ["quark_logins", "个设备上的登录凭证"],
];

function dataSummary(counts: Record<string, number> | undefined): string[] {
  return DATA_LABELS.filter(([k]) => counts && counts[k] > 0).map(([k, unit]) => counts![k] + " " + unit);
}

/** 隐私说明 + 删除我的数据两个弹窗；view 控制显示哪个 */
export function PrivacyDialogs({ view, onChange }: { view: PrivacyView; onChange: (v: PrivacyView) => void }) {
  return (
    <>
      <Dialog open={view === "privacy"} onOpenChange={(o) => !o && onChange(null)}>
        <DialogContent className="max-w-lg">{view === "privacy" && <PrivacyBody onChange={onChange} />}</DialogContent>
      </Dialog>
      <Dialog open={view === "delete"} onOpenChange={(o) => !o && onChange(null)}>
        <DialogContent>{view === "delete" && <DeleteBody onClose={() => onChange(null)} />}</DialogContent>
      </Dialog>
    </>
  );
}

type Section = { title?: string; text?: string };

function PrivacyBody({ onChange }: { onChange: (v: PrivacyView) => void }) {
  const [data, setData] = useState<{ sections: Section[]; updated?: string } | null>(null);
  useEffect(() => {
    let alive = true;
    fetch("/api/privacy")
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null)
      .then((d) => {
        if (!alive) return;
        const sections = d && Array.isArray(d.sections) && d.sections.length ? d.sections : PRIVACY_FALLBACK;
        setData({ sections, updated: d?.updated });
      });
    return () => {
      alive = false;
    };
  }, []);
  return (
    <>
      <DialogHeader>
        <DialogTitle>隐私说明</DialogTitle>
      </DialogHeader>
      <div className="flex max-h-96 flex-col gap-4 overflow-y-auto">
        {!data
          ? [0, 1, 2].map((i) => <Skeleton key={i} className="h-16 w-full" />)
          : data.sections.map((sec, i) => (
              <div key={i} className="flex flex-col gap-1">
                <h3 className="text-body font-medium text-fg">{sec.title || ""}</h3>
                {String(sec.text || "")
                  .split(/\n+/)
                  .filter(Boolean)
                  .map((t, j) => (
                    <p key={j} className="text-footnote text-fg-muted">
                      {t}
                    </p>
                  ))}
              </div>
            ))}
        {data?.updated && <p className="text-caption text-fg-subtle">更新于 {data.updated}</p>}
      </div>
      <DialogFooter className="sm:justify-between">
        <Button variant="ghost" size="sm" className="text-danger-fg" onClick={() => onChange("delete")}>
          删除我的数据
        </Button>
        <Button onClick={() => onChange(null)}>知道了</Button>
      </DialogFooter>
    </>
  );
}

// 删除前二次确认：先列出将删除的数据条数，要输入「删除」两个字才能点
function DeleteBody({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const [items, setItems] = useState<string[] | null>(null);
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    fetch("/api/me/data?" + cidParam())
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!alive) return;
        const list = d ? dataSummary(d.counts) : [];
        if (!d) list.push("订阅、订阅历史、系列订阅、提醒、转存记录、偏好设置", "所有设备上的夸克登录凭证和账号记录");
        else if (!list.length) list.push("服务器上暂时没有你的订阅或设置");
        if (d && d.logged_in) list.push("你的夸克账号记录（会退出登录）");
        setItems(list);
      })
      .catch(() => alive && setItems([]));
    return () => {
      alive = false;
    };
  }, []);

  async function go() {
    setBusy(true);
    let res: { ok: boolean; body: any };
    try {
      const resp = await fetch("/api/me/data?" + cidParam() + "&confirm=DELETE", { method: "DELETE" });
      res = { ok: resp.ok, body: await resp.json().catch(() => ({})) };
    } catch {
      res = { ok: false, body: { detail: "网络连接失败，请稍后重试" } };
    }
    if (!res.ok) {
      setBusy(false);
      toast(res.body.detail || "删除失败，请稍后重试", "error");
      return;
    }
    // 服务器已删除并清掉登录 cookie：清掉本地记录，换一个新身份回到未登录首页
    try {
      Object.keys(localStorage)
        .filter((k) => k.startsWith("qp_"))
        .forEach((k) => localStorage.removeItem(k));
    } catch {
      /* 存储不可用 */
    }
    onClose();
    const done = dataSummary(res.body.deleted);
    toast("已删除" + (done.length ? "：" + done.join("、") : "你的数据") + "，页面即将刷新", "ok", 3000);
    setTimeout(() => location.replace("/"), 1600);
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>删除我的数据</DialogTitle>
        <DialogDescription>会立即删除服务器上和你有关的数据并退出登录，删除后不能恢复。</DialogDescription>
      </DialogHeader>
      <ul className="flex list-disc flex-col gap-1 rounded-card bg-sunken py-3 pr-3 pl-7 text-footnote text-fg">
        {items === null ? <li className="text-fg-muted">正在统计…</li> : items.map((t) => <li key={t}>{t}</li>)}
      </ul>
      <p className="text-caption text-fg-subtle">
        不会删除：已经存到你网盘里的文件、全站共享的链接库。为防止刷额度，今天的用量计数会保留到明天；开启邀请制时，再登录需要新的邀请码。
      </p>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="delete-confirm">请输入「删除」确认</Label>
        <Input id="delete-confirm" autoFocus autoComplete="off" placeholder="删除" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
      </div>
      <DialogFooter>
        <Button variant="secondary" onClick={onClose}>
          取消
        </Button>
        <Button variant="danger" loading={busy} disabled={busy || confirm.trim() !== "删除"} onClick={go}>
          永久删除
        </Button>
      </DialogFooter>
    </>
  );
}

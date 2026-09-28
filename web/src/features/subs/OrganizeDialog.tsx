// 整理网盘目录：先预览（不改动），移动/改名直接执行，删除只删用户逐个勾选并二次确认的
import { Check, ChevronRight, CircleAlert } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Button, cn, Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, Skeleton } from "@cz/design-system";
import { subApi, type ApiResult } from "../../lib/api";
import { formatSize } from "../../lib/format";
import { useMe } from "../../lib/me";
import { useToast } from "../../lib/toast";
import { loadSubs, type Sub } from "./store";

type Move = { from: string; name: string; to_name: string; size?: number };
type Del = { fid: string; name: string; reason: string; folder: string; size?: number };
type Plan = { target: string; moves: Move[]; deletes: Del[]; untouched?: { name: string; folder: string }[] };
type Result = { moved?: number; renamed?: number; deleted?: number; target: string; errors?: string[] };

function Row({ title, sub, extra }: { title: string; sub?: string; extra?: string }) {
  return (
    <li className="flex items-start justify-between gap-3 py-2">
      <span className="flex min-w-0 flex-col">
        <span className="break-all text-footnote text-fg">{title}</span>
        {sub && <span className="break-all text-caption text-fg-subtle">{sub}</span>}
      </span>
      {extra && <span className="shrink-0 text-caption text-fg-subtle tabular-nums">{extra}</span>}
    </li>
  );
}

function Group({ title, count, children, note }: { title: string; count: number; children: ReactNode; note?: string }) {
  return (
    <section className="flex flex-col gap-1">
      <h3 className="flex items-center gap-2 text-footnote font-medium text-fg">
        {title}
        <span className="rounded-pill bg-sunken px-1.5 text-caption text-fg-muted tabular-nums">{count}</span>
      </h3>
      {note && <p className="text-caption text-fg-muted">{note}</p>}
      <ul className="divide-y divide-line-subtle">{children}</ul>
    </section>
  );
}

export function OrganizeDialog({ sub, open, onClose }: { sub: Sub; open: boolean; onClose: () => void }) {
  const { me, login } = useMe();
  const toast = useToast();
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState<{ text: string; retry: boolean } | null>(null);
  const [loading, setLoading] = useState(false);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [confirm, setConfirm] = useState(false);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [showUntouched, setShowUntouched] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    setPlan(null);
    try {
      const res = await subApi<Plan>("/" + sub.id + "/organize");
      setLoading(false);
      if (res.ok) {
        setPlan(res.body);
        return;
      }
      if (res.status === 401 && me.login) {
        setError({ text: res.body.detail || "需要先扫码登录夸克", retry: false });
        if (await login(res.body.detail || "整理网盘目录需要先扫码登录夸克")) load();
        return;
      }
      setError({ text: res.body.detail || "读取网盘目录失败", retry: true });
    } catch {
      setLoading(false);
      setError({ text: "读取网盘目录失败，请稍后重试", retry: true });
    }
  }

  useEffect(() => {
    if (!open) return;
    setChecked(new Set());
    setConfirm(false);
    setResult(null);
    setRunning(false);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const close = () => {
    onClose();
    if (result) loadSubs();
  };

  const moves = plan ? plan.moves.filter((m) => m.from !== plan.target) : [];
  const renames = plan ? plan.moves.filter((m) => m.from === plan.target && m.to_name !== m.name) : [];
  const renameCount = plan ? plan.moves.filter((m) => m.to_name !== m.name).length : 0;
  const parts: string[] = [];
  if (moves.length) parts.push("移动 " + moves.length);
  if (renameCount) parts.push("改名 " + renameCount);
  if (checked.size) parts.push("删除 " + checked.size);

  async function execute() {
    setRunning(true);
    setConfirm(false);
    let res: ApiResult<Result>;
    try {
      res = await subApi<Result>("/" + sub.id + "/organize", "POST", { delete_fids: [...checked] });
    } catch {
      res = { ok: false, status: 0, body: { detail: "连接失败，请稍后重试" } as any };
    }
    setRunning(false);
    if (!res.ok) {
      toast(res.body.detail || "整理失败", "error");
      if (res.status === 401 && me.login) await login(res.body.detail || "夸克登录已失效，请重新扫码");
      return;
    }
    setResult(res.body);
  }

  const toggle = (fid: string, on: boolean) => {
    const next = new Set(checked);
    if (on) next.add(fid);
    else next.delete(fid);
    setChecked(next);
    setConfirm(false);
  };

  const headText = result ? "整理完成" : plan ? "整理到：" + plan.target : loading ? "正在读取网盘目录…" : "";

  return (
    <Dialog open={open} onOpenChange={(o) => !o && close()}>
      <DialogContent className="max-h-full max-w-xl overflow-hidden">
        <DialogHeader>
          <DialogTitle>整理《{sub.resource}》的网盘目录</DialogTitle>
          {headText && (
            <DialogDescription className="truncate text-footnote" title={plan?.target}>
              {headText}
            </DialogDescription>
          )}
        </DialogHeader>

        <div className="-mx-6 flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-6">
          {loading && (
            <div className="flex flex-col gap-2">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          )}
          {error && (
            <div className="flex flex-col items-start gap-3 rounded-card border border-danger-line bg-danger-soft p-4 text-footnote text-danger-fg">
              <p>{error.text}</p>
              {error.retry && (
                <Button variant="secondary" size="sm" onClick={load}>
                  重试
                </Button>
              )}
            </div>
          )}
          {result && (
            <div className={cn("flex flex-col gap-4 rounded-card border p-4", result.errors?.length ? "border-warning-line bg-warning-soft" : "border-success-line bg-success-soft")}>
              <span className={cn("grid size-8 place-items-center rounded-full", result.errors?.length ? "bg-warning text-on-accent" : "bg-success text-on-accent")}>
                {result.errors?.length ? <CircleAlert className="size-4" /> : <Check className="size-4" />}
              </span>
              <div className="grid grid-cols-3 gap-2">
                {(
                  [
                    ["移动", result.moved],
                    ["改名", result.renamed],
                    ["删除", result.deleted],
                  ] as const
                ).map(([k, v]) => (
                  <div key={k} className="flex flex-col">
                    <b className="text-title text-fg tabular-nums">{v || 0}</b>
                    <span className="text-caption text-fg-muted">{k}</span>
                  </div>
                ))}
              </div>
              <p className="text-footnote text-fg-muted">
                文件都在「{result.target}」。这是新功能，请打开夸克网盘确认一下结果{result.deleted ? "；删错了可以在夸克回收站里恢复。" : "。"}
              </p>
              {!!result.errors?.length && (
                <ul className="list-disc pl-5 text-caption text-danger-fg">
                  {result.errors.slice(0, 10).map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {plan && !result && (
            <>
              {!moves.length && !renames.length && !plan.deletes.length && (
                <p className="rounded-card bg-sunken p-4 text-footnote text-fg-muted">目录已经很整齐了，没有需要移动、改名或删除的文件。</p>
              )}
              {moves.length > 0 && (
                <Group title="移动到整理目录" count={moves.length}>
                  {moves.map((m, i) => (
                    <Row key={i} title={m.to_name} sub={"从 " + m.from + (m.to_name !== m.name ? "，原名 " + m.name : "")} extra={formatSize(m.size)} />
                  ))}
                </Group>
              )}
              {renames.length > 0 && (
                <Group title="改名" count={renames.length}>
                  {renames.map((m, i) => (
                    <Row key={i} title={m.to_name} sub={"原名 " + m.name} extra={formatSize(m.size)} />
                  ))}
                </Group>
              )}
              {plan.deletes.length > 0 && (
                <Group
                  title="建议删除"
                  count={plan.deletes.length}
                  note={(sub.upgrade ? "洗版换下来的旧版本也列在这里。" : "") + "默认都不删。确认不需要的请逐个勾选，删除的文件会进夸克回收站，可以恢复。"}
                >
                  {plan.deletes.map((d) => (
                    <li key={d.fid} className={cn("flex items-start justify-between gap-3 py-2", checked.has(d.fid) && "text-danger-fg")}>
                      <label className="flex min-w-0 cursor-pointer items-start gap-2.5">
                        <input
                          type="checkbox"
                          className="mt-1 accent-danger"
                          checked={checked.has(d.fid)}
                          disabled={running}
                          onChange={(e) => toggle(d.fid, e.target.checked)}
                        />
                        <span className="flex min-w-0 flex-col">
                          <span className="break-all text-footnote text-fg">{d.name}</span>
                          <span className="break-all text-caption text-fg-subtle">
                            {d.reason} · {d.folder}
                          </span>
                        </span>
                      </label>
                      {d.size ? <span className="shrink-0 text-caption text-fg-subtle tabular-nums">{formatSize(d.size)}</span> : null}
                    </li>
                  ))}
                </Group>
              )}
              {!!plan.untouched?.length && (
                <div className="flex flex-col gap-1">
                  <button type="button" onClick={() => setShowUntouched(!showUntouched)} className="flex items-center gap-1.5 text-left text-footnote text-fg-muted hover:text-fg">
                    <ChevronRight className={cn("size-3.5 shrink-0 transition-transform duration-fast", showUntouched && "rotate-90")} />
                    不处理的文件（{plan.untouched.length}）：认不出集号，比如花絮
                  </button>
                  {showUntouched && (
                    <ul className="divide-y divide-line-subtle">
                      {plan.untouched.map((u, i) => (
                        <Row key={i} title={u.name} sub={u.folder} />
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </>
          )}
        </div>

        {confirm ? (
          <div className="flex flex-col gap-3 rounded-card border border-danger-line bg-danger-soft p-4">
            <p className="text-footnote text-danger-fg">确定删除勾选的 {checked.size} 个文件？它们会进夸克回收站，可以恢复。</p>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" size="sm" onClick={() => setConfirm(false)}>
                再看看
              </Button>
              <Button variant="danger" size="sm" autoFocus onClick={execute}>
                确认删除并整理
              </Button>
            </div>
          </div>
        ) : (
          <DialogFooter>
            <Button variant="secondary" size="sm" onClick={close}>
              {result ? "完成" : "取消"}
            </Button>
            {!result && (
              <Button
                size="sm"
                loading={running}
                disabled={!plan || !parts.length}
                onClick={() => (checked.size ? setConfirm(true) : execute())}
              >
                {running ? "整理中…" : !plan ? "执行整理" : parts.length ? "执行整理（" + parts.join(" · ") + "）" : "没有要执行的操作"}
              </Button>
            )}
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}

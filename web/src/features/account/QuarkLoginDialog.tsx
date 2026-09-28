import { useEffect, useRef, useState } from "react";
import { Button, Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, Input, Spinner, cn } from "@cz/design-system";
import { api } from "../../lib/api";
import { isMobileDevice } from "../../lib/format";
import { useAppState } from "../../lib/app-state";
import { useLoginRequest, useMe } from "../../lib/me";

/** 最近一次扫码登录成功时的昵称（账号按钮用来拼「登录成功（昵称）」提示） */
export let lastLoginNickname: string | null = null;

// 把二维码 SVG 画成 PNG 图片（手机相册存不了 SVG）；失败时返回 null，继续用 SVG
function qrToImage(svg: string): Promise<string | null> {
  return new Promise((resolve) => {
    const src = new Image();
    src.onload = () => {
      try {
        const size = 600;
        const c = document.createElement("canvas");
        c.width = size;
        c.height = size;
        const g = c.getContext("2d")!;
        g.fillStyle = "#fff"; // 二维码必须是白底才能被扫出来，不跟随主题
        g.fillRect(0, 0, size, size);
        g.drawImage(src, 0, 0, size, size);
        resolve(c.toDataURL("image/png"));
      } catch {
        resolve(null);
      }
    };
    src.onerror = () => resolve(null);
    let sized = /<svg[^>]*\swidth=/.test(svg) ? svg : svg.replace("<svg", '<svg width="600" height="600"');
    if (!/<svg[^>]*\sxmlns=/.test(sized)) sized = sized.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"');
    src.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(sized);
  });
}

type Qr = { svg: string; img: string | null } | null;

/** 扫码登录弹窗：由 useMe().login(reason) 打开，成功 finish(true)，关闭或失败 finish(false) */
export function QuarkLoginDialog() {
  const { request, finish } = useLoginRequest();
  const ids = useRef(new WeakMap<object, number>());
  if (!request) return null;
  if (!ids.current.has(request)) ids.current.set(request, ids.current.has(request) ? 0 : Math.random());
  return <LoginBody key={ids.current.get(request)} reason={request.reason} finish={finish} />;
}

function LoginBody({ reason, finish }: { reason?: string; finish: (ok: boolean) => void }) {
  const { me } = useMe();
  const { bumpSubs } = useAppState();
  const mobile = useRef(isMobileDevice()).current;
  const [open, setOpen] = useState(true);
  const [inviteShown, setInviteShown] = useState(me.invite_required);
  const [invite, setInvite] = useState("");
  const [qr, setQr] = useState<Qr>(null);
  const [loading, setLoading] = useState(false);
  const [tip, setTip] = useState("");
  const [tipError, setTipError] = useState(false);
  const [retry, setRetry] = useState(false);
  const [appUrl, setAppUrl] = useState<string | null>(null);
  const [showMore, setShowMore] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const closed = useRef(false);
  const timer = useRef<number | undefined>(undefined);
  const pollNow = useRef<(() => void) | null>(null);
  const inviteRef = useRef<HTMLInputElement>(null);
  const run = useRef(0);

  const done = (ok: boolean) => {
    if (closed.current) return;
    closed.current = true;
    clearInterval(timer.current);
    setOpen(false);
    // 等退出动画
    setTimeout(() => finish(ok), 150);
  };

  const stopWith = (text: string, error = true) => {
    clearInterval(timer.current);
    pollNow.current = null;
    setAppUrl(null);
    setShowMore(false);
    setQr(null);
    setLoading(false);
    setTip(text);
    setTipError(error);
  };

  async function start() {
    clearInterval(timer.current);
    const my = ++run.current;
    setRetry(false);
    setAppUrl(null);
    setShowMore(false);
    setQr(null);
    setLoading(true);
    setTipError(false);
    setTip(mobile ? "正在准备登录…" : "正在获取二维码…");
    const code = invite.trim();
    const r = await api("/api/quark/login", "POST", code ? { invite_code: code } : {});
    if (closed.current || my !== run.current) return;
    if (!r.ok) {
      stopWith(r.status === 0 ? "获取二维码失败，请稍后重试" : r.body.detail || "获取二维码失败");
      setRetry(true);
      return;
    }
    const data = r.body as { login_id: string; qr_svg?: string; qr_url?: string };
    setLoading(false);
    setQr({ svg: data.qr_svg || "", img: null });
    if (mobile) {
      if (data.qr_svg) qrToImage(data.qr_svg).then((img) => img && my === run.current && setQr((q) => (q ? { ...q, img } : q)));
      setAppUrl(data.qr_url || null);
      setShowMore(true);
      setMoreOpen(!data.qr_url);
      setTip("在夸克 App 里点「确认登录」后，回到这个页面就行，会自动登录");
    } else {
      setTip("扫码后在手机上确认登录");
    }
    const poll = async () => {
      try {
        const resp = await fetch("/api/quark/login/" + encodeURIComponent(data.login_id));
        const s = await resp.json();
        if (closed.current || my !== run.current) return;
        if (s.status === "success") {
          clearInterval(timer.current);
          lastLoginNickname = s.nickname || null;
          bumpSubs();
          done(true);
        } else if (s.status === "invite_required") {
          setInviteShown(true);
          stopWith((s.message || "新用户需要邀请码") + "，填好邀请码后点「扫码登录」重新扫码");
          setTimeout(() => inviteRef.current?.focus());
        } else if (s.status === "banned") {
          stopWith(s.message || "该账号已被停用");
        } else if (s.status !== "waiting" && s.status !== "scanned") {
          stopWith(s.message || "二维码已过期");
          setRetry(true);
        } else if (s.status === "scanned") {
          setTip(mobile ? "请在夸克 App 里点「确认登录」，然后回到这里" : "已扫码，请在手机上确认登录");
        }
      } catch {
        /* 网络抖动：下次再试 */
      }
    };
    pollNow.current = poll;
    timer.current = window.setInterval(poll, 2000);
  }

  useEffect(() => {
    // 从夸克 App 切回来时立即查一次，不用等下一轮
    const onVisible = () => {
      if (document.visibilityState === "visible" && pollNow.current) pollNow.current();
    };
    document.addEventListener("visibilitychange", onVisible);
    if (me.invite_required) {
      setTip("新用户请先填写邀请码；已经登录过的老用户直接点「扫码登录」");
      setTimeout(() => inviteRef.current?.focus());
    } else {
      start();
    }
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      clearInterval(timer.current);
      closed.current = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const qrBox = (
    <div className="mx-auto grid size-56 place-items-center overflow-hidden rounded-card border border-line-subtle bg-surface">
      {loading ? (
        <Spinner />
      ) : qr?.img ? (
        <img src={qr.img} alt="夸克登录二维码" className="size-full" />
      ) : qr?.svg ? (
        // 服务器生成的二维码 SVG；白底保证能扫
        <div className="size-full p-2 [&_svg]:size-full" style={{ background: "#fff" /* 二维码需白底 */ }} dangerouslySetInnerHTML={{ __html: qr.svg }} />
      ) : (
        <span className="text-footnote text-fg-subtle">—</span>
      )}
    </div>
  );

  return (
    <Dialog open={open} onOpenChange={(o) => !o && done(false)}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{mobile ? "登录夸克网盘" : "用夸克 App 扫码登录"}</DialogTitle>
          <DialogDescription>{reason || "登录后搜索次数更多，转存会保存到你自己的网盘"}</DialogDescription>
        </DialogHeader>

        {inviteShown && (
          <div className="flex gap-2">
            <Input
              ref={inviteRef}
              value={invite}
              onChange={(e) => setInvite(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && start()}
              maxLength={64}
              placeholder="邀请码（老用户不用填）"
              autoComplete="off"
              aria-label="邀请码"
            />
            <Button onClick={start} className="shrink-0">
              扫码登录
            </Button>
          </div>
        )}

        {mobile ? (
          <>
            {appUrl && (
              <Button asChild size="lg" className="w-full">
                <a href={appUrl} target="_blank" rel="noopener">
                  打开夸克 App 登录
                </a>
              </Button>
            )}
            {loading && (
              <div className="flex justify-center py-4">
                <Spinner />
              </div>
            )}
          </>
        ) : (
          (qr || loading) && qrBox
        )}

        {tip && (
          <p role="status" className={cn("text-center text-footnote", tipError ? "text-danger-fg" : "text-fg-muted")}>
            {tip}
          </p>
        )}

        {mobile && showMore && (
          <details open={moreOpen} onToggle={(e) => setMoreOpen((e.target as HTMLDetailsElement).open)} className="rounded-card border border-line-subtle bg-sunken p-3">
            <summary className="cursor-pointer text-footnote text-fg-muted">打不开 App？用二维码登录</summary>
            <p className="mt-2 mb-3 text-caption text-fg-subtle">长按二维码保存到相册，在夸克 App 的「扫一扫」里从相册选这张图</p>
            {qrBox}
          </details>
        )}

        <DialogFooter>
          {retry && (
            <Button variant="secondary" onClick={start}>
              重新获取二维码
            </Button>
          )}
          <Button variant="ghost" onClick={() => done(false)}>
            取消
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

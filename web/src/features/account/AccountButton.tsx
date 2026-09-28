import { UserRound } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button, cn } from "@cz/design-system";
import { useAppState } from "../../lib/app-state";
import { useMe } from "../../lib/me";
import { useToast } from "../../lib/toast";
import { PrivacyDialogs, type PrivacyView } from "../settings/PrivacyDialogs";
import { lastLoginNickname } from "./QuarkLoginDialog";

/** 顶栏账号：登录夸克 / 已登录（菜单：退出登录、隐私说明、删除我的数据） */
export function AccountButton() {
  const { save, login, logout } = useMe();
  const { bumpSubs } = useAppState();
  const toast = useToast();
  const [menu, setMenu] = useState(false);
  const [busy, setBusy] = useState(false);
  const [privacy, setPrivacy] = useState<PrivacyView>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const name = save.nickname || "夸克用户";

  useEffect(() => {
    if (!save.logged_in) setMenu(false);
  }, [save.logged_in]);

  useEffect(() => {
    if (!menu) return;
    const onClick = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setMenu(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setMenu(false);
        btn.current?.focus();
      }
    };
    document.addEventListener("click", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [menu]);

  if (!save.login) return <PrivacyDialogs view={privacy} onChange={setPrivacy} />;

  async function onButton() {
    if (save.logged_in) {
      setMenu((m) => !m);
      return;
    }
    if (await login()) {
      const nick = lastLoginNickname;
      toast("登录成功" + (nick ? "（" + nick + "）" : "") + "，转存会保存到你的夸克网盘");
    }
  }

  async function onLogout() {
    setBusy(true);
    try {
      await logout();
      bumpSubs();
      toast("已退出登录，服务器上保存的凭证已删除");
    } catch {
      toast("退出失败，请稍后重试", "error");
    }
    setBusy(false);
  }

  return (
    <div ref={wrap} className="relative">
      <Button
        ref={btn}
        variant={save.logged_in ? "ghost" : "secondary"}
        size="sm"
        onClick={onButton}
        aria-haspopup="true"
        aria-expanded={menu}
        title={save.logged_in ? "已登录夸克：" + name : "扫码登录夸克，转存到自己的网盘"}
        className={cn("max-w-40 gap-1.5", save.logged_in && "text-fg")}
      >
        <UserRound />
        <span className="truncate max-sm:hidden">{save.logged_in ? name : "登录夸克"}</span>
        {!save.logged_in && <span className="sm:hidden">登录</span>}
      </Button>
      {menu && (
        <div role="menu" className="absolute right-0 z-40 mt-2 flex w-64 flex-col gap-3 rounded-popover border border-line-subtle bg-raised p-4 shadow-lg">
          <div className="flex flex-col gap-0.5">
            <p className="text-footnote text-fg-muted">
              已登录夸克：<b className="font-medium text-fg">{name}</b>
            </p>
            <p className="text-caption text-fg-subtle">转存会保存到你自己的夸克网盘</p>
          </div>
          <Button role="menuitem" variant="secondary" size="sm" loading={busy} disabled={busy} onClick={onLogout}>
            退出登录
          </Button>
          <div className="flex items-center justify-between border-t border-line-subtle pt-3">
            <Button role="menuitem" variant="link" size="sm" onClick={() => (setMenu(false), setPrivacy("privacy"))}>
              隐私说明
            </Button>
            <Button role="menuitem" variant="link" size="sm" className="text-danger-fg" onClick={() => (setMenu(false), setPrivacy("delete"))}>
              删除我的数据
            </Button>
          </div>
        </div>
      )}
      <PrivacyDialogs view={privacy} onChange={setPrivacy} />
    </div>
  );
}

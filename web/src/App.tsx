import { Bell, Moon, Search, SlidersHorizontal, Sun } from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";
import { Button, cn } from "@cz/design-system";
import { AccountButton } from "./features/account/AccountButton";
import { QuarkLoginDialog } from "./features/account/QuarkLoginDialog";
import { SubsSync } from "./features/subs/SubsSync";
import { store } from "./lib/api";
import { useAppState, useRoute, type Route } from "./lib/app-state";
import { AdminPage } from "./pages/Admin";
import { DiscoverPage } from "./pages/Discover";
import { SettingsPage } from "./pages/Settings";
import { SubsPage } from "./pages/Subs";

function ThemeButton() {
  const dark = () => (document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark";
  const [isDark, setDark] = useState(dark);
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={isDark ? "切换到浅色" : "切换到深色"}
      onClick={() => {
        const next = dark() ? "light" : "dark";
        document.documentElement.dataset.theme = next;
        store.set("qp_theme", next);
        setDark(next === "dark");
      }}
    >
      {isDark ? <Sun /> : <Moon />}
    </Button>
  );
}

type NavItem = { view: Route["view"]; href: string; label: string; icon: ReactNode; group: string; needsSubs?: boolean };
const NAV: NavItem[] = [
  { view: "discover", href: "#/", label: "搜索资源", icon: <Search />, group: "发现" },
  { view: "subs", href: "#/subs", label: "我的订阅", icon: <Bell />, group: "订阅", needsSubs: true },
  { view: "settings", href: "#/settings", label: "偏好设置", icon: <SlidersHorizontal />, group: "账户" },
];

function Nav({ route }: { route: Route }) {
  const { subsEnabled, unread } = useAppState();
  const items = NAV.filter((n) => !n.needsSubs || subsEnabled);
  const link = (n: NavItem, mobile: boolean) => {
    const on = route.view === n.view;
    return (
      <a
        key={n.href}
        href={n.href}
        aria-current={on ? "page" : undefined}
        className={cn(
          "relative flex items-center rounded-control text-fg-muted transition duration-fast ease-standard hover:text-fg [&_svg]:size-4 [&_svg]:shrink-0",
          mobile ? "flex-1 flex-col gap-1 py-2 text-caption" : "gap-2.5 px-2.5 py-1.5 text-body hover:bg-hover",
          on && (mobile ? "text-fg" : "bg-selected text-fg"),
        )}
      >
        {n.icon}
        <span>{n.label}</span>
        {n.view === "subs" && unread > 0 && (
          <span
            className={cn(
              "grid h-4 min-w-4 place-items-center rounded-pill bg-accent px-1 text-caption text-on-accent tabular-nums",
              mobile ? "absolute top-1 left-1/2 ml-2" : "ml-auto",
            )}
          >
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </a>
    );
  };
  let lastGroup = "";
  return (
    <>
      <nav aria-label="主导航" className="sticky top-16 hidden w-52 shrink-0 flex-col gap-0.5 self-start md:flex">
        {items.map((n) => {
          const head = n.group !== lastGroup;
          lastGroup = n.group;
          return (
            <div key={n.href} className="flex flex-col">
              {head && <span className="px-2.5 pt-4 pb-1 text-caption text-fg-subtle first:pt-0">{n.group}</span>}
              {link(n, false)}
            </div>
          );
        })}
      </nav>
      <nav
        aria-label="主导航"
        className="fixed inset-x-0 bottom-0 z-30 flex border-t border-line bg-canvas px-2 pb-[env(safe-area-inset-bottom)] md:hidden"
      >
        {items.map((n) => link(n, true))}
      </nav>
    </>
  );
}

export function App() {
  const route = useRoute();
  return (
    <div className="min-h-dvh bg-canvas text-fg">
      <header className="sticky top-0 z-20 border-b border-line-subtle bg-canvas/85 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4 md:px-6">
          <a href="#/" className="flex items-center gap-2 font-medium text-fg">
            <span className="grid size-7 place-items-center rounded-sm bg-accent text-on-accent">
              <Search className="size-4" />
            </span>
            QueryPilot
          </a>
          <div className="flex items-center gap-1">
            <AccountButton />
            <ThemeButton />
          </div>
        </div>
      </header>
      <div className="mx-auto flex max-w-6xl gap-10 px-4 pt-6 pb-28 md:px-6 md:pb-16">
        <Nav route={route} />
        <main className="min-w-0 flex-1">
          {route.view === "discover" && <DiscoverPage />}
          {route.view === "subs" && <SubsPage tab={route.tab} />}
          {route.view === "settings" && <SettingsPage />}
          {route.view === "admin" && <AdminPage />}
        </main>
      </div>
      <SubsSync />
      <QuarkLoginDialog />
    </div>
  );
}

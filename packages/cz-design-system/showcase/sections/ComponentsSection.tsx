import { ArrowRight, Bell, FolderInput, Plus, Search, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import {
  AgentSteps,
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  Field,
  Input,
  Kbd,
  Label,
  Separator,
  Skeleton,
  StatusDot,
  Switch,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Textarea,
  ThinkingText,
  ToolCall,
  Tooltip,
} from "../../src";
import { SectionHeader } from "./SectionHeader";

function Demo({ title, file, children, className = "" }: { title: string; file: string; children: ReactNode; className?: string }) {
  return (
    <div className="flex min-w-0 flex-col rounded-card border border-line-subtle bg-surface">
      <div className="flex items-baseline justify-between gap-3 border-b border-line-subtle px-4 py-2">
        <span className="text-footnote font-medium text-fg">{title}</span>
        <code className="truncate font-mono text-caption font-normal text-fg-subtle">{file}</code>
      </div>
      <div className={`flex flex-1 flex-col gap-4 p-4 ${className}`}>{children}</div>
    </div>
  );
}

export function ComponentsSection() {
  const [loading, setLoading] = useState(false);
  const [autoSave, setAutoSave] = useState(true);
  return (
    <section className="flex flex-col gap-8">
      <SectionHeader id="components" source="src/components" title="组件">
        基于 Radix 的无障碍原语，按 shadcn/ui 的方式以源码交付，但样式全部重写：只使用 CZ token，
        Tailwind 默认色板和 shadcn 默认主题变量在这个系统里不存在，写了也不会生效，单元测试也会拦住。
      </SectionHeader>

      <div className="grid items-start gap-4 lg:grid-cols-2">
        <Demo title="Button" file="ui/button.tsx">
          <div className="flex flex-wrap items-center gap-2">
            <Button>
              <Plus />
              新建订阅
            </Button>
            <Button variant="secondary">取消</Button>
            <Button variant="soft">稍后提醒</Button>
            <Button variant="ghost">跳过</Button>
            <Button variant="danger">
              <Trash2 />
              删除
            </Button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="secondary">
              小号
            </Button>
            <Button size="md" variant="secondary">
              默认
            </Button>
            <Button size="lg" variant="secondary">
              大号
            </Button>
            <Tooltip content="搜索" shortcut="⌘K">
              <Button size="icon" variant="secondary" aria-label="搜索">
                <Search />
              </Button>
            </Tooltip>
            <Button
              loading={loading}
              onClick={() => {
                setLoading(true);
                setTimeout(() => setLoading(false), 1600);
              }}
            >
              {loading ? "转存中" : "点我试试加载"}
            </Button>
            <Button variant="link">
              查看全部
              <ArrowRight />
            </Button>
          </div>
        </Demo>

        <Demo title="Input · Field" file="ui/input.tsx">
          <Field label="剧名" htmlFor="demo-title" hint="支持中英文名，也可以粘贴豆瓣链接">
            <Input id="demo-title" defaultValue="繁花" />
          </Field>
          <Field label="保存到" htmlFor="demo-folder" error="网盘剩余空间不足 2.1 GB，请先清理或换一个目录">
            <Input id="demo-folder" defaultValue="/追剧/繁花" aria-invalid />
          </Field>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="demo-note">给 Agent 的备注</Label>
            <Textarea id="demo-note" placeholder="例如：只要国语版，优先 4K" />
          </div>
        </Demo>

        <Demo title="Badge · StatusDot" file="ui/badge.tsx · ai/status-dot.tsx">
          <div className="flex flex-wrap gap-2">
            <Badge>草稿</Badge>
            <Badge tone="accent">4K HDR</Badge>
            <Badge tone="success" dot>
              已转存
            </Badge>
            <Badge tone="warning" dot>
              等待确认
            </Badge>
            <Badge tone="danger" dot>
              链接失效
            </Badge>
            <Badge tone="info">新</Badge>
          </div>
          <Separator />
          <div className="flex flex-wrap gap-4">
            {(["idle", "running", "waiting", "success", "error"] as const).map((s) => (
              <span key={s} className="flex items-center gap-2 text-footnote text-fg-muted">
                <StatusDot state={s} />
                {{ idle: "空闲", running: "运行中", waiting: "等待确认", success: "已完成", error: "出错" }[s]}
              </span>
            ))}
          </div>
          <div className="flex items-center gap-2 text-body">
            <ThinkingText>正在比较 3 个来源的清晰度……</ThinkingText>
          </div>
        </Demo>

        <Demo title="Tabs · Switch · Kbd" file="ui/tabs.tsx · ui/switch.tsx">
          <Tabs defaultValue="all">
            <TabsList>
              <TabsTrigger value="all">全部</TabsTrigger>
              <TabsTrigger value="airing">连载中</TabsTrigger>
              <TabsTrigger value="done">已完结</TabsTrigger>
            </TabsList>
            <TabsContent value="all" className="text-footnote text-fg-muted">
              共 12 个订阅，3 个今天有更新。
            </TabsContent>
            <TabsContent value="airing" className="text-footnote text-fg-muted">
              9 个连载中，下一次检查在 18 分钟后。
            </TabsContent>
            <TabsContent value="done" className="text-footnote text-fg-muted">
              3 个已完结，全部集数都已转存。
            </TabsContent>
          </Tabs>
          <Separator />
          <div className="flex items-center justify-between gap-3">
            <div className="flex flex-col">
              <Label htmlFor="demo-auto">自动转存新集</Label>
              <span className="text-caption font-normal text-fg-subtle">发现更新后直接存到网盘</span>
            </div>
            <Switch id="demo-auto" checked={autoSave} onCheckedChange={setAutoSave} />
          </div>
          <div className="flex items-center gap-1.5 text-footnote text-fg-muted">
            按 <Kbd>⌘</Kbd>
            <Kbd>K</Kbd> 搜索，<Kbd>Esc</Kbd> 关闭
          </div>
        </Demo>

        <Demo title="Card" file="ui/card.tsx" className="bg-canvas">
          <Card interactive>
            <CardHeader>
              <div className="flex items-center justify-between gap-3">
                <CardTitle>繁花</CardTitle>
                <Badge tone="success" dot>
                  已更新
                </Badge>
              </div>
              <CardDescription>2023 · 剧集 · 更新至第 30 集</CardDescription>
            </CardHeader>
            <CardContent className="text-body text-fg-muted">每 30 分钟检查一次，上次检查 12 分钟前。</CardContent>
            <CardFooter>
              <Button size="sm" variant="secondary">
                <FolderInput />
                打开网盘目录
              </Button>
              <Button size="sm" variant="ghost" className="ml-auto">
                <Bell />
                通知
              </Button>
            </CardFooter>
          </Card>
        </Demo>

        <Demo title="Dialog · Skeleton" file="ui/dialog.tsx · ui/misc.tsx">
          <Dialog>
            <DialogTrigger asChild>
              <Button variant="secondary" className="w-fit">
                打开对话框
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>取消订阅《繁花》？</DialogTitle>
                <DialogDescription>
                  之后不会再自动转存新集。已经存到网盘的 30 集不受影响。
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <DialogClose asChild>
                  <Button variant="secondary">保留订阅</Button>
                </DialogClose>
                <DialogClose asChild>
                  <Button variant="danger">取消订阅</Button>
                </DialogClose>
              </DialogFooter>
            </DialogContent>
          </Dialog>
          <div className="flex items-center gap-3">
            <Skeleton className="size-10 rounded-md" />
            <div className="flex flex-1 flex-col gap-1.5">
              <Skeleton className="h-3 w-2/3" />
              <Skeleton className="h-3 w-1/3" />
            </div>
          </div>
        </Demo>

        <Demo title="AgentSteps" file="ai/agent-steps.tsx">
          <AgentSteps
            steps={[
              { id: "1", title: "读取订阅列表", status: "done", meta: "12 项" },
              { id: "2", title: "检查更新", status: "done", meta: "3.1s" },
              {
                id: "3",
                title: "转存《漫长的季节》第 6 集",
                status: "error",
                detail: "分享链接已失效。已换用备用来源，等你确认后重试。",
              },
              { id: "4", title: "发送更新通知", status: "pending" },
            ]}
          />
        </Demo>

        <Demo title="ToolCall" file="ai/tool-call.tsx">
          <ToolCall
            name="check_my_drive"
            summary="/追剧/繁花"
            status="done"
            defaultOpen
            input={{ path: "/追剧/繁花" }}
            output={{ files: 30, latest: "繁花.S01E30.2160p.mkv", free: "118 GB" }}
          />
          <ToolCall name="inspect_share" summary="pan.quark.cn/s/8f3c…" status="running" input={{ url: "pan.quark.cn/s/8f3c…" }} />
        </Demo>
      </div>
    </section>
  );
}

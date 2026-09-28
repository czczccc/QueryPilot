import { AnimatePresence, motion } from "framer-motion";
import { Paperclip, RotateCcw, Wrench } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  AgentSteps,
  Badge,
  Button,
  Composer,
  rise,
  StatusDot,
  StreamingText,
  ToolCall,
  Tooltip,
  type AgentStep,
  type AgentState,
} from "../../src";

const QUESTION = "《繁花》更新了吗？更新了就存到我的网盘";
const ANSWER =
  "已更新到第 30 集。我选了 4K HDR 的版本（2.1 GB，来源可信度高），存到了「追剧 / 繁花」。之后每次更新，我都会自动检查并转存，完成后通知你。";

type Stage = { at: number; steps: AgentStep[]; tool?: "running" | "done"; answer?: boolean; state: AgentState };

const base = (s: Partial<Record<string, AgentStep["status"]>>, meta: Record<string, string> = {}): AgentStep[] => [
  { id: "plan", title: "理解需求", status: s.plan ?? "pending", meta: meta.plan },
  {
    id: "search",
    title: "搜索最新一集",
    status: s.search ?? "pending",
    detail: "在 23 个频道中查找《繁花》第 30 集",
    meta: meta.search,
  },
  {
    id: "verify",
    title: "校验分享链接",
    status: s.verify ?? "pending",
    detail: "3 个候选链接，正在检查有效性和清晰度",
    meta: meta.verify,
  },
  { id: "save", title: "转存到网盘", status: s.save ?? "pending", detail: "写入「追剧 / 繁花」", meta: meta.save },
];

const timeline: Stage[] = [
  { at: 0, state: "running", steps: base({ plan: "running" }) },
  { at: 900, state: "running", steps: base({ plan: "done", search: "running" }, { plan: "0.4s" }), tool: "running" },
  {
    at: 2600,
    state: "running",
    steps: base({ plan: "done", search: "done", verify: "running" }, { plan: "0.4s", search: "1.7s" }),
    tool: "done",
  },
  {
    at: 4200,
    state: "running",
    steps: base(
      { plan: "done", search: "done", verify: "done", save: "running" },
      { plan: "0.4s", search: "1.7s", verify: "1.5s" },
    ),
    tool: "done",
  },
  {
    at: 5600,
    state: "success",
    steps: base(
      { plan: "done", search: "done", verify: "done", save: "done" },
      { plan: "0.4s", search: "1.7s", verify: "1.5s", save: "1.3s" },
    ),
    tool: "done",
    answer: true,
  },
];

/** 首屏演示：一次完整的 Agent 任务，全部由系统组件组成。 */
export function AgentDemo() {
  const [stage, setStage] = useState(0);
  const [run, setRun] = useState(0);
  const [draft, setDraft] = useState("");
  const timers = useRef<number[]>([]);

  useEffect(() => {
    setStage(0);
    timers.current.forEach(clearTimeout);
    timers.current = timeline.slice(1).map((s, i) => window.setTimeout(() => setStage(i + 1), s.at));
    return () => timers.current.forEach(clearTimeout);
  }, [run]);

  const current = timeline[stage];
  const running = current.state === "running";

  return (
    <div className="flex flex-col gap-3 rounded-dialog border border-line-subtle bg-sunken p-3 shadow-lg sm:p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <StatusDot state={current.state} />
          <span className="text-footnote font-medium text-fg">{running ? "Agent 工作中" : "任务完成"}</span>
          <Badge tone={running ? "accent" : "success"}>{running ? `${stage + 1} / 4` : "已转存"}</Badge>
        </div>
        <Tooltip content="重新播放">
          <Button variant="ghost" size="icon-sm" onClick={() => setRun((r) => r + 1)} aria-label="重新播放">
            <RotateCcw />
          </Button>
        </Tooltip>
      </div>

      <div className="flex justify-end">
        <p className="max-w-[85%] rounded-lg rounded-br-xs bg-accent-soft px-3 py-2 text-body text-fg">{QUESTION}</p>
      </div>

      <div className="rounded-card border border-line-subtle bg-canvas p-3">
        <AgentSteps key={run} steps={current.steps} />
      </div>

      <AnimatePresence initial={false}>
        {current.tool && (
          <motion.div key={`tool-${run}`} variants={rise} initial="hidden" animate="visible" exit="hidden">
            <ToolCall
              name="search_resources"
              summary="繁花 第30集 4K"
              status={current.tool}
              input={{ query: "繁花 第30集", quality: ">=1080p", channels: 23 }}
              output={
                current.tool === "done"
                  ? { hits: 3, best: { title: "繁花.S01E30.2160p.HDR", size: "2.1 GB", score: 0.94 } }
                  : undefined
              }
            />
          </motion.div>
        )}
      </AnimatePresence>

      <div className="min-h-26">
        {current.answer && <StreamingText key={run} text={ANSWER} className="text-body" />}
      </div>

      <Composer
        id="demo-composer"
        value={draft}
        onChange={setDraft}
        onSubmit={() => {
          setDraft("");
          setRun((r) => r + 1);
        }}
        onStop={() => setStage(timeline.length - 1)}
        running={running}
        placeholder="继续追问，例如：换成 1080p 的版本"
        tools={
          <>
            <Tooltip content="添加附件">
              <Button type="button" variant="ghost" size="icon-sm" aria-label="添加附件">
                <Paperclip />
              </Button>
            </Tooltip>
            <Button type="button" variant="ghost" size="sm">
              <Wrench />
              工具 3
            </Button>
          </>
        }
      />
    </div>
  );
}

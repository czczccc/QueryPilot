import { motion } from "framer-motion";
import { Play } from "lucide-react";
import { useState } from "react";
import { Button, cubicBezier, duration, easing, spring, type EasingName, type SpringName } from "../../src";
import { Panel, SectionHeader } from "./SectionHeader";

const easeUse: Record<EasingName, string> = {
  standard: "状态切换、颜色变化",
  enter: "元素进入：快出现、慢落定",
  exit: "元素离开：慢起步、快离开",
  emphasized: "成功反馈、开关滑块",
  linear: "流光、进度等持续动画",
};

const springUse: Record<SpringName, string> = {
  snappy: "按钮按下、开关、小图标",
  smooth: "弹出层、卡片、列表项",
  gentle: "大面板、页面切换",
  layout: "标签滑块、列表重排",
};

// 缓动曲线 → SVG 路径（0..1 映射到 100x60 的画布，y 向上）
const curvePath = ([x1, y1, x2, y2]: readonly number[]) =>
  `M0 60 C ${x1 * 100} ${60 - y1 * 60}, ${x2 * 100} ${60 - y2 * 60}, 100 0`;

export function MotionSection() {
  const [on, setOn] = useState(false);
  const [springOn, setSpringOn] = useState(false);
  return (
    <section className="flex flex-col gap-8">
      <SectionHeader id="motion" source="src/tokens/motion.ts · src/motion/index.ts" title="动效">
        动效用来解释变化，而不是吸引注意。直接反馈不超过 140ms；进入减速、退出加速，退出时长约为进入的 70%；
        空间变化用弹簧，颜色和透明度用补间；位移不超过 8px，缩放从 96% 开始。系统开启“减少动态效果”时只保留透明度。
      </SectionHeader>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel className="flex flex-col gap-4 p-4">
          <div className="flex items-center justify-between">
            <h3 className="text-title-sm">缓动曲线</h3>
            <Button variant="secondary" size="sm" onClick={() => setOn((v) => !v)}>
              <Play />
              播放
            </Button>
          </div>
          <div className="flex flex-col gap-3">
            {(Object.keys(easing) as EasingName[]).map((k) => (
              <div key={k} className="grid grid-cols-[4.5rem_minmax(0,1fr)] items-center gap-3">
                <svg viewBox="-4 -14 108 88" className="h-12 w-full overflow-visible" aria-hidden>
                  <path d="M0 60 L100 0" className="stroke-line-subtle" strokeDasharray="2 3" fill="none" />
                  <path d={curvePath(easing[k])} className="stroke-accent" strokeWidth="2" fill="none" />
                </svg>
                <div className="flex min-w-0 flex-col gap-1.5">
                  <div className="flex items-baseline justify-between gap-2">
                    <code className="font-mono text-footnote text-fg">ease-{k}</code>
                    <span className="truncate text-caption font-normal text-fg-subtle">{easeUse[k]}</span>
                  </div>
                  <div className="relative h-2 rounded-full bg-sunken">
                    <span
                      className="absolute top-1/2 left-0 size-3 -translate-y-1/2 rounded-full bg-accent shadow-sm"
                      style={{
                        left: on ? "calc(100% - 0.75rem)" : "0",
                        transitionProperty: "left",
                        transitionDuration: `${duration.slower * 2}ms`,
                        transitionTimingFunction: cubicBezier(easing[k]),
                      }}
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Panel>

        <Panel className="flex flex-col gap-4 p-4">
          <div className="flex items-center justify-between">
            <h3 className="text-title-sm">弹簧（Framer Motion）</h3>
            <Button variant="secondary" size="sm" onClick={() => setSpringOn((v) => !v)}>
              <Play />
              播放
            </Button>
          </div>
          <div className="flex flex-col gap-4">
            {(Object.keys(spring) as SpringName[]).map((k) => (
              <div key={k} className="flex flex-col gap-1.5">
                <div className="flex items-baseline justify-between gap-2">
                  <code className="font-mono text-footnote text-fg">spring.{k}</code>
                  <span className="truncate text-caption font-normal text-fg-subtle">{springUse[k]}</span>
                </div>
                <div className="relative h-8 rounded-sm bg-sunken">
                  <span className="absolute inset-y-1 right-7 left-1">
                    <motion.span
                      className="absolute top-0 size-6 rounded-xs bg-accent shadow-sm"
                      initial={false}
                      animate={{ left: springOn ? "100%" : "0%" }}
                      transition={spring[k]}
                    />
                  </span>
                </div>
                <span className="font-mono text-caption font-normal text-fg-subtle">
                  stiffness {spring[k].stiffness} · damping {spring[k].damping}
                </span>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <Panel className="p-4">
        <h3 className="mb-4 text-title-sm">时长</h3>
        <div className="flex flex-col gap-2">
          {(Object.entries(duration) as [string, number][]).map(([k, ms]) => (
            <div key={k} className="grid grid-cols-[5rem_4rem_minmax(0,1fr)] items-center gap-3">
              <code className="font-mono text-footnote text-fg">{k}</code>
              <span className="font-mono text-caption font-normal text-fg-subtle tabular-nums">{ms}ms</span>
              <span className="h-2 rounded-full bg-accent-soft">
                <span
                  className="block h-2 rounded-full bg-accent"
                  style={{ width: `${Math.min(100, (ms / duration.ambient) * 100)}%` }}
                />
              </span>
            </div>
          ))}
        </div>
      </Panel>
    </section>
  );
}

// 搜索页的数据类型与 agent 步骤文案（与旧前端 app.js 保持一致）
import { RES_LABEL, RES_RANK, episodeRanges } from "../../lib/format";

export type Quality = {
  resolution?: string | null;
  resolution_guessed?: boolean;
  hdr?: boolean;
  source?: string | null;
  low_quality?: boolean;
  has_subtitle?: boolean;
  video_count?: number;
  size_bytes?: number | null;
};

export type Link = {
  name: string;
  share: string;
  pwd?: string | null;
  time?: string;
  source?: string;
  conf?: string;
  state?: "valid" | "invalid" | "unknown" | string;
  relevance?: "match" | "mismatch" | "uncertain" | string;
  relevance_note?: string | null;
  quality?: Quality | null;
  copy_count?: number;
  from_memory?: boolean;
  last_checked?: number | null;
  share_title?: string | null;
  files_preview?: string[] | null;
};

export type Step = {
  tool: string;
  args?: Record<string, any>;
  observation?: Record<string, any>;
  thought?: string | null;
  duration_ms: number;
};

export type SearchResult = {
  query: string;
  planner?: string;
  stop_reason?: string;
  steps: Step[];
  links: Link[];
  parsed: {
    resource: string;
    english_name?: string | null;
    quality?: string | null;
    aliases?: string[];
    search_suggestions?: string[];
  };
  douban?: { title: string; year?: string | number; kind?: string } | null;
  metrics: {
    duration_ms: number;
    raw_result_count: number;
    deduplicated_result_count: number;
    memory_hits?: number;
    skipped_invalid?: number;
    fallback_used?: boolean;
    served_from_memory?: boolean;
  };
  providers: { name: string; status: string }[];
  followup?: unknown;
  session_id?: string | null;
  history?: string[];
  filters?: { season?: number; resolution?: string; subtitle?: boolean; hdr?: boolean };
  quota?: any;
};

export const TOOL_NAME: Record<string, string> = {
  recall_memory: "记忆",
  search: "搜索",
  verify: "验证",
  judge_relevance: "AI 核对",
  finish: "完成",
  interpret_followup: "追问",
  cache: "缓存",
  lookup_media: "查条目",
  inspect_share: "看文件",
  check_my_drive: "我的网盘",
};

// 网盘里已有的集：{"1":[1,2]} → 「第 1 季 1、2 集」
export function driveEpisodesText(eps: Record<string, number[]> | null | undefined): string {
  const e = eps || {};
  return Object.keys(e)
    .sort((a, b) => Number(a) - Number(b))
    .filter((k) => (e[k] || []).length)
    .map((k) => "第 " + k + " 季 " + episodeRanges(e[k]) + " 集")
    .join("；");
}

type A = Record<string, any>;
export const TOOL_TEXT: Record<string, (a: A, o: A) => string> = {
  lookup_media: (a, o) => {
    const n = (o.results || []).length;
    const alias = (o.new_names || []).filter(Boolean);
    return (
      "查条目：" + (a.name || "") + (a.year ? "（" + a.year + "）" : "") +
      (o.results ? " → 找到 " + n + " 条" : "") + (alias.length ? "，新增别名 " + alias.slice(0, 4).join("、") : "")
    );
  },
  inspect_share: (a, o) => {
    const n = (a.shares || []).length;
    const res: any[] = Object.values(o.shares || {});
    const video = res.filter((r) => r && !r.error && (r.kind === "video" || r.kind === "pack")).length;
    const bad = res.filter((r) => r && !r.error && r.kind && r.kind !== "video" && r.kind !== "pack").length;
    return "打开 " + n + " 个分享看文件" + (res.length ? " → " + video + " 个是视频" + (bad ? "，" + bad + " 个不是视频" : "") : "");
  },
  check_my_drive: (_a, o) => {
    if (o.logged_in === false) return "查你的网盘：未登录，跳过";
    const t = driveEpisodesText(o.episodes);
    return "查你的网盘：" + (t ? "已有" + t : o.videos ? "有 " + o.videos + " 个相关视频" : "还没有这部");
  },
  cache: (a) => "复用最近结果：" + (a.minutes_ago ? a.minutes_ago + " 分钟前" : "刚刚") + "有人搜过同样的内容，直接用那次验证过的链接",
  recall_memory: (_a, o) => "查记忆：记住 " + (o.remembered_valid || 0) + " 条有效链接，其中 " + (o.fresh || 0) + " 条近期验证过",
  search: (a, o) =>
    "搜索 " + (a.queries || []).map((q: string) => "「" + q + "」").join("") +
    (a.keyword ? "（网盘站关键词：" + a.keyword + "）" : "") +
    " → 找到 " + (o.found || 0) + " 条，新增候选 " + (o.new_candidates || 0) + " 条" +
    (o.known_invalid_skipped ? "，其中已知失效 " + o.known_invalid_skipped + " 条" : ""),
  verify: (_a, o) =>
    "验证 " + (o.verified || 0) + " 条 → 有效 " + (o.valid || 0) + "、失效 " + (o.invalid || 0) + "、待确认 " + (o.unknown || 0) +
    (o.wrong_title ? "、片名不符 " + o.wrong_title : "") + "；满足要求累计 " + (o.matching_total || 0) + " 条",
  judge_relevance: (a, o) =>
    "AI 核对 " + (a.count || 0) + " 条标题不明确的链接 → 相关 " + (o.match || 0) + "、不相关 " + (o.mismatch || 0) +
    "；满足要求累计 " + (o.matching_total || 0) + " 条",
  finish: (a) => "结束：" + (a.reason || ""),
  interpret_followup: (a, o) => {
    if (o.mode === "new") return "理解追问「" + a.text + "」：这是一个新的搜索";
    const bits: string[] = [];
    if (o.season) bits.push("改为第" + o.season + "季");
    if (o.resolution) bits.push("要 " + (RES_LABEL[o.resolution] || o.resolution) + " 以上");
    if (o.subtitle) bits.push("要字幕");
    if (o.hdr) bits.push("要 HDR");
    if (o.more) bits.push("再多找一些");
    return "理解追问「" + a.text + "」" + (o.by === "llm" ? "（AI）" : "") + "：" + (bits.join("、") || "沿用上一轮条件");
  },
};

export function stepText(step: Step): string {
  const obs = step.observation || {};
  const fn = TOOL_TEXT[step.tool];
  return obs.error ? step.tool + " 出错：" + obs.error : fn ? fn(step.args || {}, obs) : step.tool;
}

export function visibleLinks(links: Link[], hideDead: boolean, minRes: string): Link[] {
  return links.filter((l) => {
    // 「只看有效且相关」：只留验证有效、且确认是这部作品的（待核对的不算）
    if (hideDead && !(l.state === "valid" && l.relevance === "match")) return false;
    if (minRes) {
      const res = l.quality && l.quality.resolution;
      // 未识别出分辨率的有效链接保留（可能是好资源，只是文件名没写）
      if (res && RES_RANK[res] < RES_RANK[minRes]) return false;
      if (!res && l.state !== "valid") return false;
    }
    return true;
  });
}

// 可能相关：没能确认是这部的（只看有效时只留有效的），同样受清晰度筛选
export function maybeLinks(links: Link[], hideDead: boolean, minRes: string): Link[] {
  return visibleLinks(links, false, minRes).filter((l) => l.relevance === "uncertain" && (!hideDead || l.state === "valid"));
}

// 订阅的目标：有结果时用识别出的资源名，没结果时直接用搜索词
export function subscribeTarget(data: SearchResult) {
  const first = data.history && data.history.length ? data.history[0] : data.query;
  const empty = !data.links || data.links.length === 0;
  return { query: first, resource: empty ? first : data.parsed.resource, empty };
}

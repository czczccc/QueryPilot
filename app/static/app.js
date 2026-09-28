"use strict";

const form = document.getElementById("search-form");
const input = document.getElementById("query");
const submitBtn = document.getElementById("submit-btn");
const formError = document.getElementById("form-error");
const statusEl = document.getElementById("status");
const resultsSection = document.getElementById("results");
const parsedCard = document.getElementById("parsed-card");
const metricsEl = document.getElementById("metrics");
const linkActions = document.getElementById("link-actions");
const resultCount = document.getElementById("result-count");
const copyBtn = document.getElementById("copy-btn");
const resultList = document.getElementById("result-list");
const emptyState = document.getElementById("empty-state");
const agentPanel = document.getElementById("agent-panel");
const agentTitle = document.getElementById("agent-title");
const agentSteps = document.getElementById("agent-steps");
const agentTimer = document.getElementById("agent-timer");
const agentToggle = document.getElementById("agent-toggle");
const skeleton = document.getElementById("skeleton");
const toastsEl = document.getElementById("toasts");
const maybeBox = document.getElementById("maybe-box");
const maybeList = document.getElementById("maybe-list");
const maybeCount = document.getElementById("maybe-count");

let currentLinks = [];
let hideDead = true;
let minRes = "";
let lastQuery = "";
let saveEnabled = false; // 服务器配置了一键转存才显示「转存」按钮

function getClientId() {
  try {
    let id = localStorage.getItem("qp_client_id");
    if (!id) {
      id = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random()).replace(/[^0-9a-z]/gi, "");
      localStorage.setItem("qp_client_id", id);
    }
    return id;
  } catch (_) {
    return null; // 存储不可用：不带偏好
  }
}
const clientId = getClientId();

const RES_RANK = { SD: 1, "720p": 2, "1080p": 3, "2160p": 4 };
const RES_LABEL = { SD: "标清", "720p": "720p", "1080p": "1080p", "2160p": "4K" };

function visibleLinks(links) {
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

function formatSize(bytes) {
  if (!bytes) return "";
  const gb = bytes / 1024 ** 3;
  return gb >= 1 ? gb.toFixed(1) + "GB" : Math.round(bytes / 1024 ** 2) + "MB";
}

function setText(el, text) {
  el.textContent = text;
}

// 小工具：建元素并设 class / 文本
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function badge(text, cls, title) {
  const b = el("span", "badge " + cls, text);
  if (title) b.title = title;
  return b;
}

function qualityBadges(q) {
  const out = [];
  if (!q) return out;
  if (q.resolution) {
    out.push(badge(RES_LABEL[q.resolution] + (q.resolution_guessed ? "?" : ""), "badge-res",
      q.resolution_guessed ? "由文件体积推断" : ""));
  }
  if (q.hdr) out.push(badge("HDR", "badge-res"));
  if (q.source) out.push(badge(q.source, "badge-tag"));
  if (q.low_quality) out.push(badge("疑似枪版", "badge-dead"));
  if (q.has_subtitle) out.push(badge("字幕", "badge-tag"));
  if (q.video_count > 1) out.push(badge(q.video_count + " 个视频", "badge-tag"));
  const size = formatSize(q.size_bytes);
  if (size) out.push(badge(size, "badge-tag"));
  return out;
}

// ---- 提示 ----
function showStatus(text, kind) {
  statusEl.hidden = false;
  statusEl.textContent = text;
  statusEl.dataset.kind = kind || "info";
}

function hideStatus() {
  statusEl.hidden = true;
  statusEl.textContent = "";
}

function toast(text, kind, ms) {
  const t = el("div", "toast " + (kind || "ok"));
  t.setAttribute("role", kind === "error" ? "alert" : "status");
  t.append(el("span", "toast-ico", kind === "error" ? "!" : "✓"), el("span", "", text));
  toastsEl.appendChild(t);
  while (toastsEl.children.length > 3) toastsEl.firstChild.remove();
  setTimeout(() => {
    t.classList.add("leaving");
    setTimeout(() => t.remove(), 260);
  }, ms || (kind === "error" ? 4000 : 2200));
}

function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  // 局域网 http 访问时没有 clipboard API，退回老办法
  return new Promise((resolve, reject) => {
    const ta = el("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    if (ok) resolve(); else reject(new Error("copy failed"));
  });
}

function flashButton(btn, text) {
  const old = btn.dataset.label || btn.textContent;
  btn.dataset.label = old;
  btn.classList.add("done");
  setText(btn, text);
  clearTimeout(btn._flash);
  btn._flash = setTimeout(() => {
    btn.classList.remove("done");
    setText(btn, old);
  }, 1600);
}

function showFormError(text) {
  formError.textContent = text;
  formError.hidden = false;
  // 重新触发抖动动画
  formError.style.animation = "none";
  void formError.offsetWidth;
  formError.style.animation = "";
  input.focus();
}

function hideFormError() {
  formError.textContent = "";
  formError.hidden = true;
}

// ---- 渲染：识别卡片 ----
function chipRow(label, items) {
  const row = el("div", "chip-row");
  row.appendChild(el("span", "chip-row-label", label));
  for (const it of items) row.appendChild(el("span", "chip", it));
  return row;
}

function renderParsed(parsed, douban) {
  parsedCard.hidden = false;
  parsedCard.innerHTML = "";
  parsedCard.appendChild(el("p", "parsed-label", "资源识别"));
  parsedCard.appendChild(el("h2", "parsed-title", parsed.resource));

  const sub = [];
  if (parsed.english_name) sub.push(parsed.english_name);
  if (douban) {
    sub.push("豆瓣：" + douban.title + (douban.year ? " (" + douban.year + ")" : "") +
      (douban.kind ? " · " + douban.kind : ""));
  }
  if (sub.length) parsedCard.appendChild(el("p", "parsed-sub", sub.join("　·　")));

  const tags = [];
  if (parsed.quality) tags.push("清晰度 " + parsed.quality);
  if (parsed.aliases && parsed.aliases.length) tags.push(...parsed.aliases.map((a) => "别名 " + a));
  if (tags.length) parsedCard.appendChild(chipRow("识别", tags));
  if (parsed.search_suggestions && parsed.search_suggestions.length) {
    parsedCard.appendChild(chipRow("搜索词", parsed.search_suggestions));
  }
}

// ---- 渲染：指标 ----
function stat(label, value, cls) {
  const s = el("span", "stat" + (cls ? " " + cls : ""));
  s.append(el("span", "", label), el("b", "", String(value)));
  return s;
}

function renderMetrics(metrics, providers, isFollowup) {
  metricsEl.hidden = false;
  metricsEl.innerHTML = "";
  metricsEl.appendChild(stat("耗时", (metrics.duration_ms / 1000).toFixed(1) + "s"));
  metricsEl.appendChild(stat("原始", metrics.raw_result_count));
  metricsEl.appendChild(stat("去重后", metrics.deduplicated_result_count));
  if (metrics.memory_hits) metricsEl.appendChild(stat("记忆复用", metrics.memory_hits, "ok"));
  if (metrics.skipped_invalid) metricsEl.appendChild(stat("跳过已知失效", metrics.skipped_invalid));
  for (const p of providers) {
    const label = { ok: "正常", error: "出错", skipped: "跳过" }[p.status] || p.status;
    metricsEl.appendChild(stat(p.name, label, p.status === "ok" ? "ok" : p.status === "error" ? "bad" : ""));
  }
  if (metrics.fallback_used) {
    const w = el("span", "stat warn", "已使用基础查询（AI 解析暂不可用）");
    metricsEl.appendChild(w);
  }

  if (metrics.served_from_memory && !isFollowup) {
    const note = el("p", "memory-note", "这些结果来自之前搜索并验证过的记忆，已跳过全网搜索。");
    const btn = el("button", "secondary-btn small", "重新全网搜索");
    btn.type = "button";
    btn.addEventListener("click", () => doSearch(lastQuery, true));
    note.appendChild(btn);
    metricsEl.appendChild(note);
  }
}

// ---- 渲染：结果列表 ----
const STATE_MAP = {
  valid: ["有效", "ok"],
  invalid: ["已失效", "dead"],
  unknown: ["待确认", "unknown"],
};

function shareLink(l) {
  return "https://pan.quark.cn/s/" + l.share + (l.pwd ? "?pwd=" + l.pwd : "");
}

function renderCount(links, visible) {
  const valid = links.filter((l) => l.state === "valid" && l.relevance === "match").length;
  resultCount.innerHTML = "";
  resultCount.append("显示 ", el("b", "", String(visible.length)), " / " + links.length +
    " 条，其中有效且相关 " + valid + " 条");
}

function renderEmpty(title, text) {
  emptyState.hidden = false;
  emptyState.innerHTML = "";
  const illu = el("div", "empty-illu");
  illu.innerHTML = '<svg viewBox="0 0 24 24" width="30" height="30" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="m15.5 15.5 5 5M8 10.5h5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
  emptyState.append(illu, el("h3", "", title), el("p", "", text));
}

function renderLinks(links) {
  currentLinks = links;
  const visible = visibleLinks(links);
  resultList.innerHTML = "";
  renderCount(links, visible);

  // 待核对（uncertain）的单独折叠到「可能相关」，主列表只放确认相关的
  const main = visible.filter((l) => l.relevance !== "uncertain");
  const maybe = maybeLinks(links);
  if (main.length === 0) {
    if (maybe.length) {
      renderEmpty("没有确认相关的链接", "下面有 " + maybe.length + " 条「可能相关」的资源，没能确认是不是这部，请展开自己核对。");
    } else {
      renderEmpty("当前筛选下没有链接", "共有 " + links.length + " 条结果被筛掉了。可以关掉「只看有效且相关」或调低清晰度要求再看看。");
    }
  } else {
    emptyState.hidden = true;
  }
  main.forEach((l, i) => resultList.appendChild(linkCard(l, i)));

  maybeList.innerHTML = "";
  maybeBox.hidden = maybe.length === 0;
  setText(maybeCount, String(maybe.length));
  if (!maybe.length) maybeBox.open = false;
  maybe.forEach((l, i) => maybeList.appendChild(linkCard(l, i)));
}

// 可能相关：没能确认是这部的（只看有效时只留有效的），同样受清晰度筛选
function maybeLinks(links) {
  const saved = hideDead;
  hideDead = false;
  const pool = visibleLinks(links);
  hideDead = saved;
  return pool.filter((l) => l.relevance === "uncertain" && (!hideDead || l.state === "valid"));
}

function linkCard(l, i) {
  const li = el("li", "result-card state-" + (l.state || "unknown"));
  li.style.setProperty("--i", String(Math.min(i, 12)));

  const head = el("div", "rc-head");
  head.appendChild(el("h3", "rc-title", l.name));
  const [stateText, stateCls] = STATE_MAP[l.state] || STATE_MAP.unknown;
  head.appendChild(el("span", "state-pill " + stateCls, stateText));
  li.appendChild(head);

  const badges = el("div", "badges");
  badges.appendChild(badge(l.conf + "置信", "badge-" + (l.conf === "高" ? "high" : l.conf === "低" ? "low" : "mid")));
  for (const b of qualityBadges(l.quality)) badges.appendChild(b);
  if (l.state === "valid" && l.relevance === "mismatch") {
    badges.appendChild(badge("片名不符", "badge-dead", l.relevance_note));
  } else if (l.state === "valid" && l.relevance === "uncertain") {
    badges.appendChild(badge("待核对", "badge-unknown", l.relevance_note || "没能确认是不是这部作品"));
  }
  if (l.copy_count) badges.appendChild(badge(l.copy_count + " 次复制", "badge-accent"));
  if (l.from_memory) {
    badges.appendChild(badge("记忆", "badge-tag",
      l.last_checked ? "上次验证：" + new Date(l.last_checked * 1000).toLocaleString() : ""));
  }
  li.appendChild(badges);

  const box = el("div", "link-box");
  const url = el("a", "share-url", "https://pan.quark.cn/s/" + l.share);
  url.href = "https://pan.quark.cn/s/" + l.share;
  url.target = "_blank";
  url.rel = "noopener noreferrer";
  box.appendChild(url);
  box.appendChild(el("span", "pwd", l.pwd ? "提取码 " + l.pwd : "无提取码"));
  li.appendChild(box);

  const meta = el("div", "meta");
  if (l.time) meta.appendChild(el("span", "", l.time));
  meta.appendChild(el("span", "", "来源：" + l.source));
  li.appendChild(meta);

  if (l.share_title || (l.files_preview && l.files_preview.length)) {
    const parts = [];
    if (l.share_title) parts.push("分享标题：" + l.share_title);
    if (l.files_preview && l.files_preview.length) parts.push("内容：" + l.files_preview.join("、"));
    const fp = el("div", "files-preview", parts.join("　|　"));
    fp.title = parts.join("\n");
    li.appendChild(fp);
  }

  if (l.state === "invalid") li.classList.add("result-dead");

  const row = el("div", "row-actions");
  const copyOne = el("button", "secondary-btn small accent", "复制链接");
  copyOne.type = "button";
  copyOne.addEventListener("click", () => {
    const link = shareLink(l);
    copyText(link).then(() => {
      flashButton(copyOne, "已复制 ✓");
      toast("已复制" + (l.pwd ? "（含提取码）" : "") + "：" + link);
    }).catch(() => toast("复制失败，请手动选中链接复制", "error"));
    // 反馈：被复制过的链接下次排序更靠前（失败不影响使用）
    fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ share: l.share }),
    }).catch(() => {});
  });
  row.appendChild(copyOne);
  const open = el("a", "secondary-btn small", "打开");
  open.href = url.href;
  open.target = "_blank";
  open.rel = "noopener noreferrer";
  row.appendChild(open);
  if (saveEnabled && l.state === "valid") row.appendChild(saveButton(l));
  li.appendChild(row);
  return li;
}

copyBtn.addEventListener("click", () => {
  const visible = visibleLinks(currentLinks);
  if (!visible.length) {
    toast("当前没有可复制的链接", "error");
    return;
  }
  const lines = visible.map((l) =>
    [l.name, shareLink(l), l.pwd || "-", l.time, l.conf, l.state === "valid" ? "有效" : "失效"].join("\t"));
  copyText(lines.join("\n")).then(() => {
    flashButton(copyBtn, "已复制 ✓");
    toast("已复制 " + lines.length + " 条链接（可直接粘贴到表格）");
  }).catch(() => toast("复制失败", "error"));
});

document.getElementById("hide-dead").addEventListener("change", (e) => {
  hideDead = e.target.checked;
  if (currentLinks.length) renderLinks(currentLinks);
});

document.getElementById("min-res").addEventListener("change", (e) => {
  minRes = e.target.value;
  if (currentLinks.length) renderLinks(currentLinks);
});

// ---- agent 过程 ----
const TOOL_NAME = {
  recall_memory: "记忆",
  search: "搜索",
  verify: "验证",
  judge_relevance: "AI 核对",
  finish: "完成",
  interpret_followup: "追问",
  cache: "缓存",
};

const TOOL_TEXT = {
  cache: (a) => "复用最近结果：" + (a.minutes_ago ? a.minutes_ago + " 分钟前" : "刚刚") +
    "有人搜过同样的内容，直接用那次验证过的链接",
  recall_memory: (a, o) =>
    "查记忆：记住 " + (o.remembered_valid || 0) + " 条有效链接，其中 " +
    (o.fresh || 0) + " 条近期验证过",
  search: (a, o) =>
    "搜索 " + (a.queries || []).map((q) => "「" + q + "」").join("") +
    (a.keyword ? "（网盘站关键词：" + a.keyword + "）" : "") +
    " → 找到 " + (o.found || 0) + " 条，新增候选 " + (o.new_candidates || 0) + " 条" +
    (o.known_invalid_skipped ? "，其中已知失效 " + o.known_invalid_skipped + " 条" : ""),
  verify: (a, o) =>
    "验证 " + (o.verified || 0) + " 条 → 有效 " + (o.valid || 0) + "、失效 " +
    (o.invalid || 0) + "、待确认 " + (o.unknown || 0) +
    (o.wrong_title ? "、片名不符 " + o.wrong_title : "") + "；满足要求累计 " +
    (o.matching_total || 0) + " 条",
  judge_relevance: (a, o) =>
    "AI 核对 " + (a.count || 0) + " 条标题不明确的链接 → 相关 " + (o.match || 0) +
    "、不相关 " + (o.mismatch || 0) + "；满足要求累计 " + (o.matching_total || 0) + " 条",
  finish: (a) => "结束：" + (a.reason || ""),
  interpret_followup: (a, o) => {
    if (o.mode === "new") return "理解追问「" + a.text + "」：这是一个新的搜索";
    const bits = [];
    if (o.season) bits.push("改为第" + o.season + "季");
    if (o.resolution) bits.push("要 " + (RES_LABEL[o.resolution] || o.resolution) + " 以上");
    if (o.subtitle) bits.push("要字幕");
    if (o.hdr) bits.push("要 HDR");
    if (o.more) bits.push("再多找一些");
    return "理解追问「" + a.text + "」" + (o.by === "llm" ? "（AI）" : "") + "：" +
      (bits.join("、") || "沿用上一轮条件");
  },
};

function pendingStep(text) {
  const li = el("li", "pending", text);
  const dots = el("span", "typing");
  dots.append(el("i"), el("i"), el("i"));
  li.appendChild(dots);
  return li;
}

function renderStep(step) {
  const pending = agentSteps.querySelector(".pending");
  if (pending) pending.remove();
  const li = el("li");
  const fn = TOOL_TEXT[step.tool];
  const obs = step.observation || {};
  if (obs.error) li.classList.add("step-error");
  if (step.tool === "finish") li.classList.add("step-finish");
  li.appendChild(el("span", "step-tool", TOOL_NAME[step.tool] || step.tool));
  li.appendChild(document.createTextNode(
    obs.error ? step.tool + " 出错：" + obs.error : fn ? fn(step.args || {}, obs) : step.tool));
  li.appendChild(el("span", "took", (step.duration_ms / 1000).toFixed(1) + "s"));
  if (step.thought) li.appendChild(el("span", "thought", "💭 " + step.thought));
  agentSteps.appendChild(li);
  if (step.tool !== "finish") agentSteps.appendChild(pendingStep("正在决定下一步"));
}

function setAgentCollapsed(collapsed) {
  agentSteps.classList.toggle("collapsed", collapsed);
  agentToggle.setAttribute("aria-expanded", String(!collapsed));
  setText(agentToggle, collapsed ? "展开过程" : "收起");
}

agentToggle.addEventListener("click", () => {
  setAgentCollapsed(!agentSteps.classList.contains("collapsed"));
});

let timerHandle = null;
function startTimer() {
  const t0 = Date.now();
  clearInterval(timerHandle);
  setText(agentTimer, "0s");
  timerHandle = setInterval(() => {
    setText(agentTimer, Math.floor((Date.now() - t0) / 1000) + "s");
  }, 1000);
}

function stopTimer() {
  clearInterval(timerHandle);
  timerHandle = null;
}

function renderResult(data) {
  const pending = agentSteps.querySelector(".pending");
  if (pending) pending.remove();
  setText(agentTitle, (data.planner === "llm" ? "AI 规划" : "规则规划") +
    " · " + data.steps.length + " 步 · " + data.stop_reason);
  agentToggle.hidden = false;
  // 结果出来后默认收起过程，把视线交给结果；想看可以展开
  setAgentCollapsed(data.links.length > 0);

  renderParsed(data.parsed, data.douban);
  renderMetrics(data.metrics, data.providers, !!data.followup);
  renderFollowup(data);
  lastResult = data;
  subscribeBtn.hidden = subsPanel.hidden;
  subscribeBtn.disabled = false;
  setText(subscribeBtn, "订阅更新");
  subscribeBtn.classList.remove("done");
  renderSubscribeBox(data);

  if (data.links.length === 0) {
    resultList.innerHTML = "";
    maybeList.innerHTML = "";
    maybeBox.hidden = true;
    currentLinks = [];
    linkActions.hidden = true;
    renderEmpty("没有找到夸克网盘链接", "建议换一种写法（别名、英文名、加 4K / 全集 等）后重试，或者在上方追问「再找找」。");
  } else {
    renderLinks(data.links);
    linkActions.hidden = false;
  }
  resultsSection.hidden = false;
}

let currentSource = null;
let sessionId = null;

const followupForm = document.getElementById("followup-form");
const followupInput = document.getElementById("followup-input");
const followupHistory = document.getElementById("followup-history");

function renderFollowup(data) {
  sessionId = data.session_id || null;
  followupForm.hidden = !sessionId;
  const f = data.filters || {};
  const conds = [];
  if (f.season) conds.push("第" + f.season + "季");
  if (f.resolution) conds.push((RES_LABEL[f.resolution] || f.resolution) + " 以上");
  if (f.subtitle) conds.push("要字幕");
  if (f.hdr) conds.push("要 HDR");
  followupHistory.innerHTML = "";
  followupHistory.append("对话：", el("b", "", (data.history || []).join(" › ")));
  if (conds.length) followupHistory.append("　·　当前条件：" + conds.join("、"));
  followupInput.value = "";
}

followupForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = followupInput.value.trim();
  if (q.length < 2) return;
  doSearch(q, false, sessionId);
});

function setBusy(busy) {
  submitBtn.disabled = busy;
  submitBtn.classList.toggle("loading", busy);
  setText(submitBtn.querySelector(".btn-label"), busy ? "搜索中" : "搜索");
  document.getElementById("followup-btn").disabled = busy;
  agentPanel.classList.toggle("running", busy);
  skeleton.hidden = !busy;
  if (busy) startTimer(); else stopTimer();
}

// 自己解析 SSE（而不是 EventSource），这样能拿到 401/403/429 的状态码和提示
async function readSSE(resp, onEvent) {
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let event = "message";
      const data = [];
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      }
      if (data.length) onEvent(event, data.join("\n"));
    }
  }
}

function searchFailed(title, detail) {
  agentSteps.querySelectorAll(".pending").forEach((p) => p.remove());
  setText(agentTitle, title);
  showStatus(detail, "error");
}

async function doSearch(query, refresh = false, followupOf = null) {
  if (meState.banned) {
    showBanned(meState.banned);
    return;
  }
  lastQuery = query;
  hideFormError();
  hideQuotaNote();
  resultsSection.hidden = true;
  if (currentSource) currentSource.abort();
  document.body.classList.add("searched");

  agentPanel.hidden = false;
  agentSteps.innerHTML = "";
  agentToggle.hidden = true;
  setAgentCollapsed(false);
  setText(agentTitle, "agent 正在搜索");
  agentSteps.appendChild(pendingStep("正在理解你要找的资源"));
  showStatus("agent 正在搜索并验证链接，通常需要 20~90 秒，过程会实时显示在下方", "loading");
  setBusy(true);
  agentPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });

  const url = "/api/agent/stream?query=" + encodeURIComponent(query) +
    (refresh ? "&refresh=true" : "") +
    (clientId ? "&client_id=" + encodeURIComponent(clientId) : "") +
    (followupOf ? "&session_id=" + encodeURIComponent(followupOf) : "");
  const controller = new AbortController();
  currentSource = controller;
  let finished = false;
  controller.signal.addEventListener("abort", () => clearTimeout(timer));
  const done = () => {
    finished = true;
    clearTimeout(timer);
    if (currentSource === controller) setBusy(false);
  };
  const timer = setTimeout(() => {
    if (finished) return;
    controller.abort();
    done();
    searchFailed("搜索超时", "搜索超时（超过 150 秒），请稍后重试或换一个更精确的资源名。");
  }, 150000);

  let resp;
  try {
    resp = await fetch(url, { signal: controller.signal, headers: { Accept: "text/event-stream" } });
  } catch (_) {
    if (finished || controller.signal.aborted) return;
    done();
    searchFailed("搜索中断", "网络连接失败，请检查网络后重试。");
    return;
  }

  if (!resp.ok) {
    done();
    const body = await resp.json().catch(() => ({}));
    const detail = resp.status === 429 ? rateLimitText(resp, body)
      : typeof body.detail === "string" ? body.detail : "搜索失败，请稍后重试。";
    if (resp.status === 401) {
      // 未登录免费次数用完：直接引导扫码登录，登录成功后自动重新搜索
      searchFailed("需要登录", detail);
      if (meState.login && await quarkLogin(detail)) doSearch(query, refresh, followupOf);
    } else if (resp.status === 403) {
      searchFailed("无法搜索", detail);
      showBanned(detail);
    } else if (resp.status === 429) {
      searchFailed("请求太频繁", detail);
    } else {
      searchFailed("搜索中断", detail);
    }
    loadMe();
    return;
  }

  try {
    await readSSE(resp, (event, data) => {
      if (finished) return;
      if (event === "step") renderStep(JSON.parse(data));
      else if (event === "quota") {
        const q = JSON.parse(data);
        renderQuota(q);
        showQuotaNote(q);
      } else if (event === "result") {
        done();
        hideStatus();
        const result = JSON.parse(data);
        renderResult(result);
        if (result.quota) {
          renderQuota(result.quota);
          showQuotaNote(result.quota);
        }
      } else if (event === "error") {
        done();
        let detail = "搜索失败，请稍后重试。";
        try { detail = JSON.parse(data).detail || detail; } catch (_) { /* 保持默认提示 */ }
        searchFailed("搜索中断", detail);
      }
    });
  } catch (_) {
    if (controller.signal.aborted) return;
  }
  if (!finished) {
    done();
    searchFailed("搜索中断", "连接意外断开，请稍后重试。");
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = input.value.trim();
  if (q.length < 2 || q.length > 200) {
    showFormError("请输入 2–200 个字符的搜索内容。");
    return;
  }
  input.blur(); // 手机上收起键盘
  doSearch(q);
});

const examplesList = document.getElementById("examples-list");
const examplesLabel = document.getElementById("examples-label");
examplesList.addEventListener("click", (e) => {
  const btn = e.target.closest(".example");
  if (!btn) return;
  input.value = btn.dataset.query;
  doSearch(btn.dataset.query);
});

// ---- 热门片名：搜索框占位文字轮换 + 「试试」换成 TMDB / 豆瓣最近热门 ----
// 接口失败或没数据时保留页面里写死的示例
const TRENDING_KEY = "qp_trending";
const TRENDING_TTL = 6 * 3600 * 1000;
let placeholderTimer = null;

function trendingItems(data) {
  const list = Array.isArray(data) ? data : (data && (data.items || data.trending)) || [];
  const seen = new Set();
  return list.filter((x) => x && x.title && !seen.has(x.title) && seen.add(x.title)).slice(0, 6);
}

function renderTrending(items) {
  if (!items.length) return;
  examplesList.innerHTML = "";
  items.forEach((x, i) => {
    const btn = el("button", "example has-meta");
    btn.type = "button";
    btn.dataset.query = x.title;
    btn.style.setProperty("--i", String(i));
    btn.title = [x.title, x.year, x.media === "movie" ? "电影" : x.media === "tv" ? "剧集" : ""].filter(Boolean).join(" · ");
    if (x.poster) {
      const img = el("img", "example-poster");
      img.alt = "";
      img.loading = "lazy";
      img.referrerPolicy = "no-referrer";
      img.addEventListener("error", () => img.remove(), { once: true });
      img.src = x.poster;
      btn.appendChild(img);
    }
    btn.appendChild(el("span", "", x.title));
    if (x.media) btn.appendChild(el("span", "example-tag", x.media === "movie" ? "电影" : "剧集"));
    examplesList.appendChild(btn);
  });
  setText(examplesLabel, "正在热播");
  examplesList.scrollLeft = 0;
  rotatePlaceholder(items.map((x) => x.title));
}

// 占位文字在几部热门片名之间轮换；输入框有焦点或有内容时不动
function rotatePlaceholder(titles) {
  clearInterval(placeholderTimer);
  if (!titles.length) return;
  let i = 0;
  const narrow = window.matchMedia("(max-width: 600px)");
  const show = () => {
    const two = titles.length > 1 && !narrow.matches; // 手机上只放一个，免得被截断
    input.placeholder = "例如：" + titles[i % titles.length] + (two ? " / " + titles[(i + 1) % titles.length] : "");
  };
  show();
  placeholderTimer = setInterval(() => {
    if (document.activeElement === input || input.value || document.hidden) return;
    i += 1;
    input.classList.add("ph-swap");
    setTimeout(() => { show(); input.classList.remove("ph-swap"); }, 180);
  }, 4000);
}

async function loadTrending() {
  try {
    const cached = JSON.parse(localStorage.getItem(TRENDING_KEY) || "null");
    if (cached && Date.now() - cached.at < TRENDING_TTL) { renderTrending(cached.items); return; }
  } catch (_) { /* 无痕模式等：直接请求 */ }
  try {
    const resp = await fetch("/api/trending");
    if (!resp.ok) return;
    const data = await resp.json();
    const items = trendingItems(data);
    renderTrending(items);
    // 内置兜底列表（source=default）不缓存，下次打开再试真的热门
    if (items.length && data.source !== "default") {
      try { localStorage.setItem(TRENDING_KEY, JSON.stringify({ at: Date.now(), items })); } catch (_) { /* 忽略 */ }
    }
  } catch (_) { /* 网络问题：保留写死的示例 */ }
}
loadTrending();


// ---- 偏好设置 ----
const prefsPanel = document.getElementById("prefs-panel");
const prefMinRes = document.getElementById("pref-min-res");
const prefSub = document.getElementById("pref-sub");
const prefHdr = document.getElementById("pref-hdr");
const prefsSaved = document.getElementById("prefs-saved");

function applyDefaultFilter(value) {
  minRes = value || "";
  document.getElementById("min-res").value = minRes;
}

async function loadPrefs() {
  if (!clientId) return;
  try {
    const resp = await fetch("/api/prefs?client_id=" + encodeURIComponent(clientId));
    if (!resp.ok) return; // 记忆未开启：不显示偏好设置
    const p = await resp.json();
    prefMinRes.value = p.min_resolution || "";
    prefSub.checked = !!p.prefer_subtitle;
    prefHdr.checked = !!p.prefer_hdr;
    applyDefaultFilter(p.min_resolution);
    prefsPanel.hidden = false;
  } catch (_) { /* 网络问题：保持默认 */ }
}

async function savePrefs() {
  const body = {
    min_resolution: prefMinRes.value || null,
    prefer_subtitle: prefSub.checked,
    prefer_hdr: prefHdr.checked,
  };
  try {
    const resp = await fetch("/api/prefs?client_id=" + encodeURIComponent(clientId), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    setText(prefsSaved, resp.ok ? "✓ 已保存" : "保存失败");
    if (resp.ok) applyDefaultFilter(body.min_resolution);
  } catch (_) {
    setText(prefsSaved, "保存失败");
  }
  setTimeout(() => setText(prefsSaved, ""), 2000);
}

[prefMinRes, prefSub, prefHdr].forEach((e) => e.addEventListener("change", savePrefs));
loadPrefs();


// ---- 追剧订阅 ----
// 结构：loadSubs 拉数据 → renderSubsTab 按标签页渲染；订阅卡片 subItem 由几块拼成，
// 右上角按钮来自 SUB_ACTIONS，设置面板的字段来自 SUB_RULES，以后加功能往数组里加一项即可。
const subsPanel = document.getElementById("subs-panel");
const subsList = document.getElementById("subs-list");
const notifBox = document.getElementById("notif-box");
let notesCache = [];
const subsUnread = document.getElementById("subs-unread");
const subsTabs = document.getElementById("subs-tabs");
const newSubBtn = document.getElementById("new-sub-btn");
const subscribeBtn = document.getElementById("subscribe-btn");
let lastResult = null;
let subsCache = [];
let historyCache = [];
let subsTab = "tv";
try { subsTab = localStorage.getItem("qp_subs_tab") || "tv"; } catch (_) { /* 无痕模式 */ }

function cidParam() {
  return "client_id=" + encodeURIComponent(clientId);
}

function formatTime(ts) {
  const d = new Date(ts * 1000);
  return (d.getMonth() + 1) + "/" + d.getDate() + " " +
    String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
}

// 订阅接口的小封装：返回 { ok, status, body }
async function subApi(path, method, payload) {
  const opts = { method: method || "GET" };
  if (payload !== undefined) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(payload);
  }
  const sep = path.includes("?") ? "&" : "?";
  const resp = await fetch("/api/subscriptions" + path + sep + cidParam(), opts);
  const body = await resp.json().catch(() => ({}));
  if (resp.status === 429) body.detail = rateLimitText(resp, body);
  return { ok: resp.ok, status: resp.status, body };
}

// 被限流（429）时的友好提示：带上服务器给的 Retry-After 等待时间
function rateLimitText(resp, body) {
  const raw = body && typeof body.detail === "string" ? body.detail : "";
  const base = raw && !/请求过于频繁/.test(raw) ? raw.replace(/[。.]$/, "") : "操作太频繁了";
  const secs = Math.ceil(Number(resp.headers.get("Retry-After")) || Number(body && body.retry_after) || 0);
  if (/秒后|明天/.test(base)) return base; // 原因里已经说了要等多久
  if (secs <= 0) return base + "，请稍后再试";
  if (secs >= 3600) return base + "，明天再试";           // 当天额度用完：等到明天 0 点
  return base + "（" + (secs < 60 ? secs + " 秒" : Math.ceil(secs / 60) + " 分钟") + "后可以再试）";
}

// 需要登录的操作失败时引导扫码；扫码成功返回 true（调用方可重试）
async function loginIfNeeded(res, why) {
  if (res.status === 401 && meState.login) return quarkLogin(res.body.detail || why);
  toast(res.body.detail || "操作失败", "error");
  return false;
}

let collectionsCache = []; // 系列级设置（新作自动加入）

async function loadSubs() {
  if (!clientId) return;
  try {
    const [subsResp, notifResp, histResp, collResp] = await Promise.all([
      fetch("/api/subscriptions?" + cidParam()),
      fetch("/api/notifications?" + cidParam()),
      fetch("/api/subscriptions/history?" + cidParam()).catch(() => null),
      fetch("/api/subscriptions/collections?" + cidParam()).catch(() => null),
    ]);
    if (!subsResp.ok || !notifResp.ok) return; // 记忆未开启：不显示订阅
    subsCache = await subsResp.json();
    historyCache = histResp && histResp.ok ? await histResp.json() : [];
    collectionsCache = collResp && collResp.ok ? await collResp.json().catch(() => []) : [];
    const notes = await notifResp.json();
    subsPanel.hidden = false;
    if (lastResult) {
      subscribeBtn.hidden = false;
      if (subscribeBox.hidden) renderSubscribeBox(lastResult);
    }

    notesCache = applyLocalRead(notes);
    renderNotifs();

    renderSubsTab();
  } catch (_) { /* 网络问题：下次再试 */ }
}

// ---- 订阅提醒：卡片样式，按订阅分组；未读的一条条「已读」，已读的折叠起来 ----
// 每种提醒的标签和一句话说明；后端给了结构化字段就用字段，没给就从 message 里取
const NOTE_KIND = {
  episodes: ["新集", "accent", ""],
  maybe: ["可能相关", "warn", "找到一个可能相关的资源，请自己核对是不是这部"],
  found: ["有资源了", "accent", "找到了新资源"],
  quality: ["更高清", "info", "出现了更高清的版本"],
  upgraded: ["已洗版", "accent", "已转存更高清的版本"],
  auto_saved: ["已转存", "ok", ""],
  auto_save_failed: ["转存失败", "danger", ""],
  auto_save_paused: ["转存暂停", "warn", ""],
  completed: ["已完成", "ok", ""],
  series_new: ["系列新作", "accent", ""],
  season_new: ["新的一季", "accent", ""],
  check_failed: ["检查失败", "warn", ""],
};

function relTime(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "刚刚";
  if (s < 3600) return Math.floor(s / 60) + " 分钟前";
  if (s < 86400) return Math.floor(s / 3600) + " 小时前";
  if (s < 86400 * 7) return Math.floor(s / 86400) + " 天前";
  return formatTime(ts);
}

// 结构化字段优先（frontend-api.md），旧通知从文字里拆：「资源标题」和分享链接
function noteParts(n) {
  const msg = String(n.message || "");
  const url = n.url || (msg.match(/https?:\/\/pan\.quark\.cn\/s\/[0-9a-zA-Z]+/) || [])[0] ||
    (n.share && /^[0-9a-zA-Z]{6,}$/.test(n.share) ? "https://pan.quark.cn/s/" + n.share : "");
  const share = n.share && /^[0-9a-zA-Z]{6,}$/.test(n.share) ? n.share : (url.match(/\/s\/([0-9a-zA-Z]+)/) || [])[1] || "";
  const titleM = msg.match(/资源[「『]([^」』]+)[」』]/);
  const kind = NOTE_KIND[n.type || n.kind];
  let summary = n.summary || (kind && kind[2]) || "";
  let path = "";
  const savedM = msg.match(/转存.*?(\d+)\s*个新?文件到[「『]([^」』]+)[」』]/);
  if (!summary && savedM) { // 「已自动转存《…》的 N 个新文件到「路径」」→ 短句 + 灰色路径
    summary = `转存了 ${savedM[1]} 个新文件`;
    path = savedM[2];
  }
  if (!summary) { // 没有固定说法的：去掉片名和链接后的那句话
    summary = msg.replace(/https?:\/\/\S+/g, "").replace(/^《[^》]+》/, "").replace(/[：:，,]\s*$/, "").trim() || msg;
  }
  return {
    label: kind ? kind[0] : "提醒",
    tone: kind ? kind[1] : "neutral",
    summary,
    title: n.resource_title || (titleM ? titleM[1] : "") || (path ? "保存到 " + path : ""),
    url,
    share,
    pwd: n.pwd || null,
  };
}

function noteGroups(list) {
  const groups = [];
  const byKey = {};
  list.forEach((n) => {
    const key = n.subscription_id != null ? "s" + n.subscription_id : "r" + (n.resource || "");
    if (!byKey[key]) {
      byKey[key] = { key, name: n.subscription_name || n.resource || "", notes: [] };
      groups.push(byKey[key]);
    }
    byKey[key].notes.push(n);
  });
  return groups;
}

function renderNotifs() {
  const unread = notesCache.filter((n) => !n.read);
  const read = notesCache.filter((n) => n.read);
  subsUnread.hidden = unread.length === 0;
  setText(subsUnread, unread.length + " 条新提醒");
  notifBox.innerHTML = "";
  if (unread.length) {
    const head = el("div", "notif-head");
    head.appendChild(el("span", "notif-count", "新提醒 " + unread.length + " 条"));
    const all = el("button", "link-btn", "全部已读");
    all.type = "button";
    all.addEventListener("click", () => markNotesRead(unread));
    head.appendChild(all);
    notifBox.appendChild(head);
    noteGroups(unread.slice(0, 30)).forEach((g) => notifBox.appendChild(noteGroup(g)));
  }
  if (read.length) {
    const fold = el("details", "notif-read");
    if (notifBox.dataset.readOpen === "1") fold.open = true;
    fold.addEventListener("toggle", () => { notifBox.dataset.readOpen = fold.open ? "1" : ""; });
    fold.appendChild(el("summary", "", "已读的提醒（" + read.length + "）"));
    noteGroups(read.slice(0, 30)).forEach((g) => fold.appendChild(noteGroup(g)));
    notifBox.appendChild(fold);
  }
}

// 同一个订阅的多条提醒合成一组：组头是片名和季，下面一条一张小卡片
function noteGroup(g) {
  const box = el("section", "note-group");
  const sub = subsCache.find((x) => "s" + x.id === g.key);
  const head = el("div", "note-group-head");
  head.appendChild(posterEl(sub && sub.poster, g.name, "tiny"));
  head.appendChild(el("b", "note-group-name", "《" + tidyTitle(g.name) + "》"));
  if (g.notes.length > 1) head.appendChild(el("span", "note-group-n", g.notes.length + " 条"));
  const unread = g.notes.filter((n) => !n.read);
  if (unread.length > 1) {
    const all = el("button", "link-btn", "这组已读");
    all.type = "button";
    all.addEventListener("click", () => markNotesRead(unread));
    head.appendChild(all);
  }
  box.appendChild(head);
  const ul = el("ul", "note-cards");
  g.notes.forEach((n) => ul.appendChild(noteItem(n)));
  box.appendChild(ul);
  return box;
}

function noteItem(n) {
  const p = noteParts(n);
  const li = el("li", "note-card" + (n.read ? " is-read" : ""));
  const top = el("div", "note-top");
  top.append(el("span", "note-tag tone-" + p.tone, p.label), el("span", "note-summary", p.summary));
  const time = el("time", "note-time", relTime(n.ts));
  time.title = formatTime(n.ts);
  top.appendChild(time);
  li.appendChild(top);
  if (p.title) {
    const t = el("p", "note-res", p.title);
    t.title = p.title;
    li.appendChild(t);
  }
  const acts = el("div", "note-acts");
  if (p.url) {
    const open = el("a", "ghost-btn small", "打开链接");
    open.href = p.url;
    open.target = "_blank";
    open.rel = "noopener noreferrer";
    acts.appendChild(open);
  }
  if (p.share && saveEnabled && (n.type || n.kind) !== "auto_saved") {
    const save = saveButton({ share: p.share, pwd: p.pwd });
    save.className = "ghost-btn small";
    setText(save, "转存");
    acts.appendChild(save);
  }
  if (!n.read) {
    const btn = el("button", "ghost-btn small note-read-btn", "已读");
    btn.type = "button";
    btn.setAttribute("aria-label", "标为已读");
    btn.addEventListener("click", () => {
      li.classList.add("leaving");
      setTimeout(() => markNotesRead([n]), 220);
    });
    acts.appendChild(btn);
  }
  if (acts.children.length) li.appendChild(acts);
  return li;
}

// 先在本地标记（界面立即收起），再告诉服务器；失败时下次刷新会恢复
// 后端目前只支持「全部标记已读」；单条已读先记在本机，全部读完时再告诉服务器
const NOTES_READ_KEY = "qp_notes_read";
function localReadIds() {
  try { return new Set(JSON.parse(localStorage.getItem(NOTES_READ_KEY) || "[]")); } catch { return new Set(); }
}
function saveLocalRead(ids) {
  try { localStorage.setItem(NOTES_READ_KEY, JSON.stringify([...ids].slice(-500))); } catch { /* 无痕模式等 */ }
}
function applyLocalRead(list) {
  const ids = localReadIds();
  list.forEach((n) => { if (ids.has(n.id)) n.read = true; });
  return list;
}

async function markNotesRead(list) {
  list.forEach((n) => { n.read = true; });
  renderNotifs();
  if (notesCache.some((n) => !n.read)) {
    const ids = localReadIds();
    list.forEach((n) => { if (n.id !== undefined) ids.add(n.id); });
    saveLocalRead(ids);
    return;
  }
  saveLocalRead(new Set());
  await fetch("/api/notifications/read?" + cidParam(), { method: "POST" }).catch(() => {});
}

// 「订阅整个系列」建的订阅按系列折叠成一组，放在该系列第一部出现的位置
function groupSeries(subs) {
  const groups = {};
  const out = [];
  subs.forEach((sub) => {
    if (!sub.series || !sub.collection_id) { out.push(subItem(sub)); return; }
    let g = groups[sub.collection_id];
    if (!g) {
      g = groups[sub.collection_id] = [];
      out.push(g);
    }
    g.push(sub);
  });
  return out.map((x) => (Array.isArray(x) ? seriesGroup(x) : x));
}

function seriesGroup(subs) {
  subs.sort((a, b) => (a.collection_index || 0) - (b.collection_index || 0));
  const cid = String(subs[0].collection_id);
  const info = collectionsCache.find((c) => String(c.collection_id) === cid) || {};
  const name = collectionName(info.name || subs[0].collection_name) || "系列";
  const li = el("li", "sub-group");
  const head = el("div", "sg-head");
  const title = el("div", "sg-title");
  title.append(el("b", "", name + " 系列"), el("span", "sg-count", "订阅中 " + subs.length + " 部"));
  const tools = el("div", "sg-tools");

  if (info.collection_id !== undefined) { // 后端有系列设置时才显示开关
    const sw = el("label", "switch small");
    const cb = el("input");
    cb.type = "checkbox";
    cb.checked = !!info.auto_join;
    const track = el("span", "switch-track");
    track.setAttribute("aria-hidden", "true");
    sw.append(cb, track, "新作自动加入");
    sw.title = "每天查一次这个系列有没有新片，有就自动订阅并通知你";
    cb.addEventListener("change", async () => {
      cb.disabled = true;
      const res = await subApi("/collection/" + encodeURIComponent(cid), "PATCH", { auto_join: cb.checked })
        .catch(() => ({ ok: false, status: 0, body: {} }));
      cb.disabled = false;
      if (res.ok) {
        info.auto_join = cb.checked;
        toast(cb.checked ? "《" + name + "》系列以后出新作会自动订阅" : "已关闭《" + name + "》系列的新作自动加入", "ok");
      } else if (res.status === 404) {
        loadSubs();
      } else {
        cb.checked = !cb.checked;
        toast(res.body.detail || "操作失败", "error");
      }
    });
    tools.appendChild(sw);
  }

  const del = el("button", "ghost-btn small danger", "退订整个系列");
  del.type = "button";
  del.title = "系列里还在订阅中的部一起取消；单独订阅的部和已完成的历史不受影响";
  del.addEventListener("click", async () => {
    if (!del.classList.contains("confirm")) { // 第一次点只是确认
      del.classList.add("confirm");
      setText(del, "确定退订 " + subs.length + " 部？");
      setTimeout(() => { del.classList.remove("confirm"); setText(del, "退订整个系列"); }, 3000);
      return;
    }
    del.disabled = true;
    const res = await subApi("/collection/" + encodeURIComponent(cid), "DELETE").catch(() => ({ ok: false, body: {} }));
    if (res.ok) toast("已退订《" + name + "》系列");
    else toast(res.body.detail || "操作失败", "error");
    loadSubs();
  });
  tools.appendChild(del);
  head.append(title, tools);

  const list = el("ul", "sg-list");
  subs.forEach((sub) => list.appendChild(subItem(sub, true)));
  li.append(head, list);
  return li;
}

// 没识别出类型的关键词订阅归到「剧集」
function subMedia(sub) {
  return sub.media === "movie" ? "movie" : "tv";
}

const SUBS_TABS = {
  tv: { count: () => subsCache.filter((s) => subMedia(s) === "tv").length, empty: "还没有订阅剧集。" },
  movie: { count: () => subsCache.filter((s) => subMedia(s) === "movie").length, empty: "还没有订阅电影。" },
  calendar: { count: () => 0, empty: "" },
  history: { count: () => historyCache.length, empty: "还没有完成的订阅。集齐或手动完成的订阅会出现在这里，可以一键重新订阅。" },
};

function renderSubsTab() {
  if (!SUBS_TABS[subsTab]) subsTab = "tv";
  subsTabs.querySelectorAll("[data-tab]").forEach((b) => {
    const tab = b.dataset.tab;
    b.setAttribute("aria-selected", String(tab === subsTab));
    const n = SUBS_TABS[tab].count();
    setText(b.querySelector(".seg-n"), n ? String(n) : "");
  });
  subsList.innerHTML = "";
  if (subsTab === "calendar") { renderCalendar(); return; }
  const items = subsTab === "history"
    ? historyCache.map(historyItem)
    : groupSeries(subsCache.filter((s) => subMedia(s) === subsTab));
  if (!items.length) subsList.appendChild(el("li", "muted subs-empty", SUBS_TABS[subsTab].empty));
  items.forEach((li) => subsList.appendChild(li));
}

subsTabs.addEventListener("click", (e) => {
  const b = e.target.closest("[data-tab]");
  if (!b || b.dataset.tab === subsTab) return;
  subsTab = b.dataset.tab;
  try { localStorage.setItem("qp_subs_tab", subsTab); } catch (_) { /* 忽略 */ }
  renderSubsTab();
});

// ---- 追剧日历：按周列出订阅剧集的播出日和每集状态 ----
const CAL_STATUS = {
  saved: ["已存", "ok", "网盘里已经有这一集"],
  available: ["有资源", "info", "已经有资源，还没存进网盘"],
  no_resource: ["暂无资源", "danger", "已经播出，但还没搜到资源"],
  upcoming: ["未播出", "neutral", "还没到播出日"],
};
const WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
let calWeek = 0; // 相对本周偏移几周

function isoDate(d) {
  return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
}

function weekRange(offset) {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7) + offset * 7); // 周一
  return Array.from({ length: 7 }, (_, i) => { const x = new Date(d); x.setDate(d.getDate() + i); return x; });
}

// 点日历里的一集：切到剧集标签页并高亮对应的订阅卡片
function jumpToSub(id) {
  subsTab = "tv";
  renderSubsTab();
  const li = subsList.querySelector('[data-sub-id="' + id + '"]');
  if (!li) return;
  li.scrollIntoView({ behavior: "smooth", block: "center" });
  li.classList.remove("flash");
  void li.offsetWidth;
  li.classList.add("flash");
}

function calItem(ep) {
  const [label, tone, tip] = CAL_STATUS[ep.status] || CAL_STATUS.upcoming;
  const item = el("button", "cal-ep tone-" + tone);
  item.type = "button";
  item.title = tip + "，点击查看订阅";
  const text = el("span", "cal-ep-text");
  text.append(el("b", "cal-ep-title", tidyTitle(ep.resource)),
    el("span", "cal-ep-no", "S" + String(ep.season || 1).padStart(2, "0") + "E" + String(ep.episode).padStart(2, "0") +
      (ep.name && !/^第\s*\d+\s*集$/.test(ep.name) ? " · " + ep.name : "")));
  item.append(posterEl(ep.poster, ep.resource, "tiny"), text, el("span", "cal-ep-state", label));
  item.addEventListener("click", () => jumpToSub(ep.subscription_id));
  return item;
}

async function renderCalendar() {
  const days = weekRange(calWeek);
  const start = isoDate(days[0]);
  const end = isoDate(days[6]);
  const wrap = el("li", "cal-wrap");
  const nav = el("div", "cal-nav");
  const prev = el("button", "ghost-btn small", "‹ 上一周");
  prev.type = "button";
  const next = el("button", "ghost-btn small", "下一周 ›");
  next.type = "button";
  const label = el("span", "cal-range", (days[0].getMonth() + 1) + "/" + days[0].getDate() + " – " +
    (days[6].getMonth() + 1) + "/" + days[6].getDate());
  const center = el("div", "cal-center");
  center.appendChild(label);
  if (calWeek !== 0) {
    const now = el("button", "link-btn", "回到本周");
    now.type = "button";
    now.addEventListener("click", () => { calWeek = 0; renderSubsTab(); });
    center.appendChild(now);
  }
  prev.addEventListener("click", () => { calWeek -= 1; renderSubsTab(); });
  next.addEventListener("click", () => { calWeek += 1; renderSubsTab(); });
  nav.append(prev, center, next);
  const legend = el("div", "cal-legend");
  Object.values(CAL_STATUS).forEach(([t, tone]) => legend.appendChild(el("span", "cal-key tone-" + tone, t)));
  const grid = el("div", "cal-grid");
  grid.appendChild(el("div", "cal-loading", "加载中…"));
  wrap.append(nav, legend, grid);
  subsList.appendChild(wrap);

  let data = null;
  try {
    const resp = await fetch("/api/calendar?" + cidParam() + "&start=" + start + "&end=" + end);
    data = resp.ok ? await resp.json() : null;
  } catch (_) { data = null; }
  if (subsTab !== "calendar" || !wrap.isConnected) return; // 已经切走了
  grid.innerHTML = "";
  if (!data) {
    grid.appendChild(el("div", "cal-empty", "日历加载失败，请稍后重试。"));
    return;
  }
  const byDay = {};
  data.episodes.forEach((ep) => { (byDay[ep.air_date] = byDay[ep.air_date] || []).push(ep); });
  days.forEach((d, i) => {
    const key = isoDate(d);
    const col = el("div", "cal-day" + (key === data.today ? " is-today" : "") + (key < data.today ? " is-past" : ""));
    const head = el("div", "cal-day-head");
    head.append(el("span", "cal-wd", key === data.today ? "今天" : WEEKDAYS[i]), el("span", "cal-date", (d.getMonth() + 1) + "/" + d.getDate()));
    const list = el("div", "cal-eps");
    col.append(head, list);
    const eps = byDay[key] || [];
    if (!eps.length) list.appendChild(el("span", "cal-none", "没有播出"));
    eps.forEach((ep) => list.appendChild(calItem(ep)));
    col.classList.toggle("is-blank", !eps.length);
    grid.appendChild(col);
  });
  if (!data.episodes.length) {
    const tv = subsCache.filter((x) => x.media === "tv");
    const tip = !tv.length ? "还没有订阅剧集，订阅后这里会按播出日排好每一集。"
      : !tv.some((x) => x.tmdb_id) ? "日历需要 TMDB 的播出日期：请在服务器 .env 里配置 TMDB_API_KEY，并重新订阅剧集（只用豆瓣识别的剧没有日历）。"
        : "这一周没有订阅的剧集播出。播出日期来自 TMDB，只用豆瓣识别的剧不会出现在日历里。";
    wrap.insertBefore(el("p", "cal-empty", tip), grid);
    grid.hidden = true;
  }
}

// ---- 订阅卡片的各个部件 ----
function posterEl(src, title, cls) {
  const wrap = el("span", "poster " + (cls || ""));
  const fallback = () => {
    wrap.innerHTML = "";
    wrap.classList.add("no-img");
    wrap.textContent = (title || "?").replace(/[《》\s]/g, "").slice(0, 1) || "?";
  };
  if (!src) { fallback(); return wrap; }
  const img = el("img");
  img.alt = "";
  img.loading = "lazy";
  img.referrerPolicy = "no-referrer"; // 豆瓣海报防盗链
  img.addEventListener("error", fallback, { once: true });
  img.src = src;
  wrap.appendChild(img);
  return wrap;
}

const STATE_BADGE = {
  new: ["首次搜索中", "info", "刚订阅，服务器正在第一次搜索"],
  active: ["订阅中", "ok", "定期重搜，有资源、新集或更高清时提醒"],
  pending: ["待定", "warn", "没识别出条目或不知道总集数：照常搜索和提醒，但不会自动完成"],
  paused: ["已暂停", "neutral", "暂停期间不检查，恢复后立即检查一次"],
};

// 状态徽章：检查失败时不再显示「首次搜索中」；没上映的电影显示「未上映」而不是「待定」
function stateBadge(sub) {
  if (sub.last_error && (sub.state === "new" || sub.state === "active")) {
    return badge("检查失败", "state-warn", "上次检查失败，服务器稍后会自动重试");
  }
  const today = new Date().toISOString().slice(0, 10);
  if (sub.state === "pending" && sub.media === "movie" && (sub.collection_id || sub.release_date) &&
      (!sub.release_date || sub.release_date > today)) {
    return badge("未上映", "state-neutral", sub.release_date ? "上映日期 " + sub.release_date + "，上映后自动开始搜" : "还没定档，上映后自动开始搜");
  }
  const st = STATE_BADGE[sub.state] || STATE_BADGE.active;
  return badge(st[0], "state-" + st[1], st[2]);
}

// 把集号压缩成「3、5–7、10」
function episodeRanges(list) {
  const out = [];
  list.slice().sort((a, b) => a - b).forEach((n) => {
    const last = out[out.length - 1];
    if (last && n === last[1] + 1) last[1] = n;
    else out.push([n, n]);
  });
  return out.map(([a, b]) => (a === b ? a : a + "–" + b)).join("、");
}

function subProgress(sub) {
  const box = el("div", "sub-progress");
  if (sub.media === "movie") {
    const got = sub.saved_episodes && sub.saved_episodes.length;
    const savedRes = (sub.versions || {})["0"];
    const res = RES_LABEL[got && savedRes ? savedRes : sub.best_resolution];
    const text = el("span", "sub-progress-text", got ? "已存进网盘" + (res ? "（" + res + "）" : "")
      : res ? "已有资源，最高 " + res : "等待资源");
    const goal = sub.upgrade_to || "2160p";
    if (got && sub.upgrade && savedRes && RES_RANK[savedRes] < RES_RANK[goal]) {
      text.appendChild(el("span", "sub-lack", "　等 " + RES_LABEL[goal] + " 版本"));
    }
    box.appendChild(text);
    return box;
  }
  const total = sub.total_episodes;
  if (!total) {
    box.appendChild(el("span", "sub-progress-text",
      (sub.best_episodes ? "已见 " + sub.best_episodes + " 集" : "还没有资源") + " · 总集数未知"));
    return box;
  }
  const lack = sub.lack_episodes || [];
  const range = total - sub.start_episode + 1;
  const done = Math.max(0, range - lack.length);
  const bar = el("div", "meter");
  bar.setAttribute("role", "progressbar");
  bar.setAttribute("aria-valuemin", "0");
  bar.setAttribute("aria-valuemax", String(range));
  bar.setAttribute("aria-valuenow", String(done));
  const fill = el("span", "meter-fill");
  fill.style.width = (range ? Math.round((done / range) * 100) : 0) + "%";
  bar.appendChild(fill);
  const text = el("span", "sub-progress-text");
  text.append(el("b", "", "已存 " + done + " / " + range), " 集");
  if (lack.length && lack.length < range) text.append(el("span", "sub-lack", "　缺 " + episodeRanges(lack)));
  box.append(bar, text);
  return box;
}

// 每集已存的清晰度：一集一个小格子；开了洗版时没达到目标的标黄
function versionStrip(sub) {
  const versions = sub.versions || {};
  const goal = RES_RANK[sub.upgrade_to || "2160p"];
  const below = (res) => sub.upgrade && res && RES_RANK[res] < goal;
  if (sub.media === "movie") return null; // 电影的清晰度写在进度那一行
  if (!Object.keys(versions).length && !sub.upgrade) return null; // 还没有清晰度记录（老订阅）
  const total = sub.total_episodes;
  const saved = new Set(sub.saved_episodes || []);
  if (!total || !saved.size || total - sub.start_episode + 1 > 60) return null;
  const box = el("div", "ep-strip");
  box.setAttribute("aria-label", "每集已存的清晰度");
  for (let e = sub.start_episode; e <= total; e++) {
    const res = versions[String(e)];
    const has = saved.has(e);
    const chip = el("span", "ep-chip" + (!has ? " missing" : below(res) ? " below" : " saved"));
    chip.appendChild(el("b", "", String(e)));
    if (has) chip.appendChild(el("span", "ep-res", res ? RES_LABEL[res] || res : "?"));
    chip.title = "第 " + e + " 集：" + (!has ? "还没存" : res ? "已存 " + (RES_LABEL[res] || res) : "已存，认不出清晰度") +
      (below(res) ? "，等更高清的版本" : "");
    box.appendChild(chip);
  }
  return box;
}

// 规则摘要：≥1080p · 含「内嵌」· 排除「枪版」· 从第 3 集
function rulesSummary(sub) {
  return [
    sub.resolution ? "≥" + (RES_LABEL[sub.resolution] || sub.resolution) : "",
    sub.include ? "含「" + sub.include + "」" : "",
    sub.exclude ? "排除「" + sub.exclude + "」" : "",
    sub.media === "tv" && sub.start_episode > 1 ? "从第 " + sub.start_episode + " 集" : "",
  ].filter(Boolean).join(" · ");
}

// 订阅卡片右上角的操作按钮；以后要加新按钮，往这里追加一个函数即可
const SUB_ACTIONS = [checkNowButton, settingsButton];

// 立即检查：同步重搜，可能要几十秒；同一订阅 2 分钟冷却
function checkNowButton(sub) {
  if (sub.state === "paused") return null;
  const btn = el("button", "secondary-btn small", "立即检查");
  btn.type = "button";
  btn.title = "马上重搜一次" + (sub.auto_save ? "，并补齐网盘里缺的集" : "");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.classList.add("loading");
    setText(btn, "检查中…");
    try {
      const res = await subApi("/" + sub.id + "/check", "POST");
      if (res.ok) {
        const notes = res.body.notifications || [];
        if (!notes.length) toast("《" + sub.resource + "》暂时没有变化");
        else notes.slice(0, 3).forEach((n) => toast(n.message, n.kind === "auto_save_failed" ? "error" : "ok", 5000));
        loadSubs();
        return;
      }
      if (await loginIfNeeded(res, "检查订阅需要先扫码登录夸克")) loadSubs();
    } catch (_) {
      toast("检查失败，请稍后重试", "error");
    }
    btn.disabled = false;
    btn.classList.remove("loading");
    setText(btn, "立即检查");
  });
  return btn;
}

function settingsButton(sub, li) {
  const btn = el("button", "ghost-btn small icon-only", "");
  btn.type = "button";
  btn.title = "订阅设置";
  btn.setAttribute("aria-label", "订阅设置");
  btn.setAttribute("aria-expanded", "false");
  btn.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><circle cx="5" cy="12" r="1.8" fill="currentColor"/><circle cx="12" cy="12" r="1.8" fill="currentColor"/><circle cx="19" cy="12" r="1.8" fill="currentColor"/></svg>';
  btn.addEventListener("click", () => {
    let panel = li.querySelector(".sub-edit");
    if (!panel) {
      panel = subEditor(sub);
      li.appendChild(panel);
    } else {
      panel.hidden = !panel.hidden;
    }
    btn.setAttribute("aria-expanded", String(!panel.hidden));
    if (!panel.hidden) panel.querySelector("select, input")?.focus();
  });
  return btn;
}

// 设置面板里的规则字段；以后加新规则（比如洗版）往这里追加一项
const SUB_RULES = [
  { key: "resolution", label: "清晰度不低于", type: "select",
    options: [["", "不限"], ["2160p", "4K"], ["1080p", "1080p"], ["720p", "720p"], ["SD", "标清"]] },
  { key: "include", label: "必须包含", type: "text", placeholder: "如 内嵌 国语" },
  { key: "exclude", label: "排除", type: "text", placeholder: "如 枪版 TC" },
  { key: "start_episode", label: "从第几集开始", type: "number", tvOnly: true, min: 1 },
  { key: "total_episodes", label: "总集数", type: "number", tvOnly: true, min: 1,
    hint: (sub) => (sub.manual_total ? "手动设置" : sub.total_episodes ? "自动获取，可改" : "未知，可手动填") },
  // 洗版：只在订阅设置里改，需要先开自动转存
  { key: "upgrade", label: "洗版", type: "switch", editOnly: true, text: "出更高清的就换一份",
    disabled: (sub) => !sub.auto_save,
    hint: (sub) => (sub.auto_save ? "旧版本不会自动删，可在「整理网盘目录」里确认删除" : "需要先打开自动转存") },
  { key: "upgrade_to", label: "洗版目标", type: "select", editOnly: true, empty: "2160p",
    options: [["2160p", "4K"], ["1080p", "1080p"], ["720p", "720p"]],
    disabled: (sub) => !sub.auto_save },
];

// 字段当前值：没设置时用规则的默认值（如洗版目标默认 4K）
function ruleValue(rule, sub) {
  const cur = sub ? sub[rule.key] : null;
  if (rule.type === "switch") return !!cur;
  return cur == null || cur === "" ? (rule.empty || "") : String(cur);
}

function ruleField(rule, sub) {
  const wrap = el(rule.type === "switch" ? "div" : "label", "field");
  wrap.appendChild(el("span", "field-label", rule.label));
  let input;
  if (rule.type === "switch") {
    const sw = el("label", "switch small");
    input = el("input");
    input.type = "checkbox";
    input.checked = ruleValue(rule, sub);
    const track = el("span", "switch-track");
    track.setAttribute("aria-hidden", "true");
    sw.append(input, track, rule.text || "");
    wrap.appendChild(sw);
  } else if (rule.type === "select") {
    input = el("select");
    rule.options.forEach(([v, t]) => {
      const o = el("option", "", t);
      o.value = v;
      input.appendChild(o);
    });
  } else {
    input = el("input");
    input.type = rule.type;
    if (rule.placeholder) input.placeholder = rule.placeholder;
    if (rule.type === "text") input.maxLength = 100;
    if (rule.min) input.min = String(rule.min);
    if (rule.type === "number") input.inputMode = "numeric";
  }
  if (rule.type !== "switch") {
    input.value = ruleValue(rule, sub);
    wrap.appendChild(input);
  }
  input.dataset.key = rule.key;
  if (rule.disabled && sub && rule.disabled(sub)) {
    input.disabled = true;
    wrap.classList.add("is-disabled");
  }
  if (rule.hint && sub) wrap.appendChild(el("span", "field-hint", rule.hint(sub)));
  return wrap;
}

// 读取规则字段里改过的值（number 空着不传；text/select 空串表示清除）
function readRules(container, sub) {
  const out = {};
  container.querySelectorAll("[data-key]").forEach((input) => {
    const rule = SUB_RULES.find((r) => r.key === input.dataset.key);
    if (input.disabled) return;
    if (rule.type === "switch") {
      if (input.checked !== ruleValue(rule, sub)) out[rule.key] = input.checked;
      return;
    }
    const raw = input.value.trim();
    const before = ruleValue(rule, sub);
    if (raw === before) return;
    if (rule.type === "number") {
      const n = parseInt(raw, 10);
      if (n >= 1) out[rule.key] = n;
    } else {
      out[rule.key] = raw;
    }
  });
  return out;
}

function subEditor(sub) {
  const panel = el("div", "sub-edit");
  const grid = el("div", "sub-edit-grid");
  SUB_RULES.filter((r) => !r.tvOnly || sub.media !== "movie").forEach((r) => grid.appendChild(ruleField(r, sub)));
  // 洗版目标只在洗版打开时可选
  const upSw = grid.querySelector('[data-key="upgrade"]');
  const upTo = grid.querySelector('[data-key="upgrade_to"]');
  if (upSw && upTo && !upSw.disabled) {
    const sync = () => {
      upTo.disabled = !upSw.checked;
      upTo.closest(".field").classList.toggle("is-disabled", !upSw.checked);
    };
    upSw.addEventListener("change", sync);
    sync();
  }
  const save = el("button", "primary-btn small", "保存规则");
  save.type = "button";
  save.addEventListener("click", async () => {
    const patch = readRules(grid, sub);
    if (!Object.keys(patch).length) { toast("规则没有变化"); return; }
    save.disabled = true;
    const res = await subApi("/" + sub.id, "PATCH", patch).catch(() => ({ ok: false, body: {} }));
    save.disabled = false;
    if (res.ok) {
      toast(patch.upgrade ? "已开启《" + sub.resource + "》洗版，正在后台检查一次更高清的版本"
        : "已保存《" + sub.resource + "》的规则", "ok", patch.upgrade ? 4000 : 2200);
      loadSubs();
      if (patch.upgrade) setTimeout(loadSubs, 45000);
    }
    else toast(res.body.detail || "保存失败", "error");
  });

  const paused = sub.state === "paused";
  const pause = el("button", "secondary-btn small", paused ? "恢复订阅" : "暂停");
  pause.type = "button";
  pause.addEventListener("click", async () => {
    pause.disabled = true;
    const res = await subApi("/" + sub.id, "PATCH", { paused: !paused }).catch(() => ({ ok: false, body: {} }));
    if (res.ok) {
      toast(paused ? "已恢复《" + sub.resource + "》，正在后台检查一次" : "已暂停《" + sub.resource + "》", "ok");
      loadSubs();
      if (paused) setTimeout(loadSubs, 45000);
    } else {
      pause.disabled = false;
      toast(res.body.detail || "操作失败", "error");
    }
  });

  const complete = el("button", "secondary-btn small", "标记完成");
  complete.type = "button";
  complete.title = "不再追这个订阅，移入订阅历史，之后可以一键重新订阅";
  complete.addEventListener("click", async () => {
    complete.disabled = true;
    const res = await subApi("/" + sub.id + "/complete", "POST").catch(() => ({ ok: false, body: {} }));
    if (res.ok) { toast("《" + sub.resource + "》已完成，移入订阅历史", "ok"); loadSubs(); }
    else { complete.disabled = false; toast(res.body.detail || "操作失败", "error"); }
  });

  const del = el("button", "ghost-btn small danger", "取消订阅");
  del.type = "button";
  del.addEventListener("click", async () => {
    if (!del.classList.contains("confirm")) { // 第一次点只是确认
      del.classList.add("confirm");
      setText(del, "确定取消？");
      setTimeout(() => { del.classList.remove("confirm"); setText(del, "取消订阅"); }, 3000);
      return;
    }
    del.disabled = true;
    await subApi("/" + sub.id, "DELETE").catch(() => {});
    toast("已取消订阅《" + sub.resource + "》");
    loadSubs();
  });

  const foot = el("div", "sub-edit-foot");
  const left = el("div", "sub-edit-left");
  SUB_TOOLS.forEach((make) => { const b = make(sub); if (b) left.appendChild(b); });
  left.append(pause, complete, del);
  foot.append(left, save);
  panel.append(grid, foot);
  return panel;
}

// 设置面板左下角的工具按钮；以后加新工具（比如洗版）往这里追加一个函数
const SUB_TOOLS = [organizeButton];

function organizeButton(sub) {
  if (!meState.login) return null; // 整理要用自己的夸克登录
  const btn = el("button", "secondary-btn small", "整理网盘目录");
  btn.type = "button";
  btn.title = "把这个订阅存过的文件移到同一个目录、按标准命名；重复版本可以勾选删除";
  btn.addEventListener("click", () => openOrganizeDialog(sub));
  return btn;
}

function tidyRow(title, sub, extra) {
  const li = el("li", "tidy-row");
  const text = el("span", "tidy-text");
  text.append(el("span", "tidy-name", title));
  if (sub) text.appendChild(el("span", "tidy-sub", sub));
  li.appendChild(text);
  if (extra) li.appendChild(el("span", "tidy-size", extra));
  return li;
}

function tidyGroup(title, count, cls) {
  const sec = el("section", "tidy-group " + (cls || ""));
  const h = el("h3", "tidy-head");
  h.append(title, el("span", "tidy-count", String(count)));
  sec.appendChild(h);
  const ul = el("ul", "tidy-list");
  sec.appendChild(ul);
  return { sec, ul };
}

// 整理网盘目录：先预览（不改动），移动/改名直接执行，删除只删用户逐个勾选并二次确认的
function openOrganizeDialog(sub) {
  const dlg = el("dialog", "sub-dialog tidy-dialog");
  const head = el("div", "sd-head");
  head.appendChild(el("p", "sd-title", "整理《" + sub.resource + "》的网盘目录"));
  const x = el("button", "ghost-btn small icon-only", "✕");
  x.type = "button";
  x.setAttribute("aria-label", "关闭");
  head.appendChild(x);
  const target = el("p", "sd-hint", "正在读取网盘目录…");
  const body = el("div", "tidy-body");
  body.appendChild(el("div", "cand cand-skel"));
  body.appendChild(el("div", "cand cand-skel"));
  const confirmBox = el("div", "tidy-confirm");
  confirmBox.hidden = true;
  const foot = el("div", "sd-foot");
  const cancel = el("button", "secondary-btn small", "取消");
  cancel.type = "button";
  const run = el("button", "primary-btn small", "执行整理");
  run.type = "button";
  run.disabled = true;
  foot.append(cancel, run);
  dlg.append(head, target, body, confirmBox, foot);
  document.body.appendChild(dlg);

  let plan = null;
  let finished = false;
  const close = () => {
    dlg.close();
    dlg.remove();
    if (finished) loadSubs();
  };
  x.addEventListener("click", close);
  cancel.addEventListener("click", close);
  dlg.addEventListener("cancel", close);

  const checked = () => [...body.querySelectorAll(".tidy-del input:checked")].map((c) => c.value);
  const moveCount = () => plan.moves.filter((m) => m.from !== plan.target).length;
  const renameCount = () => plan.moves.filter((m) => m.to_name !== m.name).length;
  const refreshRun = () => {
    const parts = [];
    if (moveCount()) parts.push("移动 " + moveCount());
    if (renameCount()) parts.push("改名 " + renameCount());
    const del = checked().length;
    if (del) parts.push("删除 " + del);
    run.disabled = !parts.length;
    setText(run, parts.length ? "执行整理（" + parts.join(" · ") + "）" : "没有要执行的操作");
    confirmBox.hidden = true;
    foot.hidden = false;
  };

  function showError(text, retry) {
    body.innerHTML = "";
    const p = el("div", "tidy-empty error");
    p.appendChild(el("p", "", text));
    if (retry) {
      const again = el("button", "secondary-btn small", "重试");
      again.type = "button";
      again.addEventListener("click", load);
      p.appendChild(again);
    }
    body.appendChild(p);
    setText(target, "");
  }

  function renderPlan() {
    body.innerHTML = "";
    setText(target, "整理到：" + plan.target);
    target.title = plan.target;
    const moves = plan.moves.filter((m) => m.from !== plan.target);
    const renames = plan.moves.filter((m) => m.from === plan.target && m.to_name !== m.name);
    if (!moves.length && !renames.length && !plan.deletes.length) {
      body.appendChild(el("div", "tidy-empty", "目录已经很整齐了，没有需要移动、改名或删除的文件。"));
    }
    if (moves.length) {
      const g = tidyGroup("移动到整理目录", moves.length, "is-move");
      moves.forEach((m) => g.ul.appendChild(tidyRow(m.to_name,
        "从 " + m.from + (m.to_name !== m.name ? "，原名 " + m.name : ""), formatSize(m.size))));
      body.appendChild(g.sec);
    }
    if (renames.length) {
      const g = tidyGroup("改名", renames.length, "is-rename");
      renames.forEach((m) => g.ul.appendChild(tidyRow(m.to_name, "原名 " + m.name, formatSize(m.size))));
      body.appendChild(g.sec);
    }
    if (plan.deletes.length) {
      const g = tidyGroup("建议删除", plan.deletes.length, "is-delete");
      g.sec.appendChild(el("p", "tidy-note", (sub.upgrade ? "洗版换下来的旧版本也列在这里。" : "") +
        "默认都不删。确认不需要的请逐个勾选，删除的文件会进夸克回收站，可以恢复。"));
      g.sec.appendChild(g.ul); // 说明放在列表上面
      plan.deletes.forEach((d) => {
        const li = el("li", "tidy-row tidy-del");
        const label = el("label", "tidy-check");
        const cb = el("input");
        cb.type = "checkbox";
        cb.value = d.fid;
        cb.addEventListener("change", () => { li.classList.toggle("on", cb.checked); refreshRun(); });
        const text = el("span", "tidy-text");
        text.append(el("span", "tidy-name", d.name), el("span", "tidy-sub", d.reason + " · " + d.folder));
        label.append(cb, text);
        li.appendChild(label);
        if (d.size) li.appendChild(el("span", "tidy-size", formatSize(d.size)));
        g.ul.appendChild(li);
      });
      body.appendChild(g.sec);
    }
    if (plan.untouched && plan.untouched.length) {
      const more = el("details", "tidy-untouched");
      more.appendChild(el("summary", "", "不处理的文件（" + plan.untouched.length + "）：认不出集号，比如花絮"));
      const ul = el("ul", "tidy-list");
      plan.untouched.forEach((u) => ul.appendChild(tidyRow(u.name, u.folder)));
      more.appendChild(ul);
      body.appendChild(more);
    }
    refreshRun();
  }

  async function load() {
    run.disabled = true;
    body.innerHTML = "";
    body.append(el("div", "cand cand-skel"), el("div", "cand cand-skel"));
    setText(target, "正在读取网盘目录…");
    try {
      const res = await subApi("/" + sub.id + "/organize");
      if (res.ok) { plan = res.body; renderPlan(); return; }
      if (res.status === 401 && meState.login) {
        showError(res.body.detail || "需要先扫码登录夸克", false);
        if (await quarkLogin(res.body.detail || "整理网盘目录需要先扫码登录夸克")) load();
        return;
      }
      showError(res.body.detail || "读取网盘目录失败", true);
    } catch (_) {
      showError("读取网盘目录失败，请稍后重试", true);
    }
  }

  async function execute() {
    const fids = checked();
    run.disabled = true;
    run.classList.add("loading");
    setText(run, "整理中…");
    confirmBox.hidden = true;
    foot.hidden = false;
    body.querySelectorAll("input").forEach((i) => { i.disabled = true; });
    let res;
    try {
      res = await subApi("/" + sub.id + "/organize", "POST", { delete_fids: fids });
    } catch (_) {
      res = { ok: false, status: 0, body: { detail: "连接失败，请稍后重试" } };
    }
    run.classList.remove("loading");
    if (!res.ok) {
      body.querySelectorAll("input").forEach((i) => { i.disabled = false; });
      toast(res.body.detail || "整理失败", "error");
      if (res.status === 401 && meState.login) await quarkLogin(res.body.detail || "夸克登录已失效，请重新扫码");
      refreshRun();
      return;
    }
    finished = true;
    const r = res.body;
    const errors = r.errors || [];
    body.innerHTML = "";
    const box = el("div", "tidy-result" + (errors.length ? " has-errors" : ""));
    box.appendChild(el("span", "tidy-result-ico", errors.length ? "!" : "✓"));
    const stats = el("div", "tidy-stats");
    [["移动", r.moved], ["改名", r.renamed], ["删除", r.deleted]].forEach(([k, v]) => {
      const s = el("div", "tidy-stat");
      s.append(el("b", "", String(v || 0)), el("span", "", k));
      stats.appendChild(s);
    });
    box.appendChild(stats);
    box.appendChild(el("p", "tidy-note", "文件都在「" + r.target + "」。这是新功能，请打开夸克网盘确认一下结果" +
      (r.deleted ? "；删错了可以在夸克回收站里恢复。" : "。")));
    if (errors.length) {
      const ul = el("ul", "tidy-errors");
      errors.slice(0, 10).forEach((e) => ul.appendChild(el("li", "", e)));
      box.appendChild(ul);
    }
    body.appendChild(box);
    setText(target, "整理完成");
    run.remove();
    setText(cancel, "完成");
  }

  run.addEventListener("click", () => {
    const n = checked().length;
    if (!n) { execute(); return; }
    // 有删除项：二次确认
    confirmBox.innerHTML = "";
    confirmBox.appendChild(el("p", "", "确定删除勾选的 " + n + " 个文件？它们会进夸克回收站，可以恢复。"));
    const back = el("button", "secondary-btn small", "再看看");
    back.type = "button";
    back.addEventListener("click", () => { confirmBox.hidden = true; foot.hidden = false; });
    const yes = el("button", "secondary-btn small danger-solid", "确认删除并整理");
    yes.type = "button";
    yes.addEventListener("click", execute);
    const row = el("div", "tidy-confirm-actions");
    row.append(back, yes);
    confirmBox.appendChild(row);
    confirmBox.hidden = false;
    foot.hidden = true;
    yes.focus();
  });

  dlg.showModal();
  load();
}

function autoSaveSwitch(sub) {
  const sw = el("label", "switch small");
  const cb = el("input");
  cb.type = "checkbox";
  cb.checked = !!sub.auto_save;
  const track = el("span", "switch-track");
  track.setAttribute("aria-hidden", "true");
  sw.append(cb, track, sub.media === "movie" ? "有资源自动转存" : "自动转存补齐缺集");
  sw.title = sub.media === "movie"
    ? "出现满足清晰度要求的资源时存一次；之后更高清只提醒"
    : "打开后立即把网盘里缺的集补齐，之后出新集也自动转存";
  cb.addEventListener("change", async () => {
    const want = cb.checked;
    cb.disabled = true;
    try {
      const res = await subApi("/" + sub.id, "PATCH", { auto_save: want });
      if (res.ok) {
        toast(want ? "已开启《" + sub.resource + "》自动转存，正在后台补齐网盘缺的集，稍后在转存记录里查看"
          : "已关闭《" + sub.resource + "》自动转存", "ok", want ? 4000 : 2200);
        loadSubs();
        if (want) setTimeout(loadSubs, 45000);
      } else {
        cb.checked = !want;
        if (await loginIfNeeded(res, "自动转存需要先扫码登录夸克")) {
          cb.checked = want;
          cb.dispatchEvent(new Event("change"));
        }
      }
    } catch (_) {
      cb.checked = !want;
      toast("设置失败，请稍后重试", "error");
    }
    cb.disabled = false;
  });
  return sw;
}

function saveLogButton(sub, log) {
  const logBtn = el("button", "link-btn", "转存记录");
  logBtn.type = "button";
  logBtn.addEventListener("click", async () => {
    if (!log.hidden) { log.hidden = true; return; }
    log.hidden = false;
    log.innerHTML = "";
    log.appendChild(el("li", "muted", "加载中…"));
    try {
      const resp = await fetch("/api/subscriptions/" + sub.id + "/saves?" + cidParam());
      const rows = resp.ok ? await resp.json() : [];
      log.innerHTML = "";
      if (!rows.length) log.appendChild(el("li", "muted", "还没有自动转存过；发现缺的集时会自动存进你的网盘。"));
      rows.slice(0, 20).forEach((r) => {
        const item = el("li", r.ok ? "ok" : "fail");
        item.append(el("span", "save-log-ico", r.ok ? "✓" : "!"), el("span", "save-log-time", formatTime(r.ts)),
          el("span", "save-log-msg", r.ok
            ? (r.file_count ? r.file_count + " 个文件 → " : "") + (r.folder || "网盘默认目录")
            : (r.message || "转存失败")));
        log.appendChild(item);
      });
    } catch (_) {
      log.innerHTML = "";
      log.appendChild(el("li", "fail", "记录加载失败"));
    }
  });
  return logBtn;
}

// 一条订阅：海报 + 标题与状态 + 进度 + 规则 + 自动转存 + 设置面板
function subItem(sub, grouped) { // grouped：在系列分组里，副标题只写「第 N 部」
  const li = el("li", "sub-item" + (sub.state === "paused" ? " is-paused" : ""));
  li.dataset.subId = String(sub.id);
  const card = el("div", "sub-card");
  const body = el("div", "sub-body");

  const head = el("div", "sub-head");
  const info = el("div", "sub-info");
  const titleRow = el("div", "sub-title-row");
  titleRow.appendChild(el("b", "sub-title", "《" + tidyTitle(sub.resource) + "》"));
  titleRow.appendChild(stateBadge(sub));
  if (sub.upgrade) {
    const goal = RES_LABEL[sub.upgrade_to || "2160p"];
    titleRow.appendChild(sub.upgrade_done
      ? badge("已洗版", "state-ok", "已存的都达到 " + goal)
      : badge("洗版中 → " + goal, "state-accent", "出现更高清的分享时自动再存一份，旧版本在「整理网盘目录」里确认删除"));
  }
  info.appendChild(titleRow);
  const tags = [
    sub.season_year || sub.year || "", // 季订阅显示这一季的开播年，不是剧集首播年
    sub.collection_name
      ? (grouped ? "" : collectionName(sub.collection_name) + " 系列 · ") + (sub.collection_index ? "第 " + sub.collection_index + " 部" : "电影")
      : sub.media === "movie" ? "电影" : sub.media === "tv" ? "剧集" : "按关键词",
    sub.last_error ? "" : sub.last_checked ? "检查于 " + formatTime(sub.last_checked) : "尚未检查",
  ].filter(Boolean).join(" · ");
  info.appendChild(el("span", "sub-meta", tags));
  if (sub.last_error) {
    info.appendChild(el("span", "sub-error", "上次检查失败：" + sub.last_error +
      (sub.last_checked ? " · " + formatTime(sub.last_checked) : "")));
  }
  const acts = el("div", "sub-actions");
  SUB_ACTIONS.forEach((make) => { const b = make(sub, li); if (b) acts.appendChild(b); });
  head.append(info, acts);
  body.appendChild(head);

  body.appendChild(subProgress(sub));
  const strip = versionStrip(sub);
  if (strip) body.appendChild(strip);
  const rules = rulesSummary(sub);
  if (rules) body.appendChild(el("span", "sub-rules", rules));
  if (sub.folder) {
    const where = el("span", "sub-folder", "存到：" + sub.folder);
    where.title = sub.folder;
    body.appendChild(where);
  }

  const row = el("div", "sub-row");
  row.appendChild(autoSaveSwitch(sub));
  const log = el("ul", "save-log");
  log.hidden = true;
  if (sub.auto_save) row.appendChild(saveLogButton(sub, log));
  body.appendChild(row);

  card.append(posterEl(sub.poster, sub.resource), body);
  li.appendChild(card);

  if (sub.auto_save && sub.auto_save_status === "login_expired") {
    const warn = el("button", "sub-warn", "夸克登录已失效，自动转存已暂停 · 点此重新扫码");
    warn.type = "button";
    warn.addEventListener("click", async () => {
      if (await quarkLogin("重新扫码后，《" + sub.resource + "》的自动转存会自动恢复")) loadSubs();
    });
    li.appendChild(warn);
  }
  li.appendChild(log);
  return li;
}

// 订阅历史的一条：可一键重新订阅或删除
function historyItem(h) {
  const li = el("li", "sub-item is-history");
  const card = el("div", "sub-card");
  const body = el("div", "sub-body");
  const head = el("div", "sub-head");
  const info = el("div", "sub-info");
  const titleRow = el("div", "sub-title-row");
  titleRow.appendChild(el("b", "sub-title", "《" + tidyTitle(h.resource) + "》"));
  titleRow.appendChild(badge("已完成", "state-ok"));
  info.append(titleRow, el("span", "sub-meta", [
    h.year || "", h.media === "movie" ? "电影" : h.media === "tv" ? "剧集" : "",
    h.reason, formatTime(h.completed) + " 完成",
  ].filter(Boolean).join(" · ")));

  const acts = el("div", "sub-actions");
  const again = el("button", "secondary-btn small", "重新订阅");
  again.type = "button";
  again.addEventListener("click", async () => {
    again.disabled = true;
    const res = await subApi("/history/" + h.id + "/resubscribe", "POST").catch(() => ({ ok: false, body: {} }));
    if (res.ok) {
      toast("已重新订阅《" + res.body.resource + "》", "ok");
      subsTab = subMedia(res.body);
      loadSubs();
      return;
    }
    again.disabled = false;
    if (await loginIfNeeded(res, "订阅追剧需要先扫码登录夸克")) again.click();
  });
  const del = el("button", "ghost-btn small", "删除");
  del.type = "button";
  del.title = "删除这条历史记录";
  del.addEventListener("click", async () => {
    del.disabled = true;
    await subApi("/history/" + h.id, "DELETE").catch(() => {});
    loadSubs();
  });
  acts.append(again, del);
  head.append(info, acts);
  body.appendChild(head);
  if (h.media === "tv" && h.total_episodes) {
    body.appendChild(el("span", "sub-rules", "共 " + h.total_episodes + " 集，存了 " + h.saved_count + " 集"));
  }
  card.append(posterEl(h.poster, h.resource), body);
  li.appendChild(card);
  return li;
}

// ---- 订阅：选条目弹窗与共用的订阅请求 ----
// 订阅的目标：有结果时用识别出的资源名，没结果时直接用搜索词
function subscribeTarget(data) {
  const first = data.history && data.history.length ? data.history[0] : data.query;
  const empty = !data.links || data.links.length === 0;
  return { query: first, resource: empty ? first : data.parsed.resource, empty };
}

// 发起订阅；需要登录时引导扫码，成功后重试一次。返回订阅对象或 null
async function subscribe(payload) {
  const res = await subApi("", "POST", Object.assign({ client_id: clientId }, payload));
  if (res.ok) {
    const sub = res.body;
    toast("已订阅《" + sub.resource + "》" + (payload.auto_save
      ? "，正在后台检查，有资源会自动转存到你的网盘"
      : "，有资源或更新时会在「我的订阅」提醒"), "ok", 3500);
    subsTab = subMedia(sub);
    loadSubs();
    setTimeout(loadSubs, 45000); // 后台第一次检查完成后刷新状态、进度和通知
    return sub;
  }
  const why = payload.auto_save ? "自动转存需要先扫码登录夸克" : "订阅追剧需要先扫码登录夸克";
  if (await loginIfNeeded(res, why)) return subscribe(payload);
  return null;
}

// 订阅整个系列：后端为每一部建一个电影订阅（已订阅 / 已完成的跳过），整个系列只占 1 个名额
async function subscribeCollection(payload, name) {
  const res = await subApi("/collection", "POST", Object.assign({ client_id: clientId }, payload));
  if (res.ok) {
    const list = Array.isArray(res.body) ? res.body : [];
    toast(list.length
      ? "已订阅《" + name + "》系列的 " + list.length + " 部电影（只占 1 个订阅名额）" + (payload.auto_join ? "，以后出新作会自动加入" : "")
      : "《" + name + "》系列的电影都已经订阅过或已完成", "ok", 4000);
    subsTab = "movie";
    loadSubs();
    setTimeout(loadSubs, 45000);
    return list[0] || { resource: name };
  }
  const why = payload.auto_save ? "自动转存需要先扫码登录夸克" : "订阅追剧需要先扫码登录夸克";
  if (await loginIfNeeded(res, why)) return subscribeCollection(payload, name);
  return null;
}

// 片名末尾的全角波浪号（如「无职转生～到了异世界就拿出真本事～」）去掉再拼季，
// 否则显示成「…本事～ 第3季」
function tidyTitle(t) {
  const m = String(t || "").trim().match(/^(.*?)\s*(第\s*\d+\s*季)?$/);
  const name = m[1].replace(/[\s～~〜]+$/, "");
  return name && m[2] ? name + " " + m[2] : name || m[2] || "";
}

// 系列候选（TMDB collection）的各部，按上映顺序；index 从 1 开始
function collectionParts(c) {
  return ((c.collection && c.collection.parts) || []).slice().sort((a, b) => a.index - b.index);
}

function collectionName(name) {
  return tidyTitle(String(name || "").replace(/[（(]?系列[）)]?$|\s*Collection$/i, ""));
}

// 候选条目的一行：海报、片名、年份、类型；剧集带选季，系列带选第几部
function candidateOption(c, idx, name) {
  if (c.kind === "collection" && collectionParts(c).length) return collectionOption(c, idx, name);
  const opt = el("label", "cand");
  const radio = el("input");
  radio.type = "radio";
  radio.name = name;
  radio.value = String(idx);
  const text = el("span", "cand-text");
  const title = el("b", "cand-title", tidyTitle(c.title) || c.title);
  const meta = [c.year || "", c.media === "movie" ? "电影" : "剧集",
    c.media === "tv" && c.seasons ? "共 " + c.seasons + " 季" : "",
    c.source === "tmdb" ? "TMDB" : "豆瓣"].filter(Boolean).join(" · ");
  text.append(title);
  if (c.original_title && c.original_title !== c.title) text.appendChild(el("span", "cand-orig", c.original_title));
  text.appendChild(el("span", "cand-meta", meta));
  opt.append(radio, posterEl(c.poster, c.title, "small"), text);
  if (c.media === "tv") {
    const seasons = Object.keys(c.episodes || {}).map(Number).filter((n) => n > 0).sort((a, b) => a - b);
    if (!seasons.length) seasons.push(1);
    const sel = el("select", "cand-season");
    sel.setAttribute("aria-label", "选择季");
    seasons.forEach((s) => {
      const n = c.episodes && c.episodes[s];
      const o = el("option", "", "第 " + s + " 季" + (n ? " · " + n + " 集" : ""));
      o.value = String(s);
      sel.appendChild(o);
    });
    sel.value = String(seasons[seasons.length - 1]); // 默认最新一季
    sel.addEventListener("click", () => { radio.checked = true; radio.dispatchEvent(new Event("change", { bubbles: true })); });
    opt.appendChild(sel);
  }
  return opt;
}

// 系列候选：「系列 · 共 N 部」，下拉框选第几部，交互和剧集选季一样
function collectionOption(c, idx, name) {
  const parts = collectionParts(c);
  const opt = el("label", "cand cand-coll");
  const radio = el("input");
  radio.type = "radio";
  radio.name = name;
  radio.value = String(idx);
  const text = el("span", "cand-text");
  const title = collectionName(c.collection.name || c.title) || c.title;
  text.appendChild(el("b", "cand-title", title));
  text.appendChild(el("span", "cand-meta", ["系列 · 共 " + parts.length + " 部",
    c.source === "douban" ? "豆瓣" : "TMDB"].join(" · ")));
  const poster = c.collection.poster || c.poster || (parts[0] && parts[0].poster);
  const sel = el("select", "cand-season cand-part");
  sel.setAttribute("aria-label", "选择第几部");
  parts.forEach((p) => {
    const o = el("option", "", ["第 " + p.index + " 部", tidyTitle(p.title), p.year || ""].filter(Boolean).join(" · ") +
      (p.released === false ? "（未上映）" : ""));
    o.value = String(p.index);
    sel.appendChild(o);
  });
  if (parts.length > 1) {
    const all = el("option", "", "整个系列（" + parts.length + " 部）");
    all.value = "all";
    sel.appendChild(all);
  }
  const def = parts.some((p) => p.index === c.default_part) ? c.default_part : parts[0].index;
  sel.value = String(def);
  sel.addEventListener("click", () => { radio.checked = true; radio.dispatchEvent(new Event("change", { bubbles: true })); });
  opt.append(radio, posterEl(poster, title, "small"), text, sel);
  return opt;
}

// 订阅弹窗：先搜 TMDB/豆瓣让用户选条目，选不到就按关键词订阅
function openSubscribeDialog(target, opts) {
  opts = opts || {};
  return new Promise((resolve) => {
    const dlg = el("dialog", "sub-dialog");
    const head = el("div", "sd-head");
    head.appendChild(el("p", "sd-title", target.resource ? "订阅《" + target.resource + "》" : "新订阅"));
    const x = el("button", "ghost-btn small icon-only", "✕");
    x.type = "button";
    x.setAttribute("aria-label", "关闭");
    head.appendChild(x);

    const searchRow = el("form", "sd-search");
    const q = el("input");
    q.type = "search";
    q.placeholder = "片名，如 繁花、沙丘";
    q.maxLength = 100;
    q.value = target.resource || "";
    q.setAttribute("aria-label", "片名");
    const go = el("button", "secondary-btn small", "查找");
    go.type = "submit";
    searchRow.append(q, go);

    const hint = el("p", "sd-hint", "选中对应的影视条目，订阅会按季追踪缺的集；找不到也可以直接按关键词订阅。");
    const list = el("div", "cand-list");
    list.setAttribute("role", "radiogroup");

    const optsBox = el("div", "sd-opts");
    let autoCb = null;
    if (meState.login) {
      const sw = el("label", "switch small");
      autoCb = el("input");
      autoCb.type = "checkbox";
      autoCb.checked = !!opts.autoSave;
      const track = el("span", "switch-track");
      track.setAttribute("aria-hidden", "true");
      sw.append(autoCb, track, "自动转存到我的网盘");
      sw.title = meState.logged_in ? "剧集会立即补齐网盘里缺的集；电影有合适资源时存一次" : "需要先扫码登录夸克";
      optsBox.appendChild(sw);
    }
    // 选「整个系列」时才出现：以后出新作自动加入（默认关）
    const joinSw = el("label", "switch small sd-join");
    const joinCb = el("input");
    joinCb.type = "checkbox";
    const joinTrack = el("span", "switch-track");
    joinTrack.setAttribute("aria-hidden", "true");
    joinSw.append(joinCb, joinTrack, "以后出新作自动加入");
    joinSw.title = "每天查一次这个系列有没有新片，有就自动订阅并通知你";
    joinSw.hidden = true;
    optsBox.appendChild(joinSw);
    const more = el("details", "sd-more");
    more.appendChild(el("summary", "", "更多规则（清晰度、关键词、起始集）"));
    const rulesGrid = el("div", "sub-edit-grid");
    SUB_RULES.filter((r) => r.key !== "total_episodes" && !r.editOnly).forEach((r) => {
      const f = ruleField(r, null);
      if (r.tvOnly) f.classList.add("tv-only");
      rulesGrid.appendChild(f);
    });
    more.appendChild(rulesGrid);
    optsBox.appendChild(more);

    const foot = el("div", "sd-foot");
    const cancel = el("button", "secondary-btn small", "取消");
    cancel.type = "button";
    const ok = el("button", "primary-btn small", "订阅");
    ok.type = "button";
    foot.append(cancel, ok);
    dlg.append(head, searchRow, hint, list, optsBox, foot);
    document.body.appendChild(dlg);

    let cands = [];
    let closed = false;
    const finish = (sub) => {
      if (closed) return;
      closed = true;
      dlg.close();
      dlg.remove();
      resolve(sub);
    };
    x.addEventListener("click", () => finish(null));
    cancel.addEventListener("click", () => finish(null));
    dlg.addEventListener("cancel", () => finish(null));

    const selected = () => {
      const r = list.querySelector("input[type=radio]:checked");
      return r && r.value !== "kw" ? cands[Number(r.value)] : null;
    };
    const syncOpts = () => { // 电影没有起始集
      const c = selected();
      rulesGrid.querySelectorAll(".tv-only").forEach((f) => { f.hidden = !!c && c.media === "movie"; });
      const part = list.querySelector(".cand input:checked") && list.querySelector(".cand input:checked").closest(".cand").querySelector(".cand-part");
      joinSw.hidden = !(part && part.value === "all");
      setText(ok, joinSw.hidden ? "订阅" : "订阅整个系列");
      list.querySelectorAll(".cand").forEach((l) => l.classList.toggle("on", l.querySelector("input").checked));
    };
    list.addEventListener("change", syncOpts);

    async function lookup() {
      const name = q.value.trim();
      list.innerHTML = "";
      cands = [];
      if (!name) { q.focus(); return; }
      for (let i = 0; i < 3; i++) list.appendChild(el("div", "cand cand-skel"));
      try {
        const resp = await fetch("/api/media/search?q=" + encodeURIComponent(name));
        cands = resp.ok ? await resp.json() : [];
        if (resp.status === 429) toast(rateLimitText(resp, await resp.json().catch(() => ({}))), "error", 4000);
      } catch (_) { cands = []; }
      list.innerHTML = "";
      cands.slice(0, 10).forEach((c, i) => list.appendChild(candidateOption(c, i, "sd-cand")));
      const kw = el("label", "cand cand-kw");
      const kwRadio = el("input");
      kwRadio.type = "radio";
      kwRadio.name = "sd-cand";
      kwRadio.value = "kw";
      const kwText = el("span", "cand-text");
      kwText.append(el("b", "cand-title", "按关键词「" + name + "」订阅"),
        el("span", "cand-meta", cands.length ? "上面都不对时选这个" : "没找到对应的影视条目，照常定期搜索和提醒"));
      kw.append(kwRadio, el("span", "poster small no-img", "#"), kwText);
      list.appendChild(kw);
      (list.querySelector("input[type=radio]") || kwRadio).checked = true;
      syncOpts();
    }
    searchRow.addEventListener("submit", (e) => { e.preventDefault(); lookup(); });

    ok.addEventListener("click", async () => {
      const name = q.value.trim();
      if (name.length < 2 && !selected()) { toast("片名至少 2 个字", "error"); q.focus(); return; }
      const c = selected();
      const payload = {};
      const partSel = c && c.kind === "collection" ? list.querySelector(".cand.on .cand-part") : null;
      if (partSel && partSel.value === "all") {
        const rules = readRules(rulesGrid, null);
        const body = { collection_id: String(c.collection.id), auto_join: joinCb.checked, auto_save: !!(autoCb && autoCb.checked) };
        ["resolution", "include", "exclude", "upgrade", "upgrade_to"].forEach((k) => { if (rules[k] !== undefined && rules[k] !== "") body[k] = rules[k]; });
        ok.disabled = true;
        ok.classList.add("loading");
        dlg.close();
        const sub = await subscribeCollection(body, collectionName(c.collection.name) || c.title);
        if (sub || closed) { finish(sub); return; }
        dlg.showModal();
        ok.disabled = false;
        ok.classList.remove("loading");
        return;
      }
      if (c && c.kind === "collection" && collectionParts(c).length) { // 选了系列里的某一部：按普通电影订阅
        const sel = list.querySelector(".cand.on .cand-part");
        const parts = collectionParts(c);
        const p = parts.find((x) => String(x.index) === (sel && sel.value)) || parts[0];
        const t = tidyTitle(p.title) || p.title;
        payload.media = "movie";
        payload.tmdb_id = p.id ? String(p.id) : undefined;
        payload.year = p.year || undefined;
        payload.poster = p.poster || c.collection.poster || undefined;
        payload.resource = t;
        payload.query = t.length >= 2 ? t : name;
        payload.collection_id = c.collection.id ? String(c.collection.id) : undefined;
        payload.collection_name = collectionName(c.collection.name) || undefined;
        payload.collection_index = p.index;
      } else if (c) {
        const sel = list.querySelector(".cand.on .cand-season");
        payload.media = c.media;
        payload.year = c.year || undefined;
        payload.poster = c.poster || undefined;
        payload[c.source === "douban" ? "douban_id" : "tmdb_id"] = c.id || undefined;
        const t = tidyTitle(c.title) || c.title;
        payload.resource = t;
        payload.query = t.length >= 2 ? t : (target.query || name);
        if (c.media === "tv") {
          payload.season = sel ? Number(sel.value) : 1;
          if (payload.season > 1) payload.query = t + " 第" + payload.season + "季";
        }
      } else {
        payload.resource = name;
        payload.query = name === target.resource && target.query ? target.query : name;
      }
      const rules = readRules(rulesGrid, null);
      Object.keys(rules).forEach((k) => { if (rules[k] !== "") payload[k] = rules[k]; });
      if (c && c.media === "movie") delete payload.start_episode;
      if (autoCb && autoCb.checked) payload.auto_save = true;
      Object.keys(payload).forEach((k) => payload[k] === undefined && delete payload[k]);
      ok.disabled = true;
      ok.classList.add("loading");
      dlg.close(); // 扫码弹窗可能要叠在上面
      const sub = await subscribe(payload);
      if (sub || closed) { finish(sub); return; }
      dlg.showModal();
      ok.disabled = false;
      ok.classList.remove("loading");
    });

    dlg.showModal();
    if (target.resource) lookup(); else q.focus();
  });
}

const subscribeBox = document.getElementById("subscribe-box");

function renderSubscribeBox(data) {
  subscribeBox.innerHTML = "";
  if (subsPanel.hidden) { // 服务器没开记忆/订阅
    subscribeBox.hidden = true;
    return;
  }
  const target = subscribeTarget(data);
  const text = el("div", "sb-text");
  text.append(
    el("b", "sb-title", target.empty ? "暂时没有资源，要不要先订阅？" : "追更《" + target.resource + "》"),
    el("span", "sb-sub", target.empty
      ? "服务器会定期替你重搜「" + target.query + "」，有资源了第一时间提醒你"
      : "按季追踪缺的集，有新集或更高清的版本时提醒你；可以自动补齐网盘"),
  );
  const controls = el("div", "sb-controls");
  let cb = null;
  if (meState.login) {
    const sw = el("label", "switch small");
    cb = el("input");
    cb.type = "checkbox";
    const track = el("span", "switch-track");
    track.setAttribute("aria-hidden", "true");
    sw.append(cb, track, target.empty ? "有资源时自动转存" : "自动转存");
    if (!meState.logged_in) sw.title = "需要先扫码登录夸克";
    controls.appendChild(sw);
  }
  const go = el("button", target.empty ? "primary-btn small" : "secondary-btn", target.empty ? "有资源时通知我" : "订阅");
  go.type = "button";
  go.addEventListener("click", async () => {
    go.disabled = true;
    const sub = await openSubscribeDialog(target, { autoSave: !!(cb && cb.checked) });
    if (sub) {
      go.className = "secondary-btn done";
      setText(go, "已订阅 ✓");
      if (cb) cb.disabled = true;
      subscribeBtn.disabled = true;
      setText(subscribeBtn, "已订阅 ✓");
      subscribeBtn.classList.add("done");
    } else {
      go.disabled = false;
    }
  });
  controls.appendChild(go);
  const icon = el("span", "sb-icon");
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = "🔔";
  subscribeBox.append(icon, text, controls);
  subscribeBox.classList.toggle("is-empty", target.empty);
  subscribeBox.hidden = false;
}

// 工具栏上的「订阅更新」：滚到订阅卡片并高亮
subscribeBtn.addEventListener("click", () => {
  if (subscribeBox.hidden) return;
  subscribeBox.scrollIntoView({ behavior: "smooth", block: "center" });
  subscribeBox.classList.remove("flash");
  void subscribeBox.offsetWidth;
  subscribeBox.classList.add("flash");
});

// 订阅面板里的「＋ 新订阅」：不用先搜资源
newSubBtn.addEventListener("click", () => openSubscribeDialog({ query: "", resource: "", empty: true }));


loadSubs();
setInterval(loadSubs, 5 * 60 * 1000);


// ---- 一键转存 ----
// 优先扫码登录自己的夸克（凭证加密存在服务器，浏览器只拿一个 HttpOnly 会话）；
// 部署者在 .env 配了自己的 cookie 时，也可以凭口令存到部署者的网盘。
let saveStatus = { enabled: false, login: false, logged_in: false, token_mode: false };
// /api/me 的内容：是否开放登录、要不要邀请码、是否被停用、剩余次数
const meState = { login: false, invite_required: false, logged_in: false, nickname: null, banned: null, quota: null };

function getSaveToken(forceAsk) {
  let token = null;
  try { token = forceAsk ? null : localStorage.getItem("qp_save_token"); } catch (_) { /* 忽略 */ }
  if (!token) {
    token = window.prompt("请输入转存口令（服务器 .env 里的 SAVE_TOKEN，只保存在本浏览器）");
    if (token) {
      try { localStorage.setItem("qp_save_token", token); } catch (_) { /* 忽略 */ }
    }
  }
  return token;
}

// 扫码登录弹窗：显示二维码并轮询，成功返回 true，关闭或失败返回 false。
// reason：为什么要登录（例如免费次数用完），显示在标题下面。
// 开启邀请制时，新用户先填邀请码再扫码；扫码后提示邀请码无效时可以改了重试。
function isMobileDevice() {
  const ua = navigator.userAgent || "";
  return /Android|iPhone|iPad|iPod|HarmonyOS|Mobile/i.test(ua) ||
    (navigator.maxTouchPoints > 1 && /Macintosh/.test(ua)); // iPadOS 伪装成 Mac
}

// 把二维码 SVG 画成 PNG 图片（手机相册存不了 SVG）；失败时保留原来的 SVG
function qrToImage(box, svg) {
  if (!svg) return;
  const src = new Image();
  src.onload = () => {
    try {
      const size = 600;
      const c = document.createElement("canvas");
      c.width = size;
      c.height = size;
      const g = c.getContext("2d");
      g.fillStyle = "#fff";
      g.fillRect(0, 0, size, size);
      g.drawImage(src, 0, 0, size, size);
      const img = el("img", "qr-img");
      img.alt = "夸克登录二维码";
      img.src = c.toDataURL("image/png");
      box.innerHTML = "";
      box.appendChild(img);
    } catch (_) { /* 画布被污染等：继续用 SVG */ }
  };
  let sized = /<svg[^>]*\swidth=/.test(svg) ? svg : svg.replace("<svg", '<svg width="600" height="600"');
  if (!/<svg[^>]*\sxmlns=/.test(sized)) sized = sized.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"');
  src.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(sized);
}

function quarkLogin(reason) {
  return new Promise((resolve) => {
    const dlg = el("dialog", "qr-dialog");
    const title = el("p", "qr-title", isMobileDevice() ? "登录夸克网盘" : "用夸克 App 扫码登录");
    const sub = el("p", "qr-sub", reason || "登录后搜索次数更多，转存会保存到你自己的网盘");
    const invite = el("div", "qr-invite");
    const inviteInput = el("input");
    inviteInput.type = "text";
    inviteInput.maxLength = 64;
    inviteInput.placeholder = "邀请码（老用户不用填）";
    inviteInput.autocomplete = "off";
    inviteInput.setAttribute("aria-label", "邀请码");
    const inviteGo = el("button", "primary-btn small", "扫码登录");
    inviteGo.type = "button";
    invite.append(inviteInput, inviteGo);
    invite.hidden = !meState.invite_required;
    const box = el("div", "qr-box");
    const tip = el("p", "qr-tip");
    const actions = el("div", "qr-actions");
    const retry = el("button", "secondary-btn small", "重新获取二维码");
    retry.type = "button";
    retry.hidden = true;
    const close = el("button", "secondary-btn small", "取消");
    close.type = "button";
    actions.append(retry, close);
    // 手机上扫不了自己屏幕上的码：主推「打开夸克 App」，二维码收进备选里
    const mobile = isMobileDevice();
    const appBtn = el("a", "primary-btn small qr-app-btn", "打开夸克 App 登录");
    appBtn.target = "_blank";
    appBtn.rel = "noopener";
    appBtn.hidden = true;
    const qrMore = el("details", "qr-more");
    qrMore.hidden = true;
    qrMore.append(el("summary", "", "打不开 App？用二维码登录"),
      el("p", "qr-album", "长按二维码保存到相册，在夸克 App 的「扫一扫」里从相册选这张图"));
    if (mobile) {
      qrMore.appendChild(box);
      dlg.append(title, sub, invite, appBtn, tip, qrMore, actions);
    } else {
      dlg.append(title, sub, invite, box, tip, actions);
    }
    document.body.appendChild(dlg);

    let timer = null;
    let closed = false;
    let pollNow = null; // 从夸克 App 切回来时立即查一次，不用等下一轮
    const onVisible = () => { if (document.visibilityState === "visible" && pollNow) pollNow(); };
    document.addEventListener("visibilitychange", onVisible);
    const finish = (ok) => {
      if (closed) return;
      closed = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      dlg.close();
      dlg.remove();
      resolve(ok);
    };
    close.addEventListener("click", () => finish(false));
    dlg.addEventListener("cancel", () => finish(false));

    const stopWith = (text, kind) => {
      clearInterval(timer);
      pollNow = null;
      appBtn.hidden = true;
      qrMore.hidden = true;
      box.innerHTML = "";
      box.classList.add("empty-qr");
      setText(tip, text);
      tip.className = "qr-tip " + (kind || "");
    };

    async function start() {
      clearInterval(timer);
      retry.hidden = true;
      appBtn.hidden = true;
      qrMore.hidden = true;
      box.classList.remove("empty-qr");
      box.innerHTML = '<span class="qr-loading" aria-hidden="true"></span>';
      tip.className = "qr-tip";
      setText(tip, mobile ? "正在准备登录…" : "正在获取二维码…");
      try {
        const code = inviteInput.value.trim();
        const resp = await fetch("/api/quark/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(code ? { invite_code: code } : {}),
        });
        const data = await resp.json().catch(() => ({}));
        if (closed) return;
        if (!resp.ok) {
          stopWith(data.detail || "获取二维码失败", "error");
          retry.hidden = false;
          return;
        }
        box.innerHTML = data.qr_svg || ""; // 服务器生成的二维码 SVG
        if (mobile) {
          qrToImage(box, data.qr_svg); // 转成图片才能长按保存到相册
          if (data.qr_url) {
            appBtn.href = data.qr_url;
            appBtn.hidden = false;
          }
          qrMore.hidden = false;
          qrMore.open = !data.qr_url;
          setText(tip, "在夸克 App 里点「确认登录」后，回到这个页面就行，会自动登录");
        } else {
          setText(tip, "扫码后在手机上确认登录");
        }
        const poll = async () => {
          try {
            const r = await (await fetch("/api/quark/login/" + encodeURIComponent(data.login_id))).json();
            if (closed) return;
            if (r.status === "success") {
              clearInterval(timer);
              saveStatus.logged_in = true;
              saveStatus.nickname = r.nickname;
              meState.logged_in = true;
              meState.nickname = r.nickname;
              renderAccount();
              loadMe();
              loadSubs();
              finish(true);
            } else if (r.status === "invite_required") {
              meState.invite_required = true;
              invite.hidden = false;
              stopWith((r.message || "新用户需要邀请码") + "，填好邀请码后点「扫码登录」重新扫码", "error");
              inviteInput.focus();
            } else if (r.status === "banned") {
              stopWith(r.message || "该账号已被停用", "error");
            } else if (r.status !== "waiting" && r.status !== "scanned") {
              stopWith(r.message || "二维码已过期", "error");
              retry.hidden = false;
            } else if (r.status === "scanned") {
              setText(tip, mobile ? "请在夸克 App 里点「确认登录」，然后回到这里" : "已扫码，请在手机上确认登录");
            }
          } catch (_) { /* 网络抖动：下次再试 */ }
        };
        pollNow = poll;
        timer = setInterval(poll, 2000);
      } catch (_) {
        if (!closed) {
          stopWith("获取二维码失败，请稍后重试", "error");
          retry.hidden = false;
        }
      }
    }

    retry.addEventListener("click", start);
    inviteGo.addEventListener("click", start);
    inviteInput.addEventListener("keydown", (e) => { if (e.key === "Enter") start(); });
    dlg.showModal();
    if (meState.invite_required) {
      box.classList.add("empty-qr");
      setText(tip, "新用户请先填写邀请码；已经登录过的老用户直接点「扫码登录」");
      inviteInput.focus();
    } else {
      start();
    }
  });
}

function saveButton(l) {
  const btn = el("button", "secondary-btn small", "转存到网盘");
  btn.type = "button";
  btn.addEventListener("click", async () => {
    let token = null;
    if (!saveStatus.logged_in) {
      if (saveStatus.login) {
        if (!(await quarkLogin())) return;
      } else {
        token = getSaveToken(false);
        if (!token) return;
      }
    }
    btn.disabled = true;
    setText(btn, "转存中…");
    try {
      let resp = await postSave(l, token);
      if (resp.status === 401 && token) { // 口令不对：清掉重新问一次
        token = getSaveToken(true);
        resp = token ? await postSave(l, token) : resp;
      }
      const body = await resp.json().catch(() => ({}));
      const ok = resp.ok && body.ok;
      if (!ok && /重新扫码|先扫码/.test(body.message || body.detail || "")) {
        saveStatus.logged_in = false;
        renderAccount();
      }
      setText(btn, ok ? "已转存 ✓" : resp.status === 429 ? "今天已达上限" : "转存失败");
      btn.classList.toggle("done", ok);
      const msg = body.message || body.detail || (ok ? "已转存" : "转存失败");
      toast(ok ? "转存成功" + (body.folder ? "，已放进「" + body.folder + "」" : "") : msg,
        ok ? "ok" : "error");
      showSaveResult(btn, ok, body, msg);
      if (!ok) btn.disabled = false;
    } catch (_) {
      setText(btn, "转存失败");
      showSaveResult(btn, false, {}, "网络出错，转存没有完成，请稍后重试");
      btn.disabled = false;
    }
  });
  return btn;
}

// 转存结果常驻在卡片里：存到了哪个目录、识别成什么类别、依据是什么
function showSaveResult(btn, ok, body, msg) {
  const card = btn.closest(".result-card");
  if (!card) return;
  let box = card.querySelector(".save-result");
  if (!box) {
    box = el("div", "save-result");
    box.setAttribute("role", "status");
    card.appendChild(box);
  }
  box.innerHTML = "";
  box.className = "save-result " + (ok ? "ok" : "error");
  if (!ok) {
    box.append(el("span", "save-ico", "!"), el("span", "save-main", msg));
    return;
  }
  const basis = (msg.match(/依据：([^）)]+)/) || [])[1];
  const pending = /后台处理/.test(msg);
  const main = el("span", "save-main");
  main.append(pending ? "已提交转存，夸克正在后台处理 · 目录 " : "已存入 ");
  main.append(el("b", "save-folder", body.folder || "你的夸克网盘默认目录"));
  box.append(el("span", "save-ico", "✓"), main);
  const tags = el("span", "save-tags");
  if (body.category) tags.appendChild(badge("识别为 " + body.category, "badge-accent"));
  if (basis) tags.appendChild(badge("依据 " + basis, "badge-tag"));
  if (!body.category) tags.appendChild(badge("未自动分类", "badge-tag", "没识别出类别，存到了默认目录"));
  if (body.file_count) tags.appendChild(badge(body.file_count + " 个文件", "badge-tag"));
  box.appendChild(tags);
}

function postSave(l, token) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers["X-Save-Token"] = token;
  return fetch("/api/save", {
    method: "POST",
    headers,
    body: JSON.stringify({ share: l.share, pwd: l.pwd || null }),
  });
}

// ---- 顶栏账号：登录夸克 / 已登录 / 退出 ----
const accountEl = document.getElementById("account");
const loginBtn = document.getElementById("login-btn");
const loginLabel = document.getElementById("login-label");
const accountMenu = document.getElementById("account-menu");
const accountName = document.getElementById("account-name");
const logoutBtn = document.getElementById("logout-btn");

function renderAccount() {
  accountEl.hidden = !saveStatus.login; // 服务器没开扫码登录就不显示
  const name = saveStatus.nickname || "夸克用户";
  accountEl.classList.toggle("logged-in", !!saveStatus.logged_in);
  setText(loginLabel, saveStatus.logged_in ? name : "登录夸克");
  loginBtn.title = saveStatus.logged_in ? "已登录夸克：" + name : "扫码登录夸克，转存到自己的网盘";
  setText(accountName, name);
  if (!saveStatus.logged_in) setMenuOpen(false);
}

function setMenuOpen(open) {
  accountMenu.hidden = !open;
  loginBtn.setAttribute("aria-expanded", String(open));
}

loginBtn.addEventListener("click", async () => {
  if (saveStatus.logged_in) {
    setMenuOpen(accountMenu.hidden);
    return;
  }
  if (await quarkLogin()) toast("登录成功" + (saveStatus.nickname ? "（" + saveStatus.nickname + "）" : "") + "，转存会保存到你的夸克网盘");
});

logoutBtn.addEventListener("click", async () => {
  logoutBtn.disabled = true;
  try {
    const resp = await fetch("/api/quark/logout", { method: "POST" });
    if (!resp.ok) throw new Error("logout failed");
    saveStatus.logged_in = false;
    saveStatus.nickname = null;
    renderAccount();
    loadMe();
    loadSubs();
    toast("已退出登录，服务器上保存的凭证已删除");
  } catch (_) {
    toast("退出失败，请稍后重试", "error");
  }
  logoutBtn.disabled = false;
});

// ---- 隐私说明 + 删除我的数据（frontend-api.md §17） ----
// 文字以后端 GET /api/privacy 为准；接口不可用时用这份简版兜底
const PRIVACY_FALLBACK = [
  { title: "夸克登录凭证", text: "扫码登录后，夸克登录凭证加密保存在服务器上，只用来转存到你的网盘、检查和整理订阅。退出登录会删除这台设备的凭证。" },
  { title: "订阅和用量", text: "保存你的订阅、订阅历史、通知、转存记录、偏好设置，以及每天的搜索和 AI 用量（用于额度限制）。" },
  { title: "删除我的数据", text: "可以随时删除上面这些数据并退出登录。已经存到你网盘里的文件不受影响。" },
];

const DATA_LABELS = [
  ["subscriptions", "个订阅"], ["history", "条订阅历史"], ["collections", "个系列订阅"],
  ["notifications", "条提醒"], ["auto_saves", "条转存记录"], ["prefs", "份偏好设置"], ["quark_logins", "个设备上的登录凭证"],
];

function dataSummary(counts) {
  return DATA_LABELS.filter(([k]) => counts && counts[k] > 0).map(([k, unit]) => counts[k] + " " + unit);
}

function dialogShell(title, cls) {
  const dlg = el("dialog", "sub-dialog " + cls);
  const head = el("div", "sd-head");
  head.appendChild(el("p", "sd-title", title));
  const x = el("button", "ghost-btn small icon-only", "✕");
  x.type = "button";
  x.setAttribute("aria-label", "关闭");
  head.appendChild(x);
  dlg.appendChild(head);
  document.body.appendChild(dlg);
  const close = () => { if (dlg.open) dlg.close(); dlg.remove(); };
  x.addEventListener("click", close);
  dlg.addEventListener("cancel", close);
  return { dlg, close };
}

async function openPrivacyDialog() {
  const { dlg, close } = dialogShell("隐私说明", "privacy-dialog");
  const body = el("div", "privacy-body");
  for (let i = 0; i < 3; i++) body.appendChild(el("div", "privacy-skel"));
  const foot = el("div", "sd-foot");
  const del = el("button", "ghost-btn small danger", "删除我的数据");
  del.type = "button";
  const ok = el("button", "primary-btn small", "知道了");
  ok.type = "button";
  foot.append(del, ok);
  dlg.append(body, foot);
  ok.addEventListener("click", close);
  del.addEventListener("click", () => { close(); openDeleteDialog(); });
  dlg.showModal();

  let data = null;
  try {
    const resp = await fetch("/api/privacy");
    if (resp.ok) data = await resp.json();
  } catch (_) { /* 用兜底文字 */ }
  const sections = data && Array.isArray(data.sections) && data.sections.length ? data.sections : PRIVACY_FALLBACK;
  body.innerHTML = "";
  sections.forEach((sec) => {
    body.appendChild(el("h3", "privacy-h", sec.title || ""));
    String(sec.text || "").split(/\n+/).filter(Boolean).forEach((t) => body.appendChild(el("p", "privacy-p", t)));
  });
  if (data && data.updated) body.appendChild(el("p", "privacy-updated", "更新于 " + data.updated));
}

// 删除前二次确认：先列出将删除的数据条数，要输入「删除」两个字才能点
async function openDeleteDialog() {
  const { dlg, close } = dialogShell("删除我的数据", "delete-dialog");
  const lead = el("p", "delete-lead", "会立即删除服务器上和你有关的数据并退出登录，删除后不能恢复。");
  const counts = el("ul", "privacy-list delete-counts");
  counts.appendChild(el("li", "muted", "正在统计…"));
  const keep = el("p", "sd-hint", "不会删除：已经存到你网盘里的文件、全站共享的链接库。为防止刷额度，今天的用量计数会保留到明天；开启邀请制时，再登录需要新的邀请码。");
  const label = el("label", "delete-confirm");
  label.appendChild(el("span", "", "请输入「删除」确认"));
  const input = el("input");
  input.type = "text";
  input.autocomplete = "off";
  input.placeholder = "删除";
  label.appendChild(input);
  const foot = el("div", "sd-foot");
  const cancel = el("button", "secondary-btn small", "取消");
  cancel.type = "button";
  const go = el("button", "primary-btn small danger-btn", "永久删除");
  go.type = "button";
  go.disabled = true;
  foot.append(cancel, go);
  dlg.append(lead, counts, keep, label, foot);
  cancel.addEventListener("click", close);
  input.addEventListener("input", () => { go.disabled = input.value.trim() !== "删除"; });
  dlg.showModal();
  input.focus();

  fetch("/api/me/data?" + cidParam()).then((r) => (r.ok ? r.json() : null)).then((d) => {
    counts.innerHTML = "";
    const items = d ? dataSummary(d.counts) : [];
    if (!d) items.push("订阅、订阅历史、系列订阅、提醒、转存记录、偏好设置", "所有设备上的夸克登录凭证和账号记录");
    else if (!items.length) items.push("服务器上暂时没有你的订阅或设置");
    if (d && d.logged_in) items.push("你的夸克账号记录（会退出登录）");
    items.forEach((t) => counts.appendChild(el("li", "", t)));
  }).catch(() => { counts.innerHTML = ""; });

  go.addEventListener("click", async () => {
    go.disabled = true;
    go.classList.add("loading");
    let res;
    try {
      const resp = await fetch("/api/me/data?" + cidParam() + "&confirm=DELETE", { method: "DELETE" });
      res = { ok: resp.ok, body: await resp.json().catch(() => ({})) };
    } catch (_) {
      res = { ok: false, body: { detail: "网络连接失败，请稍后重试" } };
    }
    if (!res.ok) {
      go.classList.remove("loading");
      go.disabled = false;
      toast(res.body.detail || "删除失败，请稍后重试", "error");
      return;
    }
    // 服务器已删除并清掉登录 cookie：清掉本地记录（订阅身份、缓存等），换一个新身份回到未登录首页
    try {
      Object.keys(localStorage).filter((k) => k.startsWith("qp_")).forEach((k) => localStorage.removeItem(k));
    } catch (_) { /* 存储不可用 */ }
    close();
    const done = dataSummary(res.body.deleted);
    toast("已删除" + (done.length ? "：" + done.join("、") : "你的数据") + "，页面即将刷新", "ok", 3000);
    setTimeout(() => location.replace("/"), 1600);
  });
}

document.addEventListener("click", (e) => {
  if (e.target.closest("[data-privacy]")) { setMenuOpen(false); openPrivacyDialog(); }
  else if (e.target.closest("[data-delete-data]")) { setMenuOpen(false); openDeleteDialog(); }
});

document.addEventListener("click", (e) => {
  if (!accountMenu.hidden && !accountEl.contains(e.target)) setMenuOpen(false);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !accountMenu.hidden) {
    setMenuOpen(false);
    loginBtn.focus();
  }
});

fetch("/api/save/status")
  .then((r) => r.json())
  .then((d) => { saveStatus = Object.assign(saveStatus, d); saveEnabled = !!d.enabled; renderAccount(); })
  .catch(() => {});


// ---- 访客信息：剩余次数、降级提示、停用状态（/api/me） ----
const quotaLine = document.getElementById("quota-line");
const quotaNote = document.getElementById("quota-note");
const banBanner = document.getElementById("ban-banner");

function renderQuota(q) {
  meState.quota = q;
  quotaLine.innerHTML = "";
  if (!q) {
    quotaLine.hidden = true;
    return;
  }
  const ai = q.limit === 0 || q.remaining == null ? "不限" : String(q.remaining);
  const loggedIn = q.logged_in || meState.logged_in;
  const pill = el("span", "quota-pill" + (q.ai ? "" : " degraded"));
  pill.appendChild(el("span", "quota-dot"));
  if (!loggedIn && q.searches_remaining != null) {
    pill.append("今日还可免费搜索 ", el("b", "", String(q.searches_remaining)), " 次（其中 AI 搜索 ",
      el("b", "", ai), " 次）");
  } else {
    pill.append("今日 AI 搜索剩余 ", el("b", "", ai), " 次");
  }
  if (!q.ai) pill.append(el("span", "quota-tag", "当前为基础模式"));
  quotaLine.appendChild(pill);
  if (!loggedIn && meState.login) {
    const btn = el("button", "link-btn", "扫码登录获得更多");
    btn.type = "button";
    btn.addEventListener("click", () => quarkLogin());
    quotaLine.appendChild(btn);
  }
  quotaLine.hidden = false;
}

function showQuotaNote(q) {
  if (!q || !q.message) return;
  quotaNote.innerHTML = "";
  quotaNote.append(el("span", "quota-note-ico", "!"), el("span", "", q.message));
  if (q.reason === "user_quota" && !(q.logged_in || meState.logged_in) && meState.login) {
    const btn = el("button", "secondary-btn small", "扫码登录");
    btn.type = "button";
    btn.addEventListener("click", () => quarkLogin());
    quotaNote.appendChild(btn);
  }
  quotaNote.hidden = false;
}

function hideQuotaNote() {
  quotaNote.hidden = true;
}

function showBanned(text) {
  meState.banned = text;
  banBanner.innerHTML = "";
  banBanner.append(el("strong", "", "无法使用搜索"), el("span", "", text));
  banBanner.hidden = false;
  submitBtn.disabled = true;
  input.disabled = true;
}

async function loadMe() {
  try {
    const resp = await fetch("/api/me");
    if (!resp.ok) return;
    const me = await resp.json();
    Object.assign(meState, me);
    saveStatus.login = !!me.login;
    saveStatus.logged_in = !!me.logged_in;
    saveStatus.nickname = me.nickname;
    renderAccount();
    renderQuota(me.quota);
    if (me.banned) showBanned(me.banned);
  } catch (_) { /* 网络问题：保持现状 */ }
}

loadMe();


// ---- 首页：记忆库统计 ----
fetch("/api/memory/stats")
  .then((r) => r.json())
  .then((d) => {
    if (!d.enabled || !d.valid) return;
    const line = document.getElementById("memory-stats");
    line.innerHTML = "";
    line.append("已为大家验证并记住 ", el("b", "", String(d.valid)), " 条有效链接 · 累计搜索 ",
      el("b", "", String(d.searches || 0)), " 次");
    line.hidden = false;
  })
  .catch(() => {});


// ---- 界面细节：主题、快捷键、滚动 ----
const themeBtn = document.getElementById("theme-btn");
const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

themeBtn.addEventListener("click", () => {
  const current = document.documentElement.dataset.theme || (darkQuery.matches ? "dark" : "light");
  const next = current === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("qp_theme", next); } catch (_) { /* 忽略 */ }
  themeBtn.classList.toggle("spin");
});

// 按 / 聚焦搜索框（输入框里打字时不拦截）
document.addEventListener("keydown", (e) => {
  if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
  const tag = (document.activeElement && document.activeElement.tagName) || "";
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
  e.preventDefault();
  input.focus();
  input.select();
});

const toTop = document.getElementById("to-top");
toTop.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));

let scrollTicking = false;
window.addEventListener("scroll", () => {
  if (scrollTicking) return;
  scrollTicking = true;
  requestAnimationFrame(() => {
    document.body.classList.toggle("scrolled", window.scrollY > 8);
    document.body.classList.toggle("scrolled-far", window.scrollY > 600);
    scrollTicking = false;
  });
}, { passive: true });

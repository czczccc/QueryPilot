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
    if (hideDead && (l.state === "invalid" || l.relevance === "mismatch")) return false;
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

function toast(text, kind) {
  const t = el("div", "toast " + (kind || "ok"));
  t.setAttribute("role", kind === "error" ? "alert" : "status");
  t.append(el("span", "toast-ico", kind === "error" ? "!" : "✓"), el("span", "", text));
  toastsEl.appendChild(t);
  while (toastsEl.children.length > 3) toastsEl.firstChild.remove();
  setTimeout(() => {
    t.classList.add("leaving");
    setTimeout(() => t.remove(), 260);
  }, kind === "error" ? 4000 : 2200);
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
  const valid = links.filter((l) => l.state === "valid" && l.relevance !== "mismatch").length;
  resultCount.innerHTML = "";
  resultCount.append("显示 ", el("b", "", String(visible.length)), " / " + links.length +
    " 条，其中有效 " + valid + " 条");
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

  if (visible.length === 0) {
    renderEmpty("当前筛选下没有链接", "共有 " + links.length + " 条结果被筛掉了。可以关掉「只看有效且相关」或调低清晰度要求再看看。");
  } else {
    emptyState.hidden = true;
  }

  visible.forEach((l, i) => {
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

    resultList.appendChild(li);
  });
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
};

const TOOL_TEXT = {
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

  if (data.links.length === 0) {
    resultList.innerHTML = "";
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

function doSearch(query, refresh = false, followupOf = null) {
  lastQuery = query;
  hideFormError();
  resultsSection.hidden = true;
  if (currentSource) currentSource.close();
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
  const source = new EventSource(url);
  currentSource = source;
  let finished = false;

  const done = () => {
    finished = true;
    source.close();
    clearTimeout(timer);
    setBusy(false);
  };
  const timer = setTimeout(() => {
    if (finished) return;
    done();
    showStatus("搜索超时（超过 150 秒），请稍后重试或换一个更精确的资源名。", "error");
  }, 150000);

  source.addEventListener("step", (e) => renderStep(JSON.parse(e.data)));
  source.addEventListener("result", (e) => {
    done();
    hideStatus();
    renderResult(JSON.parse(e.data));
  });
  source.addEventListener("error", (e) => {
    if (finished) return;
    done();
    agentSteps.querySelectorAll(".pending").forEach((p) => p.remove());
    setText(agentTitle, "搜索中断");
    let detail = "搜索失败或请求过于频繁，请稍后重试。";
    if (e.data) {
      try { detail = JSON.parse(e.data).detail || detail; } catch (_) { /* 保持默认提示 */ }
    }
    showStatus(detail, "error");
  });
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

document.querySelectorAll(".example").forEach((btn) => {
  btn.addEventListener("click", () => {
    input.value = btn.dataset.query;
    doSearch(btn.dataset.query);
  });
});


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
const subsPanel = document.getElementById("subs-panel");
const subsList = document.getElementById("subs-list");
const notifList = document.getElementById("notif-list");
const subsUnread = document.getElementById("subs-unread");
const subscribeBtn = document.getElementById("subscribe-btn");
let lastResult = null;

function cidParam() {
  return "client_id=" + encodeURIComponent(clientId);
}

function formatTime(ts) {
  const d = new Date(ts * 1000);
  return (d.getMonth() + 1) + "/" + d.getDate() + " " +
    String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
}

async function loadSubs() {
  if (!clientId) return;
  try {
    const [subsResp, notifResp] = await Promise.all([
      fetch("/api/subscriptions?" + cidParam()),
      fetch("/api/notifications?" + cidParam()),
    ]);
    if (!subsResp.ok || !notifResp.ok) return; // 记忆未开启：不显示订阅
    const subs = await subsResp.json();
    const notes = await notifResp.json();
    subsPanel.hidden = false;
    if (lastResult) subscribeBtn.hidden = false;

    const unread = notes.filter((n) => !n.read).length;
    subsUnread.hidden = unread === 0;
    setText(subsUnread, unread + " 条新提醒");

    notifList.innerHTML = "";
    notes.slice(0, 10).forEach((n) => {
      const li = el("li", n.read ? "" : "unread-item", formatTime(n.ts) + "　" + n.message);
      notifList.appendChild(li);
    });

    subsList.innerHTML = "";
    if (subs.length === 0) subsList.appendChild(el("li", "muted", "还没有订阅。"));
    subs.forEach((sub) => {
      const li = el("li");
      const res = RES_LABEL[sub.best_resolution] || "";
      const text = el("span", "", "《" + sub.resource + "》" +
        (sub.best_episodes ? "　已见 " + sub.best_episodes + " 集" : "") +
        (res ? "　最高 " + res : "") +
        "　" + (sub.last_checked ? "上次检查 " + formatTime(sub.last_checked) : "尚未检查"));
      const del = el("button", "secondary-btn small", "取消");
      del.type = "button";
      del.addEventListener("click", async () => {
        del.disabled = true;
        await fetch("/api/subscriptions/" + sub.id + "?" + cidParam(), { method: "DELETE" }).catch(() => {});
        toast("已取消订阅《" + sub.resource + "》");
        loadSubs();
      });
      li.append(text, del);
      subsList.appendChild(li);
    });
  } catch (_) { /* 网络问题：下次再试 */ }
}

subscribeBtn.addEventListener("click", async () => {
  if (!lastResult) return;
  subscribeBtn.disabled = true;
  try {
    const resp = await fetch("/api/subscriptions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        client_id: clientId,
        query: lastResult.history && lastResult.history.length ? lastResult.history[0] : lastResult.query,
        resource: lastResult.parsed.resource,
      }),
    });
    if (resp.ok) {
      setText(subscribeBtn, "已订阅 ✓");
      subscribeBtn.classList.add("done");
      toast("已订阅《" + lastResult.parsed.resource + "》，有更新会在「我的订阅」提醒");
      loadSubs();
    } else {
      const body = await resp.json().catch(() => ({}));
      setText(subscribeBtn, "订阅更新");
      toast(body.detail || "订阅失败", "error");
      subscribeBtn.disabled = false;
    }
  } catch (_) {
    toast("订阅失败", "error");
    subscribeBtn.disabled = false;
  }
});

subsPanel.addEventListener("toggle", async () => {
  if (!subsPanel.open || subsUnread.hidden) return;
  await fetch("/api/notifications/read?" + cidParam(), { method: "POST" }).catch(() => {});
  subsUnread.hidden = true;
});

loadSubs();
setInterval(loadSubs, 5 * 60 * 1000);


// ---- 一键转存 ----
// 优先扫码登录自己的夸克（凭证加密存在服务器，浏览器只拿一个 HttpOnly 会话）；
// 部署者在 .env 配了自己的 cookie 时，也可以凭口令存到部署者的网盘。
let saveStatus = { enabled: false, login: false, logged_in: false, token_mode: false };

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

// 扫码登录弹窗：显示二维码并轮询，成功返回 true，关闭或过期返回 false
function quarkLogin() {
  return new Promise(async (resolve) => {
    const dlg = document.createElement("dialog");
    dlg.className = "qr-dialog";
    const title = document.createElement("p");
    setText(title, "用夸克 App 扫码登录，转存会保存到你自己的网盘");
    const box = document.createElement("div");
    box.className = "qr-box";
    const tip = document.createElement("p");
    tip.className = "qr-tip";
    setText(tip, "正在获取二维码…");
    const close = document.createElement("button");
    close.type = "button";
    close.className = "secondary-btn small";
    setText(close, "取消");
    dlg.append(title, box, tip, close);
    document.body.appendChild(dlg);
    let timer = null;
    const finish = (ok) => {
      clearInterval(timer);
      dlg.close();
      dlg.remove();
      resolve(ok);
    };
    close.addEventListener("click", () => finish(false));
    dlg.addEventListener("cancel", () => finish(false));
    dlg.showModal();
    try {
      const resp = await fetch("/api/quark/login", { method: "POST" });
      const data = await resp.json();
      if (!resp.ok) { setText(tip, data.detail || "获取二维码失败"); return; }
      box.innerHTML = data.qr_svg || ""; // 服务器生成的二维码 SVG
      setText(tip, "扫码后在手机上确认登录");
      timer = setInterval(async () => {
        try {
          const r = await (await fetch("/api/quark/login/" + encodeURIComponent(data.login_id))).json();
          if (r.status === "success") {
            saveStatus.logged_in = true;
            saveStatus.nickname = r.nickname;
            finish(true);
          } else if (r.status !== "waiting") {
            clearInterval(timer);
            setText(tip, r.message || "二维码已过期，请关闭后重试");
          }
        } catch (_) { /* 网络抖动：下次再试 */ }
      }, 2000);
    } catch (_) {
      setText(tip, "获取二维码失败，请稍后重试");
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
      if (!ok && /重新扫码|先扫码/.test(body.message || body.detail || "")) saveStatus.logged_in = false;
      setText(btn, ok ? "已转存 ✓" : "转存失败");
      btn.classList.toggle("done", ok);
      toast(body.message || body.detail || (ok ? "已转存" : "转存失败"), ok ? "ok" : "error");
      if (!ok) btn.disabled = false;
    } catch (_) {
      setText(btn, "转存失败");
      btn.disabled = false;
    }
  });
  return btn;
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

fetch("/api/save/status")
  .then((r) => r.json())
  .then((d) => { saveStatus = d; saveEnabled = !!d.enabled; })
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

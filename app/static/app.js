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
const copyBtn = document.getElementById("copy-btn");
const resultList = document.getElementById("result-list");
const emptyState = document.getElementById("empty-state");
const agentPanel = document.getElementById("agent-panel");
const agentTitle = document.getElementById("agent-title");
const agentSteps = document.getElementById("agent-steps");

let currentLinks = [];
let hideDead = true;
let minRes = "";
let lastQuery = "";

const RES_RANK = { SD: 1, "720p": 2, "1080p": 3, "2160p": 4 };
const RES_LABEL = { SD: "标清", "720p": "720p", "1080p": "1080p", "2160p": "4K" };

function visibleLinks(links) {
  return links.filter((l) => {
    if (hideDead && l.state === "invalid") return false;
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

function qualityBadges(q) {
  const out = [];
  const add = (text, cls, title) => {
    const b = document.createElement("span");
    b.className = "badge " + cls;
    setText(b, text);
    if (title) b.title = title;
    out.push(b);
  };
  if (!q) return out;
  if (q.resolution) {
    add(RES_LABEL[q.resolution] + (q.resolution_guessed ? "?" : ""), "badge-res",
      q.resolution_guessed ? "由文件体积推断" : "");
  }
  if (q.hdr) add("HDR", "badge-res");
  if (q.source) add(q.source, "badge-tag");
  if (q.low_quality) add("疑似枪版", "badge-dead");
  if (q.has_subtitle) add("字幕", "badge-tag");
  if (q.video_count > 1) add(q.video_count + " 个视频", "badge-tag");
  const size = formatSize(q.size_bytes);
  if (size) add(size, "badge-tag");
  return out;
}

function setText(el, text) {
  el.textContent = text;
}

function showStatus(text, kind) {
  statusEl.hidden = false;
  statusEl.textContent = text;
  statusEl.dataset.kind = kind || "info";
}

function hideStatus() {
  statusEl.hidden = true;
  statusEl.textContent = "";
}

function showFormError(text) {
  formError.textContent = text;
  formError.hidden = false;
  input.focus();
}

function hideFormError() {
  formError.textContent = "";
  formError.hidden = true;
}

function renderParsed(parsed, douban) {
  parsedCard.hidden = false;
  parsedCard.innerHTML = "";
  const h = document.createElement("h2");
  setText(h, "资源识别");
  parsedCard.appendChild(h);

  const p = document.createElement("p");
  const bits = [parsed.resource];
  if (parsed.quality) bits.push("清晰度: " + parsed.quality);
  if (parsed.english_name) bits.push("英文名: " + parsed.english_name);
  if (parsed.aliases && parsed.aliases.length) bits.push("别名: " + parsed.aliases.join("、"));
  setText(p, bits.join("　|　"));
  parsedCard.appendChild(p);

  if (douban) {
    const d = document.createElement("p");
    setText(d, "来源：豆瓣链接 → " + douban.title +
      (douban.year ? " (" + douban.year + ")" : "") +
      (douban.kind ? " · " + douban.kind : ""));
    parsedCard.appendChild(d);
  }

  const v = document.createElement("p");
  setText(v, "搜索查询：" + parsed.search_suggestions.join(" ｜ "));
  parsedCard.appendChild(v);
}

function renderMetrics(metrics, providers) {
  metricsEl.hidden = false;
  metricsEl.innerHTML = "";
  const parts = [
    "总耗时 " + metrics.duration_ms + "ms",
    "原始 " + metrics.raw_result_count + " 条",
    "去重后 " + metrics.deduplicated_result_count + " 条",
    providers.map((p) => p.name + ": " + p.status).join("；"),
  ];
  if (metrics.fallback_used) parts.push("已使用基础查询（AI 解析暂不可用）");
  if (metrics.memory_hits) parts.push("记忆复用 " + metrics.memory_hits + " 条");
  if (metrics.skipped_invalid) parts.push("跳过已知失效 " + metrics.skipped_invalid + " 条");
  const p = document.createElement("p");
  setText(p, parts.join("　·　"));
  metricsEl.appendChild(p);

  if (metrics.served_from_memory) {
    const note = document.createElement("p");
    setText(note, "这些结果来自之前搜索并验证过的记忆，已跳过全网搜索。");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "secondary-btn small";
    setText(btn, "重新全网搜索");
    btn.addEventListener("click", () => doSearch(lastQuery, true));
    note.appendChild(document.createTextNode(" "));
    note.appendChild(btn);
    metricsEl.appendChild(note);
  }
}

function renderLinks(links) {
  currentLinks = links;
  const visible = visibleLinks(links);
  resultList.innerHTML = "";
  for (const l of visible) {
    const li = document.createElement("li");
    li.className = "result-card";

    const head = document.createElement("div");
    head.className = "link-head";

    const name = document.createElement("strong");
    setText(name, l.name);
    head.appendChild(name);

    const conf = document.createElement("span");
    conf.className = "badge badge-" + (l.conf === "高" ? "high" : l.conf === "低" ? "low" : "mid");
    setText(conf, l.conf + "置信");
    head.appendChild(conf);

    const stateBadge = document.createElement("span");
    const stateMap = {
      valid: ["有效", "ok"],
      invalid: ["已失效", "dead"],
      unknown: ["待确认", "unknown"],
    };
    const [stateText, stateCls] = stateMap[l.state] || ["待确认", "unknown"];
    stateBadge.className = "badge badge-" + stateCls;
    setText(stateBadge, stateText);
    head.appendChild(stateBadge);
    for (const b of qualityBadges(l.quality)) head.appendChild(b);
    if (l.from_memory) {
      const mem = document.createElement("span");
      mem.className = "badge badge-tag";
      setText(mem, "记忆");
      if (l.last_checked) {
        mem.title = "上次验证：" + new Date(l.last_checked * 1000).toLocaleString();
      }
      head.appendChild(mem);
    }
    li.appendChild(head);

    const url = document.createElement("a");
    url.className = "share-url";
    url.href = "https://pan.quark.cn/s/" + l.share;
    url.target = "_blank";
    url.rel = "noopener noreferrer";
    setText(url, "https://pan.quark.cn/s/" + l.share);
    li.appendChild(url);

    const meta = document.createElement("div");
    meta.className = "meta";
    const pwdTxt = l.pwd ? "提取码: " + l.pwd : "无提取码";
    setText(meta, pwdTxt + "　|　" + l.time + "　|　来源: " + l.source);
    li.appendChild(meta);

    if (l.state === "invalid") {
      li.classList.add("result-dead");
    }

    const row = document.createElement("div");
    row.className = "row-actions";
    const copyOne = document.createElement("button");
    copyOne.type = "button";
    copyOne.className = "secondary-btn small";
    setText(copyOne, "复制");
    copyOne.addEventListener("click", () => {
      const link = "https://pan.quark.cn/s/" + l.share + (l.pwd ? "?pwd=" + l.pwd : "");
      navigator.clipboard.writeText(link).then(() => {
        showStatus("已复制: " + link, "info");
        setTimeout(hideStatus, 2000);
      });
    });
    row.appendChild(copyOne);
    li.appendChild(row);

    resultList.appendChild(li);
  }
}

copyBtn.addEventListener("click", () => {
  const visible = visibleLinks(currentLinks);
  const lines = visible.map((l) => {
    const link = "https://pan.quark.cn/s/" + l.share + (l.pwd ? "?pwd=" + l.pwd : "");
    return [l.name, link, l.pwd || "-", l.time, l.conf, l.state === "valid" ? "有效" : "失效"].join("\t");
  });
  navigator.clipboard.writeText(lines.join("\n")).then(() => {
    showStatus("已复制 " + lines.length + " 条链接", "info");
    setTimeout(hideStatus, 2000);
  });
});

document.getElementById("hide-dead").addEventListener("change", (e) => {
  hideDead = e.target.checked;
  if (currentLinks.length) renderLinks(currentLinks);
});

document.getElementById("min-res").addEventListener("change", (e) => {
  minRes = e.target.value;
  if (currentLinks.length) renderLinks(currentLinks);
});

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
    (o.invalid || 0) + "、待确认 " + (o.unknown || 0) + "；满足要求累计 " +
    (o.matching_total || 0) + " 条",
  finish: (a) => "结束：" + (a.reason || ""),
};

function renderStep(step) {
  const pending = agentSteps.querySelector(".pending");
  if (pending) pending.remove();
  const li = document.createElement("li");
  const fn = TOOL_TEXT[step.tool];
  const obs = step.observation || {};
  setText(li, obs.error ? step.tool + " 出错：" + obs.error : fn ? fn(step.args || {}, obs) : step.tool);
  const took = document.createElement("span");
  took.className = "took";
  setText(took, (step.duration_ms / 1000).toFixed(1) + "s");
  li.appendChild(took);
  if (step.thought) {
    const t = document.createElement("span");
    t.className = "thought";
    setText(t, "💭 " + step.thought);
    li.appendChild(t);
  }
  agentSteps.appendChild(li);
  if (step.tool !== "finish") {
    const next = document.createElement("li");
    next.className = "pending";
    setText(next, "…正在决定下一步");
    agentSteps.appendChild(next);
  }
}

function renderResult(data) {
  const pending = agentSteps.querySelector(".pending");
  if (pending) pending.remove();
  setText(agentTitle, "搜索过程（" + (data.planner === "llm" ? "AI 规划" : "规则规划") +
    "，" + data.steps.length + " 步）· " + data.stop_reason);

  renderParsed(data.parsed, data.douban);
  renderMetrics(data.metrics, data.providers);

  if (data.links.length === 0) {
    resultList.innerHTML = "";
    linkActions.hidden = true;
    emptyState.hidden = false;
    setText(emptyState, "没有找到夸克网盘链接。建议换一种写法（别名、英文名、加 4K/全集 等）后重试。");
  } else {
    emptyState.hidden = true;
    renderLinks(data.links);
    linkActions.hidden = false;
  }
  resultsSection.hidden = false;
}

let currentSource = null;

function doSearch(query, refresh = false) {
  lastQuery = query;
  hideFormError();
  resultsSection.hidden = true;
  if (currentSource) currentSource.close();

  agentPanel.hidden = false;
  agentSteps.innerHTML = "";
  setText(agentTitle, "搜索过程");
  const first = document.createElement("li");
  first.className = "pending";
  setText(first, "…正在理解你要找的资源");
  agentSteps.appendChild(first);
  showStatus("agent 正在搜索并验证链接，通常需要 20~90 秒，过程会实时显示在下方", "loading");
  submitBtn.disabled = true;

  const url = "/api/agent/stream?query=" + encodeURIComponent(query) + (refresh ? "&refresh=true" : "");
  const source = new EventSource(url);
  currentSource = source;
  let finished = false;

  const done = () => {
    finished = true;
    source.close();
    clearTimeout(timer);
    submitBtn.disabled = false;
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
    agentSteps.querySelectorAll(".pending").forEach((el) => el.remove());
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
  doSearch(q);
});

document.querySelectorAll(".example").forEach((btn) => {
  btn.addEventListener("click", () => {
    input.value = btn.dataset.query;
    doSearch(btn.dataset.query);
  });
});

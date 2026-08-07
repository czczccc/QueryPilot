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

let currentLinks = [];

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

function renderParsed(parsed) {
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
  const p = document.createElement("p");
  setText(p, parts.join("　·　"));
  metricsEl.appendChild(p);
}

function renderLinks(links) {
  currentLinks = links;
  resultList.innerHTML = "";
  for (const l of links) {
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
    const httpTxt = l.http === 200 ? "✓ 可达" : (l.http ? "HTTP " + l.http : "✗ 不可达");
    setText(meta, pwdTxt + "　|　" + httpTxt + "　|　" + l.time + "　|　来源: " + l.source);
    li.appendChild(meta);

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
  const lines = currentLinks.map((l) => {
    const link = "https://pan.quark.cn/s/" + l.share + (l.pwd ? "?pwd=" + l.pwd : "");
    return [l.name, link, l.pwd || "-", l.time, l.conf, l.http === 200 ? "可达" : "不可达"].join("\t");
  });
  navigator.clipboard.writeText(lines.join("\n")).then(() => {
    showStatus("已复制 " + lines.length + " 条链接", "info");
    setTimeout(hideStatus, 2000);
  });
});

async function doSearch(query) {
  hideFormError();
  resultsSection.hidden = true;
  showStatus("正在解析资源、搜索网盘链接并验证可达性…通常需要 20~60 秒，请耐心等待", "loading");
  submitBtn.disabled = true;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 120000);

  try {
    const resp = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
      signal: controller.signal,
    });

    if (resp.status === 422) {
      showFormError("输入不合法：长度需为 2–200 个字符。");
      hideStatus();
      return;
    }
    if (resp.status === 503) {
      hideStatus();
      showStatus("所有搜索源暂不可用，请稍后重试。", "error");
      return;
    }
    if (!resp.ok) {
      hideStatus();
      showStatus("搜索失败（HTTP " + resp.status + "），请稍后重试。", "error");
      return;
    }

    const data = await resp.json();
    hideStatus();

    renderParsed(data.parsed);
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
  } catch (err) {
    hideStatus();
    if (err.name === "AbortError") {
      showStatus("搜索超时（超过 120 秒），请稍后重试或换一个更精确的资源名。", "error");
    } else {
      showStatus("网络错误，无法连接服务，请稍后重试。", "error");
    }
  } finally {
    clearTimeout(timer);
    submitBtn.disabled = false;
  }
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

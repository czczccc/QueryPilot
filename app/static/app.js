"use strict";

const form = document.getElementById("search-form");
const input = document.getElementById("query");
const submitBtn = document.getElementById("submit-btn");
const formError = document.getElementById("form-error");
const statusEl = document.getElementById("status");
const resultsSection = document.getElementById("results");
const intentCard = document.getElementById("intent-card");
const metricsEl = document.getElementById("metrics");
const resultList = document.getElementById("result-list");
const emptyState = document.getElementById("empty-state");

/** 在文本节点安全插入，避免渲染第三方 HTML。 */
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

function renderIntent(intent) {
  intentCard.hidden = false;
  intentCard.innerHTML = "";
  const title = document.createElement("h2");
  setText(title, "系统理解");
  intentCard.appendChild(title);

  const typeLine = document.createElement("p");
  setText(typeLine, "资源类型：" + intent.resource_type + "　关键词：" + intent.keywords.join("、"));
  intentCard.appendChild(typeLine);

  const variants = document.createElement("p");
  setText(variants, "搜索查询：" + intent.query_variants.join(" ｜ "));
  intentCard.appendChild(variants);
}

function renderMetrics(metrics, providers) {
  metricsEl.hidden = false;
  metricsEl.innerHTML = "";
  const parts = [
    "总耗时 " + metrics.duration_ms + "ms",
    "原始结果 " + metrics.raw_result_count + " 条",
    "去重后 " + metrics.deduplicated_result_count + " 条",
    providers.map((p) => p.name + ": " + p.status).join("；"),
  ];
  if (metrics.fallback_used) {
    parts.push("已使用基础查询（AI 解析暂不可用）");
  }
  const p = document.createElement("p");
  setText(p, parts.join("　·　"));
  metricsEl.appendChild(p);
}

function renderResults(results) {
  resultList.innerHTML = "";
  for (const r of results) {
    const li = document.createElement("li");
    li.className = "result-card";

    const link = document.createElement("a");
    link.href = r.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    setText(link, r.title);
    li.appendChild(link);

    const snippet = document.createElement("p");
    setText(snippet, r.snippet || "（无摘要）");
    li.appendChild(snippet);

    const meta = document.createElement("div");
    meta.className = "meta";
    setText(meta,
      "相关度 " + r.score + "/100　来源: " + r.sources.join("+") + "　" + r.reason);
    li.appendChild(meta);

    resultList.appendChild(li);
  }
}

async function doSearch(query) {
  hideFormError();
  resultsSection.hidden = true;
  showStatus("正在理解需求、生成查询、检索并整理结果…", "loading");
  submitBtn.disabled = true;

  try {
    const resp = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
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

    renderIntent(data.intent);
    renderMetrics(data.metrics, data.providers);

    const partial = data.providers.some((p) => p.status === "error");
    if (data.results.length === 0) {
      resultList.innerHTML = "";
      emptyState.hidden = false;
      emptyState.textContent = partial
        ? "没有找到结果，且部分搜索源不可用。建议简化约束或更换关键词后重试。"
        : "没有找到结果。建议简化约束、更换关键词或换一种表述。";
    } else {
      emptyState.hidden = true;
      renderResults(data.results);
      if (partial) {
        showStatus("部分搜索源暂不可用，以下为可用来源的结果。", "warn");
      }
    }
    resultsSection.hidden = false;
  } catch (err) {
    hideStatus();
    showStatus("网络错误，无法连接服务，请稍后重试。", "error");
  } finally {
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

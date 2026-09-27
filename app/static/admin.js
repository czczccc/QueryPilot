"use strict";

// 站长后台：所有请求带 X-Admin-Token；口令只存在 sessionStorage（关掉标签页就忘）

const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "qp_admin_token";

function getToken() {
  try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch (_) { return memToken; }
}
let memToken = "";
function setToken(t) {
  memToken = t;
  try {
    if (t) sessionStorage.setItem(TOKEN_KEY, t); else sessionStorage.removeItem(TOKEN_KEY);
  } catch (_) { /* 存储不可用：只放内存 */ }
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function toast(text, kind) {
  const t = el("div", "toast " + (kind || "ok"));
  t.append(el("span", "toast-ico", kind === "error" ? "!" : "✓"), el("span", "", text));
  $("toasts").appendChild(t);
  setTimeout(() => { t.classList.add("leaving"); setTimeout(() => t.remove(), 260); }, kind === "error" ? 4000 : 2200);
}

class AuthError extends Error {}

async function api(path, opts = {}) {
  const headers = Object.assign({ "X-Admin-Token": getToken() }, opts.body ? { "Content-Type": "application/json" } : {});
  const resp = await fetch("/api/admin" + path, {
    method: opts.method || "GET",
    headers,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await resp.json().catch(() => ({}));
  if (resp.status === 401) throw new AuthError(data.detail || "管理口令不正确");
  if (resp.status === 404 && path === "/overview") throw new AuthError("服务器没有配置 ADMIN_TOKEN，后台未开启");
  if (!resp.ok) throw new Error(typeof data.detail === "string" ? data.detail : "请求失败（" + resp.status + "）");
  return data;
}

// ---- 格式化 ----
const nf = new Intl.NumberFormat("zh-CN");
function num(n) { return n == null ? "-" : nf.format(n); }
function tokens(n) {
  if (!n) return "0";
  if (n >= 1e6) return "约 " + (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return "约 " + (n / 1e3).toFixed(1) + "k";
  return "约 " + n;
}
function ts(sec) {
  if (!sec) return "-";
  const d = new Date(sec * 1000);
  const pad = (x) => String(x).padStart(2, "0");
  return (d.getMonth() + 1) + "/" + d.getDate() + " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
}
function subjectLabel(row) {
  const s = row.subject || "";
  if (s === "system") return "后台任务";
  if (s.startsWith("ip:")) return "未登录 " + s.slice(3);
  return row.nickname || s.replace(/^user:/, "");
}

// ---- 表格 ----
function renderTable(table, cols, rows, empty) {
  table.innerHTML = "";
  const thead = el("thead");
  const tr = el("tr");
  cols.forEach((c) => {
    const th = el("th", c.num ? "num" : "", c.label);
    tr.appendChild(th);
  });
  thead.appendChild(tr);
  const tbody = el("tbody");
  if (!rows.length) {
    const r = el("tr");
    const td = el("td", "empty-cell", empty || "暂无数据");
    td.colSpan = cols.length;
    r.appendChild(td);
    tbody.appendChild(r);
  }
  rows.forEach((row) => {
    const r = el("tr");
    cols.forEach((c) => {
      const td = el("td", c.num ? "num" : "");
      td.dataset.label = c.label;
      const v = c.render ? c.render(row) : row[c.key];
      if (v instanceof Node) td.appendChild(v); else td.textContent = v == null ? "-" : String(v);
      r.appendChild(td);
    });
    tbody.appendChild(r);
  });
  table.append(thead, tbody);
}

function pill(text, kind) { return el("span", "pill " + (kind || ""), text); }
function btn(text, cls, onClick) {
  const b = el("button", "secondary-btn small " + (cls || ""), text);
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
}
function group(...nodes) {
  const g = el("span", "btn-group");
  g.append(...nodes);
  return g;
}

// ---- 概览 ----
function kpi(label, value, sub) {
  const k = el("div", "kpi");
  k.append(el("span", "kpi-label", label), el("span", "kpi-value", value));
  if (sub) k.appendChild(el("span", "kpi-sub", sub));
  return k;
}

function renderTrend(trend) {
  const box = $("trend");
  box.innerHTML = "";
  if (!trend || !trend.length) {
    box.appendChild(el("p", "muted small", "还没有数据"));
    return;
  }
  const max = Math.max(1, ...trend.map((d) => d.searches || 0));
  trend.forEach((d) => {
    const col = el("div", "bar-col");
    col.title = d.day + "：搜索 " + num(d.searches) + " 次，AI 搜索 " + num(d.llm_searches) + " 次，" + tokens(d.tokens) + " tokens";
    const bar = el("div", "bar");
    const h = Math.round(((d.searches || 0) / max) * 100);
    bar.style.height = Math.max(h, d.searches ? 3 : 0) + "%";
    const ai = el("div", "bar-ai");
    ai.style.height = Math.round(((d.llm_searches || 0) / max) * 100) + "%";
    const wrap = el("div", "bar-wrap");
    wrap.append(bar, ai);
    col.append(el("span", "bar-num", d.searches ? num(d.searches) : ""), wrap, el("span", "bar-day", (d.day || "").slice(5)));
    box.appendChild(col);
  });
}

const LIMIT_LABEL = {
  anon_daily_ai: ["未登录每天 AI 搜索", "次"],
  anon_daily_searches: ["未登录每天搜索", "次"],
  user_daily_ai: ["登录用户每天 AI 搜索", "次"],
  ip_daily_searches: ["每个 IP 每天搜索上限", "次"],
  site_daily_tokens: ["全站每天 token 预算", ""],
};

async function loadOverview() {
  const d = await api("/overview");
  const t = d.today || {};
  const k = $("kpis");
  k.innerHTML = "";
  k.append(
    kpi("今日搜索", num(t.searches), d.day),
    kpi("今日 AI 搜索", num(t.llm_searches), "LLM 调用 " + num(t.llm_calls) + " 次"),
    kpi("今日 tokens", tokens(t.tokens), "拿不到接口用量时为估算"),
    kpi("账号总数", num(d.users), "扫码登录过的夸克账号"),
  );
  renderTrend(d.trend);
  renderTable($("top-table"), [
    { label: "身份", render: (r) => {
      const s = el("span", "", subjectLabel(r));
      if (r.banned) s.appendChild(pill("已停用", "danger"));
      return s;
    } },
    { label: "搜索", key: "searches", num: true },
    { label: "AI", key: "llm_searches", num: true },
    { label: "tokens", render: (r) => tokens(r.tokens), num: true },
  ], d.top || [], "今天还没有人搜索");
  const dl = $("limits");
  dl.innerHTML = "";
  if (!d.limits) {
    dl.appendChild(el("p", "muted small", "额度功能未开启"));
    return;
  }
  Object.entries(d.limits).forEach(([key, v]) => {
    const [label, unit] = LIMIT_LABEL[key] || [key, ""];
    dl.append(el("dt", "", label), el("dd", "", v === 0 ? "不限" : (key.includes("tokens") ? num(v) : num(v) + " " + unit)));
  });
}

// ---- 账号 ----
const PAGE = 50;
let userOffset = 0;

async function banUser(u) {
  const reason = window.prompt("停用「" + (u.nickname || u.user_id) + "」的原因（会显示给对方，可留空）", "");
  if (reason === null) return;
  await api("/users/" + encodeURIComponent(u.user_id) + "/ban", { method: "POST", body: { reason } });
  toast("已停用 " + (u.nickname || u.user_id));
  loadUsers();
}

async function unbanUser(u) {
  await api("/users/" + encodeURIComponent(u.user_id) + "/unban", { method: "POST" });
  toast("已恢复 " + (u.nickname || u.user_id));
  loadUsers();
}

async function limitUser(u) {
  const cur = u.ai_limit == null ? "" : String(u.ai_limit);
  const v = window.prompt("给「" + (u.nickname || u.user_id) + "」单独设每日 AI 搜索次数\n留空恢复默认，0 表示不限", cur);
  if (v === null) return;
  const t = v.trim();
  if (t !== "" && !/^\d+$/.test(t)) { toast("请输入非负整数", "error"); return; }
  await api("/users/" + encodeURIComponent(u.user_id) + "/limit", { method: "PUT", body: { ai_limit: t === "" ? null : Number(t) } });
  toast("已更新额度");
  loadUsers();
}

function safe(fn) {
  return (...args) => fn(...args).catch((e) => handleError(e));
}

async function loadUsers() {
  const q = $("user-q").value.trim();
  const rows = await api("/users?limit=" + PAGE + "&offset=" + userOffset + (q ? "&q=" + encodeURIComponent(q) : ""));
  renderTable($("users-table"), [
    { label: "账号", render: (u) => {
      const s = el("span", "user-cell");
      s.append(el("b", "", u.nickname || "（无昵称）"), el("small", "muted", u.user_id));
      return s;
    } },
    { label: "状态", render: (u) => u.banned ? pill("已停用" + (u.ban_reason ? "：" + u.ban_reason : ""), "danger") : pill("正常", "ok") },
    { label: "最近活跃", render: (u) => ts(u.last_seen) },
    { label: "今日搜索", key: "today_searches", num: true },
    { label: "累计搜索", key: "total_searches", num: true },
    { label: "累计 tokens", render: (u) => tokens(u.total_tokens), num: true },
    { label: "AI 额度", render: (u) => u.ai_limit == null ? "默认" : (u.ai_limit === 0 ? "不限" : u.ai_limit + " 次/天") },
    { label: "邀请码", render: (u) => u.invite_code || "-" },
    { label: "操作", render: (u) => group(
      btn("改额度", "", safe(() => limitUser(u))),
      u.banned ? btn("恢复", "", safe(() => unbanUser(u))) : btn("停用", "danger", safe(() => banUser(u))),
    ) },
  ], rows, q ? "没有匹配的账号" : "还没有账号");
  $("users-prev").disabled = userOffset === 0;
  $("users-next").disabled = rows.length < PAGE;
  $("users-page").textContent = "第 " + (userOffset / PAGE + 1) + " 页";
}

// ---- 每日用量 ----
function todayStr() {
  const d = new Date();
  const pad = (x) => String(x).padStart(2, "0");
  return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
}

async function loadUsage() {
  const day = $("usage-day").value;
  const d = await api("/usage?limit=200" + (day ? "&day=" + day : ""));
  const s = d.site || {};
  const box = $("usage-site");
  box.innerHTML = "";
  box.append(kpi("搜索", num(s.searches), d.day), kpi("AI 搜索", num(s.llm_searches)),
    kpi("LLM 调用", num(s.llm_calls)), kpi("tokens", tokens(s.tokens)));
  renderTable($("usage-table"), [
    { label: "身份", render: (r) => {
      const sp = el("span", "", subjectLabel(r));
      if (r.banned) sp.appendChild(pill("已停用", "danger"));
      return sp;
    } },
    { label: "类型", render: (r) => (r.subject || "").startsWith("ip:") ? "未登录" : r.subject === "system" ? "系统" : "账号" },
    { label: "搜索", key: "searches", num: true },
    { label: "AI 搜索", key: "llm_searches", num: true },
    { label: "LLM 调用", key: "llm_calls", num: true },
    { label: "tokens", render: (r) => tokens(r.tokens), num: true },
    { label: "操作", render: (r) => (r.subject || "").startsWith("ip:")
      ? btn("封此 IP", "danger", safe(async () => {
        const ip = r.subject.slice(3);
        const reason = window.prompt("封禁 " + ip + " 的原因（可留空）", "");
        if (reason === null) return;
        await api("/bans", { method: "POST", body: { ip, reason } });
        toast("已封禁 " + ip);
      }))
      : "" },
  ], d.rows || [], "这一天没有用量记录");
}

// ---- 邀请码 ----
async function loadInvites() {
  const rows = await api("/invites");
  renderTable($("invites-table"), [
    { label: "邀请码", render: (r) => el("code", "code", r.code) },
    { label: "备注", key: "note" },
    { label: "已用 / 可用", render: (r) => num(r.uses || 0) + " / " + (r.max_uses === 0 ? "不限" : num(r.max_uses)) },
    { label: "状态", render: (r) => r.max_uses && r.uses >= r.max_uses ? pill("已用完", "") : pill("可用", "ok") },
    { label: "创建", render: (r) => ts(r.created) },
    { label: "操作", render: (r) => group(
      btn("复制", "", () => copy(r.code)),
      btn("删除", "danger", safe(async () => {
        if (!window.confirm("删除邀请码 " + r.code + "？")) return;
        await api("/invites/" + encodeURIComponent(r.code), { method: "DELETE" });
        toast("已删除");
        loadInvites();
      })),
    ) },
  ], rows, "还没有邀请码");
}

function copy(text) {
  const done = () => toast("已复制：" + text);
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done, () => toast("复制失败", "error"));
    return;
  }
  const ta = el("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  const ok = document.execCommand("copy");
  ta.remove();
  if (ok) done(); else toast("复制失败", "error");
}

// ---- 封禁 IP ----
async function loadBans() {
  const rows = await api("/bans");
  renderTable($("bans-table"), [
    { label: "IP", render: (r) => el("code", "code", r.ip) },
    { label: "原因", key: "reason" },
    { label: "时间", render: (r) => ts(r.created) },
    { label: "操作", render: (r) => btn("解封", "", safe(async () => {
      await api("/bans/" + encodeURIComponent(r.ip), { method: "DELETE" });
      toast("已解封 " + r.ip);
      loadBans();
    })) },
  ], rows, "没有被封的 IP");
}

// ---- 切换与启动 ----
const LOADERS = { overview: loadOverview, users: loadUsers, usage: loadUsage, invites: loadInvites, bans: loadBans };
let currentTab = "overview";

function handleError(e) {
  if (e instanceof AuthError) {
    lock(e.message);
    return;
  }
  toast(e.message || "请求失败", "error");
}

function showTab(name) {
  currentTab = name;
  document.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.tab === name)));
  document.querySelectorAll(".tab-panel").forEach((p) => { p.hidden = p.dataset.panel !== name; });
  LOADERS[name]().catch(handleError);
}

function lock(message) {
  setToken("");
  $("app-view").hidden = true;
  $("refresh-btn").hidden = true;
  $("lock-btn").hidden = true;
  $("login-view").hidden = false;
  $("login-error").hidden = !message;
  $("login-error").textContent = message || "";
  $("token-input").value = "";
  $("token-input").focus();
}

async function unlock() {
  await loadOverview(); // 口令不对会在这里抛 AuthError
  $("login-view").hidden = true;
  $("app-view").hidden = false;
  $("refresh-btn").hidden = false;
  $("lock-btn").hidden = false;
  showTab("overview");
}

$("token-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const t = $("token-input").value.trim();
  if (!t) return;
  setToken(t);
  unlock().catch(handleError);
});

document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));
$("refresh-btn").addEventListener("click", () => showTab(currentTab));
$("lock-btn").addEventListener("click", () => lock(""));
$("user-search").addEventListener("submit", (e) => { e.preventDefault(); userOffset = 0; loadUsers().catch(handleError); });
$("users-prev").addEventListener("click", () => { userOffset = Math.max(0, userOffset - PAGE); loadUsers().catch(handleError); });
$("users-next").addEventListener("click", () => { userOffset += PAGE; loadUsers().catch(handleError); });
$("usage-day").value = todayStr();
$("usage-form").addEventListener("submit", (e) => { e.preventDefault(); loadUsage().catch(handleError); });

$("invite-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const body = {
    count: Math.min(100, Math.max(1, Number($("inv-count").value) || 1)),
    max_uses: Math.max(0, Number($("inv-uses").value) || 0),
    note: $("inv-note").value.trim(),
  };
  api("/invites", { method: "POST", body }).then((codes) => {
    toast("已生成 " + codes.length + " 个邀请码");
    if (codes.length) copy(codes.map((c) => c.code).join("\n"));
    loadInvites();
  }).catch(handleError);
});

$("ban-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const ip = $("ban-ip").value.trim();
  if (!ip) return;
  api("/bans", { method: "POST", body: { ip, reason: $("ban-reason").value.trim() } }).then(() => {
    toast("已封禁 " + ip);
    $("ban-ip").value = "";
    $("ban-reason").value = "";
    loadBans();
  }).catch(handleError);
});

if (getToken()) unlock().catch(handleError); else $("token-input").focus();

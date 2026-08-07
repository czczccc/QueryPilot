#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
夸克网盘资源搜索工具 v3.1
输入影视/剧集关键词（支持自然语言），自动全网搜索夸克网盘分享链接并验证可达性。

v3.1：支持提取码——从链接上下文/页面正文自动提取分享提取码并展示、复制、导出

v3.0 升级：
  A. 多查询词轰炸：DeepSeek 生成 4~6 个搜索变体（别名/英文名/后缀组合），Tavily 逐个查询
  B. 深度抓取：对 Tavily 找到的资源页抓取完整 HTML 提取正文中的夸克链接（并发）
  C. 引擎并行：智能模式 = Tavily(多查询) + Bing + 夸克云搜 三路并行
  D. 正则放宽：支持 pan/share/drive.quark.cn 域名变体与 10~20 位分享码

搜索模式：
  [智能模式] 配置了 DeepSeek + Tavily key：DeepSeek 理解+生成查询变体，Tavily 全网搜索
  [免费模式] 未配置 key：规则清洗 + 夸克云搜 + Bing

API key 通过 GUI「设置」填写，保存在同目录 config.json（仅本机）。
用法：python quark_search_gui.py            （图形界面）
      python quark_search_gui.py --selftest 关键词
      python quark_search_gui.py --testapi
"""

import os
import re
import json
import queue
import threading
import base64
import datetime
import sys
import webbrowser
import urllib.parse
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# D. 正则放宽：支持 pan/share/drive.quark.cn 变体，分享码 10~20 位
QUARK_RE = re.compile(r"(?:pan|share|drive)?\.?quark\.cn/s/([0-9a-zA-Z]{10,20})", re.I)
# 提取码：匹配“提取码/访问码/pwd/code”后的 4 位字符
PWD_RE = re.compile(r"(?:提取码|提取密码|访问码|pwd|passcode|code)\s*[:：为是=]?\s*[（(]?\s*([0-9a-zA-Z]{4})\s*[）)]?", re.I)
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
TAVILY_URL = "https://api.tavily.com/search"

# 深度抓取时跳过的反爬重站
BLOCKED_DOMAINS = ("weibo.com", "tieba.baidu.com", "bilibili.com", "zhihu.com")


# ---------------- 配置 ----------------
def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------------- HTTP ----------------
def http_get(url, timeout=15, referer=None):
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("gbk", "ignore")
        return resp.status, text


def http_post_json(url, payload, headers=None, timeout=30):
    data = json.dumps(payload).encode("utf-8")
    h = {"User-Agent": UA, "Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8", "ignore"))


def verify_quark(share_id, timeout=12):
    """验证夸克链接可达性（弱验证：静态壳页均返回 200）"""
    try:
        url = "https://pan.quark.cn/s/" + share_id
        headers = {"User-Agent": UA, "Referer": "https://www.quark.cn/"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except Exception:
        return None


# ---------------- DeepSeek：理解 + 生成查询变体（A） ----------------
def llm_parse(raw, api_key):
    """用 DeepSeek 解析输入并生成多个搜索查询变体"""
    sys_prompt = (
        "你是影视资源网盘搜索助手。把用户输入解析成 JSON，只输出 JSON 本身。"
        "格式：{\"resource\":\"资源名（必填）\","
        "\"quality\":\"清晰度要求如 4K HDR / 1080p，没有则 null\","
        "\"preference\":\"网盘偏好如 夸克网盘，没有则 null\","
        "\"aliases\":[\"常见别名（如 绝命律师/风骚律师）\",...],"
        "\"english_name\":\"英文名如 The Long Season，没有则 null\","
        "\"search_suggestions\":[\"完整搜索查询词1\",\"查询词2\",\"查询词3\",\"查询词4\",\"查询词5\",\"查询词6\"]}"
        "search_suggestions 必须生成 5~6 个不同的完整查询词，"
        "组合使用资源名、别名、英文名、清晰度、'夸克网盘/网盘/云盘/资源/全集' 等词，"
        "确保覆盖用户可能的搜索习惯。"
    )
    _, body = http_post_json(
        DEEPSEEK_URL,
        {
            "model": "deepseek-v4-flash",
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": raw},
            ],
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        },
        headers={"Authorization": "Bearer " + api_key},
    )
    content = body["choices"][0]["message"]["content"]
    return json.loads(content)


def default_queries(resource, quality):
    """备用：DeepSeek 失败或无建议时用规则生成查询词"""
    qs = [resource + " 夸克网盘", resource + " 夸克 分享", resource + " 网盘 全集"]
    if quality:
        qs.append("%s %s 夸克" % (resource, quality))
    qs.append(resource + " 云盘 资源")
    return qs[:5]


# ---------------- Tavily：多查询搜索 + 深度抓取（A + B） ----------------
def make_entry(title, sid, source, pub, pwd=None):
    """构造结果条目（不含验证，验证统一在最后做）"""
    conf = "中"
    if pub:
        try:
            pd = datetime.datetime.strptime(pub[:10], "%Y-%m-%d")
            days = (datetime.datetime.now() - pd).days
            conf = "高" if days <= 30 else ("中" if days <= 180 else "低")
        except Exception:
            pass
    return {
        "name": (title[:40] or "?"), "share": sid, "pwd": pwd, "source": source,
        "time": pub[:10] or "未知", "conf": conf, "http": None,
    }


def deep_fetch_links(url, timeout=10):
    """抓取页面完整 HTML，提取 [(share_id, pwd)]（失败返回空列表）"""
    try:
        _, html = http_get(url, timeout=timeout)
        return extract_links_with_pwd(html)
    except Exception:
        return []


def tavily_search_multi(suggestions, api_key, progress):
    """Tavily 多查询搜索 + 对资源页深度抓取（并发）"""
    out = []
    seen = set()
    fetch_plan = []  # (url, title, pub)

    for q in suggestions[:5]:
        progress("[Tavily] 查询: %s" % q)
        try:
            payload = {
                "api_key": api_key, "query": q,
                "search_depth": "basic", "max_results": 8,
                "include_answer": False,
            }
            _, body = http_post_json(TAVILY_URL, payload)
            results = body.get("results", [])[:5]
            for r in results:
                url = r.get("url", "") or ""
                content = r.get("content", "") or ""
                title = r.get("title", "") or ""
                pub = r.get("published_date") or ""
                # 1) 从 URL + 摘要提取（含提取码）
                for sid, pwd in extract_links_with_pwd(url + " " + content):
                    if sid in seen:
                        continue
                    seen.add(sid)
                    out.append(make_entry(title, sid, url, pub, pwd))
                # 2) 排入深度抓取计划
                if url and not any(b in url for b in BLOCKED_DOMAINS):
                    fetch_plan.append((url, title, pub))
        except Exception as e:
            progress("  [Tavily 查询失败] %s" % e)

    # 深度抓取（并发，B）
    if fetch_plan:
        progress("[深度抓取] 检查 %d 个资源页正文..." % len(fetch_plan))
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {ex.submit(deep_fetch_links, u): (u, t, p) for u, t, p in fetch_plan}
            for f in futures:
                u, t, p = futures[f]
                try:
                    for sid, pwd in f.result():
                        if sid in seen:
                            continue
                        seen.add(sid)
                        out.append(make_entry(t, sid, u + " [正文]", p, pwd))
                except Exception:
                    pass
    return out


def find_pwd(text):
    """从文本中找提取码（4 位）"""
    m = PWD_RE.search(text)
    return m.group(1) if m else None


def extract_links_with_pwd(text):
    """从 HTML/文本提取 [(share_id, pwd)]，pwd 取链接前后 200 字符内的提取码"""
    out = []
    seen = set()
    for m in QUARK_RE.finditer(text):
        sid = m.group(1)
        if sid in seen:
            continue
        seen.add(sid)
        around = text[max(0, m.start() - 200): m.end() + 200]
        out.append((sid, find_pwd(around)))
    return out


# ---------------- 关键词清洗（免费模式） ----------------
STOP_WORDS = ["夸克网盘", "百度网盘", "阿里云盘", "夸克", "网盘", "百度", "阿里",
              "迅雷", "4k", "hdr", "1080p", "1080", "2160p", "2160", "高清",
              "超清", "无删减", "全集", "全季", "全六季", "资源", "分享", "下载",
              "链接", "字幕", "中英", "完整版", "合集", "美剧", "韩剧", "日剧",
              "国剧", "电视剧", "电影"]


def clean_keyword(raw):
    kw = raw.strip()
    for w in STOP_WORDS:
        kw = re.sub(re.escape(w), " ", kw, flags=re.I)
    kw = re.sub(r"\s+", " ", kw).strip()
    return kw or raw.strip()


# ---------------- 引擎：夸克云搜（始终启用） ----------------
def parse_qkyunso_search(html):
    items = []
    blocks = re.split(r'data-resource-id="(\d+)"', html)
    for i in range(1, len(blocks), 2):
        rid = blocks[i]
        block = blocks[i + 1] if i + 1 < len(blocks) else ""
        name_m = re.search(r'data-resource-name="([^"]+)"', block)
        time_m = re.search(r"<span>([0-9]+个月前|更早)</span>", block)
        items.append({
            "id": rid,
            "name": name_m.group(1) if name_m else "?",
            "time": time_m.group(1) if time_m else "未知",
        })
    if not items:
        for m in re.finditer(r'detail\?id=(\d+)[^>]*data-resource-name="([^"]+)"', html):
            items.append({"id": m.group(1), "name": m.group(2), "time": "未知"})
    return items


def parse_qkyunso_detail(html):
    links = QUARK_RE.findall(html)
    if not links:
        return None, None
    times = [int(t) for t in re.findall(r"([0-9]+)个月前", html)]
    return links[0], min(times) if times else None


def confidence_from_months(m):
    if m is None:
        return "中"
    if m <= 1:
        return "高"
    if m <= 6:
        return "中"
    return "低"


def search_qkyunso(kw, progress):
    out = []
    try:
        url = "https://qkyunso.com/search?keyword=" + urllib.parse.quote(kw)
        _, html = http_get(url, referer="https://qkyunso.com/")
        items = parse_qkyunso_search(html)
        progress("[引擎 夸克云搜] 找到 %d 条资源记录" % len(items))
        for it in items:
            try:
                durl = "https://qkyunso.com/detail?id=" + it["id"]
                _, dhtml = http_get(durl, referer=url)
                sid, months = parse_qkyunso_detail(dhtml)
                if not sid:
                    continue
                pwd = find_pwd(dhtml)
                out.append({
                    "name": it["name"], "share": sid, "pwd": pwd,
                    "source": "夸克云搜 detail?id=" + it["id"],
                    "time": ("%d个月前" % months) if months else it["time"],
                    "conf": confidence_from_months(months),
                    "http": None,
                })
            except Exception as e:
                progress("  [详情解析失败] %s" % e)
    except Exception as e:
        progress("[引擎 夸克云搜] 搜索失败: %s" % e)
    return out


# ---------------- 引擎：Bing 中文（免费 + 智能模式都用） ----------------
def decode_bing_url(href):
    m = re.search(r"[?&]u=a1([^&]+)", href)
    if not m:
        return None
    try:
        b64 = urllib.parse.unquote(m.group(1))
        b64 += "=" * (-len(b64) % 4)
        return base64.urlsafe_b64decode(b64).decode("utf-8", "ignore")
    except Exception:
        return None


def search_bing(kw, progress):
    out = []
    seen = set()
    queries = ['"%s" 夸克网盘' % kw, "%s 夸克 分享" % kw, "%s 4K 夸克" % kw,
               "%s 网盘 全集" % kw]
    for q in queries:
        try:
            url = ("https://cn.bing.com/search?q=" + urllib.parse.quote(q) +
                   "&mkt=zh-CN&setlang=zh-hans")
            _, html = http_get(url, referer="https://cn.bing.com/")
            for m in re.finditer(
                    r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a></h2>',
                    html, re.S):
                real = decode_bing_url(m.group(1)) or m.group(1)
                if not re.match(r"^https?://", real):
                    continue
                title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                seg = html[m.end():m.end() + 6000]
                for sid, pwd in extract_links_with_pwd(seg):
                    if sid in seen:
                        continue
                    seen.add(sid)
                    tm = re.search(r"([0-9]+)\s*(天|小时|分钟)?\s*(之前|前)", seg)
                    out.append({
                        "name": title[:40] or kw, "share": sid, "pwd": pwd,
                        "source": real, "time": tm.group(0) if tm else "未知",
                        "conf": "中", "http": None,
                    })
                sm = QUARK_RE.search(seg)
                if not sm:
                    continue
        except Exception as e:
            progress("[引擎 Bing] 查询失败: %s" % e)
    return out


# ---------------- 主入口（C：三引擎并行） ----------------
def search_all(raw_kw, progress):
    cfg = load_config()
    dk = cfg.get("deepseek_api_key", "")
    tv = cfg.get("tavily_api_key", "")

    kw = clean_keyword(raw_kw)
    resource = kw
    results = []
    smart = bool(dk and tv)

    if smart:
        progress("智能模式：DeepSeek 解析输入并生成查询变体...")
        try:
            parsed = llm_parse(raw_kw, dk)
            resource = (parsed.get("resource") or kw).strip()
            quality = parsed.get("quality")
            preference = parsed.get("preference")
            suggestions = parsed.get("search_suggestions") or []
            progress("  LLM: 资源=%r 清晰度=%r 偏好=%r 查询变体%d个"
                     % (resource, quality, preference, len(suggestions)))
            if not suggestions:
                suggestions = default_queries(resource, quality)
            try:
                results += tavily_search_multi(suggestions, tv, progress)
            except Exception as e:
                progress("  [Tavily 调用失败] %s" % e)
        except Exception as e:
            progress("  [DeepSeek 调用失败，降级免费模式] %s" % e)
            smart = False

    if not smart:
        progress("免费模式：规则关键词清洗 + 公开网页搜索")
        results += search_qkyunso(kw, progress)
        results += search_bing(kw, progress)
    else:
        # C: 三引擎并行——智能模式也跑 Bing 和夸克云搜
        results += search_bing(resource, progress)
        results += search_qkyunso(resource, progress)

    # 统一去重 + 验证
    uniq = {}
    for r in results:
        uniq.setdefault(r["share"], r)
    final = list(uniq.values())
    progress("去重后 %d 条，开始验证链接..." % len(final))
    for r in final:
        r["http"] = verify_quark(r["share"])
    progress("搜索完成，共 %d 条结果" % len(final))
    return final


# ---------------- 图形界面 ----------------
class App:
    def __init__(self, root):
        self.root = root
        root.title("夸克网盘资源搜索工具 v3.1")
        root.geometry("980x560")
        root.minsize(780, 430)

        top = ttk.Frame(root, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="资源关键词：").pack(side="left")
        self.entry = ttk.Entry(top, width=42)
        self.entry.pack(side="left", padx=6)
        self.entry.bind("<Return>", lambda e: self.start_search())
        self.btn = ttk.Button(top, text="开始搜索", command=self.start_search)
        self.btn.pack(side="left", padx=4)
        self.stop_btn = ttk.Button(top, text="停止", command=self.stop_search, state="disabled")
        self.stop_btn.pack(side="left")
        ttk.Button(top, text="API设置", command=self.open_settings).pack(side="left", padx=6)
        self.mode_lbl = ttk.Label(top, text="", foreground="#2a7")
        self.mode_lbl.pack(side="left", padx=6)

        mid = ttk.Frame(root, padding=(8, 0))
        mid.pack(fill="both", expand=True)
        cols = ("name", "share", "pwd", "source", "time", "conf", "http")
        heads = {"name": "资源", "share": "夸克链接(pan.quark.cn/s/)", "pwd": "提取码",
                 "source": "来源", "time": "来源时间", "conf": "置信度", "http": "验证"}
        widths = {"name": 160, "share": 200, "pwd": 70, "source": 250,
                  "time": 90, "conf": 60, "http": 60}
        self.tree = ttk.Treeview(mid, columns=cols, show="headings")
        for c in cols:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=widths[c], anchor="w")
        vsb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        bot = ttk.Frame(root, padding=8)
        bot.pack(fill="x")
        for txt, cmd in [("复制全部链接", self.copy_links), ("打开选中", self.open_selected),
                         ("导出TXT", self.export_txt), ("清空", self.clear_all)]:
            ttk.Button(bot, text=txt, command=cmd).pack(side="left", padx=3)
        self.status = ttk.Label(bot, text="就绪", foreground="#555")
        self.status.pack(side="right")

        self.log = tk.Text(root, height=6, state="disabled", bg="#fafafa")
        self.log.pack(fill="x", padx=8, pady=(0, 8))

        self.q = queue.Queue()
        self.worker = None
        self.running = False
        self.root.after(100, self.poll)
        self._refresh_mode()

    def _refresh_mode(self):
        cfg = load_config()
        if cfg.get("deepseek_api_key") and cfg.get("tavily_api_key"):
            self.mode_lbl.config(text="智能模式 (DeepSeek+Tavily+Bing+夸克云搜)", foreground="#2a7")
        elif cfg.get("deepseek_api_key") or cfg.get("tavily_api_key"):
            self.mode_lbl.config(text="配置不完整（需两个key，已降级免费模式）", foreground="#c60")
        else:
            self.mode_lbl.config(text="免费模式（未配置API）", foreground="#888")

    def open_settings(self):
        cfg = load_config()
        win = tk.Toplevel(self.root)
        win.title("API 设置")
        win.resizable(False, False)
        win.grab_set()
        pad = {"padx": 10, "pady": 4}

        ttk.Label(win, text="配置 DeepSeek + Tavily 后启用智能模式（自然语言理解+全网搜索）",
                  foreground="#2a7").grid(row=0, column=0, columnspan=2, sticky="w", **pad)

        ttk.Label(win, text="DeepSeek API Key:").grid(row=1, column=0, sticky="w", **pad)
        e1 = ttk.Entry(win, width=52, show="*")
        e1.grid(row=1, column=1, **pad)
        e1.insert(0, cfg.get("deepseek_api_key", ""))

        ttk.Label(win, text="Tavily API Key:").grid(row=2, column=0, sticky="w", **pad)
        e2 = ttk.Entry(win, width=52, show="*")
        e2.grid(row=2, column=1, **pad)
        e2.insert(0, cfg.get("tavily_api_key", ""))

        def save():
            cfg["deepseek_api_key"] = e1.get().strip()
            cfg["tavily_api_key"] = e2.get().strip()
            save_config(cfg)
            self._refresh_mode()
            self._log("API 配置已保存（config.json，仅本机）")
            win.destroy()

        def clear():
            cfg["deepseek_api_key"] = ""
            cfg["tavily_api_key"] = ""
            save_config(cfg)
            self._refresh_mode()
            self._log("API 配置已清除")
            win.destroy()

        btns = ttk.Frame(win)
        btns.grid(row=3, column=0, columnspan=2, pady=8)
        ttk.Button(btns, text="保存", command=save).pack(side="left", padx=5)
        ttk.Button(btns, text="清除", command=clear).pack(side="left", padx=5)

        ttk.Label(win, text="获取 key：DeepSeek platform.deepseek.com（充值后可用）\n"
                            "Tavily tavily.com（免费额度 1000 次/月）",
                  foreground="#888").grid(row=4, column=0, columnspan=2, sticky="w", **pad)

    def start_search(self):
        raw = self.entry.get().strip()
        if not raw:
            messagebox.showinfo("提示", "请输入资源关键词")
            return
        self.clear_all()
        self.running = True
        self.btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status.config(text="搜索中...")
        self.worker = threading.Thread(target=self._run, args=(raw,), daemon=True)
        self.worker.start()

    def stop_search(self):
        self.running = False
        self.status.config(text="已停止")

    def _run(self, raw):
        def progress(msg):
            self.q.put(("log", msg))
        try:
            results = search_all(raw, progress)
            self.q.put(("done", results))
        except Exception as e:
            self.q.put(("error", str(e)))

    def poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "done":
                    self._finish(payload)
                elif kind == "error":
                    self._log("发生错误: %s" % payload)
                    self._finish([])
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def _finish(self, results):
        self.running = False
        self.btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        if not results:
            self.status.config(text="完成，未找到结果（可换关键词或稍后再试）")
            return
        for r in results:
            http_txt = ("HTTP %s" % r["http"]) if r["http"] else "不可达"
            pwd_txt = r.get("pwd") or "-"
            self.tree.insert("", "end", values=(
                r["name"], r["share"], pwd_txt, r["source"], r["time"], r["conf"], http_txt))
        self.status.config(text="完成，共 %d 条结果" % len(results))

    def copy_links(self):
        rows = self.tree.get_children()
        if not rows:
            return
        lines = []
        for r in rows:
            v = self.tree.item(r, "values")
            link = "https://pan.quark.cn/s/" + v[1]
            if v[2] != "-":
                link += "?pwd=" + v[2]
            lines.append(link)
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(lines))
        self.status.config(text="已复制 %d 条链接" % len(lines))

    def open_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        sid = self.tree.item(sel[0], "values")[1]
        webbrowser.open("https://pan.quark.cn/s/" + sid)

    def export_txt(self):
        rows = self.tree.get_children()
        if not rows:
            messagebox.showinfo("提示", "没有可导出的结果")
            return
        path = filedialog.asksaveasfilename(
            title="导出举报清单", defaultextension=".txt",
            initialfile="夸克资源清单.txt",
            filetypes=[("文本文件", "*.txt")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write("夸克网盘资源搜索清单\n")
            f.write("=" * 60 + "\n")
            for r in rows:
                v = self.tree.item(r, "values")
                link = "https://pan.quark.cn/s/" + v[1]
                if v[2] != "-":
                    link += "?pwd=" + v[2]
                f.write("资源: %s\n" % v[0])
                f.write("链接: %s\n" % link)
                f.write("提取码: %s\n" % v[2])
                f.write("来源: %s\n" % v[3])
                f.write("来源时间: %s | 置信度: %s | 验证: %s\n" % (v[4], v[5], v[6]))
                f.write("-" * 40 + "\n")
        self.status.config(text="已导出: %s" % path)

    def clear_all(self):
        self.tree.delete(*self.tree.get_children())
        self._log_clear()

    def _log(self, msg):
        self.log.config(state="normal")
        self.log.insert("end", "[%s] %s\n" % (datetime.datetime.now().strftime("%H:%M:%S"), msg))
        self.log.see("end")
        self.log.config(state="disabled")

    def _log_clear(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")


# ---------------- 自测 / API 测试 ----------------
def selftest(kw):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    def progress(msg):
        print("[.]", msg)
    print("测试关键词:", kw)
    results = search_all(kw, progress)
    print("\n===== 结果 =====")
    for r in results:
        print(json.dumps(r, ensure_ascii=False))


def testapi():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("[1] 测试 DeepSeek API...")
    try:
        llm_parse("测试", "sk-invalid-test-key")
        print("    意外成功？")
    except urllib.error.HTTPError as e:
        print("    HTTP %s（请求格式正确，仅 key 无效）" % e.code)
    except Exception as e:
        print("    请求失败: %s" % e)
    print("[2] 测试 Tavily API...")
    try:
        tavily_search_multi(["测试 夸克"], "tvly-invalid-test-key", lambda m: None)
        print("    意外成功？")
    except urllib.error.HTTPError as e:
        print("    HTTP %s（同上）" % e.code)
    except Exception as e:
        print("    请求失败: %s" % e)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        idx = sys.argv.index("--selftest")
        k = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else "漫长的季节"
        selftest(k)
    elif "--testapi" in sys.argv:
        testapi()
    else:
        root = tk.Tk()
        App(root)
        root.mainloop()

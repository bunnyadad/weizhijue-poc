"""來源探測：判斷每家機構的方案資料能不能自動取得、要用哪種方式。

依序嘗試：
1. 一般 HTTP 抓取（最便宜）
2. 首頁沒價格時，往下找一層方案子頁
3. headless 瀏覽器渲染（scripts/render.js，處理 JS 載入）
失敗時依錯誤原因分類，方便判斷是技術可修還是真的被擋。
"""
from __future__ import annotations
import json
import re
import socket
import subprocess
import urllib.error
import urllib.request
from html import unescape
from urllib.parse import urljoin, urlparse

from .data import DATA
from .watch import normalize, ssl_context

ROOT = DATA.parent
OUT = DATA / "probe"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

PRICE = re.compile(r"(?<![\d.])(?:\d{1,3}(?:,\d{3})+|\d{4,6})\s*(?:元|/人|NT)|(?:NT\$|\$|＄)\s*\d{1,3}(?:,\d{3})+")
GI = re.compile(r"胃鏡|大腸鏡|腸胃鏡|胃腸鏡|內視鏡|endoscop|colonoscop", re.I)
BLOCK = re.compile(r"Just a moment|cf-browser-verification|Attention Required|Access denied|captcha|Security Verification|REQUEST BLOCKED|安全驗證", re.I)
PDF_LINK = re.compile(r"href=[\"']([^\"']+\.pdf)", re.I)
ANCHOR = re.compile(r"<a\b[^>]*href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
SUB_HINT = re.compile(r"專案|方案|套餐|套組|組套|套裝|腸胃|胃鏡|價目|收費|費用|健檢項目|program|package|plan|c03\.php", re.I)
BLOCK_STATUS = {403, 429, 503, 520, 521, 522, 523, 524, 525, 526}
MAX_SUBPAGES = 8

LABEL = {
    "static": "可直接抓（HTTP）",
    "subpage": "可抓（價格在子頁）",
    "headless": "可抓（需瀏覽器渲染）",
    "browser": "可抓（真實瀏覽器）",
    "browser_subpage": "可抓（真實瀏覽器，價格在子頁）",
    "agent": "可抓（jev 代理導航）",
    "pdf": "PDF（需解析）",
    "no_price": "頁面抓得到，但找不到價格",
    "blocked": "被反爬或防火牆擋下",
    "cert_chain": "網站憑證鏈不完整（可補中繼憑證）",
    "tls": "TLS 握手失敗（多半需瀏覽器）",
    "dns": "DNS 查不到網域",
    "timeout": "連線逾時",
    "reset": "連線被重置",
    "error": "其他錯誤",
}


def analyze(raw: str) -> dict:
    text = normalize(raw)
    return {
        "chars": len(text),
        "prices": len(PRICE.findall(text)),
        "gi": len(GI.findall(text)),
        "pdfs": sorted(set(PDF_LINK.findall(raw)))[:10],
        "blocked": bool(BLOCK.search(raw[:8000])),
        "text": text,
    }


def classify_error(e: Exception) -> str:
    s = f"{e} {getattr(e, 'reason', '')}"
    if "CERTIFICATE_VERIFY_FAILED" in s:
        return "cert_chain"
    if isinstance(getattr(e, "reason", None), socket.gaierror) or re.search(r"nodename|not known|getaddrinfo", s):
        return "dns"
    if isinstance(e, TimeoutError) or "timed out" in s:
        return "timeout"
    if re.search(r"reset|RemoteDisconnected|closed connection", s, re.I):
        return "reset"
    if "SSL" in s or "EOF" in s:
        return "tls"
    return "error"


def http_get(url: str, timeout: int = 40, tries: int = 2) -> tuple[int | None, str]:
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept-Language": "zh-TW,zh;q=0.9"})
    for n in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
                body = r.read()
                if url.lower().endswith(".pdf") or r.headers.get_content_type() == "application/pdf":
                    return r.status, "%PDF"
                return r.status, body.decode(r.headers.get_content_charset() or "utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:
            # 逾時與重置常是暫時性，重試一次；憑證與 DNS 錯誤重試也沒用
            if n == tries - 1 or classify_error(e) not in ("timeout", "reset"):
                raise


def sublinks(raw: str, base: str) -> list[str]:
    host = urlparse(base).netloc
    seen, out = set(), []
    for href, label in ANCHOR.findall(raw):
        label = normalize(unescape(label))
        url = urljoin(base, unescape(href))
        if urlparse(url).netloc != host or url in seen or url.rstrip("/") == base.rstrip("/"):
            continue
        if url.lower().endswith(".pdf"):
            continue
        if SUB_HINT.search(label) or SUB_HINT.search(href):
            seen.add(url)
            out.append(url)
    return out[:MAX_SUBPAGES]


def follow(raw: str, base: str) -> dict | None:
    best = None
    for url in sublinks(raw, base):
        try:
            status, sub = http_get(url, timeout=25, tries=1)
        except Exception:
            continue
        if status != 200 or sub == "%PDF":
            continue
        a = analyze(sub)
        score = (a["prices"] > 0 and a["gi"] > 0, a["prices"], a["gi"])
        if a["prices"] and (best is None or score > best["score"]):
            best = {"url": url, "prices": a["prices"], "gi": a["gi"], "text": a["text"], "score": score}
    if best:
        best.pop("score")
    return best


def render(url: str) -> dict:
    try:
        out = subprocess.run(["node", str(ROOT / "scripts/render.js"), url], capture_output=True, text=True, timeout=90)
        return json.loads(out.stdout or "{}") if out.returncode == 0 else {"error": out.stderr.strip()[-300:]}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def probe(src: dict, use_browser: bool = True) -> dict:
    row = {"id": src["id"], "name": src["name"], "city": src["city"], "url": src["url"]}
    status, raw, fail = None, "", None
    try:
        status, raw = http_get(src["url"])
        row["http_status"] = status
    except Exception as e:
        fail = classify_error(e)
        row["http_error"] = f"{type(e).__name__}: {e}"[:200]

    if raw == "%PDF":
        row["method"] = "pdf"
        return row

    a = analyze(raw) if raw else None
    if a:
        row["http"] = {k: v for k, v in a.items() if k != "text"}
        if status == 200 and a["prices"] and a["gi"]:
            row.update(method="static", text=a["text"])
            return row
        if status == 200 and not a["blocked"]:
            sub = follow(raw, src["url"])
            if sub:
                row["subpage"] = {k: v for k, v in sub.items() if k != "text"}
                row.update(method="subpage", text=sub["text"])
                return row

    if use_browser:
        r = render(src["url"])
        if r.get("html"):
            b = analyze(r["html"])
            row["browser"] = {**{k: v for k, v in b.items() if k != "text"}, "status": r.get("status")}
            if b["blocked"] or r.get("status") in BLOCK_STATUS:
                row["method"] = "blocked"
            elif b["prices"] and b["gi"]:
                row.update(method="headless", text=b["text"])
            else:
                row.update(method="no_price", text=b["text"])
            return row
        row["browser_error"] = r.get("error")

    if (a and a["blocked"]) or status in BLOCK_STATUS:
        row["method"] = "blocked"
    elif a and a["chars"]:
        row.update(method="no_price", text=a["text"])
    else:
        row["method"] = fail or "error"
    return row


def summary_line(r: dict) -> str:
    h = r.get("subpage") or r.get("browser") or r.get("http") or {}
    parts = [f"{r['name']}：{LABEL.get(r['method'], r['method'])}"]
    if "prices" in h:
        parts.append(f"價格 {h['prices']}、腸胃字樣 {h.get('gi', 0)}")
    if r.get("subpage"):
        parts.append(r["subpage"]["url"])
    if r["method"] in ("blocked",) and r.get("http_status"):
        parts.append(f"HTTP {r['http_status']}")
    return "｜".join(parts)


def run(ids: list[str] | None = None, use_browser: bool = True) -> list[dict]:
    srcs = json.loads((DATA / "sources.json").read_text("utf-8"))["sources"]
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for s in srcs:
        if ids and s["id"] not in ids:
            continue
        r = probe(s, use_browser)
        text = r.pop("text", "")
        if text:
            (OUT / f"{s['id']}.txt").write_text(text, "utf-8")
        rows.append(r)
    (OUT / "result.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), "utf-8")
    return rows

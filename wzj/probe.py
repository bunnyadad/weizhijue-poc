"""來源探測：判斷每家機構的方案資料能不能自動取得、要用哪種方式。

依序嘗試：
1. 一般 HTTP 抓取（最便宜）
2. headless 瀏覽器渲染（scripts/render.js，處理 JS 載入）
結果分成：static（HTTP 即可）、headless（需渲染）、pdf、blocked（被擋）、no_price（抓得到但頁面沒有價格）、error。
"""
import json
import re
import subprocess
from pathlib import Path

from .data import DATA
from .watch import fetch, normalize

ROOT = DATA.parent
OUT = DATA / "probe"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

PRICE = re.compile(r"(?<![\d.])(?:\d{1,3}(?:,\d{3})+|\d{4,6})\s*(?:元|/人|NT)|(?:NT\$|\$|＄)\s*\d{1,3}(?:,\d{3})+")
GI = re.compile(r"胃鏡|大腸鏡|腸胃鏡|胃腸鏡|內視鏡|endoscop|colonoscop", re.I)
BLOCK = re.compile(r"Just a moment|cf-browser-verification|Attention Required|Access denied|captcha", re.I)
PDF_LINK = re.compile(r"href=[\"']([^\"']+\.pdf)", re.I)


def analyze(raw: str) -> dict:
    text = normalize(raw)
    return {
        "chars": len(text),
        "prices": len(PRICE.findall(text)),
        "gi": len(GI.findall(text)),
        "pdfs": sorted(set(PDF_LINK.findall(raw)))[:10],
        "blocked": bool(BLOCK.search(raw[:5000])),
        "text": text,
    }


def http_get(url: str) -> tuple[int | None, str]:
    import urllib.error
    import urllib.request
    import ssl

    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept-Language": "zh-TW,zh;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=25, context=ssl.create_default_context()) as r:
            body = r.read()
            if url.lower().endswith(".pdf") or r.headers.get_content_type() == "application/pdf":
                return r.status, "%PDF"
            return r.status, body.decode(r.headers.get_content_charset() or "utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def render(url: str) -> dict:
    try:
        out = subprocess.run(["node", str(ROOT / "scripts/render.js"), url], capture_output=True, text=True, timeout=90)
        return json.loads(out.stdout or "{}") if out.returncode == 0 else {"error": out.stderr[-300:]}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def probe(src: dict, use_browser: bool = True) -> dict:
    row = {"id": src["id"], "name": src["name"], "city": src["city"], "url": src["url"]}
    try:
        status, raw = http_get(src["url"])
        row["http_status"] = status
    except Exception as e:
        status, raw = None, ""
        row["http_error"] = f"{type(e).__name__}: {e}"[:200]

    if raw == "%PDF":
        row.update(method="pdf", prices=None, gi=None)
        return row

    a = analyze(raw) if raw else None
    if a:
        row["http"] = {k: v for k, v in a.items() if k != "text"}
    if a and status == 200 and a["prices"] and a["gi"]:
        row["method"] = "static"
        row["text"] = a["text"]
        return row

    if use_browser:
        r = render(src["url"])
        if r.get("html"):
            b = analyze(r["html"])
            row["browser"] = {k: v for k, v in b.items() if k != "text"} | {"status": r.get("status")}
            if b["blocked"]:
                row["method"] = "blocked"
            elif b["prices"] and b["gi"]:
                row["method"] = "headless"
            elif b["gi"] or b["pdfs"]:
                row["method"] = "no_price"
            else:
                row["method"] = "no_price"
            row["text"] = b["text"]
            return row
        row["browser_error"] = r.get("error")

    if a and a["blocked"] or status in (403, 429, 503):
        row["method"] = "blocked"
    elif a and a["chars"]:
        row["method"] = "no_price"
        row["text"] = a["text"]
    else:
        row["method"] = "error"
    return row


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

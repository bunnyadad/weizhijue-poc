"""變動偵測：重抓來源頁、正規化後比對 hash，累積兩週後推估維護工時。"""
from __future__ import annotations
import hashlib
import html
import json
import re
import ssl
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from .data import DATA, Catalog

SNAP = DATA / "snapshots"
CERTS = DATA / "certs"
UA = "Mozilla/5.0 (compatible; weizhijue-poc)"


def normalize(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    # 去掉常見的動態雜訊（時間戳、token）以免每次都判定變動
    text = re.sub(r"\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}(:\d{2})?", "", text)
    return re.sub(r"\s+", " ", text).strip()


PRICE = re.compile(r"(?<![\d.])\d{1,3}(?:,\d{3})+(?![\d.])")


def prices(text: str) -> list[str]:
    return sorted(set(PRICE.findall(text)))


def price_key(text: str) -> str:
    """只取千分位格式的金額；全文 hash 容易被動態區塊誤報，價格才是要盯的欄位"""
    return hashlib.sha256(" ".join(prices(text)).encode()).hexdigest()[:16]


def ssl_context() -> ssl.SSLContext:
    """系統 CA＋repo 內附的中繼憑證；有些醫院網站漏送中繼憑證，瀏覽器會自動補，Python 不會"""
    ctx = ssl.create_default_context()
    for pem in sorted(CERTS.glob("*.pem")):
        ctx.load_verify_locations(cafile=str(pem))
    return ctx


def fetch(url: str, timeout: int = 20, tries: int = 3) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for n in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
                return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")
        except OSError:
            if n == tries - 1:
                raise
            time.sleep(2 ** n)


def check(cat: Catalog, snap_dir: Path = SNAP, fetcher=fetch) -> list[dict]:
    snap_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now().isoformat(timespec="seconds")
    out = []
    for inst in cat.institutions.values():
        path = snap_dir / f"{inst.id}.json"
        hist = json.loads(path.read_text("utf-8")) if path.exists() else []
        row = {"at": now, "url": inst.source_url}
        try:
            text = normalize(fetcher(inst.source_url))
            row.update(ok=True, hash=hashlib.sha256(text.encode()).hexdigest()[:16],
                       key=price_key(text), prices=len(prices(text)), chars=len(text))
            (snap_dir / f"{inst.id}.txt").write_text(text, "utf-8")
        except Exception as e:  # 逾時、擋爬、憑證錯誤都算「不可自動化」的證據
            row.update(ok=False, error=f"{type(e).__name__}: {e}"[:200])
        prev = next((h for h in reversed(hist) if h.get("ok")), None)
        row["changed"] = bool(row.get("ok") and prev and prev.get("key") != row["key"])
        row["text_changed"] = bool(row.get("ok") and prev and prev["hash"] != row["hash"])
        hist.append(row)
        path.write_text(json.dumps(hist, ensure_ascii=False, indent=1), "utf-8")
        out.append({"inst": inst.name, **row})
    return out


def summary(cat: Catalog, snap_dir: Path = SNAP) -> dict:
    runs = changes = fails = noise = 0
    first = last = None
    for inst in cat.institutions.values():
        path = snap_dir / f"{inst.id}.json"
        if not path.exists():
            continue
        for h in json.loads(path.read_text("utf-8")):
            runs += 1
            changes += h.get("changed", False)
            noise += h.get("text_changed", False) and not h.get("changed", False)
            fails += not h.get("ok")
            first = min(first or h["at"], h["at"])
            last = max(last or h["at"], h["at"])
    days = (datetime.fromisoformat(last) - datetime.fromisoformat(first)).days if first else 0
    return {"runs": runs, "changes": changes, "fails": fails, "noise": noise, "days": days}

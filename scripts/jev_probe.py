"""用 jev-ultrafast 在本機真實 Chrome（台灣 IP）探測來源。

預設只用 jev 的 Browser：開背景分頁、讀頁面文字、往下找一層方案子頁，不呼叫 LLM、不花費。
加 --agent 才用 jev Agent 讓它自己點到腸胃方案頁（需 TYPESAFE_API_KEY／TEXT_MODEL_API_KEY，會計費）。

在 weizhijue-poc 目錄執行（jev-ultrafast 與本 repo 放在同一層）：
  uv run --project ../jev-ultrafast --env-file ../jev-ultrafast/.env python scripts/jev_probe.py
  uv run --project ../jev-ultrafast --env-file ../jev-ultrafast/.env python scripts/jev_probe.py tsgh skh --agent
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wzj.data import DATA  # noqa: E402
from wzj.probe import OUT, SUB_HINT, analyze, summary_line  # noqa: E402

# 預設先測還沒確定能自動抓的 B1／B2
DEFAULT_CATS = {"B1", "B2"}
MAX_SUBPAGES = 8
GOAL = (
    "找到這家醫院含胃鏡或大腸鏡（腸胃鏡、無痛內視鏡）的自費健康檢查方案，"
    "打開列出方案名稱與價格的頁面。看到方案名稱與價格時就停止，不要預約、不要填表。"
)
TEXT_JS = "document.body ? document.body.innerText : ''"
LINKS_JS = (
    "JSON.stringify([...document.querySelectorAll('a[href]')]"
    ".map(a => [a.href, (a.innerText || a.title || '').trim().slice(0, 80)]))"
)


def wait_ready(b, seconds: float = 20, settle: float = 1.5) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if b.evaluate("document.readyState") == "complete":
                break
        except Exception:
            pass
        time.sleep(0.1)
    # 等 JS 把方案列表畫出來
    time.sleep(settle)


def read(b) -> tuple[str, list[list[str]]]:
    text = b.evaluate(TEXT_JS) or ""
    try:
        links = json.loads(b.evaluate(LINKS_JS) or "[]")
    except Exception:
        links = []
    return text, links


def goto(b, url: str) -> None:
    b.call("Page.navigate", url=url)
    wait_ready(b)


def candidate_links(links: list[list[str]], base: str) -> list[str]:
    host = urlparse(base).netloc
    seen, out = {base.rstrip("/")}, []
    for href, label in links:
        if urlparse(href).netloc != host or href.rstrip("/") in seen or href.lower().endswith(".pdf"):
            continue
        if SUB_HINT.search(label) or SUB_HINT.search(href):
            seen.add(href.rstrip("/"))
            out.append(href)
    return out[:MAX_SUBPAGES]


def stats(a: dict) -> dict:
    return {"prices": a["prices"], "gi": a["gi"], "chars": a["chars"], "blocked": a["blocked"]}


def probe_browser(src: dict, Browser) -> tuple[dict, str]:
    row = {"id": src["id"], "name": src["name"], "city": src["city"], "url": src["url"], "via": "jev-browser"}
    try:
        b = Browser(src["url"])
    except Exception as e:
        row.update(method="error", error=f"{type(e).__name__}: {e}"[:200])
        return row, ""
    try:
        wait_ready(b)
        text, links = read(b)
        a = analyze(text)
        row["browser"] = stats(a)
        if a["blocked"]:
            row["method"] = "blocked"
            return row, a["text"]
        if a["prices"] and a["gi"]:
            row["method"] = "browser"
            return row, a["text"]
        best, best_text = None, ""
        for url in candidate_links(links, src["url"]):
            try:
                goto(b, url)
                sub_text, _ = read(b)
            except Exception:
                continue
            s = analyze(sub_text)
            score = (s["prices"] > 0 and s["gi"] > 0, s["prices"], s["gi"])
            if s["prices"] and (best is None or score > best[0]):
                best, best_text = (score, {"url": url, **stats(s)}), s["text"]
        if best:
            row.update(method="browser_subpage", subpage=best[1])
            return row, best_text
        row["method"] = "no_price"
        return row, a["text"]
    except Exception as e:
        row.update(method="error", error=f"{type(e).__name__}: {e}"[:200])
        return row, ""
    finally:
        b.close()


def probe_agent(src: dict, Agent) -> tuple[dict, str]:
    row = {"id": src["id"], "name": src["name"], "city": src["city"], "url": src["url"], "via": "jev-agent"}
    try:
        with Agent(src["url"], GOAL) as agent:
            state = None
            for state in agent.run():
                pass
            wait_ready(agent.browser, settle=0.5)
            text, _ = read(agent.browser)
            a = analyze(text)
            row["browser"] = stats(a)
            row["final_url"] = (state or {}).get("page", {}).get("url")
            row["steps"] = len((state or {}).get("history", []))
            row["agent_status"] = (state or {}).get("status")
            row["method"] = "agent" if a["prices"] and a["gi"] else "blocked" if a["blocked"] else "no_price"
            return row, a["text"]
    except Exception as e:
        row.update(method="error", error=f"{type(e).__name__}: {e}"[:200])
        return row, ""


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="用 jev 在本機 Chrome 探測健檢方案來源")
    ap.add_argument("ids", nargs="*", help="只測指定機構 id；不給則測 B1／B2 全部")
    ap.add_argument("--agent", action="store_true", help="改用 jev Agent 自動導航（會呼叫付費 API）")
    ap.add_argument("--all", action="store_true", help="測全部 26 家")
    a = ap.parse_args(argv)

    srcs = json.loads((DATA / "sources.json").read_text("utf-8"))["sources"]
    if a.ids:
        srcs = [s for s in srcs if s["id"] in a.ids]
    elif not a.all:
        srcs = [s for s in srcs if s.get("category") in DEFAULT_CATS]

    if a.agent:
        from jev_ultrafast import Agent
        run_one = lambda s: probe_agent(s, Agent)  # noqa: E731
    else:
        from jev_ultrafast.browser import Browser
        run_one = lambda s: probe_browser(s, Browser)  # noqa: E731

    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for s in srcs:
        t0 = time.monotonic()
        row, text = run_one(s)
        row["seconds"] = round(time.monotonic() - t0, 1)
        if text:
            (OUT / f"{s['id']}.jev.txt").write_text(text, "utf-8")
        rows.append(row)
        err = f"｜{row['error']}" if row.get("error") else ""
        print(f"{summary_line(row)}｜{row['seconds']} 秒{err}", flush=True)

    out = OUT / ("jev_agent_result.json" if a.agent else "jev_result.json")
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), "utf-8")
    print(f"結果存於 {out}")


if __name__ == "__main__":
    main()

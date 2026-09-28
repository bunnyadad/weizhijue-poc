from __future__ import annotations
import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

from . import extract, report, watch
from .data import DATA, load
from .match import INFERRED, OK, UNKNOWN, search
from .parser import parse

PREP = {
    "common": ["身分證字號、生日、聯絡電話（預約表單必填）", "檢查前一晚起空腹（依機構通知）"],
    "colonoscopy": ["大腸鏡需依指示清腸，通常前幾天開始低渣飲食", "確認機構寄送清腸藥的方式與時間"],
    "sedation": ["無痛麻醉須有陪同者，當天不可開車或騎車"],
}


def render(q, results, more, dropped, cat, verbose=False) -> str:
    L = []
    g = {"F": "女性", "M": "男性"}.get(q.gender, "不限")
    need = "、".join(cat.item_name(i) for i in q.required) or "（未指定）"
    budget = f"{q.budget:,} 元內" if q.budget else "不限"
    L += [f"需求：{q.city or '不限地區'}｜{budget}｜{g}｜{need}", ""]
    if not results:
        L.append("找不到符合的方案。")
    for n, r in enumerate(results, 1):
        inst = cat.institutions[r.inst_id]
        price = f"{r.price:,} 元" if r.price else "價格未公開"
        L.append(f"{n}. {inst.name}｜{r.name}｜{price}")
        for iid, (st, why) in r.coverage.items():
            mark = "✓" if st in OK and st != INFERRED else "?" if st in (INFERRED, UNKNOWN) else "✗"
            note = f"（{why}）" if st != "有" else ""
            L.append(f"   {mark} {cat.item_name(iid)}：{st}{note}")
        if r.missing_popular:
            L.append(f"   此方案沒有：{'、'.join(cat.item_name(i) for i in r.missing_popular)}")
        if r.extra_fees:
            L.append(f"   另計：{'、'.join(r.extra_fees)}")
        b = inst.booking
        reply = f"，約 {b['reply_days']} 個工作天回覆" if b.get("reply_days") else ""
        L.append(f"   預約：{b['method']}{reply}" + (f"；{b['calendar']}" if b.get("calendar") else ""))
        L.append(f"   來源：{inst.source_url}｜最後確認 {r.confirmed_at}" + ("（已超過 30 天，請以官網為準）" if r.stale else ""))
        if inst.notes:
            L.append(f"   注意：{inst.notes}")
        L.append("")
    if more:
        L.append(f"另有 {len(more)} 個符合的組合未列出。")
    if dropped:
        L.append(f"已排除 {len(dropped)} 個組合" + ("：" if verbose else "（加 -v 看原因）"))
        if verbose:
            for r in dropped:
                L.append(f"   - {cat.institutions[r.inst_id].name}｜{r.name}：{'；'.join(r.reasons)}")
    if results:
        prep = PREP["common"] + [x for k in ("colonoscopy", "sedation") if k in q.required for x in PREP[k]]
        L += ["", "預約準備清單："] + [f"   □ {x}" for x in prep]
    L += ["", "※ 本工具只呈現方案內容，不構成醫療建議；檢查項目請與醫師討論，價格與內容以官方為準。"]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wzj", description="微直覺 PoC")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="一句話搜尋方案")
    s.add_argument("text")
    s.add_argument("-n", type=int, default=3)
    s.add_argument("-v", action="store_true")
    s.add_argument("--json", action="store_true")
    s.add_argument("--today", help="模擬日期 YYYY-MM-DD")
    sub.add_parser("report", help="Go / No-go 指標")
    sub.add_parser("watch", help="抓取來源並偵測變動")
    pr = sub.add_parser("probe", help="探測雙北候選機構能否自動取得方案資料")
    pr.add_argument("ids", nargs="*", help="只測指定機構 id")
    pr.add_argument("--no-browser", action="store_true", help="不用 headless 瀏覽器")
    e = sub.add_parser("eval", help="LLM 抽取錯誤率")
    e.add_argument("--from", dest="src", required=True, help="LLM 抽取結果 JSON")
    t = sub.add_parser("log-time", help="記錄人工標準化耗時")
    t.add_argument("plan_id")
    t.add_argument("minutes", type=float)
    b = sub.add_parser("bench", help="用需求句產出對照測試表")
    b.add_argument("--out", default="bench.csv")
    a = ap.parse_args(argv)

    cat = load()
    if a.cmd == "search":
        q = parse(a.text, cat)
        today = date.fromisoformat(a.today) if a.today else None
        res, more, dropped = search(q, cat, today, a.n)
        if a.json:
            print(json.dumps([{
                "inst": cat.institutions[r.inst_id].name, "plan": r.name, "price": r.price,
                "coverage": {cat.item_name(k): v[0] for k, v in r.coverage.items()},
                "confirmed_at": r.confirmed_at,
            } for r in res], ensure_ascii=False, indent=2))
        else:
            print(render(q, res, more, dropped, cat, a.v))
    elif a.cmd == "report":
        print(report.render(report.metrics(cat)))
    elif a.cmd == "watch":
        for r in watch.check(cat):
            st = ("價格有變動" if r["changed"] else "正文有變動（價格未變）" if r["text_changed"] else "無變動") \
                if r["ok"] else f"失敗 {r['error']}"
            if r["ok"] and not r["prices"]:
                st += "；⚠ 頁面上抓不到任何價格（可能要 JS 渲染或價格在 PDF），此來源無法自動盯價"
            print(f"{r['inst']}：{st}")
    elif a.cmd == "probe":
        from . import probe
        for r in probe.run(a.ids or None, not a.no_browser):
            print(probe.summary_line(r))
        print(f"結果存於 {probe.OUT / 'result.json'}；依結果更新 data/sources.json 的 category")
    elif a.cmd == "eval":
        res = extract.evaluate(cat, extract.FileExtractor(Path(a.src)))
        report.EVAL.write_text(json.dumps(res, ensure_ascii=False, indent=2), "utf-8")
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif a.cmd == "log-time":
        path = DATA / "plans.json"
        raw = json.loads(path.read_text("utf-8"))
        hit = [p for p in raw["plans"] if p["id"] == a.plan_id]
        if not hit:
            sys.exit(f"找不到方案 {a.plan_id}")
        hit[0]["normalize_minutes"] = a.minutes
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", "utf-8")
        print(f"已記錄 {a.plan_id}：{a.minutes} 分鐘")
    elif a.cmd == "bench":
        lines = [l.strip() for l in (DATA / "queries.txt").read_text("utf-8").splitlines() if l.strip()]
        with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["需求句", "來源", "推薦方案", "價格", "價格錯誤", "漏列", "項目錯誤", "備註"])
            for line in lines:
                res, _, _ = search(parse(line, cat), cat)
                top = "；".join(f"{cat.institutions[r.inst_id].name} {r.name}" for r in res)
                w.writerow([line, "微直覺", top, "；".join(str(r.price or "") for r in res), "", "", "", ""])
                for src in ("ChatGPT", "Perplexity"):
                    w.writerow([line, src, "", "", "", "", "", ""])
        print(f"已產生 {a.out}，共 {len(lines)} 句；請填入 ChatGPT／Perplexity 的回答再逐欄評分")


if __name__ == "__main__":
    main()
